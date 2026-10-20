"""JSON 提取：模型输出常带 ```json 围栏或前后废话。"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


class JSONParseError(ValueError):
    pass


def extract_json(text: str) -> Any:
    """从任意文本里取出第一个完整 JSON 对象/数组。"""
    if not text or not text.strip():
        raise JSONParseError("模型返回为空")

    candidates: list[str] = [m.strip() for m in _FENCE.findall(text)]
    candidates.append(text.strip())

    for raw in candidates:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        for chunk in _balanced_chunks(raw):
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise JSONParseError(f"无法解析 JSON，前 200 字：{text[:200]!r}")


def _balanced_chunks(text: str):
    """按括号配对切出可能的 JSON 片段（跳过字符串内的括号）。"""
    start = None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : i + 1]
                    start = None
        elif ch == '"' and depth > 0:
            in_str = True
