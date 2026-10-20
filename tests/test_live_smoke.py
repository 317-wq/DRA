"""真实链路 smoke：会消耗 API 额度，默认不跑。"""

from __future__ import annotations

import json
import os

import pytest

from dra.agent import ResearchAgent
from dra.budget import Budget
from dra.config import Settings
from dra.store import RunStore


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("DRA_LIVE") != "1", reason="设置 DRA_LIVE=1 才跑真实链路")
def test_live_small_run(tmp_path):
    settings = Settings(runs_dir=tmp_path)
    agent = ResearchAgent(settings, store=RunStore(tmp_path))
    result = agent.run(
        "MCP 协议是什么",
        budget=Budget(max_tool_calls=14, max_seconds=180, max_rounds=1),
        max_sources=3,
    )
    assert result.status in ("done", "partial"), result.notes
    assert result.sources, result.notes
    report = (tmp_path / result.run_id / "report.md").read_text(encoding="utf-8")
    assert "## 参考资料" in report
    cited = json.loads((tmp_path / result.run_id / "sources.json").read_text(encoding="utf-8"))
    assert cited and all(s["url"].startswith("http") for s in cited)
