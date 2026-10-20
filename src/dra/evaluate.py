"""报告证据质量评估：引用覆盖、数字可核验性、来源多样性。

纯本地计算，不调用模型、不消耗搜索额度；用现有 run 的 report + sources 即可跑。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from dra.models import Source

CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
UNIT_RE = r"(?:%|ms|qps|倍|万|亿|gb|mb|tok)"
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
YEARS = {"2023", "2024", "2025", "2026", "2027"}
FACTUAL_HINTS = (
    "显示",
    "表明",
    "达到",
    "占比",
    "增长",
    "下降",
    "提升",
    "降低",
    "倍",
    "成本",
    "延迟",
    "吞吐",
)


def _cite_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for group in CITE_RE.findall(text):
        numbers.extend(int(item) for item in re.findall(r"\d+", group))
    return numbers


def _numbers(text: str) -> list[str]:
    found: list[str] = []
    # 先把 [n] 引用标记去掉，避免把编号当成数字
    clean = CITE_RE.sub("", text)
    for raw in NUM_RE.findall(clean):
        if raw in YEARS:
            continue
        has_unit = bool(re.search(rf"{re.escape(raw)}\s*{UNIT_RE}", clean, re.I))
        if float(raw) < 10 and "." not in raw and not has_unit:
            continue
        found.append(raw)
    return found


def evaluate_report(
    *,
    run_id: str,
    topic: str,
    sources: list[Source],
    report_text: str,
) -> dict[str, Any]:
    """计算一份报告的证据质量指标。"""
    by_n = {src.n: src for src in sources}
    body = report_text.split("## 参考资料")[0]
    # 标题与引用块里的日期/编号不算正文事实
    body = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith(("#", ">"))
    )
    sentences = [item.strip() for item in SENT_SPLIT.split(body) if item.strip()]

    citations_total = 0
    invalid = 0
    factual = 0
    cited_factual = 0
    numbers_total = 0
    numbers_supported = 0
    unverified: list[dict[str, Any]] = []

    for sentence in sentences:
        cited_ns = _cite_numbers(sentence)
        citations_total += len(cited_ns)
        invalid += sum(1 for n in cited_ns if n not in by_n)
        nums = _numbers(sentence)
        is_factual = bool(nums) or any(hint in sentence for hint in FACTUAL_HINTS)
        if not is_factual:
            continue
        factual += 1
        if cited_ns:
            cited_factual += 1
        for num in nums:
            numbers_total += 1
            pattern = re.compile(rf"(?<![\d.]){re.escape(num)}(?![\d.])")
            supported = any(
                pattern.search(by_n[n].snippet or "") for n in cited_ns if n in by_n
            )
            if supported:
                numbers_supported += 1
            else:
                unverified.append(
                    {
                        "number": num,
                        "cited": cited_ns,
                        "sentence": sentence[:120],
                    }
                )

    domains = {src.domain for src in sources}
    tiers = Counter(src.tier for src in sources)
    tier_avg = (sum(src.tier for src in sources) / len(sources) / 3) if sources else 0.0
    citation_rate = cited_factual / factual if factual else 1.0
    support_rate = numbers_supported / numbers_total if numbers_total else 1.0
    diversity = min(len(domains) / 6, 1.0)

    score = round(
        100
        * (
            0.35 * citation_rate
            + 0.35 * support_rate
            + 0.15 * diversity
            + 0.15 * tier_avg
        )
    )
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"

    warnings: list[str] = []
    if invalid:
        warnings.append(f"{invalid} 处引用指向不存在的来源")
    if factual and citation_rate < 0.8:
        warnings.append(f"仅 {citation_rate:.0%} 的事实句带引用")
    if numbers_total and support_rate < 0.8:
        warnings.append(
            f"{numbers_total - numbers_supported}/{numbers_total} 个数字未能在引用来源中找到"
        )
    if len(domains) < 3 and sources:
        warnings.append(f"来源只覆盖 {len(domains)} 个域名，独立性不足")
    if tier_avg < 0.4 and sources:
        warnings.append("一手来源占比偏低，结论更依赖二手材料")

    return {
        "run_id": run_id,
        "topic": topic,
        "score": score,
        "grade": grade,
        "sources": {
            "total": len(sources),
            "domains": len(domains),
            "tiers": {str(k): v for k, v in sorted(tiers.items(), reverse=True)},
            "tier_avg": round(tier_avg, 2),
        },
        "citations": {
            "total": citations_total,
            "invalid": invalid,
            "factual_sentences": factual,
            "cited_factual": cited_factual,
            "citation_rate": round(citation_rate, 3),
        },
        "numbers": {
            "total": numbers_total,
            "supported": numbers_supported,
            "support_rate": round(support_rate, 3),
            "unverified": unverified[:20],
        },
        "warnings": warnings,
    }


def evaluate_run(run) -> dict[str, Any]:
    """从 run 目录读取产物并评估。"""
    meta_path = run.dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    sources_path = run.dir / "sources.json"
    report_path = run.dir / "report.md"
    if not sources_path.exists() or not report_path.exists():
        raise FileNotFoundError("这次研究缺少 report.md 或 sources.json")
    sources = [Source(**item) for item in json.loads(sources_path.read_text(encoding="utf-8"))]
    result = evaluate_report(
        run_id=run.id,
        topic=meta.get("topic", run.id),
        sources=sources,
        report_text=report_path.read_text(encoding="utf-8"),
    )
    (run.dir / "eval.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """对一组评估做基线汇总。"""
    if not results:
        return {"runs": 0}
    scores = [item["score"] for item in results]
    return {
        "runs": len(results),
        "mean_score": round(sum(scores) / len(scores), 1),
        "min_score": min(scores),
        "max_score": max(scores),
        "mean_citation_rate": round(
            sum(item["citations"]["citation_rate"] for item in results) / len(results), 3
        ),
        "mean_support_rate": round(
            sum(item["numbers"]["support_rate"] for item in results) / len(results), 3
        ),
        "grades": dict(Counter(item["grade"] for item in results)),
    }


def latest_eval_path(run_dir: Path) -> Path:
    return run_dir / "eval.json"
