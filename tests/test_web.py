from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dra.config import Settings
from dra.jobs import RunManager
from dra.store import RunStore
from dra.web.server import create_app, find_free_port


class FakeAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None):
        run.write_text("report.md", "# 测试报告\n\n结论[1]。\n")
        run.write_json("sources.json", [{"n": 1, "url": "https://example.com", "title": "示例"}])
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "done",
                "started_at": "2026-10-20T10:00:00",
                "finished_at": "2026-10-20T10:00:01",
                "duration_s": 1,
                "sources": [],
                "notes": [],
                "usage": {"total_tokens": 10},
            },
        )
        return None


def build_client(tmp_path: Path):
    settings = Settings(api_key="k", tavily_api_key="t", runs_dir=tmp_path, _env_file=None)
    store = RunStore(tmp_path)
    manager = RunManager(settings, store=store, agent_factory=lambda: FakeAgent())
    app = create_app(settings, manager=manager)
    return TestClient(app), store, manager


def seed_run(store: RunStore) -> str:
    run = store.create("已完成的主题")
    run.write_text("report.md", "# 标题\n\n正文[1]\n\n## 参考资料\n\n1. 示例 — <https://example.com>\n")
    run.write_json(
        "sources.json",
        [{"n": 1, "url": "https://example.com", "title": "示例", "domain": "example.com"}],
    )
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "已完成的主题",
            "status": "done",
            "started_at": "2026-10-20T10:00:00",
            "finished_at": "2026-10-20T10:00:01",
            "duration_s": 1,
            "sources": [{"n": 1}],
            "notes": [],
        },
    )
    events = [
        {
            "ts": "2026-10-20T10:00:00",
            "run_id": run.id,
            "seq": 1,
            "type": "run.started",
            "stage": "start",
            "payload": {},
        },
        {
            "ts": "2026-10-20T10:00:01",
            "run_id": run.id,
            "seq": 2,
            "type": "run.finished",
            "stage": "done",
            "payload": {"status": "done"},
        },
    ]
    run.write_text("trace.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
    return run.id


def test_index_and_history(tmp_path):
    client, store, _ = build_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "Deep Research" in r.text

    rid = seed_run(store)
    data = client.get("/api/runs").json()
    assert any(item["run_id"] == rid for item in data["runs"])


def test_report_and_sources(tmp_path):
    client, store, _ = build_client(tmp_path)
    rid = seed_run(store)

    html = client.get(f"/api/runs/{rid}/report").text
    assert "<h1>" in html
    assert "<table>" not in html  # 简单正文不应凭空出现表格

    sources = client.get(f"/api/runs/{rid}/sources").json()
    assert sources[0]["url"] == "https://example.com"


def test_start_run_and_sse(tmp_path):
    client, store, manager = build_client(tmp_path)
    resp = client.post("/api/runs", json={"topic": "新主题", "max_sources": 8})
    assert resp.status_code == 202
    rid = resp.json()["run_id"]

    deadline = time.time() + 5
    while manager.is_running(rid) and time.time() < deadline:
        time.sleep(0.05)

    meta = client.get(f"/api/runs/{rid}").json()
    assert meta["status"] == "done"


def test_unknown_and_invalid_run_ids(tmp_path):
    client, _, _ = build_client(tmp_path)
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.get("/api/runs/../../etc/passwd").status_code == 404


def test_find_free_port():
    # 用 0 让系统分配，避免共享 runner 上的端口冲突
    port = find_free_port("127.0.0.1", 0)
    assert port > 0
