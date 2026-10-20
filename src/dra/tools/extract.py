"""网页正文读取：Tavily Extract 优先，失败降级为原始抓取。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from dra.config import Settings
from dra.tools.url_utils import PolicyError, assert_safe_url

MAX_CONTENT_CHARS = 40_000
MAX_HTML_BYTES = 2_000_000
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "head", "nav", "footer"}


@dataclass
class Document:
    url: str
    title: str
    content: str
    via: str = "tavily-extract"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        """初始化实例。"""
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        """HTML 解析回调：维护跳过深度与标题状态。"""
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """HTML 解析回调：退出跳过与标题状态。"""
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """HTML 解析回调：收集标题或正文文本。"""
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        elif self._skip_depth == 0:
            self.parts.append(text)


def html_to_text(html: str) -> tuple[str, str]:
    """把 HTML 转成纯文本并提取标题。"""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    title = " ".join(parser.title_parts).strip()
    text = re.sub(r"[ \t]+", " ", "\n".join(parser.parts))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


class Reader:
    """批量读正文：一次 extract 调用处理一批 URL，失败项单独兜底。"""

    def __init__(self, settings: Settings) -> None:
        """初始化实例。"""
        self.settings = settings
        self._client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    def read(self, urls: list[str]) -> tuple[list[Document], list[tuple[str, str]]]:
        """返回 (成功文档, [(失败URL, 原因)])。"""
        safe, rejected = [], []
        for url in urls:
            try:
                safe.append(assert_safe_url(url))
            except PolicyError as exc:
                rejected.append((url, str(exc)))
        if not safe:
            return [], rejected

        docs: list[Document] = []
        failed: list[tuple[str, str]] = []
        pending = list(dict.fromkeys(safe))

        for batch in _chunks(pending, 8):
            got, bad = self._tavily_extract(batch)
            docs.extend(got)
            failed.extend(bad)

        # 兜底：Tavily 没拿到的逐个原始抓取
        still_missing = [u for u, _ in failed]
        fallback_reasons: dict[str, str] = dict(failed)
        for url in still_missing:
            doc, err = self._fetch(url)
            if doc:
                docs.append(doc)
            else:
                failed.append((url, err or fallback_reasons.get(url, "抓取失败")))

        seen: set[str] = set()
        unique: list[Document] = []
        for doc in docs:
            if doc.url in seen:
                continue
            seen.add(doc.url)
            content = (doc.content or "").strip()[:MAX_CONTENT_CHARS]
            if len(content) < 80:
                failed.append((doc.url, "正文过短（可能被反爬或需登录）"))
                continue
            doc.content = content
            unique.append(doc)
        return unique, rejected + [f for f in failed if f[0] not in seen]

    # ------------------------------------------------------------------
    def _tavily_extract(self, urls: list[str]) -> tuple[list[Document], list[tuple[str, str]]]:
        """调用 Tavily extract 批量取正文。"""
        payload = {
            "api_key": self.settings.tavily_api_key,
            "urls": urls,
            "extract_depth": "basic",
        }
        try:
            resp = self._client.post(
                f"{self.settings.tavily_base_url.rstrip('/')}/extract", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # 网络/额度问题都走兜底
            return [], [(u, f"extract 调用失败：{type(exc).__name__}") for u in urls]

        docs = []
        for item in data.get("results", []) or []:
            content = item.get("raw_content") or ""
            docs.append(
                Document(
                    url=item.get("url") or "",
                    title=(item.get("title") or "")[:300],
                    content=content,
                    via="tavily-extract",
                )
            )
        bad = []
        for item in data.get("failed_results", []) or []:
            url = item.get("url") or ""
            bad.append((url, (item.get("error") or "extract 失败")[:120]))
        got = {d.url for d in docs}
        for url in urls:
            if url not in got and url not in {u for u, _ in bad}:
                bad.append((url, "extract 未返回"))
        return docs, bad

    def _fetch(self, url: str) -> tuple[Document | None, str | None]:
        """兜底抓取：直接请求网页并抽正文。"""
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return None, f"非文本内容：{ctype[:40]}"
            raw = resp.content[:MAX_HTML_BYTES]
            charset = resp.encoding or "utf-8"
            html = raw.decode(charset, errors="replace")
        except PolicyError as exc:
            return None, str(exc)
        except Exception as exc:
            return None, f"抓取失败：{type(exc).__name__}"
        title, text = html_to_text(html)
        if not title:
            title = url
        return Document(url=url, title=title[:300], content=text, via="fallback-fetch"), None


def _chunks(items: list[str], size: int):
    """把列表切成固定大小的批次。"""
    for i in range(0, len(items), size):
        yield items[i : i + size]
