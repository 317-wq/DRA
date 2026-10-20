"""引用复核：报告写完后，对实际引用的 URL 做一次轻量可达性检查。

不是"死链清理"——403/超时多数是反爬或临时网络问题，浏览器里可能正常。
复核结果写进 sources.json，让人一眼看出哪些链接需要人工确认。
"""

from __future__ import annotations

import asyncio

import httpx

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


async def _check(client: httpx.AsyncClient, url: str, timeout: float) -> tuple[str, str]:
    """检查单个 URL：先 HEAD，失败再带 Range 的 GET。"""
    try:
        resp = await client.head(url, timeout=timeout)
        if resp.status_code >= 400:
            resp = await client.get(
                url,
                timeout=timeout,
                headers={"Range": "bytes=0-2047"},
            )
        return url, str(resp.status_code)
    except Exception:
        return url, "unreachable"


def check_urls(
    urls: list[str], *, timeout: float = 10.0, concurrency: int = 4
) -> dict[str, str]:
    """并发检查 URL，返回 {url: "200" | "403" | "unreachable" | ...}。"""
    if not urls:
        return {}

    async def main() -> dict[str, str]:
        """并发调度所有检查。"""
        sem = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": UA}) as client:
            async def one(u: str) -> tuple[str, str]:
                """在信号量内检查一个 URL。"""
                async with sem:
                    return await _check(client, u, timeout)

            return dict(await asyncio.gather(*[one(u) for u in urls]))

    return asyncio.run(main())


def is_ok(status: str) -> bool:
    """2xx/3xx 视为可达。"""
    return status.isdigit() and 200 <= int(status) < 400
