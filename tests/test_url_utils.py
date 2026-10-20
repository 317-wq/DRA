from __future__ import annotations

import pytest

from dra.tools.url_utils import PolicyError, assert_safe_url, canonicalize, domain_of


def test_canonicalize_strips_tracking_and_fragment():
    url = "https://Example.com:443/a/b/?utm_source=x&b=2&a=1#frag"
    assert canonicalize(url) == "https://example.com/a/b?a=1&b=2"


def test_canonicalize_trailing_slash():
    assert canonicalize("https://example.com/path/") == "https://example.com/path"


def test_canonicalize_keeps_root():
    assert canonicalize("https://example.com/") == "https://example.com/"


def test_canonicalize_adds_scheme():
    assert canonicalize("example.com/x") == "https://example.com/x"


def test_domain_of():
    assert domain_of("https://www.example.com/a") == "www.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://localhost:8000/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "ftp://example.com/a",
    ],
)
def test_rejects_internal_or_non_http(url):
    with pytest.raises(PolicyError):
        assert_safe_url(url)


def test_allows_public():
    assert assert_safe_url("https://example.com/a") == "https://example.com/a"
