from __future__ import annotations

from dra.evaluate import evaluate_report, summarize
from dra.models import Source


def make_sources() -> list[Source]:
    return [
        Source(
            n=1,
            url="https://arxiv.org/abs/1",
            title="基准论文",
            domain="arxiv.org",
            tier=3,
            tier_label="一手",
            quality=90,
            snippet="延迟 15ms，准确率 96.1%。",
            fetched_at="2026-10-21T00:00:00",
        ),
        Source(
            n=2,
            url="https://medium.com/x",
            title="博客",
            domain="medium.com",
            tier=0,
            tier_label="自媒体",
            quality=30,
            snippet="成本约为 3%。",
            fetched_at="2026-10-21T00:00:00",
        ),
    ]


REPORT = """# 测试报告

## 摘要

测试显示延迟为 15ms[1]。准确率达到 96.1%[1]。成本约为 3%[2]。

另有 42% 的提升未能在来源中找到[2]。没有引用的结论是 99% 增长。另一个数字 77% 也没有来源。

无效引用在这里[9]。
"""


def test_metrics_and_warnings():
    result = evaluate_report(
        run_id="r1", topic="测试", sources=make_sources(), report_text=REPORT
    )
    assert result["citations"]["factual_sentences"] >= 4
    assert result["citations"]["invalid"] == 1
    assert result["citations"]["citation_rate"] < 0.8
    assert result["numbers"]["total"] >= 4
    assert result["numbers"]["supported"] >= 2
    assert result["numbers"]["support_rate"] < 1
    assert 0 <= result["score"] <= 100
    assert result["grade"] in {"A", "B", "C", "D"}
    assert any("未能在引用来源中找到" in w for w in result["warnings"])
    assert any("引用指向不存在" in w for w in result["warnings"])

    unverified = {item["number"] for item in result["numbers"]["unverified"]}
    assert "42" in unverified or "99" in unverified


def test_fully_supported_report_scores_high():
    report = "# 好报告\n\n延迟 15ms[1]。准确率 96.1%[1]。成本 3%[2]。\n"
    result = evaluate_report(
        run_id="r2", topic="好", sources=make_sources(), report_text=report
    )
    assert result["numbers"]["support_rate"] == 1.0
    assert result["score"] >= 60
    assert result["grade"] in {"A", "B", "C"}


def test_summarize_baseline():
    a = {"score": 80, "grade": "B", "citations": {"citation_rate": 0.9}, "numbers": {"support_rate": 0.8}}
    b = {"score": 60, "grade": "C", "citations": {"citation_rate": 0.7}, "numbers": {"support_rate": 0.6}}
    summary = summarize([a, b])
    assert summary["runs"] == 2
    assert summary["mean_score"] == 70.0
    assert summary["grades"] == {"B": 1, "C": 1}
