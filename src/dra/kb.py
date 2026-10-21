"""本地知识库：报告切块、向量化、混合检索（BM25 + 向量 + RRF）。

设计取舍：
- 不引入向量数据库，SQLite 存元数据与 embedding，规模在几千块以内足够；
- 中文检索用「字 bigram」做 BM25，避免 SQLite FTS 对中文分词不友好的问题；
- 没有 API Key 或向量失败时自动退化为纯关键词检索，不阻塞使用。
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dra.config import Settings
from dra.llm import ModelAdapter

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    title TEXT NOT NULL,
    source_path TEXT,
    content_hash TEXT UNIQUE,
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    chars INTEGER NOT NULL,
    embedding BLOB,
    dim INTEGER DEFAULT 0,
    model TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """英文按词、中文按字 bigram 切分，供 BM25 使用。"""
    lowered = (text or "").lower()
    tokens = WORD_RE.findall(lowered)
    for segment in CJK_RE.findall(lowered):
        if len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return tokens


def chunk_text(text: str, target: int = 700, overlap: int = 100) -> list[str]:
    """按小节与段落切块；跳过“参考资料”这类纯链接段。"""
    cleaned = re.sub(r"\r\n?", "\n", text or "").strip()
    if not cleaned:
        return []

    sections: list[str] = []
    current: list[str] = []
    skip = False
    for line in cleaned.split("\n"):
        if line.startswith("## "):
            if current and not skip:
                sections.append("\n".join(current).strip())
            current = [line]
            skip = "参考资料" in line
            continue
        if not skip:
            current.append(line)
    if current and not skip:
        sections.append("\n".join(current).strip())

    chunks: list[str] = []
    for section in sections:
        if not section:
            continue
        if len(section) <= target:
            chunks.append(section)
            continue
        paragraphs = [p.strip() for p in section.split("\n\n") if p.strip()]
        buffer = ""
        for para in paragraphs:
            if len(buffer) + len(para) + 2 <= target:
                buffer = f"{buffer}\n\n{para}" if buffer else para
                continue
            if buffer:
                chunks.append(buffer)
            if len(para) <= target:
                buffer = para
            else:
                step = max(200, target - overlap)
                for start in range(0, len(para), step):
                    piece = para[start : start + target]
                    if piece.strip():
                        chunks.append(piece.strip())
                buffer = ""
        if buffer:
            chunks.append(buffer)
    return chunks


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def bm25_scores(query: str, texts: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """对给定文本集合计算 BM25 分数。"""
    q_terms = set(tokenize(query))
    if not q_terms:
        return [0.0] * len(texts)
    docs = [tokenize(t) for t in texts]
    depths = [len(d) or 1 for d in docs]
    avg = sum(depths) / len(depths)
    df: Counter[str] = Counter()
    for doc in docs:
        for term in set(doc):
            if term in q_terms:
                df[term] += 1
    total = len(docs)
    scores = []
    for doc, depth in zip(docs, depths, strict=False):
        tf = Counter(doc)
        score = 0.0
        for term in q_terms:
            if not tf.get(term):
                continue
            idf = math.log(1 + (total - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1 - b + b * depth / avg)
            score += idf * (tf[term] * (k1 + 1)) / denom
        scores.append(score)
    return scores


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion：把多路排名合成一个分数。"""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank)
    return fused


@dataclass
class SearchHit:
    chunk_id: int
    doc_id: int
    title: str
    run_id: str | None
    seq: int
    text: str
    score: float
    keyword: float
    vector: float
    source_path: str | None


class KnowledgeBase:
    """一个 SQLite 文件就是一个知识库。"""

    def __init__(
        self,
        settings: Settings,
        *,
        path: Path | None = None,
        adapter: ModelAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.path = Path(path or settings.kb_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------------- write
    def add_report(
        self,
        *,
        run_id: str,
        title: str,
        text: str,
        source_path: str | None = None,
    ) -> tuple[int, int]:
        """导入一份报告；返回 (doc_id, 新增块数)。内容未变则直接返回已有 id。"""
        digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
        row = self._conn.execute(
            "SELECT id FROM documents WHERE content_hash = ?", (digest,)
        ).fetchone()
        if row:
            return int(row["id"]), 0

        pieces = chunk_text(text)
        if not pieces:
            raise ValueError("内容为空，无法导入")

        vectors: list[list[float]] = []
        model = ""
        if self.adapter is not None:
            try:
                vectors = self.adapter.embed(pieces)
                model = self.settings.embedding_model
            except Exception:
                vectors = []

        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO documents(run_id, title, source_path, content_hash, chunk_count, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    title,
                    source_path,
                    digest,
                    len(pieces),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            doc_id = int(cur.lastrowid)
            for seq, piece in enumerate(pieces, start=1):
                vector = vectors[seq - 1] if len(vectors) >= seq else None
                self._conn.execute(
                    "INSERT INTO chunks(doc_id, seq, text, chars, embedding, dim, model)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        seq,
                        piece,
                        len(piece),
                        _pack(vector) if vector else None,
                        len(vector) if vector else 0,
                        model if vector else None,
                    ),
                )
        return doc_id, len(pieces)

    def remove(self, doc_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        return cur.rowcount > 0

    # ---------------------------------------------------------------- read
    def list_documents(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL)"
            " AS embedded FROM documents d ORDER BY d.id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, doc_id: int) -> dict[str, Any] | None:
        """读取单篇文档及其全部分块（供界面查看）。"""
        row = self._conn.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL)"
            " AS embedded FROM documents d WHERE d.id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        chunks = self._conn.execute(
            "SELECT seq, text, chars, dim, model,"
            " CASE WHEN embedding IS NULL THEN 0 ELSE 1 END AS embedded"
            " FROM chunks WHERE doc_id = ? ORDER BY seq ASC",
            (doc_id,),
        ).fetchall()
        return {"document": dict(row), "chunks": [dict(item) for item in chunks]}

    def stats(self) -> dict[str, Any]:
        doc_count = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        embedded = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        return {
            "path": str(self.path),
            "documents": doc_count,
            "chunks": chunk_count,
            "embedded": embedded,
        }

    def search(self, query: str, k: int = 6) -> tuple[list[SearchHit], dict[str, Any]]:
        """混合检索：BM25 + 向量，RRF 融合。向量不可用时自动只用关键词。"""
        rows = self._conn.execute(
            "SELECT c.id, c.doc_id, c.seq, c.text, c.embedding, c.dim,"
            " d.title, d.run_id, d.source_path"
            " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        ).fetchall()
        if not rows:
            return [], {"mode": "empty"}

        texts = [row["text"] for row in rows]
        keyword_scores = bm25_scores(query, texts)

        vector_scores = [0.0] * len(rows)
        mode = "keyword"
        if self.adapter is not None:
            try:
                query_vec = self.adapter.embed([query])[0]
            except Exception:
                query_vec = []
            if query_vec:
                mode = "hybrid"
                for index, row in enumerate(rows):
                    if row["embedding"]:
                        vector_scores[index] = _cosine(
                            query_vec, _unpack(row["embedding"], row["dim"])
                        )

        kw_rank = sorted(
            [i for i, score in enumerate(keyword_scores) if score > 0],
            key=lambda i: keyword_scores[i],
            reverse=True,
        )
        vec_rank = sorted(
            [i for i, score in enumerate(vector_scores) if score > 0],
            key=lambda i: vector_scores[i],
            reverse=True,
        )
        fused = rrf_fuse([kw_rank, vec_rank])
        if not fused:
            return [], {"mode": mode, "keyword_hits": 0, "vector_hits": 0}

        order = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
        hits = []
        for index in order:
            row = rows[index]
            hits.append(
                SearchHit(
                    chunk_id=row["id"],
                    doc_id=row["doc_id"],
                    title=row["title"],
                    run_id=row["run_id"],
                    seq=row["seq"],
                    text=row["text"],
                    score=fused[index],
                    keyword=keyword_scores[index],
                    vector=vector_scores[index],
                    source_path=row["source_path"],
                )
            )
        return hits, {
            "mode": mode,
            "keyword_hits": len(kw_rank),
            "vector_hits": len(vec_rank),
        }

    def export_index(self) -> dict[str, Any]:
        """给 UI / 调试用的索引概览。"""
        return {"stats": self.stats(), "documents": self.list_documents()}
