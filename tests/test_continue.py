from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dra.agent import ResearchAgent
from dra.config import Settings
from dra.jobs import RunManager
from dra.llm import ChatResult
from dra.models import ChatUsage
from dra.store import RunStore
from dra.tools.extract import Document
from dra.tools.search import SearchHit
from dra.web.server import create_app


class FakeLLM:
    def chat(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        if "子问题与证据" in prompt:
            text = json.dumps(
                {
                    "items": [
                        {
                            "id": 1,
                            "key_points": ["旧结论仍成立[1]，新证据 42%[2]"],
                            "evidence_ns": [1, 2],
                            "coverage": "enough",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        elif "写作要求" in prompt:
            text = (
                "## 摘要\n\n旧结论仍成立[1]，新证据显示 42%[2]。\n\n"
                "## 结论与建议\n\n沿用旧结论并补充新证据。\n\n"
                "## 证据缺口与不确定性\n\n缺少长期数据。"
            )
        else:
            text = "{}"
        return ChatResult(
            text=text,
            model="fake",
            usage=ChatUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, calls=1),
            latency_ms=1,
        )


class FakeSearcher:
    def search(self, query, max_results=5):
        return [
            SearchHit(title="新证据", url="https://docs.example.com/new", score=0.9),
        ]


class FakeReader:
    def read(self, urls):
        return [
            Document(url=u, title="新证据", content="新证据 42%。" + "补充。" * 40)
            for u in urls
        ], []


def seed_parent(store: RunStore) -> str:
    run = store.create("父主题")
    run.write_text("report.md", "# 父主题\n\n## 摘要\n\n旧结论[1]。\n")
    run.write_json(
        "sources.json",
        [
            {
                "n": 1,
                "url": "https://a.com/old",
                "title": "旧证据",
                "domain": "a.com",
                "snippet": "旧证据 42%。",
                "fetched_at": "2026-10-21T00:00:00",
            }
        ],
    )
    run.write_json(
        "run.json",
        {"run_id": run.id, "topic": "父主题", "status": "done", "sources": [], "notes": []},
    )
    return run.id


def build(tmp_path: Path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(settings.runs_dir)
    manager = RunManager(
        settings,
        store=store,
        agent_factory=lambda: ResearchAgent(
            settings,
            llm=FakeLLM(),  # type: ignore[arg-type]
            searcher=FakeSearcher(),  # type: ignore[arg-type]
            reader=FakeReader(),  # type: ignore[arg-type]
            store=store,
            verify_links=False,
        ),
    )
    return TestClient(create_app(settings, manager=manager)), store, manager


def test_continue_inherits_sources_and_keeps_numbers(tmp_path):
    client, store, manager = build(tmp_path)
    parent = seed_parent(store)

    res = client.post(f"/api/runs/{parent}/continue", json={"questions": ["新问题"], "max_sources": 2})
    assert res.status_code == 202
    child = res.json()["run_id"]
    assert manager.wait_idle(5)

    meta = manager.meta(child)
    assert meta["parent_run_id"] == parent
    assert meta["status"] in ("done", "partial")

    sources = json.loads((store.open(child).dir / "sources.json").read_text(encoding="utf-8"))
    numbers = [item["n"] for item in sources]
    assert 1 in numbers and 2 in numbers

    report = (store.open(child).dir / "report.md").read_text(encoding="utf-8")
    assert "[1]" in report
    assert "[2]" in report
    manager.close()


def test_continue_requires_report(tmp_path):
    client, store, manager = build(tmp_path)
    parent = seed_parent(store)
    (store.open(parent).dir / "report.md").unlink()
    res = client.post(f"/api/runs/{parent}/continue", json={"questions": ["新问题"]})
    assert res.status_code == 409
    manager.close()
