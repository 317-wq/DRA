"""Run 产物存储：原子写入、分页列表、删除。

文件布局保持 `runs/<run_id>/...`，方便直接看、直接备份；
所有 JSON/文本写入先落临时文件再 `os.replace`，避免读到半截内容。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
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
        """原子写文本产物（临时文件 + os.replace）。"""
        p = self.path(name)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return p

    def write_json(self, name: str, data: Any) -> Path:
        """原子写 JSON 产物。"""
        return self.write_text(name, json.dumps(data, ensure_ascii=False, indent=2))

    def save_source(self, n: int, content: str) -> Path:
        """保存单篇来源正文快照。"""
        return self.write_text(f"sources/{n:03d}.md", content)

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

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict]:
        """列出 run 元数据（新的在前），支持分页与状态过滤。"""
        items: list[dict] = []
        for meta in sorted(self.root.glob("*/run.json"), reverse=True):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            data["run_id"] = meta.parent.name
            if status and data.get("status") != status:
                continue
            items.append(data)
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return items

    def count(self, status: str | None = None) -> int:
        """统计 run 数量。"""
        return len(self.list(status=status))

    def delete(self, run_id: str) -> bool:
        """删除整个 run 目录；不存在返回 False。"""
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            return False
        shutil.rmtree(run_dir)
        return True
