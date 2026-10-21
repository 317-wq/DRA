"""后台任务管理：持久队列、取消、重试、重启恢复。

本地单用户，一次只跑一个研究；排队而不是拒绝，是为了让“连续发起”不丢任务。
队列落在 runs 目录下的 SQLite（`.jobs.sqlite3`），服务重启后排队任务会重新入队。
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dra.budget import Budget
from dra.config import Settings
from dra.models import Source
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


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobStore:
    """任务元数据的 SQLite 持久化；每次操作独立连接，避免线程共享连接。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    max_sources INTEGER NOT NULL DEFAULT 12,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def upsert(
        self,
        run_id: str,
        topic: str,
        max_sources: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """写入或更新一条任务记录。"""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(run_id, topic, max_sources, status, created_at, updated_at, error)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    topic=excluded.topic,
                    max_sources=excluded.max_sources,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    error=excluded.error
                """,
                (run_id, topic, max_sources, status, now, now, error),
            )

    def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        """只更新状态。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, error=? WHERE run_id=?",
                (status, _now(), error, run_id),
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        """读取单条任务记录。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def pending(self) -> list[dict[str, Any]]:
        """按创建时间返回排队中的任务。"""
        return self.by_status("queued")

    def by_status(self, status: str) -> list[dict[str, Any]]:
        """按状态返回任务（旧的在前面）。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at ASC", (status,)
            ).fetchall()
            return [dict(row) for row in rows]

    def delete(self, run_id: str) -> None:
        """删除任务记录。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))


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
        self.job_store = JobStore(self.store.root / ".jobs.sqlite3")
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
        """创建任务并持久化入队，立刻返回 run_id。"""
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
        self.job_store.upsert(run.id, topic, max_sources, "queued")
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
                self.job_store.set_status(run_id, "cancelled")
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
        parent_id = meta.get("parent_run_id")
        if parent_id:
            return self.start_continue(
                parent_id,
                [str(q) for q in (meta.get("continue_questions") or [])],
                max_sources,
            )
        return self.start(topic, max_sources)

    def start_continue(
        self,
        parent_id: str,
        questions: list[str],
        max_sources: int = 6,
    ) -> str:
        """在已完成报告上继续研究：继承父来源，只补新问题。"""
        parent = self.store.open(parent_id)
        meta = self.meta(parent_id)
        if meta.get("status") in ("queued", "running"):
            raise BusyError("父研究还在进行中，等它结束再继续")
        if not (parent.dir / "report.md").exists():
            raise ValueError("父研究还没有报告，无法继续")
        cleaned = [q.strip() for q in questions if q.strip()][:4]
        if not cleaned:
            raise ValueError("继续研究至少需要一个新问题")
        topic = meta.get("topic") or parent.topic
        run = self.store.create(topic)
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
                "parent_run_id": parent_id,
                "mode": "continue",
                "continue_questions": cleaned,
            },
        )
        self.job_store.upsert(run.id, topic, max_sources, "queued")
        job = Job(run=run, topic=topic, max_sources=max_sources)
        with self._lock:
            self._jobs[run.id] = job
            self._queue.put(run.id)
        return run.id

    def delete(self, run_id: str) -> bool:
        """删除任务与产物；运行中的任务拒绝删除。"""
        with self._lock:
            if self.is_running(run_id):
                raise BusyError("任务还在运行，先取消再删除")
            self._jobs.pop(run_id, None)
            self.job_store.delete(run_id)
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
                    self.job_store.set_status(run_id, "cancelled")
                    continue
                job.started = True
                self._current = run_id
                self._update(job, status="running", notes=[])
                self.job_store.set_status(run_id, "running")
            try:
                meta = self.meta(run_id)
                parent_id = meta.get("parent_run_id")
                seed_sources: list[Source] | None = None
                seed_report = ""
                questions = [str(q) for q in (meta.get("continue_questions") or [])]
                total_sources = job.max_sources
                if parent_id:
                    parent = self.store.open(parent_id)
                    raw = json.loads((parent.dir / "sources.json").read_text(encoding="utf-8"))
                    seed_sources = [Source(**item) for item in raw]
                    report_path = parent.dir / "report.md"
                    seed_report = (
                        report_path.read_text(encoding="utf-8") if report_path.exists() else ""
                    )
                    total_sources = len(seed_sources) + job.max_sources

                agent = self._factory()
                kwargs: dict[str, Any] = {
                    "budget": Budget(),
                    "max_sources": total_sources,
                    "run": job.run,
                    "cancel_event": job.cancel_event,
                }
                if parent_id:
                    kwargs.update(
                        {
                            "seed_sources": seed_sources,
                            "seed_report": seed_report,
                            "seed_questions": questions or None,
                            "parent_run_id": parent_id,
                        }
                    )
                agent.run(job.topic, **kwargs)
                # Agent 的最终 run.json 不带这个字段，补回去供“重试”使用
                self._update(job, requested_max_sources=job.max_sources)
                final = self.meta(run_id).get("status", "done")
                self.job_store.set_status(run_id, final)
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
        self.job_store.set_status(job.run.id, "failed", note)

    def _recover_stale(self) -> None:
        """重启后：DB 里 queued 的重新入队；running 的标 interrupted。"""
        for row in self.job_store.pending():
            run_id = row["run_id"]
            try:
                run = self.store.open(run_id)
            except FileNotFoundError:
                self.job_store.delete(run_id)
                continue
            job = Job(
                run=run,
                topic=row.get("topic") or run.topic,
                max_sources=int(row.get("max_sources") or 12),
            )
            self._jobs[run_id] = job
            self._queue.put(run_id)

        for row in self.job_store.by_status("running"):
            run_id = row["run_id"]
            try:
                run = self.store.open(run_id)
            except FileNotFoundError:
                self.job_store.delete(run_id)
                continue
            meta = self.meta(run_id)
            notes = list(meta.get("notes") or [])
            notes.append("服务重启，任务被中断")
            meta.update({"status": "interrupted", "finished_at": _now(), "notes": notes})
            run.write_json("run.json", meta)
            self.job_store.set_status(run_id, "interrupted")

        # 兼容 1.1 之前没有 jobs 表的 run
        for meta in self.store.list():
            status = meta.get("status")
            if status not in ("queued", "running"):
                continue
            run_id = meta.get("run_id", "")
            if not run_id or self.job_store.get(run_id):
                continue
            try:
                run = self.store.open(run_id)
            except FileNotFoundError:
                continue
            if status == "queued":
                topic = meta.get("topic") or run.topic
                max_sources = int(meta.get("requested_max_sources") or 12)
                self.job_store.upsert(run_id, topic, max_sources, "queued")
                self._jobs[run_id] = Job(run=run, topic=topic, max_sources=max_sources)
                self._queue.put(run_id)
            else:
                notes = list(meta.get("notes") or [])
                notes.append("服务重启，任务被中断")
                meta.update({"status": "interrupted", "finished_at": _now(), "notes": notes})
                run.write_json("run.json", meta)
                self.job_store.upsert(
                    run_id, meta.get("topic") or run.topic, 12, "interrupted"
                )
