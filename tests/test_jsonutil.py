from __future__ import annotations

import pytest

from dra.jsonutil import JSONParseError, extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    text = '```json\n{"subquestions": [{"id": 1}]}\n```'
    assert extract_json(text)["subquestions"][0]["id"] == 1


def test_prose_around_json():
    text = '好的，结果如下：\n{"items": []}\n以上。'
    assert extract_json(text) == {"items": []}


def test_braces_inside_string():
    text = '{"q": "怎么用 {x} 和 [y]"}'
    assert extract_json(text)["q"] == "怎么用 {x} 和 [y]"


def test_array_root():
    assert extract_json("```\n[1, 2, 3]\n```") == [1, 2, 3]


def test_invalid_raises():
    with pytest.raises(JSONParseError):
        extract_json("完全不是 JSON")
