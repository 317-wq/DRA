"""首次配置与自检：`dra init` 与 `dra doctor` 背后的逻辑。

放在这里而不是 cli.py，是为了让"写配置"和"检查连通性"可以单独测试，
也方便以后做本地 UI 时复用同一套配置模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from dra.config import Settings, config_path, write_toml_config
from dra.llm import ModelAdapter
from dra.tools.search import TavilySearch

# 常见 OpenAI 兼容端点。只是填默认值，用户仍可改。
PRESETS: dict[str, dict[str, str]] = {
    "dashscope": {
        "label": "阿里云百炼 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-max",
        "embedding_model": "qwen3.7-text-embedding-flash",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "embedding_model": "",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "embedding_model": "text-embedding-3-small",
    },
    "custom": {
        "label": "自定义（任意 OpenAI 兼容端点）",
        "base_url": "",
        "model": "",
        "embedding_model": "",
    },
}


@dataclass
class Check:
    """一条自检结果；critical=False 表示失败只提示、不阻断。"""

    name: str
    ok: bool
    detail: str
    critical: bool = True


def save_config(
    *,
    base_url: str,
    api_key: str,
    model: str,
    tavily_api_key: str,
    embedding_model: str = "",
    runs_dir: str = "",
    path: Path | None = None,
) -> Path:
    """写入用户配置；密钥以 0600 权限保存。"""
    values: dict[str, object] = {
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "tavily_api_key": tavily_api_key.strip(),
    }
    if embedding_model.strip():
        values["embedding_model"] = embedding_model.strip()
    if runs_dir.strip():
        values["runs_dir"] = runs_dir.strip()
    return write_toml_config(values, path)


def check_models_endpoint(settings: Settings, timeout: float = 10.0) -> Check:
    """检查 /models 是否可达；部分代理不实现该端点，失败不算致命。"""
    url = settings.base_url.rstrip("/") + "/models"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return Check("模型端点", False, f"{type(exc).__name__}：{url}", critical=False)
    if resp.status_code == 200:
        return Check("模型端点", True, f"HTTP 200 · {url}", critical=False)
    if resp.status_code in (401, 403):
        return Check("模型端点", False, f"HTTP {resp.status_code}：API Key 无效或无权限", critical=False)
    return Check("模型端点", False, f"HTTP {resp.status_code}（可继续，以对话检查为准）", critical=False)


def check_chat(settings: Settings) -> Check:
    """真实发一条最小对话，验证 Key、模型名与网络。"""
    adapter = ModelAdapter(settings.model_copy(update={"request_timeout_s": 30.0}))
    try:
        res = adapter.chat(
            [{"role": "user", "content": "只回复 OK"}],
            model=settings.plan_model_resolved,
            max_tokens=16,
            temperature=0.0,
        )
    except Exception as exc:  # 自检不能把异常抛给用户
        return Check("对话调用", False, f"{type(exc).__name__}：{str(exc)[:120]}")
    return Check(
        "对话调用",
        bool(res.text.strip()),
        f"{res.model} · {res.latency_ms}ms · {res.usage.total_tokens} tok",
    )


def check_embedding(settings: Settings) -> Check:
    """检查向量模型；0.2 尚未使用，失败只提示。"""
    if not settings.embedding_model:
        return Check("向量模型", True, "未配置（0.2 不需要）", critical=False)
    adapter = ModelAdapter(settings.model_copy(update={"request_timeout_s": 30.0}))
    try:
        vectors = adapter.embed(["ping"])
    except Exception as exc:
        return Check("向量模型", False, f"{type(exc).__name__}：{str(exc)[:100]}", critical=False)
    dims = len(vectors[0]) if vectors else 0
    return Check("向量模型", dims > 0, f"{settings.embedding_model} · {dims} 维", critical=False)


def check_search(settings: Settings) -> Check:
    """发一次最小搜索，验证 Tavily Key（消耗 1 次额度）。"""
    try:
        hits = TavilySearch(settings.model_copy(update={"request_timeout_s": 20.0})).search(
            "ping", max_results=1
        )
    except Exception as exc:
        return Check("检索服务", False, f"{type(exc).__name__}：{str(exc)[:120]}")
    return Check("检索服务", bool(hits), f"Tavily 返回 {len(hits)} 条")


def check_runs_dir(settings: Settings) -> Check:
    """确认产物目录可写。"""
    target = Path(settings.runs_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("数据目录", False, f"不可写：{target}（{exc}）")
    return Check("数据目录", True, str(target))


def collect_checks(settings: Settings) -> list[Check]:
    """按"先本地、后网络"的顺序执行全部自检。"""
    checks = [
        Check(
            "配置文件",
            config_path().exists(),
            str(config_path()) if config_path().exists() else f"不存在：{config_path()}（运行 dra init）",
            critical=False,
        ),
        Check(
            "模型密钥",
            bool(settings.api_key),
            "已配置" if settings.api_key else "未配置（运行 dra init）",
        ),
        Check(
            "检索密钥",
            bool(settings.tavily_api_key),
            "已配置" if settings.tavily_api_key else "未配置（运行 dra init）",
        ),
        check_runs_dir(settings),
    ]
    if settings.api_key:
        checks.append(check_models_endpoint(settings))
        checks.append(check_chat(settings))
        checks.append(check_embedding(settings))
    if settings.tavily_api_key:
        checks.append(check_search(settings))
    return checks


def failed_critical(checks: list[Check]) -> list[Check]:
    """返回真正阻断使用的失败项。"""
    return [c for c in checks if not c.ok and c.critical]
