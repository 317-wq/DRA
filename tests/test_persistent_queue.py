from __future__ import annotations

from pathlib import Path

from dra.config import Settings
from dra.jobs import JobStore, RunManager
from dra.store import RunStore


class DoneAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        run.write_json(
            "run.json",
            {"run_id": run.id, "topic": topic, "status": "done", "sources": [], "notes": []},
        )


def build(tmp_path: Path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path,
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(tmp_path)
    manager = RunManager(settings, store=store, agent_factory=lambda: DoneAgent())
    return manager, store, JobStore(tmp_path / ".jobs.sqlite3")


def test_queued_job_survives_restart(tmp_path):
    store = RunStore(tmp_path)
    run = store.create("重启后继续")
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "重启后继续",
            "status": "queued",
            "requested_max_sources": 6,
            "sources": [],
            "notes": [],
        },
    )
    JobStore(tmp_path / ".jobs.sqlite3").upsert(run.id, "重启后继续", 6, "queued")

    manager, store, jobs = build(tmp_path)
    assert manager.wait_idle(5)
    assert manager.meta(run.id)["status"] == "done"
    assert jobs.get(run.id)["status"] == "done"
    manager.close()


def test_running_job_marked_interrupted(tmp_path):
    store = RunStore(tmp_path)
    run = store.create("上次跑一半")
    run.write_json(
        "run.json",
        {"run_id": run.id, "topic": "上次跑一半", "status": "running", "sources": [], "notes": []},
    )
    JobStore(tmp_path / ".jobs.sqlite3").upsert(run.id, "上次跑一半", 6, "running")

    manager, store, jobs = build(tmp_path)
    meta = manager.meta(run.id)
    assert meta["status"] == "interrupted"
    assert "服务重启" in meta["notes"][0]
    assert jobs.get(run.id)["status"] == "interrupted"
    assert not manager.is_running(run.id)
    manager.close()


def test_job_status_persisted_during_run(tmp_path):
    manager, store, jobs = build(tmp_path)
    rid = manager.start("正常跑完", 6)
    assert jobs.get(rid)["status"] in ("queued", "running", "done")
    assert manager.wait_idle(5)
    assert jobs.get(rid)["status"] == "done"
    manager.close()
