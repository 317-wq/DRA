"""后台研究任务管理：UI 只负责发起与订阅，不直接碰 Agent 内部。

0.3 是本地单用户工具，所以一次只允许一个研究在跑；并发留给 1.0。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dra.budget import Budget
from dra.config import Settings
from dra.store import Run, RunStore


class BusyError(RuntimeError):
    """已有研究在跑。"""


@dataclass
class Job:
    run: Run
    topic: str
    thread: threading.Thread


AgentFactory = Callable[[], Any]


def read_new_events(path: Path, sent: int) -> tuple[list[dict[str, Any]], int]:
    """从 trace.jsonl 读取尚未发送的完整事件行；最后一行不完整就留到下次。"""
    if not path.exists():
        return [], sent
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], sent
    events: list[dict[str, Any]] = []
    index = sent
    while index < len(lines):
        try:
            events.append(json.loads(lines[index]))
        except json.JSONDecodeError:
            break
        index += 1
    return events, index


def read_all_events(path: Path) -> list[dict[str, Any]]:
    """读取全部事件（用于历史回放）。"""
    events, _ = read_new_events(path, 0)
    return events


class RunManager:
    """发起研究、维护运行状态、提供 run 读取。"""

    def __init__(
        self,
        settings: Settings,
        *,
        store: RunStore | None = None,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or RunStore(settings.runs_dir)
        if agent_factory is None:
            from dra.agent import ResearchAgent

            agent_factory = lambda: ResearchAgent(settings, store=self.store)  # noqa: E731
        self._factory = agent_factory
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: str | None = None

    # ------------------------------------------------------------------ start
    def start(self, topic: str, max_sources: int = 12) -> str:
        """创建一个 run 并在后台线程里执行，立刻返回 run_id。"""
        with self._lock:
            if self._active and self.is_running(self._active):
                raise BusyError("已有研究在进行中，等它结束再开新的")
            run = self.store.create(topic)
            run.write_json(
                "run.json",
                {
                    "run_id": run.id,
                    "topic": topic,
                    "status": "running",
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "sources": [],
                    "notes": [],
                },
            )
            thread = threading.Thread(
                target=self._work,
                args=(run, topic, max_sources),
                daemon=True,
                name=f"dra-{run.id}",
            )
            self._jobs[run.id] = Job(run=run, topic=topic, thread=thread)
            self._active = run.id
            thread.start()
            return run.id

    def _work(self, run: Run, topic: str, max_sources: int) -> None:
        try:
            agent = self._factory()
            agent.run(topic, budget=Budget(), max_sources=max_sources, run=run)
        except BaseException as exc:  # 线程里不能把异常留给调用方
            self._mark_failed(run, topic, exc)

    def _mark_failed(self, run: Run, topic: str, exc: BaseException) -> None:
        meta = run.dir / "run.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                if data.get("status") not in (None, "running"):
                    return
            except json.JSONDecodeError:
                pass
        note = f"{type(exc).__name__}: {exc}"
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "failed",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "duration_s": 0,
                "sources": [],
                "notes": [note],
            },
        )

    # ----------------------------------------------------------------- state
    def is_running(self, run_id: str) -> bool:
        job = self._jobs.get(run_id)
        return bool(job and job.thread.is_alive())

    def active_run(self) -> str | None:
        if self._active and self.is_running(self._active):
            return self._active
        return None

    def get_run(self, run_id: str) -> Run:
        return self.store.open(run_id)

    def meta(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        path = run.dir / "run.json"
        if not path.exists():
            return {"run_id": run_id, "topic": run.topic, "status": "running"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"run_id": run_id, "topic": run.topic, "status": "unknown"}
