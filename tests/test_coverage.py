from __future__ import annotations

from dra.agent import enough_supported
from dra.models import Source


def make(n: int, domain: str) -> Source:
    return Source(n=n, url=f"https://{domain}/{n}", title="t", domain=domain, fetched_at="2026-10-20")


def test_two_domains_support_enough():
    sources = [make(1, "a.com"), make(2, "b.com")]
    assert enough_supported([1, 2], sources)


def test_single_domain_not_enough():
    sources = [make(1, "a.com"), make(2, "a.com")]
    assert not enough_supported([1, 2], sources)


def test_empty_not_enough():
    assert not enough_supported([], [make(1, "a.com")])


def test_subset_of_cited_sources():
    sources = [make(1, "a.com"), make(2, "b.com"), make(3, "c.com")]
    assert enough_supported([1, 2], sources)
    assert not enough_supported([1], sources)
