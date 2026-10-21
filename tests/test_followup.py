from __future__ import annotations

from pathlib import Path

import pytest

from dra.config import Settings
from dra.followup import FollowupEngine
from dra.llm import ChatResult
from dra.models import ChatUsage
from dra.store import RunStore


class FakeLLM:
    def __init__(self, text: str = "结论来自证据[2]，无效引用[9]。") -> None:
        self.text = text

    def chat(self, messages, **kwargs):
        return ChatResult(
            text=self.text,
            model="fake",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, calls=1),
            latency_ms=1,
        )


def seed(tmp_path: Path) -> tuple[Settings, RunStore, str]:
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(settings.runs_dir)
    run = store.create("父主题")
    run.write_text("report.md", "# 父主题\n\n## 摘要\n\nalpha 的结论。\n")
    def src(n: int, domain: str, title: str, snippet: str) -> dict:
        return {
            "n": n,
            "url": f"https://{domain}/{n}",
            "title": title,
            "domain": domain,
            "fetched_at": "2026-10-20T10:00:00",
            "snippet": snippet,
        }

    run.write_json(
        "sources.json",
        [
            src(1, "a.com", "alpha 文档", "alpha 的证据"),
            src(2, "b.com", "beta 文档", "beta 的证据"),
            src(3, "c.com", "gamma 文档", "gamma 的证据"),
        ],
    )
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "父主题",
            "status": "done",
            "started_at": "2026-10-20T10:00:00",
            "finished_at": "2026-10-20T10:00:01",
            "duration_s": 1,
            "sources": [],
            "notes": [],
        },
    )
    return settings, store, run.id


def test_ask_writes_answer_and_preserves_numbers(tmp_path):
    settings, store, parent = seed(tmp_path)
    engine = FollowupEngine(settings, store, llm=FakeLLM())
    result = engine.ask(parent, "beta 是什么？")

    assert result.cited_ns == [2]
    assert "[2]" in result.answer
    assert "[9]" not in result.answer
    assert result.path.exists()
    assert "## 参考资料" in result.answer

    items = engine.list(parent)
    assert len(items) == 1
    assert items[0]["question"] == "beta 是什么？"

    meta, answer = engine.get(parent, result.followup_id)
    assert meta["id"] == result.followup_id
    assert "[2]" in answer


def test_ask_requires_report(tmp_path):
    settings, store, parent = seed(tmp_path)
    run = store.open(parent)
    (run.dir / "report.md").unlink()
    engine = FollowupEngine(settings, store, llm=FakeLLM())
    with pytest.raises(ValueError):
        engine.ask(parent, "还能问吗？")


def test_strips_model_heading(tmp_path):
    settings, store, parent = seed(tmp_path)
    engine = FollowupEngine(settings, store, llm=FakeLLM("# 模型自拟标题\n\n结论[2]。"))
    result = engine.ask(parent, "beta 是什么？")
    assert "模型自拟标题" not in result.answer
    assert "结论[2]" in result.answer


def test_list_empty(tmp_path):
    settings, store, parent = seed(tmp_path)
    engine = FollowupEngine(settings, store, llm=FakeLLM())
    assert engine.list(parent) == []


def test_chain_followup(tmp_path):
    settings, store, parent = seed(tmp_path)
    engine = FollowupEngine(settings, store, llm=FakeLLM("追问结论[2]。"))

    first = engine.ask(parent, "beta 是什么？")
    second = engine.ask(first.followup_id, "那 gamma 呢？")

    assert second.parent_run_id == parent
    assert second.parent_followup == first.followup_id
    assert "的追问" in second.answer

    items = engine.list(parent)
    assert len(items) == 2
    assert items[0]["parent_followup"] == first.followup_id

    children = engine.list(first.followup_id)
    assert [item["id"] for item in children] == [second.followup_id]


def test_chain_followup_unknown_parent(tmp_path):
    settings, store, _ = seed(tmp_path)
    engine = FollowupEngine(settings, store, llm=FakeLLM())
    with pytest.raises(FileNotFoundError):
        engine.ask("20261021-000000-abcd", "问不到？")
