"""追问：复用某次研究的证据回答新问题，默认不重新检索。

设计取舍：
- 追问的价值在于"基于同一批证据继续深挖"，重新搜一遍既费额度又会引入新口径；
- 证据选择复用知识库的 BM25 实现，避免再写一套排序；
- 引用沿用父报告的编号（renumber=False），保证 [n] 在两份文档里指向同一来源。
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dra.citations import count_chinese_chars, finalize_report
from dra.config import Settings
from dra.kb import bm25_scores
from dra.llm import ModelAdapter
from dra.models import Source
from dra.prompts import followup_messages
from dra.store import Run, RunStore

FOLLOWUP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


@dataclass
class FollowupResult:
    parent_id: str
    parent_run_id: str
    parent_followup: str | None
    followup_id: str
    question: str
    answer: str
    cited_ns: list[int]
    chars: int
    path: Path
    duration_s: float


class FollowupEngine:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        *,
        llm: ModelAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm or ModelAdapter(settings)

    # ------------------------------------------------------------------ ask
    def ask(self, parent_id: str, question: str, *, k: int = 6) -> FollowupResult:
        question = question.strip()
        if not question:
            raise ValueError("追问不能为空")
        run, meta, excerpt_path, parent_followup = self._resolve(parent_id)
        if meta.get("status") == "running":
            raise ValueError("这次研究还在进行中，等它结束再追问")
        if not excerpt_path.exists():
            raise ValueError("父级内容不存在，无法追问")
        sources_raw = self._read_json(run.dir / "sources.json")
        sources = [Source(**item) for item in sources_raw]
        if not sources:
            raise ValueError("这次研究没有可用的来源")

        # 追问树：用“父追问 + 新问题”一起选证据，避免上下文丢失
        parent_question = ""
        if parent_followup:
            parent_meta = self._read_json(excerpt_path.parent / "meta.json", default={})
            parent_question = str(parent_meta.get("question") or "")
        selection_query = f"{parent_question} {question}".strip()
        selected = self._select(selection_query, sources, k)
        started = time.monotonic()
        res = self.llm.chat(
            followup_messages(
                topic=meta.get("topic") or parent_id,
                report_excerpt=excerpt_path.read_text(encoding="utf-8"),
                sources=selected,
                question=question,
            ),
            model=self.settings.write_model_resolved,
            temperature=0.4,
            max_tokens=3000,
        )
        # 去掉模型自拟的一级标题，避免和“# 追问：…”重复
        text = re.sub(r"^#\s+[^\n]*\n+", "", res.text.strip(), count=1)
        body, cited, dropped = finalize_report(text, selected, renumber=False)

        fid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        folder = run.dir / "followups" / fid
        folder.mkdir(parents=True, exist_ok=True)
        origin = "的追问" if parent_followup else ""
        header = (
            f"# 追问：{question}\n\n"
            f"> 基于《{meta.get('topic') or run.id}》{origin} ｜ 引用 {len(cited)} 篇 ｜ "
            f"{datetime.now().strftime('%Y-%m-%d')}\n\n"
        )
        answer = header + body
        (folder / "answer.md").write_text(answer, encoding="utf-8")
        duration = round(time.monotonic() - started, 1)
        record = {
            "id": fid,
            "parent_run_id": run.id,
            "parent_followup": parent_followup,
            "question": question,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "chars": count_chinese_chars(answer),
            "cited_ns": [src.n for src in cited],
            "model": res.model,
            "tokens": res.usage.total_tokens,
            "duration_s": duration,
            "dropped": dropped,
        }
        (folder / "meta.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._append_trace(run, fid, question, selected, record, res)
        self._update_index(run, record)
        return FollowupResult(
            parent_id=parent_id,
            parent_run_id=run.id,
            parent_followup=parent_followup,
            followup_id=fid,
            question=question,
            answer=answer,
            cited_ns=record["cited_ns"],
            chars=record["chars"],
            path=folder / "answer.md",
            duration_s=duration,
        )

    # -------------------------------------------------------------- read
    def list(self, parent_id: str) -> list[dict[str, Any]]:
        run, _, _, parent_followup = self._resolve(parent_id)
        items = self._read_json(run.dir / "followups.json", default=[])
        if parent_followup:
            items = [item for item in items if item.get("parent_followup") == parent_followup]
        return items

    def get(self, parent_id: str, followup_id: str) -> tuple[dict[str, Any], str]:
        if not FOLLOWUP_ID_RE.match(followup_id):
            raise FileNotFoundError("追问不存在")
        run = self.store.open(parent_id)
        folder = run.dir / "followups" / followup_id
        meta_path = folder / "meta.json"
        answer_path = folder / "answer.md"
        if not meta_path.exists() or not answer_path.exists():
            raise FileNotFoundError("追问不存在")
        return self._read_json(meta_path), answer_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------- helpers
    def _resolve(self, parent_id: str) -> tuple[Run, dict[str, Any], Path, str | None]:
        """父级可以是 run，也可以是一条追问；返回 (run, run_meta, 上下文文件, 父追问 id)。"""
        try:
            run = self.store.open(parent_id)
            meta = self._read_json(run.dir / "run.json")
            return run, meta, run.dir / "report.md", None
        except FileNotFoundError:
            pass
        if not FOLLOWUP_ID_RE.match(parent_id):
            raise FileNotFoundError(f"找不到父级：{parent_id}")
        for run_dir in self.store.root.iterdir():
            folder = run_dir / "followups" / parent_id
            meta_path = folder / "meta.json"
            if not meta_path.exists():
                continue
            try:
                run = self.store.open(run_dir.name)
            except FileNotFoundError:
                continue
            return run, self._read_json(run.dir / "run.json"), folder / "answer.md", parent_id
        raise FileNotFoundError(f"找不到追问：{parent_id}")

    def _select(self, question: str, sources: list[Source], k: int) -> list[Source]:
        texts = [f"{src.title} {src.snippet}" for src in sources]
        scores = bm25_scores(question, texts)
        order = sorted(range(len(sources)), key=lambda i: scores[i], reverse=True)
        selected = [sources[i] for i in order[: max(1, k)] if scores[i] > 0]
        if not selected:
            selected = sources[: max(1, k)]
        return selected

    def _update_index(self, run: Run, record: dict[str, Any]) -> None:
        path = run.dir / "followups.json"
        items = self._read_json(path, default=[])
        items.insert(0, record)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_trace(self, run: Run, fid: str, question: str, selected: list[Source],
                      record: dict[str, Any], res) -> None:
        path = run.dir / "followups" / fid / "trace.jsonl"
        events = [
            {"ts": record["created_at"], "type": "followup.started", "payload": {"question": question}},
            {
                "ts": record["created_at"],
                "type": "followup.evidence",
                "payload": {"selected": [src.n for src in selected]},
            },
            {
                "ts": record["created_at"],
                "type": "followup.llm",
                "payload": {"model": res.model, "tokens": res.usage.total_tokens},
            },
            {"ts": record["created_at"], "type": "followup.finished", "payload": record},
        ]
        path.write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        if not path.exists():
            return {} if default is None else default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {} if default is None else default
