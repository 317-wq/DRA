from __future__ import annotations

from dra.agent import best_excerpt


def test_short_text_unchanged():
    assert best_excerpt("hello", limit=100) == "hello"


def test_picks_numeric_window():
    intro = "这是引言，没有任何数字。" * 100
    table = "| 模型 | 512 | 4096 |\n| A | 7.2 | 0.09 |\n| B | 5.1 | 0.07 |\n" * 20
    text = intro + table
    excerpt = best_excerpt(text, limit=300)
    assert "7.2" in excerpt or "0.09" in excerpt
    assert "这是引言" not in excerpt


def test_marks_truncation():
    text = "开头" + ("数字 42% 延迟 15ms " * 200)
    excerpt = best_excerpt(text, limit=200)
    assert excerpt.startswith("…")


def test_short_trailing_table_is_found():
    text = "intro " * 200 + "| A | 0.09 | 7.2 |"
    excerpt = best_excerpt(text, limit=200)
    assert "0.09" in excerpt or "7.2" in excerpt
