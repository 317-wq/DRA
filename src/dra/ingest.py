"""从上传文件里提取纯文本：支持 Markdown / 纯文本 / PDF。"""

from __future__ import annotations

import io
import re
from pathlib import Path

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

KIND_BY_SUFFIX = {
    ".md": "note",
    ".markdown": "note",
    ".txt": "note",
    ".pdf": "pdf",
}

KIND_LABELS = {
    "report": "研究沉淀",
    "pdf": "PDF",
    "note": "笔记",
    "web": "网页收藏",
}


class IngestError(ValueError):
    """文件无法解析或类型不支持。"""


def kind_for(filename: str) -> str:
    """按扩展名判断文档类型。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in KIND_BY_SUFFIX:
        raise IngestError("只支持 .md / .txt / .pdf 文件")
    return KIND_BY_SUFFIX[suffix]


def extract_text(filename: str, data: bytes) -> str:
    """从文件字节里提取正文；解析失败抛 IngestError。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError("文件超过 20 MB 上限")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
            raise IngestError("缺少 PDF 解析依赖，请先安装 pypdf") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型很杂
            raise IngestError(f"PDF 解析失败：{exc}") from exc
        text = "\n\n".join(pages)
    elif suffix in (".md", ".markdown", ".txt"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="ignore")
    else:
        raise IngestError("只支持 .md / .txt / .pdf 文件")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise IngestError("文件没有可提取的文本（扫描版 PDF 需要先 OCR）")
    return text
