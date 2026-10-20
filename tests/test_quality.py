from __future__ import annotations

from dra.quality import assess_source, domain_tier, tier_label


def test_domain_tiers():
    assert domain_tier("arxiv.org") == 3
    assert domain_tier("docs.python.org") == 3
    assert domain_tier("mit.edu") == 3
    assert domain_tier("reuters.com") == 2
    assert domain_tier("mem0.ai") == 1
    assert domain_tier("reddit.com") == 0
    assert domain_tier("unknown-blog.xyz") == 1


def test_tier_labels():
    assert tier_label(3) == "一手"
    assert tier_label(0) == "自媒体"


def test_primary_scores_higher_than_ugc():
    rich = assess_source(
        domain="arxiv.org",
        content="延迟 15ms，准确率 96.1%，成本 3%。" * 50,
        published_at="2026-05-01",
    )
    poor = assess_source(domain="reddit.com", content="听说不错", published_at=None)
    assert rich["tier"] == 3
    assert rich["score"] > poor["score"]
    assert 0 <= poor["score"] <= 100
