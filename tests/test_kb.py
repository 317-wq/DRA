from __future__ import annotations

from pathlib import Path

from dra.config import Settings
from dra.kb import KnowledgeBase, bm25_scores, chunk_text, rrf_fuse, tokenize


class FakeAdapter:
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            lowered = text.lower()
            out.append([1.0, 0.0] if "alpha" in lowered else [0.0, 1.0])
        return out


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )


def test_tokenize_cjk_bigrams():
    tokens = tokenize("换电 模式")
    assert "换电" in tokens
    assert "模式" in tokens
    assert "alpha" in tokenize("Alpha")


def test_chunk_text_skips_references():
    text = "## 摘要\n\n" + "结论。" * 200 + "\n\n## 参考资料\n\n1. https://example.com"
    chunks = chunk_text(text)
    assert chunks
    assert all("https://example.com" not in chunk for chunk in chunks)
    assert all(len(chunk) <= 800 for chunk in chunks)


def test_bm25_ranks_relevant_first():
    texts = ["alpha beta gamma", "完全无关的文本", "alpha alpha"]
    scores = bm25_scores("alpha", texts)
    assert scores[0] > scores[1]
    assert scores[2] > scores[1]


def test_rrf_fuse_prefers_agreement():
    fused = rrf_fuse([[0, 1, 2], [1, 0]])
    assert fused[0] > fused[2]
    assert fused[1] > fused[2]


def test_add_search_list_remove(tmp_path):
    settings = make_settings(tmp_path)
    kb = KnowledgeBase(settings, adapter=FakeAdapter())
    doc_id, chunks = kb.add_report(
        run_id="r1",
        title="Alpha 报告",
        text="## 摘要\n\nalpha 是关键字。\n\n## 细节\n\nalpha 还有一些细节。",
    )
    assert chunks > 0
    docs = kb.list_documents()
    assert docs and docs[0]["title"] == "Alpha 报告"

    hits, info = kb.search("alpha", k=3)
    assert hits
    assert info["mode"] == "hybrid"
    assert hits[0].title == "Alpha 报告"

    same_text = "## 摘要\n\nalpha 是关键字。\n\n## 细节\n\nalpha 还有一些细节。"
    again_id, again_chunks = kb.add_report(run_id="r1", title="Alpha 报告", text=same_text)
    assert again_id == doc_id and again_chunks == 0

    assert kb.remove(doc_id) is True
    assert kb.list_documents() == []
    kb.close()


def test_keyword_only_without_adapter(tmp_path):
    settings = make_settings(tmp_path)
    kb = KnowledgeBase(settings, adapter=None)
    kb.add_report(run_id="r1", title="无向量", text="## 摘要\n\nalpha 只有关键词。")
    hits, info = kb.search("alpha")
    assert info["mode"] == "keyword"
    assert hits
    kb.close()
