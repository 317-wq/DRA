from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from dra.config import Settings
from dra.jobs import RunManager
from dra.store import RunStore


class RecordingAgent:
    def __init__(self) -> None:
        self.topics: list[str] = []

    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        self.topics.append(topic)
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "done",
                "finished_at": "2026-10-21T00:00:00",
                "sources": [],
                "notes": [],
            },
        )


class BlockingAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        self.started.set()
        cancelled = cancel_event.wait(3.0) if cancel_event is not None else False
        if not cancelled:
            self.release.wait(3.0)
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "cancelled" if cancelled else "done",
                "sources": [],
                "notes": [],
            },
        )


def build(tmp_path: Path, agent) -> tuple[RunManager, RunStore]:
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path,
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(tmp_path)
    manager = RunManager(settings, store=store, agent_factory=lambda: agent)
    return manager, store


class GateAgent(RecordingAgent):
    """第一个任务阻塞，确保第二个任务真的处于排队状态。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        self.topics.append(topic)
        if topic == "第一个":
            self.started.set()
            self.release.wait(3.0)
        run.write_json(
            "run.json",
            {"run_id": run.id, "topic": topic, "status": "done", "sources": [], "notes": []},
        )


def test_queue_runs_in_order(tmp_path):
    agent = GateAgent()
    manager, store = build(tmp_path, agent)
    first = manager.start("第一个", 6)
    assert agent.started.wait(2)
    second = manager.start("第二个", 6)
    assert manager.meta(second).get("status") == "queued"

    agent.release.set()
    assert manager.wait_idle(5)
    assert agent.topics == ["第一个", "第二个"]
    assert manager.meta(first)["status"] == "done"
    assert manager.meta(second)["status"] == "done"
    manager.close()


def test_cancel_queued(tmp_path):
    agent = BlockingAgent()
    manager, store = build(tmp_path, agent)
    first = manager.start("占住 worker", 6)
    assert agent.started.wait(2)
    second = manager.start("排队后取消", 6)
    time.sleep(0.05)

    assert manager.cancel(second) == "queued"
    agent.release.set()
    assert manager.wait_idle(5)
    assert manager.meta(second)["status"] == "cancelled"
    assert manager.meta(first)["status"] == "done"
    manager.close()


def test_cancel_running(tmp_path):
    agent = BlockingAgent()
    manager, store = build(tmp_path, agent)
    run_id = manager.start("运行中取消", 6)
    assert agent.started.wait(2)
    assert manager.cancel(run_id) == "running"
    assert manager.wait_idle(5)
    assert manager.meta(run_id)["status"] == "cancelled"
    manager.close()


def test_retry_creates_new_run(tmp_path):
    manager, store = build(tmp_path, RecordingAgent())
    run = store.create("失败的主题")
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "失败的主题",
            "status": "failed",
            "requested_max_sources": 6,
            "sources": [],
            "notes": ["boom"],
        },
    )
    new_id = manager.retry(run.id)
    assert new_id != run.id
    assert manager.wait_idle(5)
    meta = manager.meta(new_id)
    assert meta["topic"] == "失败的主题"
    assert meta["requested_max_sources"] == 6
    manager.close()


def test_recover_stale_runs(tmp_path):
    store = RunStore(tmp_path)
    run = store.create("上次没跑完")
    run.write_json("run.json", {"run_id": run.id, "topic": "上次没跑完", "status": "running"})

    manager = RunManager(
        Settings(api_key="k", tavily_api_key="t", runs_dir=tmp_path, _env_file=None),
        store=store,
        agent_factory=lambda: RecordingAgent(),
    )
    meta = manager.meta(run.id)
    assert meta["status"] == "interrupted"
    assert "服务重启" in meta["notes"][0]
    manager.close()


def test_delete_run(tmp_path):
    manager, store = build(tmp_path, RecordingAgent())
    run = store.create("要删除的")
    run.write_json("run.json", {"run_id": run.id, "topic": "要删除的", "status": "done"})
    assert manager.delete(run.id) is True
    assert not run.dir.exists()
    assert manager.delete(run.id) is False
    manager.close()


def test_atomic_json_write(tmp_path):
    store = RunStore(tmp_path)
    run = store.create("原子写")
    run.write_json("run.json", {"a": 1})
    run.write_json("run.json", {"a": 2})
    assert json.loads((run.dir / "run.json").read_text(encoding="utf-8"))["a"] == 2
    leftovers = [p.name for p in run.dir.iterdir() if p.name.startswith(".")]
    assert leftovers == []
