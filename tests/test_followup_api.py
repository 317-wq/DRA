from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dra.config import Settings
from dra.followup import FollowupEngine
from dra.jobs import RunManager
from dra.llm import ChatResult
from dra.models import ChatUsage
from dra.store import RunStore
from dra.web.server import create_app


class FakeLLM:
    def chat(self, messages, **kwargs):
        return ChatResult(
            text="追问结论[2]。",
            model="fake",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, calls=1),
            latency_ms=1,
        )


class FakeAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None):
        return None


def seed(store: RunStore) -> str:
    run = store.create("父主题")
    run.write_text("report.md", "# 父主题\n\n## 摘要\n\nalpha 的结论。\n")
    run.write_json(
        "sources.json",
        [
            {
                "n": 2,
                "url": "https://b.com/2",
                "title": "beta 文档",
                "domain": "b.com",
                "fetched_at": "2026-10-21T00:00:00",
                "snippet": "beta 的证据",
            }
        ],
    )
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "父主题",
            "status": "done",
            "started_at": "2026-10-21T00:00:00",
            "finished_at": "2026-10-21T00:00:01",
            "duration_s": 1,
            "sources": [],
            "notes": [],
        },
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
    app = create_app(
        settings,
        manager=manager,
        followup_factory=lambda: FollowupEngine(settings, store, llm=FakeLLM()),
    )
    return TestClient(app), store


def test_followup_flow(tmp_path):
    client, store = build(tmp_path)
    parent = seed(store)

    assert client.get(f"/api/runs/{parent}/followups").json()["items"] == []

    created = client.post(
        f"/api/runs/{parent}/followups", json={"question": "beta 是什么？", "k": 3}
    )
    assert created.status_code == 200
    body = created.json()
    assert body["cited_ns"] == [2]

    items = client.get(f"/api/runs/{parent}/followups").json()["items"]
    assert len(items) == 1

    detail = client.get(f"/api/runs/{parent}/followups/{body['id']}").json()
    assert "[2]" in detail["answer"]
    assert "<h1>" in detail["html"]


def test_followup_not_found(tmp_path):
    client, store = build(tmp_path)
    parent = seed(store)
    assert client.get(f"/api/runs/{parent}/followups/20261021-000000-abcd").status_code == 404


def test_followup_without_report(tmp_path):
    client, store = build(tmp_path)
    parent = seed(store)
    run = store.open(parent)
    (run.dir / "report.md").unlink()
    res = client.post(f"/api/runs/{parent}/followups", json={"question": "还能问吗？"})
    assert res.status_code == 409
