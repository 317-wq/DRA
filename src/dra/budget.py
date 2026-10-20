"""执行预算：硬上限，防止研究跑飞。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        """记录预算上限与初始用量。"""
        super().__init__(reason)
        self.reason = reason


@dataclass
class Budget:
    max_tool_calls: int = 30
    max_seconds: float = 240.0
    max_tokens: int = 150_000
    max_rounds: int = 2

    tool_calls: int = 0
    tokens: int = 0
    llm_calls: int = 0
    rounds_used: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        """已用墙钟时间（秒）。"""
        return time.monotonic() - self.started

    def spend_tool(self, n: int = 1) -> None:
        """记一次工具调用。"""
        self.tool_calls += n

    def spend_tokens(self, n: int) -> None:
        """记一次模型调用及其 token。"""
        self.tokens += n
        self.llm_calls += 1

    def spend_round(self) -> None:
        """记一轮检索-分析。"""
        self.rounds_used += 1

    def exhausted_reason(self) -> str | None:
        # 轮次是循环次数上限，由调用方控制，不作为中途硬停条件
        """返回触顶原因；未触顶返回 None。"""
        if self.tool_calls >= self.max_tool_calls:
            return f"工具调用达到上限 {self.max_tool_calls}"
        if self.elapsed >= self.max_seconds:
            return f"耗时达到上限 {self.max_seconds:.0f}s"
        if self.tokens >= self.max_tokens:
            return f"token 达到上限 {self.max_tokens}"
        return None

    def can_continue(self) -> bool:
        """预算是否还允许继续。"""
        return self.exhausted_reason() is None

    def check(self) -> None:
        """触顶则抛出 BudgetExceeded。"""
        reason = self.exhausted_reason()
        if reason:
            raise BudgetExceeded(reason)

    def snapshot(self) -> dict:
        """导出预算快照，供 run.json 与事件使用。"""
        return {
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "rounds": self.rounds_used,
            "elapsed_s": round(self.elapsed, 1),
            "limits": {
                "max_tool_calls": self.max_tool_calls,
                "max_seconds": self.max_seconds,
                "max_tokens": self.max_tokens,
                "max_rounds": self.max_rounds,
            },
        }
