"""模型适配层：屏蔽供应商差异（reasoning 字段、JSON 模式、思考开关、模型降级）。

对外只暴露 chat() 与 embed()，业务代码不直接接触 SDK。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from dra.config import Settings
from dra.models import ChatUsage


class LLMError(RuntimeError):
    """模型调用失败的统一异常。"""


class ModelUnavailable(LLMError):
    """当前模型不可用（额度耗尽 / 不存在 / 被限流），应切换到下一个模型。"""


@dataclass
class ChatResult:
    text: str
    model: str
    usage: ChatUsage
    latency_ms: int
    finish_reason: str = "stop"
    reasoning: str | None = None
    raw: Any = field(default=None, repr=False)


def is_model_level_error(exc: Exception) -> bool:
    """判断异常是否意味着"这个模型不能用了"，从而触发降级。"""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in (403, 404, 429):
        return True
    text = str(exc).lower()
    markers = (
        "model not found",
        "invalid model",
        "model does not exist",
        "unsupported model",
        "quota",
        "exceeded",
        "arrearage",
    )
    return any(marker in text for marker in markers)


class ModelAdapter:
    """统一入口：chat() 与 embed()。"""

    def __init__(self, settings: Settings) -> None:
        """保存配置并创建 OpenAI 兼容客户端。"""
        self.settings = settings
        self._client = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            timeout=settings.request_timeout_s,
            max_retries=0,
        )

    # ------------------------------------------------------------------ chat
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 3000,
        json_mode: bool = False,
        retries: int = 1,
    ) -> ChatResult:
        """调用对话模型；主模型不可用时按 .env 里的模型顺序逐个降级。"""
        primary = model or self.settings.plan_model
        tried: list[str] = []
        errors: list[str] = []
        for candidate in self.settings.model_chain(primary):
            tried.append(candidate)
            try:
                return self._chat_once(
                    candidate,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    retries=retries,
                )
            except ModelUnavailable as exc:
                errors.append(f"{candidate}: {exc}")
                continue
        raise LLMError("所有候选模型均不可用：" + " | ".join(errors or tried))

    def _chat_once(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        retries: int,
    ) -> ChatResult:
        """对单个模型发起调用，处理兼容性降级与网络重试。"""
        last_err: Exception | None = None
        use_json = json_mode
        use_no_think = self.settings.disable_thinking and model.lower().startswith("qwen")

        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if use_json:
                    kwargs["response_format"] = {"type": "json_object"}
                if use_no_think:
                    kwargs["extra_body"] = {"enable_thinking": False}
                resp = self._client.chat.completions.create(**kwargs)
            except BadRequestError as exc:
                # 兼容性降级：先去掉思考开关，再去掉 JSON 模式，各重试一次
                if use_no_think:
                    use_no_think = False
                    last_err = exc
                    continue
                if use_json:
                    use_json = False
                    last_err = exc
                    continue
                if is_model_level_error(exc):
                    raise ModelUnavailable(str(exc)[:200]) from exc
                raise LLMError(f"{model} 请求被拒绝：{exc}") from exc
            except (RateLimitError, APITimeoutError) as exc:
                if is_model_level_error(exc):
                    raise ModelUnavailable(str(exc)[:200]) from exc
                last_err = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            except APIStatusError as exc:
                if is_model_level_error(exc):
                    raise ModelUnavailable(f"HTTP {exc.status_code}") from exc
                last_err = exc
                if exc.status_code >= 500:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise LLMError(f"{model} HTTP {exc.status_code}：{exc}") from exc
            except Exception as exc:  # 网络层异常，换模型不如重试
                last_err = exc
                time.sleep(0.8 * (attempt + 1))
                continue

            latency = int((time.monotonic() - started) * 1000)
            choice = resp.choices[0]
            msg = choice.message
            text = msg.content or ""
            reasoning = None
            if msg.model_extra:
                reasoning = msg.model_extra.get("reasoning_content")
            usage = ChatUsage(
                prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(resp.usage, "total_tokens", 0) or 0,
                calls=1,
            )
            details = getattr(resp.usage, "completion_tokens_details", None)
            if details is not None:
                usage.reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

            if choice.finish_reason == "length" and not text.strip():
                raise LLMError(
                    f"{model} 输出被 max_tokens={max_tokens} 截断且正文为空"
                    "（reasoning 模型会先消耗思考 token，请调大 max_tokens）"
                )
            return ChatResult(
                text=text,
                model=model,
                usage=usage,
                latency_ms=latency,
                finish_reason=choice.finish_reason or "stop",
                reasoning=reasoning,
                raw=resp,
            )

        raise LLMError(f"{model} 调用失败（已重试 {retries} 次）：{last_err}")

    # ----------------------------------------------------------------- embed
    def embed(self, texts: list[str]) -> list[list[float]]:
        """向量化；0.1 未用到，0.3 知识库会用到。"""
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), 10):
            batch = texts[i : i + 10]
            resp = self._client.embeddings.create(
                model=self.settings.qwen_embedding_model,
                input=batch,
            )
            out.extend([d.embedding for d in resp.data])
        return out
