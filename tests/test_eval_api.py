from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dra.config import Settings
from dra.jobs import RunManager
from dra.store import RunStore
from dra.web.server import create_app


class FakeAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None):
        return None


def seed(store: RunStore) -> str:
    run = store.create("评估主题")
    run.write_text("report.md", "# 报告\n\n延迟 15ms[1]。\n\n## 参考资料\n\n1. 论文 — <https://arxiv.org/abs/1>\n")
    run.write_json(
        "sources.json",
        [
            {
                "n": 1,
                "url": "https://arxiv.org/abs/1",
                "title": "论文",
                "domain": "arxiv.org",
                "tier": 3,
                "tier_label": "一手",
                "quality": 90,
                "snippet": "延迟 15ms。",
                "fetched_at": "2026-10-21T00:00:00",
            }
        ],
    )
    run.write_json(
        "run.json",
        {"run_id": run.id, "topic": "评估主题", "status": "done", "sources": [], "notes": []},
    )
    return run.id


def build(tmp_path: Path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(settings.runs_dir)
    manager = RunManager(settings, store=store, agent_factory=lambda: FakeAgent())
    return TestClient(create_app(settings, manager=manager)), store


def test_eval_endpoint(tmp_path):
    client, store = build(tmp_path)
    rid = seed(store)
    result = client.get(f"/api/runs/{rid}/eval").json()
    assert 0 <= result["score"] <= 100
    assert result["numbers"]["support_rate"] == 1.0
    assert (store.open(rid).dir / "eval.json").exists()
