"""来源质量：域名分级 + 内容信号，给每条证据一个可解释的分数。

分级原则（不是绝对真理，只用于提示"这条证据有多硬"）：
- 3 一手：论文、官方文档、代码仓库、政府/高校站点
- 2 专业媒体 / 研究机构
- 1 一般站点、厂商博客
- 0 自媒体 / UGC（论坛、个人博客、聚合站）
"""

from __future__ import annotations

import re
from typing import Any

PRIMARY_DOMAINS = {
    "arxiv.org",
    "github.com",
    "gitlab.com",
    "acm.org",
    "ieee.org",
    "nature.com",
    "science.org",
    "openai.com",
    "anthropic.com",
    "deepmind.com",
    "huggingface.co",
    "pytorch.org",
    "tensorflow.org",
}
PRIMARY_SUFFIXES = (".edu", ".gov", ".edu.cn", ".gov.cn", ".ac.cn", ".mil")
PRIMARY_PREFIXES = ("docs.", "developer.", "research.", "platform.openai.com")

MEDIA_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "economist.com",
    "wsj.com",
    "nytimes.com",
    "infoq.com",
    "gartner.com",
    "mckinsey.com",
    "thoughtworks.com",
    "caixin.com",
    "36kr.com",
    "hbr.org",
    "technologyreview.com",
}

UGC_DOMAINS = {
    "reddit.com",
    "zhihu.com",
    "medium.com",
    "dev.to",
    "csdn.net",
    "cnblogs.com",
    "linkedin.com",
    "juejin.cn",
    "substack.com",
    "youtube.com",
    "bilibili.com",
    "x.com",
    "twitter.com",
}

VENDOR_DOMAINS = {
    "mem0.ai",
    "zilliz.com",
    "qdrant.tech",
    "weaviate.io",
    "pinecone.io",
    "milvus.io",
    "elastic.co",
    "cloudflare.com",
}

NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms|qps|倍|万|亿|gb|mb|tok)", re.I)
YEAR_RE = re.compile(r"^20[0-9]{2}$")


def domain_tier(domain: str) -> int:
    """返回 0–3 的域名等级。"""
    host = (domain or "").lower().strip(".")
    if not host:
        return 1
    if (
        host in PRIMARY_DOMAINS
        or host.endswith(PRIMARY_SUFFIXES)
        or host.startswith(PRIMARY_PREFIXES)
    ):
        return 3
    if host in MEDIA_DOMAINS or any(host.endswith("." + item) for item in MEDIA_DOMAINS):
        return 2
    if host in UGC_DOMAINS or any(host.endswith("." + item) for item in UGC_DOMAINS):
        return 0
    if host in VENDOR_DOMAINS or any(host.endswith("." + item) for item in VENDOR_DOMAINS):
        return 1
    return 1


TIER_LABELS = {3: "一手", 2: "专业媒体", 1: "一般", 0: "自媒体"}


def tier_label(tier: int) -> str:
    return TIER_LABELS.get(tier, "一般")


def assess_source(
    *,
    domain: str,
    content: str,
    published_at: str | None = None,
) -> dict[str, Any]:
    """给一条来源打分（0–100），并给出可解释的信号。"""
    tier = domain_tier(domain)
    body = content or ""
    numbers = len(NUM_RE.findall(body))
    length_score = min(len(body) / 6000, 1.0)
    number_score = min(numbers / 15, 1.0)
    if published_at:
        year = published_at[:4]
        recency = 1.0 if year >= "2025" else 0.6
    else:
        recency = 0.3

    score = tier / 3 * 40 + length_score * 20 + number_score * 20 + recency * 20
    return {
        "tier": tier,
        "tier_label": tier_label(tier),
        "score": int(round(min(score, 100))),
        "signals": {
            "numbers": numbers,
            "chars": len(body),
            "recency": round(recency, 2),
        },
    }
