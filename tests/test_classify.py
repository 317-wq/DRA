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


def test_classify_medical():
    category, tags = classify("医疗 QA", "人体正常体温范围与血压、心率的参考值。")
    assert category == "医疗健康"
    assert "医疗健康" in tags


def test_classify_fallback():
    category, tags = classify("随记", "今天天气不错。")
    assert category == "其他"
    assert tags == ["未分类"]


def test_tags_roundtrip():
    value = tags_to_text(["RAG", "LLM 应用", "RAG"])
    assert value == "RAG,LLM 应用"
    assert tags_from_text(value) == ["RAG", "LLM 应用"]


def test_chunk_text_splits_qa_headings():
    from dra.kb import chunk_text

    text = (
        "## 一、基础医学知识\n\n"
        "### Q1：静息心率是多少？\n\n成年人 60-100 次/分钟。\n\n"
        "### Q2：体温范围是多少？\n\n腋下 36.0-37.0 ℃。\n"
    )
    chunks = chunk_text(text)
    q1 = [chunk for chunk in chunks if "Q1" in chunk]
    q2 = [chunk for chunk in chunks if "Q2" in chunk]
    assert q1 and q2
    assert "Q2" not in q1[0]
    assert "36.0-37.0" in q2[0]
