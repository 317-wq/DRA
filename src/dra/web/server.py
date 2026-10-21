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
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from dra import __version__
from dra.config import Settings, config_path, load_toml, write_toml_config
from dra.evaluate import evaluate_run
from dra.followup import FollowupEngine
from dra.ingest import IngestError, extract_text, kind_for
from dra.jobs import BusyError, RunManager, read_all_events, read_new_events
from dra.kb import KnowledgeBase
from dra.llm import ModelAdapter
from dra.providers import ProviderError, fetch_models, provider_list, test_chat

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


class StartRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    max_sources: int = Field(default=12, ge=4, le=20)


class SinkRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=32)


class FollowupRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    k: int = Field(default=6, ge=1, le=12)
    parent_followup: str | None = Field(
        default=None, pattern=r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$"
    )


class ContinueRequest(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=4)
    max_sources: int = Field(default=6, ge=1, le=20)


class ProviderModelsRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=300)
    api_key: str | None = None


class ProviderTestRequest(ProviderModelsRequest):
    model: str = Field(min_length=1, max_length=200)


class SettingsPatch(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    fallback_models: list[str] | None = None
    tavily_api_key: str | None = None
    embedding_model: str | None = None
    min_number_support: float | None = Field(default=None, ge=0, le=1)


def _static_dir() -> Path:
    return Path(__file__).parent / "static"


def create_app(
    settings: Settings,
    manager: RunManager | None = None,
    kb_factory=None,
    followup_factory=None,
) -> FastAPI:
    manager = manager or RunManager(settings)
    kb_factory = kb_factory or (
        lambda: KnowledgeBase(settings, adapter=ModelAdapter(settings))
    )
    followup_factory = followup_factory or (lambda: FollowupEngine(settings, manager.store))
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

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "active": manager.active_run(),
            "queued": manager.queue_size(),
            "runs": manager.store.count(),
        }

    @app.get("/api/runs")
    def list_runs(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        items = manager.store.list(limit=limit, offset=offset, status=status)
        for item in items:
            try:
                run = manager.store.open(item["run_id"])
                followups = run.dir / "followups.json"
                item["followup_count"] = (
                    len(json.loads(followups.read_text(encoding="utf-8")))
                    if followups.exists()
                    else 0
                )
                report = run.dir / "report.md"
                item["chars"] = (
                    len(report.read_text(encoding="utf-8")) if report.exists() else 0
                )
            except (OSError, ValueError):
                item.setdefault("followup_count", 0)
                item.setdefault("chars", 0)
        return {
            "runs": items,
            "total": manager.store.count(status=status),
            "active": manager.active_run(),
            "queued": manager.queue_size(),
        }

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
        meta["queued"] = meta.get("status") == "queued"
        return meta

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> dict[str, str]:
        run_or_404(run_id)
        result = manager.cancel(run_id)
        if result == "unknown":
            raise HTTPException(status_code=404, detail="run 不存在")
        return {"result": result}

    @app.post("/api/runs/{run_id}/retry", status_code=202)
    def retry_run(run_id: str) -> dict[str, str]:
        run_or_404(run_id)
        try:
            new_id = manager.retry(run_id)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": new_id}

    @app.post("/api/runs/{run_id}/continue", status_code=202)
    def continue_run(run_id: str, body: ContinueRequest) -> dict[str, str]:
        run_or_404(run_id)
        try:
            new_id = manager.start_continue(run_id, body.questions, body.max_sources)
        except (BusyError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": new_id}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, bool]:
        run_or_404(run_id)
        try:
            removed = manager.delete(run_id)
        except BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"removed": removed}

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

    @app.get("/api/runs/{run_id}/export")
    def export_report(run_id: str, format: str = "md") -> Response:
        run = run_or_404(run_id)
        path = run.dir / "report.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="报告还没生成")
        text = path.read_text(encoding="utf-8")
        if format == "html":
            body = markdown.render(text)
            html = (
                "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                f"<title>{run.topic or run_id}</title>"
                "<style>body{max-width:900px;margin:40px auto;padding:0 20px;"
                "font:16px/1.8 -apple-system,'Segoe UI','PingFang SC',sans-serif;color:#222}"
                "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:6px}"
                "a{color:#2563eb}</style></head><body>"
                f"{body}</body></html>"
            )
            return Response(
                html,
                media_type="text/html; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.html"'},
            )
        return Response(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'},
        )

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

    @app.get("/api/runs/{run_id}/eval")
    def run_eval(run_id: str) -> dict[str, Any]:
        run = run_or_404(run_id)
        if not (run.dir / "report.md").exists() or not (run.dir / "sources.json").exists():
            raise HTTPException(status_code=409, detail="这次运行缺少报告或来源")
        return evaluate_run(run)

    @app.get("/api/runs/{run_id}/followups")
    def list_followups(run_id: str) -> dict[str, Any]:
        run_or_404(run_id)
        engine = followup_factory()
        return {"items": engine.list(run_id)}

    @app.post("/api/runs/{run_id}/followups")
    def ask_followup(run_id: str, body: FollowupRequest) -> dict[str, Any]:
        run_or_404(run_id)
        engine = followup_factory()
        target = run_id
        if body.parent_followup:
            try:
                engine.get(run_id, body.parent_followup)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="父追问不存在") from exc
            target = body.parent_followup
        try:
            result = engine.ask(target, body.question, k=body.k)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "id": result.followup_id,
            "parent_run_id": result.parent_run_id,
            "parent_followup": result.parent_followup,
            "question": result.question,
            "cited_ns": result.cited_ns,
            "chars": result.chars,
            "duration_s": result.duration_s,
        }

    @app.get("/api/runs/{run_id}/followups/{followup_id}")
    def get_followup(run_id: str, followup_id: str) -> dict[str, Any]:
        run_or_404(run_id)
        engine = followup_factory()
        try:
            meta, answer = engine.get(run_id, followup_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="追问不存在") from exc
        return {"meta": meta, "answer": answer, "html": markdown.render(answer)}

    @app.get("/api/providers")
    def providers() -> dict[str, Any]:
        return {"providers": provider_list()}

    @app.post("/api/providers/models")
    def provider_models(body: ProviderModelsRequest) -> dict[str, Any]:
        key = body.api_key or settings.api_key
        try:
            models = fetch_models(body.base_url, key)
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"models": models}

    @app.post("/api/providers/test")
    def provider_test(body: ProviderTestRequest) -> dict[str, Any]:
        key = body.api_key or settings.api_key
        try:
            return test_chat(body.base_url, key, body.model)
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        current = Settings()
        data = current.public_summary()
        data["model"] = current.model
        data["plan_model"] = current.plan_model_resolved
        data["analyze_model"] = current.analyze_model_resolved
        data["write_model"] = current.write_model_resolved
        data["fallback_models"] = current.fallback_models
        data["min_number_support"] = current.min_number_support
        data["api_key_set"] = bool(current.api_key)
        data["tavily_key_set"] = bool(current.tavily_api_key)
        data["config_file"] = str(config_path())
        data["restart_required_hint"] = "修改后新任务生效，已启动的服务需重启"
        return data

    @app.put("/api/settings")
    def update_settings(body: SettingsPatch) -> dict[str, Any]:
        values = load_toml()
        # 空字符串表示“不修改”，避免前端留空把已配置的 Key 清掉
        patch = {
            key: value
            for key, value in body.model_dump(exclude_none=True).items()
            if value != ""
        }
        if not patch:
            return {"updated": [], "path": str(config_path()), "reloaded": []}
        values.update(patch)
        path = write_toml_config(values)
        reloaded = settings.apply_hot(Settings())
        return {
            "updated": sorted(patch),
            "path": str(path),
            "reloaded": reloaded,
            "restart_required": False,
        }

    @app.get("/api/kb")
    def kb_index() -> dict[str, Any]:
        kb = kb_factory()
        try:
            return kb.export_index()
        finally:
            kb.close()

    @app.get("/api/kb/search")
    def kb_search(q: str, k: int = 6) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"mode": "empty", "hits": [], "keyword_hits": 0, "vector_hits": 0}
        kb = kb_factory()
        try:
            hits, info = kb.search(query, k=max(1, min(k, 20)))
        finally:
            kb.close()
        return {
            **info,
            "hits": [
                {
                    "doc_id": hit.doc_id,
                    "title": hit.title,
                    "run_id": hit.run_id,
                    "seq": hit.seq,
                    "text": hit.text,
                    "snippet": hit.snippet,
                    "coverage": hit.coverage,
                    "score": hit.score,
                    "keyword": hit.keyword,
                    "vector": hit.vector,
                }
                for hit in hits
            ],
        }

    @app.post("/api/kb/upload")
    async def kb_upload(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        data = await file.read()
        filename = file.filename or "未命名"
        try:
            kind = kind_for(filename)
            text = extract_text(filename, data)
        except IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        title = Path(filename).stem or "未命名"
        kb = kb_factory()
        try:
            doc_id, chunks = kb.add_document(
                title=title,
                text=text,
                kind=kind,
                source_path=filename,
            )
            detail = kb.get_document(doc_id) or {}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            kb.close()
        document = detail.get("document", {})
        return {
            "doc_id": doc_id,
            "chunks": chunks,
            "title": title,
            "kind": kind,
            "category": document.get("category", ""),
            "tags": document.get("tags", []),
        }

    @app.post("/api/kb/{doc_id}/reindex")
    def kb_reindex(doc_id: int) -> dict[str, Any]:
        kb = kb_factory()
        try:
            chunks = kb.reindex_document(doc_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            kb.close()
        return {"doc_id": doc_id, "chunks": chunks}

    @app.get("/api/kb/{doc_id}")
    def kb_document(doc_id: int) -> dict[str, Any]:
        kb = kb_factory()
        try:
            data = kb.get_document(doc_id)
        finally:
            kb.close()
        if data is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return data

    @app.post("/api/kb/sink")
    def kb_sink(body: SinkRequest) -> dict[str, Any]:
        run = run_or_404(body.run_id)
        report = run.dir / "report.md"
        if not report.exists():
            raise HTTPException(status_code=409, detail="这次运行还没有报告")
        meta = manager.meta(body.run_id)
        kb = kb_factory()
        try:
            doc_id, chunks = kb.add_report(
                run_id=body.run_id,
                title=meta.get("topic") or body.run_id,
                text=report.read_text(encoding="utf-8"),
                source_path=str(report),
            )
        finally:
            kb.close()
        return {"doc_id": doc_id, "chunks": chunks, "skipped": chunks == 0}

    @app.delete("/api/kb/{doc_id}")
    def kb_delete(doc_id: int) -> dict[str, bool]:
        kb = kb_factory()
        try:
            return {"removed": kb.remove(doc_id)}
        finally:
            kb.close()

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
            return sock.getsockname()[1]
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
