"""跨模块共享的数据模型。字段名与 PROJECT.md 第 4 节保持一致。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Coverage = Literal["enough", "partial", "gap"]


class SubQuestion(BaseModel):
    id: int
    question: str
    queries: list[str] = Field(default_factory=list)
    coverage: Coverage | None = None
    key_points: list[str] = Field(default_factory=list)
    gap: str | None = None
    rounds: int = 0
    source_ns: list[int] = Field(default_factory=list)


class Source(BaseModel):
    n: int
    url: str
    title: str
    domain: str
    published_at: str | None = None
    snippet: str = ""
    chars: int = 0
    fetched_at: str
    queries: list[str] = Field(default_factory=list)
    subquestion_ids: list[int] = Field(default_factory=list)
    via: str = "tavily-extract"  # tavily-extract | fallback-fetch
    link_status: str = "unchecked"  # 报告收尾时复核的 HTTP 状态
    tier: int = 1  # 3 一手 / 2 专业媒体 / 1 一般 / 0 自媒体
    tier_label: str = "一般"
    quality: int = 50  # 0–100，见 dra.quality


class AnalysisItem(BaseModel):
    id: int
    key_points: list[str] = Field(default_factory=list)
    evidence_ns: list[int] = Field(default_factory=list)
    coverage: Coverage = "partial"
    gap: str | None = None


class Analysis(BaseModel):
    items: list[AnalysisItem] = Field(default_factory=list)


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0

    def merge(self, other: ChatUsage) -> None:
        """把另一次调用的用量累加进来。"""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.calls += other.calls


class RunResult(BaseModel):
    run_id: str
    topic: str
    status: Literal["done", "partial", "failed", "cancelled"]
    started_at: str
    finished_at: str
    duration_s: float
    report_path: str
    sources: list[Source] = Field(default_factory=list)
    cited_ns: list[int] = Field(default_factory=list)
    subquestions: list[SubQuestion] = Field(default_factory=list)
    usage: ChatUsage = Field(default_factory=ChatUsage)
    budget: dict = Field(default_factory=dict)
    model: str = ""
    notes: list[str] = Field(default_factory=list)
