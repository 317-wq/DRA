"""FastAPI 本地界面：静态页 + REST + SSE。

只绑定 127.0.0.1；事件流直接读 runs/<id>/trace.jsonl，因此刷新页面、
甚至重启进程后回放都不会丢。
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from dra.config import Settings
from dra.jobs import BusyError, RunManager, read_all_events, read_new_events

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


class StartRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    max_sources: int = Field(default=12, ge=4, le=20)


def _static_dir() -> Path:
    return Path(__file__).parent / "static"


def create_app(settings: Settings, manager: RunManager | None = None) -> FastAPI:
    manager = manager or RunManager(settings)
    static_dir = _static_dir()
    app = FastAPI(title="Deep Research Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    markdown = MarkdownIt("commonmark", {"html": False, "linkify": True})

    def run_or_404(run_id: str):
        if not RUN_ID_RE.match(run_id):
            raise HTTPException(status_code=404, detail="run 不存在")
        try:
            return manager.get_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run 不存在") from exc

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/runs")
    def list_runs() -> dict[str, Any]:
        return {"runs": manager.store.list(), "active": manager.active_run()}

    @app.post("/api/runs", status_code=202)
    def start_run(body: StartRequest) -> dict[str, str]:
        try:
            run_id = manager.start(body.topic.strip(), body.max_sources)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    def run_meta(run_id: str) -> dict[str, Any]:
        run_or_404(run_id)
        meta = manager.meta(run_id)
        meta["running"] = manager.is_running(run_id)
        return meta

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: str, format: str = "html") -> HTMLResponse:
        run = run_or_404(run_id)
        path = run.dir / "report.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="报告还没生成")
        text = path.read_text(encoding="utf-8")
        if format == "md":
            return HTMLResponse(f"<pre>{text}</pre>", media_type="text/html; charset=utf-8")
        return HTMLResponse(markdown.render(text), media_type="text/html; charset=utf-8")

    @app.get("/api/runs/{run_id}/sources")
    def run_sources(run_id: str) -> list[dict[str, Any]]:
        run = run_or_404(run_id)
        path = run.dir / "sources.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    @app.get("/api/runs/{run_id}/trace")
    def run_trace(run_id: str) -> list[dict[str, Any]]:
        run = run_or_404(run_id)
        return read_all_events(run.trace_path)

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> StreamingResponse:
        run = run_or_404(run_id)

        async def stream():
            sent = 0
            finished = False
            while True:
                events, sent = read_new_events(run.trace_path, sent)
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") in ("run.finished", "run.failed"):
                        finished = True
                if finished and not events:
                    break
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def find_free_port(host: str, start: int, tries: int = 20) -> int:
    """从 start 开始找一个能绑定的端口。"""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"{host} 上 {start}–{start + tries - 1} 没有空闲端口")


def serve(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """启动本地服务；只监听回环地址。"""
    import uvicorn

    app = create_app(settings)
    real_port = find_free_port(host, port)
    url = f"http://{host}:{real_port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Deep Research Agent · 本地界面 {url}", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    uvicorn.run(app, host=host, port=real_port, log_level="warning")
