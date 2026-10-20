"""Run 产物存储：runs/<run_id>/{run.json, report.md, sources.json, trace.jsonl, sources/*.md}"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Run:
    id: str
    dir: Path
    topic: str

    def path(self, *parts: str) -> Path:
        """返回 run 目录下的路径并确保父目录存在。"""
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_text(self, name: str, text: str) -> Path:
        """写文本产物。"""
        p = self.path(name)
        p.write_text(text, encoding="utf-8")
        return p

    def write_json(self, name: str, data: Any) -> Path:
        """写 JSON 产物（UTF-8、缩进 2）。"""
        p = self.path(name)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def save_source(self, n: int, content: str) -> Path:
        """保存单篇来源正文快照。"""
        p = self.path("sources", f"{n:03d}.md")
        p.write_text(content, encoding="utf-8")
        return p

    @property
    def report_path(self) -> Path:
        """报告文件路径。"""
        return self.dir / "report.md"

    @property
    def trace_path(self) -> Path:
        """事件轨迹文件路径。"""
        return self.dir / "trace.jsonl"


class RunStore:
    def __init__(self, root: Path) -> None:
        """初始化存储根目录。"""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, topic: str) -> Run:
        """新建一次 run 的目录与 id。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = f"{stamp}-{secrets.token_hex(2)}"
        run_dir = self.root / run_id
        (run_dir / "sources").mkdir(parents=True, exist_ok=True)
        return Run(id=run_id, dir=run_dir, topic=topic)

    def open(self, run_id: str) -> Run:
        """按 id 打开已有 run。"""
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            raise FileNotFoundError(f"找不到 run：{run_id}（目录 {run_dir}）")
        topic = ""
        meta = run_dir / "run.json"
        if meta.exists():
            try:
                topic = json.loads(meta.read_text(encoding="utf-8")).get("topic", "")
            except json.JSONDecodeError:
                pass
        return Run(id=run_id, dir=run_dir, topic=topic)

    def list(self) -> list[dict]:
        """列出全部 run 的元数据（新的在前）。"""
        items: list[dict] = []
        for meta in sorted(self.root.glob("*/run.json"), reverse=True):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            data["run_id"] = meta.parent.name
            items.append(data)
        return items
