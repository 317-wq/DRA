"""服务商预设、模型发现与连通性测试（全部 OpenAI 兼容端点）。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx


class ProviderError(RuntimeError):
    """服务商请求失败，消息可直接展示给用户。"""


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    models: tuple[str, ...]
    note: str = ""
    supports_models: bool = True


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "dashscope",
        "通义千问 / DashScope",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen3.8-max", "qwen-plus", "qwen-turbo"),
        "国内直连，支持 OpenAI 兼容模式",
    ),
    Provider(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        ("gpt-4.1", "gpt-4.1-mini", "o4-mini"),
        "需要能访问 api.openai.com",
    ),
    Provider(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        ("deepseek-chat", "deepseek-reasoner"),
        "价格较低，推理任务可用 reasoner",
    ),
    Provider(
        "moonshot",
        "Kimi / Moonshot",
        "https://api.moonshot.cn/v1",
        ("kimi-k2-0905-preview", "moonshot-v1-8k"),
        "长上下文见长",
    ),
    Provider(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        ("glm-4.6", "glm-4.5"),
        "国内直连",
    ),
    Provider(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        ("openai/gpt-4.1", "anthropic/claude-sonnet-4", "google/gemini-2.5-pro"),
        "一个 Key 访问多家模型，也支持 Anthropic",
    ),
    Provider(
        "siliconflow",
        "SiliconFlow",
        "https://api.siliconflow.cn/v1",
        ("Qwen/Qwen3-235B-A22B", "deepseek-ai/DeepSeek-V3"),
        "国内聚合平台",
    ),
    Provider(
        "ollama",
        "Ollama（本地）",
        "http://localhost:11434/v1",
        ("llama3.1", "qwen3:8b"),
        "本地模型，Key 可留空",
        supports_models=True,
    ),
    Provider(
        "custom",
        "自定义 OpenAI 兼容端点",
        "",
        (),
        "任何兼容 /chat/completions 的服务",
        supports_models=True,
    ),
)


def provider_list() -> list[dict]:
    """给前端的服务商预设。"""
    return [asdict(item) for item in PROVIDERS]


def _base(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ProviderError("Base URL 不能为空")
    if not base.startswith(("http://", "https://")):
        raise ProviderError("Base URL 必须以 http:// 或 https:// 开头")
    return base


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def fetch_models(base_url: str, api_key: str | None = None, timeout: float = 15.0) -> list[str]:
    """请求 /models 并返回模型 id；失败抛 ProviderError。"""
    url = _base(base_url) + "/models"
    try:
        resp = httpx.get(url, headers=_headers(api_key), timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(f"连接失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError("返回不是 JSON，可能不是 OpenAI 兼容端点") from exc
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("models")
    if not isinstance(items, list):
        raise ProviderError("没有从返回里找到模型列表")
    models: list[str] = []
    for item in items:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if model_id:
                models.append(str(model_id))
        elif isinstance(item, str):
            models.append(item)
    if not models:
        raise ProviderError("模型列表为空")
    return sorted(dict.fromkeys(models))


def test_chat(
    base_url: str,
    api_key: str | None,
    model: str,
    timeout: float = 30.0,
) -> dict:
    """发一条最小 chat 请求，验证 Key、URL、模型是否可用。"""
    if not model.strip():
        raise ProviderError("请先选择或填写模型")
    url = _base(base_url) + "/chat/completions"
    payload = {
        "model": model.strip(),
        "messages": [{"role": "user", "content": "只回复 OK 两个字母"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    started = time.monotonic()
    try:
        resp = httpx.post(url, headers=_headers(api_key), json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(f"连接失败：{exc}") from exc
    latency = round((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}：{resp.text[:300]}")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("返回格式不是 OpenAI chat/completions") from exc
    return {
        "ok": True,
        "model": data.get("model") or model,
        "latency_ms": latency,
        "reply": str(content or "").strip()[:80],
    }
