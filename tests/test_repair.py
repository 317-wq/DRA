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


class RepairLLM:
    """第一版故意写错数字，收到校对提示后改成证据里的数字。"""

    def __init__(self) -> None:
        self.repairs = 0

    def chat(self, messages, *, model=None, temperature=0.3, max_tokens=3000, json_mode=False, retries=1):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "校对者" in system:
            self.repairs += 1
            text = "## 摘要\n\n准确率 62.1%[1]，延迟 15ms[1]。\n\n## 结论与建议\n\n按证据选型。\n"
        elif "拆成" in user:
            text = json.dumps(
                {
                    "subquestions": [
                        {"id": 1, "question": "准确率如何", "queries": ["q1"]},
                        {"id": 2, "question": "延迟如何", "queries": ["q2"]},
                    ]
                },
                ensure_ascii=False,
            )
        elif "子问题与证据" in user:
            text = json.dumps(
                {
                    "items": [
                        {
                            "id": 1,
                            "key_points": ["准确率 62.1%[1]"],
                            "evidence_ns": [1, 2],
                            "coverage": "enough",
                        },
                        {
                            "id": 2,
                            "key_points": ["延迟 15ms[2]"],
                            "evidence_ns": [1, 2],
                            "coverage": "enough",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        elif "写作要求" in user:
            text = "## 摘要\n\n准确率 99.9%[1]，延迟 1ms[1]，成本 2ms[1]。\n\n## 结论与建议\n\n按证据选型。\n"
        else:
            text = "{}"
        return ChatResult(
            text=text,
            model=model or "fake",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, calls=1),
            latency_ms=1,
        )


class Searcher:
    def search(self, query, max_results=5):
        return [
            SearchHit(title="来源A", url="https://a.com/1", score=0.9),
            SearchHit(title="来源B", url="https://b.com/1", score=0.8),
        ]


class Reader:
    def read(self, urls):
        docs = [
            Document(
                url=url,
                title="基准",
                content="准确率 62.1%，延迟 15ms。" + "补充说明。" * 40,
            )
            for url in urls
        ]
        return docs, []


def test_repair_rewrites_unverified_numbers(tmp_path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path,
        kb_path=tmp_path / "kb.sqlite3",
        min_number_support=0.75,
        _env_file=None,
    )
    llm = RepairLLM()
    agent = ResearchAgent(
        settings,
        llm=llm,  # type: ignore[arg-type]
        searcher=Searcher(),  # type: ignore[arg-type]
        reader=Reader(),  # type: ignore[arg-type]
        store=RunStore(tmp_path),
    )
    result = agent.run("测试主题", budget=Budget(max_tool_calls=20), max_sources=4)

    assert llm.repairs == 1
    run_dir = tmp_path / result.run_id
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "99.9%" not in report
    assert "62.1%" in report

    evaluation = json.loads((run_dir / "eval.json").read_text(encoding="utf-8"))
    assert evaluation["numbers"]["support_rate"] == 1.0

    events = [
        json.loads(line)["type"]
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "write.verify" in events
    assert "write.verified" in events


class DegradingLLM(RepairLLM):
    """校对阶段返回一段没有引用的短文，应被验收闸门拒绝。"""

    def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        if "校对者" in system:
            self.repairs += 1
            return ChatResult(
                text="简短回答，没有引用。",
                model="fake",
                usage=ChatUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10, calls=1),
                latency_ms=1,
            )
        return super().chat(messages, **kwargs)


def test_repair_rejected_when_it_loses_citations(tmp_path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path,
        kb_path=tmp_path / "kb.sqlite3",
        min_number_support=0.75,
        _env_file=None,
    )
    llm = DegradingLLM()
    agent = ResearchAgent(
        settings,
        llm=llm,  # type: ignore[arg-type]
        searcher=Searcher(),  # type: ignore[arg-type]
        reader=Reader(),  # type: ignore[arg-type]
        store=RunStore(tmp_path),
    )
    result = agent.run("测试主题", budget=Budget(max_tool_calls=20), max_sources=4)
    run_dir = tmp_path / result.run_id
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "99.9%" in report  # 原稿被保留
    assert any("保留原稿" in note for note in result.notes)
