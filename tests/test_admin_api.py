from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dra import __version__
from dra.config import Settings
from dra.jobs import RunManager
from dra.store import RunStore
from dra.web.server import create_app


class DoneAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "done",
                "sources": [],
                "notes": [],
            },
        )


def build(tmp_path: Path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(settings.runs_dir)
    manager = RunManager(settings, store=store, agent_factory=lambda: DoneAgent())
    return TestClient(create_app(settings, manager=manager)), store, manager


def seed(store: RunStore, topic: str, status: str = "done") -> str:
    run = store.create(topic)
    run.write_text("report.md", f"# {topic}\n\n正文[1]。\n\n## 参考资料\n\n1. 示例 — <https://example.com>\n")
    run.write_json(
        "sources.json",
        [
            {
                "n": 1,
                "url": "https://example.com",
                "title": "示例",
                "domain": "example.com",
                "snippet": "正文",
                "fetched_at": "2026-10-21T00:00:00",
            }
        ],
    )
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": topic,
            "status": status,
            "requested_max_sources": 6,
            "sources": [],
            "notes": [],
        },
    )
    return run.id


def test_health_and_pagination(tmp_path):
    client, store, manager = build(tmp_path)
    seed(store, "旧一点")
    seed(store, "新一点")

    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["version"] == __version__
    assert health["runs"] == 2

    page = client.get("/api/runs", params={"limit": 1, "offset": 0}).json()
    assert page["total"] == 2
    assert len(page["runs"]) == 1
    assert page["runs"][0]["topic"] in {"旧一点", "新一点"}

    filtered = client.get("/api/runs", params={"status": "done"}).json()
    assert filtered["total"] == 2
    manager.close()


def test_export_and_delete(tmp_path):
    client, store, manager = build(tmp_path)
    rid = seed(store, "导出主题")

    md = client.get(f"/api/runs/{rid}/export", params={"format": "md"})
    assert md.status_code == 200
    assert "markdown" in md.headers["content-type"]
    assert "attachment" in md.headers["content-disposition"]
    assert md.text.startswith("# 导出主题")

    html = client.get(f"/api/runs/{rid}/export", params={"format": "html"})
    assert "<!doctype html>" in html.text
    assert "导出主题" in html.text

    removed = client.delete(f"/api/runs/{rid}").json()
    assert removed["removed"] is True
    assert client.get(f"/api/runs/{rid}").status_code == 404
    manager.close()


def test_retry_endpoint(tmp_path):
    client, store, manager = build(tmp_path)
    rid = seed(store, "需要重试", status="failed")

    resp = client.post(f"/api/runs/{rid}/retry")
    assert resp.status_code == 202
    new_id = resp.json()["run_id"]
    assert new_id != rid
    assert manager.wait_idle(5)
    assert client.get(f"/api/runs/{new_id}").json()["status"] == "done"
    manager.close()


def test_settings_read_write(tmp_path, monkeypatch):
    client, store, manager = build(tmp_path)
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))

    initial = client.get("/api/settings").json()
    assert initial["config_file"] == str(cfg)

    updated = client.put("/api/settings", json={"model": "test-model", "min_number_support": 0.6})
    assert updated.status_code == 200
    assert "model" in updated.json()["updated"]
    assert cfg.exists()

    after = client.get("/api/settings").json()
    assert after["model"] == "test-model"
    assert after["min_number_support"] == 0.6
    manager.close()
