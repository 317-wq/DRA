from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dra.config import Settings
from dra.jobs import RunManager
from dra.kb import KnowledgeBase
from dra.store import RunStore
from dra.web.server import create_app


class FakeAdapter:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "alpha" in text.lower() else [0.0, 1.0] for text in texts]


class FakeAgent:
    def run(self, topic, *, budget=None, max_sources=12, on_event=None, run=None, cancel_event=None):
        return None


def build(tmp_path: Path):
    settings = Settings(
        api_key="k",
        tavily_api_key="t",
        runs_dir=tmp_path / "runs",
        kb_path=tmp_path / "kb.sqlite3",
        _env_file=None,
    )
    store = RunStore(settings.runs_dir)
    manager = RunManager(settings, store=store, agent_factory=lambda: FakeAgent())

    def kb_factory():
        return KnowledgeBase(settings, path=settings.kb_path, adapter=FakeAdapter())

    app = create_app(settings, manager=manager, kb_factory=kb_factory)
    return TestClient(app), store


def seed(store: RunStore) -> str:
    run = store.create("Alpha 主题")
    run.write_text("report.md", "## 摘要\n\nalpha 是核心结论。\n\n## 细节\n\nalpha 还有更多细节。\n")
    run.write_json(
        "run.json",
        {
            "run_id": run.id,
            "topic": "Alpha 主题",
            "status": "done",
            "started_at": "2026-10-21T00:00:00",
            "finished_at": "2026-10-21T00:00:01",
            "duration_s": 1,
            "sources": [],
            "notes": [],
        },
    )
    return run.id


def test_kb_index_and_sink(tmp_path):
    client, store = build(tmp_path)
    rid = seed(store)

    before = client.get("/api/kb").json()
    assert before["stats"]["documents"] == 0

    res = client.post("/api/kb/sink", json={"run_id": rid})
    assert res.status_code == 200
    body = res.json()
    assert body["chunks"] > 0
    assert body["skipped"] is False

    after = client.get("/api/kb").json()
    assert after["stats"]["documents"] == 1
    assert after["documents"][0]["title"] == "Alpha 主题"
    assert after["documents"][0]["format"] == "MD"
    assert after["documents"][0]["origin"] == "研究沉淀"

    again = client.post("/api/kb/sink", json={"run_id": rid}).json()
    assert again["skipped"] is True


def test_kb_search_and_delete(tmp_path):
    client, store = build(tmp_path)
    rid = seed(store)
    doc_id = client.post("/api/kb/sink", json={"run_id": rid}).json()["doc_id"]

    res = client.get("/api/kb/search", params={"q": "alpha", "k": 3}).json()
    assert res["mode"] == "hybrid"
    assert res["hits"]
    assert res["hits"][0]["title"] == "Alpha 主题"

    empty = client.get("/api/kb/search", params={"q": "   "}).json()
    assert empty["hits"] == []

    removed = client.delete(f"/api/kb/{doc_id}").json()
    assert removed["removed"] is True
    assert client.get("/api/kb").json()["stats"]["documents"] == 0


def test_sink_missing_report(tmp_path):
    client, store = build(tmp_path)
    run = store.create("没有报告")
    run.write_json(
        "run.json",
        {"run_id": run.id, "topic": "没有报告", "status": "failed", "sources": [], "notes": []},
    )
    res = client.post("/api/kb/sink", json={"run_id": run.id})
    assert res.status_code == 409


def test_kb_document_detail(tmp_path):
    client, store = build(tmp_path)
    rid = seed(store)
    sink = client.post("/api/kb/sink", json={"run_id": rid}).json()

    detail = client.get(f"/api/kb/{sink['doc_id']}").json()
    assert detail["document"]["title"] == "Alpha 主题"
    assert len(detail["chunks"]) == sink["chunks"]
    assert "alpha" in detail["chunks"][0]["text"].lower()
    assert detail["chunks"][0]["embedded"] == 1

    assert client.get("/api/kb/99999").status_code == 404


def test_upload_markdown_with_auto_tags(tmp_path):
    client, _ = build(tmp_path)
    content = "# RAG 入门\n\n检索增强生成结合向量数据库与重排。".encode()
    res = client.post(
        "/api/kb/upload",
        files={"file": ("rag-note.md", content, "text/markdown")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "note"
    assert body["chunks"] > 0
    assert "RAG" in body["tags"] or "向量数据库" in body["tags"]
    assert body["kind"] == "note"

    detail = client.get(f"/api/kb/{body['doc_id']}").json()
    assert detail["document"]["format"] == "MD"
    assert detail["document"]["origin"] == "上传"
    assert detail["document"]["category"]
    assert detail["document"]["tags"]
    assert detail["chunks"]


def test_upload_rejects_unknown_type(tmp_path):
    client, _ = build(tmp_path)
    res = client.post(
        "/api/kb/upload",
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert res.status_code == 400
