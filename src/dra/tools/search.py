"""Tavily 搜索工具。"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from dra.config import Settings


class ToolError(RuntimeError):
    pass


@dataclass
class SearchHit:
    title: str
    url: str
    content: str = ""
    score: float = 0.0
    published_date: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


class TavilySearch:
    def __init__(self, settings: Settings) -> None:
        """初始化检索客户端。"""
        self.settings = settings
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

    def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        topic: str = "general",
        days: int | None = None,
    ) -> list[SearchHit]:
        """执行一次搜索，返回结构化结果。"""
        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "topic": topic,
        }
        if days:
            payload["days"] = days
        try:
            resp = self._client.post(f"{self.settings.tavily_base_url.rstrip('/')}/search", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200] if exc.response is not None else ""
            raise ToolError(f"搜索失败 {exc.response.status_code}：{query} / {body}") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"搜索网络错误：{query} / {exc}") from exc

        data = resp.json()
        hits: list[SearchHit] = []
        for item in data.get("results", []) or []:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=(item.get("title") or url)[:300],
                    url=url,
                    content=item.get("content") or "",
                    score=float(item.get("score") or 0.0),
                    published_date=item.get("published_date"),
                    raw=item,
                )
            )
        return hits
