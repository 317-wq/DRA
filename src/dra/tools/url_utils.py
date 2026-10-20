"""URL 规范化与安全校验（防 SSRF、去跟踪参数、去重）。"""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "spm", "from_", "ref_")
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "yclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "igshid",
    "share_token",
}
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
}
BLOCKED_SUFFIXES = (".local", ".internal", ".lan", ".localhost", ".home.arpa")


class PolicyError(ValueError):
    pass


def canonicalize(url: str) -> str:
    """统一 scheme/host/端口，去掉 fragment、跟踪参数，路径去尾斜杠。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return raw
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_KEYS
        and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query_pairs.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def domain_of(url: str) -> str:
    """取 URL 的主机名。"""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def assert_safe_url(url: str) -> str:
    """只放行公网 http(s)。刻意不做 DNS 解析（0.1 用域名黑名单兜底）。"""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PolicyError(f"URL 无法解析：{url}") from exc
    if parts.scheme not in ("http", "https"):
        raise PolicyError(f"只允许 http/https：{url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise PolicyError(f"URL 缺少主机名：{url}")
    if len(url) > 2000:
        raise PolicyError("URL 过长")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise PolicyError(f"拒绝内网主机：{host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url  # 普通域名
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise PolicyError(f"拒绝内网地址：{host}")
    return url
