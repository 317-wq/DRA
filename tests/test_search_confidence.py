from __future__ import annotations

from dra.config import Settings
from dra.kb import KnowledgeBase


def build_kb(tmp_path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        kb_path=tmp_path / "kb.sqlite3",
        runs_dir=tmp_path / "runs",
        _env_file=None,
    )
    kb = KnowledgeBase(settings, path=settings.kb_path, adapter=None)
    kb.add_document(
        title="医疗 QA",
        kind="note",
        source_path="medical-qa.md",
        text=(
            "### Q1：糖尿病患者为什么要监测血糖？\n\n"
            "监测有助于了解饮食、活动、疾病和药物对血糖的影响，并识别低血糖或高血糖风险。\n\n"
            "### Q2：为什么要接种疫苗？\n\n"
            "疫苗可降低特定感染及其严重并发症风险，也有助于群体防护。\n\n"
            "### Q3：空腹抽血为什么重要？\n\n"
            "空腹状态能减少饮食对血糖、血脂等指标的干扰。\n"
        ),
    )
    return kb


def test_generic_question_is_low_confidence(tmp_path):
    kb = build_kb(tmp_path)
    hits, info = kb.search("为什么要学习", k=6)
    assert hits == []
    assert info["low_confidence"] is True
    assert "学习" in info["query_terms"]
    kb.close()


def test_specific_question_hits_answer(tmp_path):
    kb = build_kb(tmp_path)
    hits, info = kb.search("糖尿病 监测 血糖", k=6)
    assert hits
    assert info["low_confidence"] is False
    assert "血糖" in hits[0].text
    kb.close()
