"""事件总线与 Trace 落盘。

事件是 0.1 唯一的过程输出通道：CLI 订阅它渲染，0.2 的 SSE 也直接消费它。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# run.started | plan.ready | search.done | fetch.done | analysis.done
# replan | write.started | run.finished | run.failed
EventType = str


@dataclass
class Event:
    ts: str
    run_id: str
    seq: int
    type: EventType
    stage: str
    payload: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None
    tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """转成可写入 JSONL 的字典。"""
        return {
            "ts": self.ts,
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "stage": self.stage,
            "payload": self.payload,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
        }


Listener = Callable[[Event], None]


class EventBus:
    def __init__(self, run_id: str) -> None:
        """初始化实例。"""
        self.run_id = run_id
        self._seq = 0
        self._listeners: list[Listener] = []

    def subscribe(self, fn: Listener) -> None:
        """注册事件监听器。"""
        self._listeners.append(fn)

    def emit(
        self,
        type_: EventType,
        stage: str,
        payload: dict[str, Any] | None = None,
        *,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> Event:
        """发出事件并通知所有监听器。"""
        self._seq += 1
        ev = Event(
            ts=datetime.now().isoformat(timespec="seconds"),
            run_id=self.run_id,
            seq=self._seq,
            type=type_,
            stage=stage,
            payload=payload or {},
            duration_ms=duration_ms,
            tokens=tokens,
        )
        for fn in list(self._listeners):
            fn(ev)
        return ev


class TraceWriter:
    """把事件追加写入 runs/<id>/trace.jsonl，逐条 flush，便于 tail -f。"""

    def __init__(self, path) -> None:
        """初始化实例。"""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def __call__(self, ev: Event) -> None:
        """把事件追加写入 JSONL。"""
        self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        """关闭文件句柄。"""
        try:
            self._fh.close()
        except Exception:
            pass


def now_iso() -> str:
    """当前时间的 ISO 字符串。"""
    return datetime.now().isoformat(timespec="seconds")


def elapsed_ms(started: float) -> int:
    """从 monotonic 起点到现在的毫秒数。"""
    return int((time.monotonic() - started) * 1000)
