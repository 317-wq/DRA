"""后台任务管理：单 worker 队列、取消、重试、重启恢复。

本地单用户，一次只跑一个研究；排队而不是拒绝，是为了让“连续发起”不丢任务。
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dra.budget import Budget
from dra.config import Settings
from dra.store import Run, RunStore


class BusyError(RuntimeError):
    """已有任务在跑（保留给需要严格互斥的调用方）。"""


@dataclass
class Job:
    run: Run
    topic: str
    max_sources: int
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started: bool = False


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


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RunManager:
    """发起、排队、执行、取消与重试研究任务。"""

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
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._current: str | None = None
        self._recover_stale()
        self._worker = threading.Thread(target=self._loop, daemon=True, name="dra-worker")
        self._worker.start()

    # ------------------------------------------------------------------ API
    def start(self, topic: str, max_sources: int = 12) -> str:
        """创建任务并排队，立刻返回 run_id。"""
        run = self.store.create(topic)
        job = Job(run=run, topic=topic, max_sources=max_sources)
        run.write_json(
            "run.json",
            {
                "run_id": run.id,
                "topic": topic,
                "status": "queued",
                "started_at": _now(),
                "sources": [],
                "notes": [],
                "requested_max_sources": max_sources,
            },
        )
        with self._lock:
            self._jobs[run.id] = job
            self._queue.put(run.id)
        return run.id

    def cancel(self, run_id: str) -> str:
        """取消排队中或运行中的任务。返回 queued/running/finished/unknown。"""
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                return "unknown"
            meta = self.meta(run_id)
            status = meta.get("status")
            if status in ("done", "partial", "failed", "cancelled", "interrupted"):
                return "finished"
            job.cancel_event.set()
            if not job.started:
                self._update(job, status="cancelled", finished_at=_now(), notes=["排队中被取消"])
                return "queued"
            return "running"

    def retry(self, run_id: str) -> str:
        """用同一主题与来源上限重跑，返回新 run_id。"""
        meta = self.meta(run_id)
        if meta.get("status") in ("queued", "running"):
            raise BusyError("任务还在运行，不能重试")
        topic = meta.get("topic") or self.store.open(run_id).topic
        if not topic:
            raise ValueError("原任务没有主题，无法重试")
        max_sources = int(meta.get("requested_max_sources") or 12)
        return self.start(topic, max_sources)

    def delete(self, run_id: str) -> bool:
        """删除任务与产物；运行中的任务拒绝删除。"""
        with self._lock:
            if self.is_running(run_id):
                raise BusyError("任务还在运行，先取消再删除")
            self._jobs.pop(run_id, None)
            return self.store.delete(run_id)

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            if self._current == run_id:
                return True
            job = self._jobs.get(run_id)
            return bool(job and not job.started and not job.cancel_event.is_set())

    def active_run(self) -> str | None:
        with self._lock:
            if self._current:
                return self._current
            for run_id, job in self._jobs.items():
                if not job.started and not job.cancel_event.is_set():
                    return run_id
        return None

    def queue_size(self) -> int:
        return self._queue.qsize()

    def get_run(self, run_id: str) -> Run:
        return self.store.open(run_id)

    def meta(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        path = run.dir / "run.json"
        if not path.exists():
            return {"run_id": run_id, "topic": run.topic, "status": "unknown"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"run_id": run_id, "topic": run.topic, "status": "unknown"}

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """等待队列与当前任务结束（测试/脚本用）。"""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._current is None and self._queue.empty():
                    return True
            time.sleep(0.02)
        return False

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=2)

    # -------------------------------------------------------------- worker
    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            with self._lock:
                job = self._jobs.get(run_id)
                if job is None:
                    continue
                if job.cancel_event.is_set():
                    job.started = True
                    self._update(job, status="cancelled", finished_at=_now())
                    continue
                job.started = True
                self._current = run_id
                self._update(job, status="running", notes=[])
            try:
                agent = self._factory()
                agent.run(
                    job.topic,
                    budget=Budget(),
                    max_sources=job.max_sources,
                    run=job.run,
                    cancel_event=job.cancel_event,
                )
                # Agent 的最终 run.json 不带这个字段，补回去供“重试”使用
                self._update(job, requested_max_sources=job.max_sources)
            except BaseException as exc:  # 线程里不能把异常留给调用方
                self._mark_failed(job, exc)
            finally:
                with self._lock:
                    self._current = None

    def _update(self, job: Job, **fields: Any) -> dict[str, Any]:
        path = job.run.dir / "run.json"
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        data.setdefault("run_id", job.run.id)
        data.setdefault("topic", job.topic)
        data.setdefault("started_at", _now())
        data.setdefault("sources", [])
        data.setdefault("requested_max_sources", job.max_sources)
        data.update(fields)
        job.run.write_json("run.json", data)
        return data

    def _mark_failed(self, job: Job, exc: BaseException) -> None:
        meta = self.meta(job.run.id)
        if meta.get("status") in ("done", "partial", "cancelled"):
            return
        note = f"{type(exc).__name__}: {exc}"
        self._update(job, status="failed", finished_at=_now(), notes=[note])

    def _recover_stale(self) -> None:
        """服务重启后，把上次遗留的 queued/running 标成 interrupted。"""
        for meta in self.store.list():
            if meta.get("status") not in ("queued", "running"):
                continue
            run_id = meta.get("run_id", "")
            if not run_id:
                continue
            try:
                run = self.store.open(run_id)
            except FileNotFoundError:
                continue
            notes = list(meta.get("notes") or [])
            notes.append("服务重启，任务被中断")
            meta.update({"status": "interrupted", "finished_at": _now(), "notes": notes})
            run.write_json("run.json", meta)
