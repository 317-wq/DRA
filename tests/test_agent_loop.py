from __future__ import annotations

import json

from dra.agent import ResearchAgent
from dra.budget import Budget
from dra.config import Settings
from dra.llm import ChatResult
from dra.models import ChatUsage
from dra.store import RunStore
from dra.tools.extract import Document
from dra.tools.search import SearchHit


class FakeLLM:
    def __init__(self, writer_cites: str = "[1]") -> None:
        self.calls = 0
        self.writer_cites = writer_cites

    def chat(
        self,
        messages,
        *,
        model=None,
        temperature=0.3,
        max_tokens=3000,
        json_mode=False,
        retries=2,
    ):
        self.calls += 1
        prompt = messages[-1]["content"]
        if "拆成" in prompt and "子问题" in prompt:
            text = json.dumps(
                {
                    "subquestions": [
                        {"id": 1, "question": "现状如何", "queries": ["q1", "q2"]},
                        {"id": 2, "question": "有哪些方案", "queries": ["q3"]},
                    ]
                },
                ensure_ascii=False,
            )
        elif "子问题与证据" in prompt:
            text = json.dumps(
                {
                    "items": [
                        {
                            "id": 1,
                            "key_points": ["现状是甲[1]"],
                            "evidence_ns": [1],
                            "coverage": "enough",
                            "gap": None,
                        },
                        {
                            "id": 2,
                            "key_points": ["方案有乙[2]"],
                            "evidence_ns": [2],
                            "coverage": "partial",
                            "gap": "缺少对比数据",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        elif "写作要求" in prompt:
            text = (
                "## 摘要\n\n这是摘要结论。\n\n"
                f"## 1. 研究背景与问题界定\n\n现状是甲{self.writer_cites}，方案有乙[2]。\n\n"
                "## 结论与建议\n\n建议先做小范围验证。\n\n"
                "## 证据缺口与不确定性\n\n缺少横向对比数据。"
            )
        else:
            text = "{}"
        return ChatResult(
            text=text,
            model=model or "fake",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, calls=1),
            latency_ms=1,
        )


class FakeSearcher:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5):
        self.queries.append(query)
        i = len(self.queries)
        return [
            SearchHit(title=f"{query} 的官方文档", url=f"https://docs.example.com/{i}/a", score=0.9),
            SearchHit(title=f"{query} 的评测", url=f"https://blog.example.org/{i}/b", score=0.8),
        ]


class FakeReader:
    def read(self, urls):
        docs = [
            Document(url=u, title=f"文档 {i}", content="正文内容。" * 60, via="tavily-extract")
            for i, u in enumerate(urls, start=1)
        ]
        return docs, []


def build_agent(tmp_path, writer_cites="[1]"):
    settings = Settings(
        qwen_api_key="test-key",
        tavily_api_key="test-key",
        runs_dir=tmp_path,
    )
    llm = FakeLLM(writer_cites=writer_cites)
    return ResearchAgent(
        settings,
        llm=llm,  # type: ignore[arg-type]
        searcher=FakeSearcher(),  # type: ignore[arg-type]
        reader=FakeReader(),  # type: ignore[arg-type]
        store=RunStore(tmp_path),
        verify_links=False,
    )


def test_full_loop_writes_artifacts(tmp_path):
    agent = build_agent(tmp_path)
    events: list[str] = []
    result = agent.run(
        "测试主题",
        budget=Budget(max_tool_calls=20, max_seconds=60, max_rounds=2),
        max_sources=6,
        on_event=lambda ev: events.append(ev.type),
    )

    assert result.status == "partial"  # 有一个子问题 partial，符合收敛逻辑
    assert "plan.ready" in events
    assert "analysis.done" in events
    assert "run.finished" in events

    run_dir = tmp_path / result.run_id
    assert (run_dir / "report.md").exists()
    assert (run_dir / "sources.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "sources" / "001.md").exists()

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## 参考资料" in report
    assert "[1]" in report
    assert "https://docs.example.com" in report

    cited = json.loads((run_dir / "sources.json").read_text(encoding="utf-8"))
    assert len(cited) == len(result.sources)
    assert [s["n"] for s in cited] == list(range(1, len(cited) + 1))
    assert result.usage.calls >= 3


def test_unknown_citation_is_dropped(tmp_path):
    agent = build_agent(tmp_path, writer_cites="[99]")
    result = agent.run("测试主题", budget=Budget(max_tool_calls=20), max_sources=6)
    report = (tmp_path / result.run_id / "report.md").read_text(encoding="utf-8")
    assert "[99]" not in report
    assert any("丢弃" in n and "引用" in n for n in result.notes)


def test_dedup_sources_across_queries(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.run("测试主题", budget=Budget(max_tool_calls=20), max_sources=10)
    urls = [s.url for s in result.sources]
    assert len(urls) == len(set(urls))
    assert len(urls) <= 10
