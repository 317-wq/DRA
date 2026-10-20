from __future__ import annotations

from dra.citations import count_chinese_chars, finalize_report, strip_urls_for_check
from dra.models import Source


def make_sources(n: int) -> list[Source]:
    return [
        Source(
            n=i,
            url=f"https://example.com/{i}",
            title=f"来源{i}",
            domain="example.com",
            fetched_at="2026-10-20T20:00:00",
        )
        for i in range(1, n + 1)
    ]


def test_renumbers_by_first_appearance():
    body = "结论甲[2]，结论乙[1]，再提一次[2]。"
    out, cited, dropped = finalize_report(body, make_sources(3))
    assert dropped == 0
    assert [s.n for s in cited] == [1, 2]
    assert "结论甲[1]" in out
    assert "结论乙[2]" in out
    assert cited[0].url == "https://example.com/2"


def test_drops_unknown_numbers():
    body = "有依据[1]，没依据[9]。"
    out, cited, dropped = finalize_report(body, make_sources(2))
    assert dropped == 1
    assert "[9]" not in out
    assert len(cited) == 1


def test_grouped_citations():
    body = "两个来源支撑[1,3]。"
    out, cited, _ = finalize_report(body, make_sources(3))
    assert "[1,2]" in out
    assert [s.n for s in cited] == [1, 2]


def test_appends_references_with_urls():
    out, _, _ = finalize_report("只有一条[1]。", make_sources(1))
    assert "## 参考资料" in out
    assert "1. 来源1 — <https://example.com/1>" in out


def test_no_citation_returns_body_only():
    out, cited, _ = finalize_report("没有引用。", make_sources(2))
    assert cited == []
    assert "## 参考资料" not in out


def test_count_chinese():
    assert count_chinese_chars("abc中文123") == 2


def test_strip_urls_flags_body_only():
    text = "正文 https://a.com/x\n## 参考资料\n1. <https://b.com/y>"
    assert strip_urls_for_check(text) == ["https://a.com/x"]
