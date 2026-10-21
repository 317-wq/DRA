from __future__ import annotations

from dra.classify import classify, tags_from_text, tags_to_text


def test_classify_rag():
    category, tags = classify("RAG 与大模型应用", "检索增强生成结合向量数据库与重排。")
    assert category == "RAG"
    assert "RAG" in tags


def test_classify_network():
    category, tags = classify("epoll 网络编程", "用 epoll 实现高并发 TCP 服务，关注延迟与吞吐。")
    assert category == "网络编程"
    assert "网络编程" in tags


def test_classify_fallback():
    category, tags = classify("随记", "今天天气不错。")
    assert category == "其他"
    assert tags == ["未分类"]


def test_tags_roundtrip():
    value = tags_to_text(["RAG", "LLM 应用", "RAG"])
    assert value == "RAG,LLM 应用"
    assert tags_from_text(value) == ["RAG", "LLM 应用"]
