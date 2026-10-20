"""引用收尾：只保留证据库里存在的编号，重排为连续编号，参考资料由程序生成。"""

from __future__ import annotations

import re

from dra.models import Source

CITE_RE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
URL_RE = re.compile(r"https?://\S+")


def finalize_report(
    body: str, sources: list[Source], *, renumber: bool = True
) -> tuple[str, list[Source], int]:
    """返回 (正文+参考资料, 被引用的来源(已重排), 被丢弃的无效引用数)。

    - 模型只能引用 sources 里已有的编号，其余一律丢弃；
    - 引用按"首次出现顺序"重排为 1..k，保证报告里编号连续；
    - 未被引用的来源不进报告，也不进 sources.json（避免"凑数"）。
    """
    valid = {s.n: s for s in sources}
    order: list[int] = []
    mapping: dict[int, int] = {}
    dropped = 0

    def repl(match: re.Match[str]) -> str:
        """把模型写的引用编号映射为连续编号，丢弃无效编号。"""
        nonlocal dropped
        nums = [int(x) for x in re.findall(r"\d+", match.group(1))]
        new_nums: list[int] = []
        for n in nums:
            if n not in valid:
                dropped += 1
                continue
            if renumber:
                if n not in mapping:
                    order.append(n)
                    mapping[n] = len(order)
                if mapping[n] not in new_nums:
                    new_nums.append(mapping[n])
            else:
                if n not in order:
                    order.append(n)
                if n not in new_nums:
                    new_nums.append(n)
        if not new_nums:
            return ""
        return "[" + ",".join(str(x) for x in new_nums) + "]"

    body = CITE_RE.sub(repl, body)
    body = re.sub(r"[ \t]+([，。；：])", r"\1", body)
    body = re.sub(r"\(\s*\)", "", body)

    cited: list[Source] = []
    for old_n in order:
        src = valid[old_n].model_copy(deep=True)
        if renumber:
            src.n = mapping[old_n]
        cited.append(src)

    if not cited:
        return body.rstrip() + "\n", [], dropped

    lines = ["", "## 参考资料", ""]
    for src in cited:
        date = f"（{src.published_at[:10]}）" if src.published_at else ""
        lines.append(f"{src.n}. {src.title} — <{src.url}> {date}".rstrip())
    lines.append("")
    return body.rstrip() + "\n" + "\n".join(lines), cited, dropped


def count_chinese_chars(text: str) -> int:
    """统计中文字符数（用于报告长度判断）。"""
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def strip_urls_for_check(text: str) -> list[str]:
    """报告正文里不该出现裸 URL（参考资料除外）。返回命中列表，供告警。"""
    body = text.split("## 参考资料")[0]
    return URL_RE.findall(body)
