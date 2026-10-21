"""本地规则分类：给知识库文档自动打标签，不调用模型、不消耗额度。"""

from __future__ import annotations

# 标签 → 关键词。标题命中权重 3，正文命中权重 1。
TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "RAG",
        ("rag", "检索增强", "retrieval augmented", "重排", "rerank", "召回", "embedding"),
    ),
    (
        "Agent",
        ("agent", "智能体", "tool use", "function calling", "mcp", "多智能体"),
    ),
    (
        "LLM 应用",
        ("llm", "大模型", "prompt", "上下文窗口", "推理", "微调", "fine-tun"),
    ),
    (
        "向量数据库",
        (
            "向量数据库",
            "vector database",
            "faiss",
            "milvus",
            "qdrant",
            "pinecone",
            "weaviate",
            "hnsw",
            "ann-benchmarks",
        ),
    ),
    (
        "网络编程",
        ("socket", "epoll", "tcp", "udp", "网络编程", "io 多路复用", "reactor", "grpc"),
    ),
    (
        "系统设计",
        ("架构", "系统设计", "高并发", "分布式", "微服务", "缓存", "消息队列", "扩容"),
    ),
    (
        "性能优化",
        ("延迟", "吞吐", "性能", "benchmark", "优化", "qps", "p99", "成本"),
    ),
    (
        "安全与合规",
        ("安全", "注入", "攻击", "漏洞", "合规", "隐私", "越权", "风控"),
    ),
    (
        "医疗健康",
        ("医疗", "医学", "临床", "体温", "血压", "心率", "症状", "治疗", "药物", "健康", "疾病"),
    ),
    (
        "数据处理",
        ("etl", "数据清洗", "pandas", "sql", "数据仓库", "流处理"),
    ),
    (
        "产品与商业",
        ("产品", "商业", "市场", "定价", "用户增长", "竞品"),
    ),
]

UNCLASSIFIED = "其他"
MAX_BODY = 30_000


def classify(title: str, text: str) -> tuple[str, list[str]]:
    """返回 (主分类, 标签列表)。标题权重更高，最多 3 个标签。"""
    title_l = (title or "").lower()
    body_l = (text or "")[:MAX_BODY].lower()
    scores: dict[str, int] = {}
    for tag, keywords in TAG_RULES:
        score = 0
        for keyword in keywords:
            if keyword in title_l:
                score += 3
            if keyword in body_l:
                score += 1
        if score > 0:
            scores[tag] = score
    if not scores:
        return UNCLASSIFIED, ["未分类"]
    tags = sorted(scores, key=lambda name: scores[name], reverse=True)[:3]
    return tags[0], tags


def tags_to_text(tags: list[str]) -> str:
    """标签列表 → 数据库里的逗号分隔字符串。"""
    return ",".join(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


def tags_from_text(value: str | None) -> list[str]:
    """数据库字符串 → 标签列表。"""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
