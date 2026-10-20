"""配置：统一从 .env 读取，模型按角色分配。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """所有密钥与可调参数。env 名沿用项目里已有的 .env 命名。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ===== 模型服务（阿里云百炼兼容端点）=====
    qwen_api_key: str = Field(default="", alias="QWEN_API_KEY")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL",
    )
    qwen_chat_model_1: str = Field(default="qwen3.8-max", alias="QWEN_CHAT_MODEL_1")
    qwen_chat_model_2: str = Field(default="kimi-k3", alias="QWEN_CHAT_MODEL_2")
    qwen_chat_model_3: str = Field(default="glm-5.3", alias="QWEN_CHAT_MODEL_3")
    qwen_chat_model_4: str = Field(default="deepseek-v4-flash-0731", alias="QWEN_CHAT_MODEL_4")
    qwen_chat_model_5: str = Field(default="deepseek-v4-pro-0813", alias="QWEN_CHAT_MODEL_5")
    qwen_chat_model_6: str = Field(default="qwen3.8-flash", alias="QWEN_CHAT_MODEL_6")
    qwen_chat_model_7: str = Field(default="deepseek-v4.1-flash", alias="QWEN_CHAT_MODEL_7")
    qwen_chat_model_8: str = Field(default="deepseek-v4.1-flash-1017", alias="QWEN_CHAT_MODEL_8")
    qwen_embedding_model: str = Field(
        default="qwen3.7-text-embedding-flash", alias="QWEN_EMBEDDING_MODEL"
    )

    # ===== 检索服务 =====
    tavily_base_url: str = Field(default="https://api.tavily.com", alias="TAVILY_BASE_URL")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    # ===== 角色模型（可用 DRA_*_MODEL 覆盖）=====
    planner_model: str = Field(default="", alias="DRA_PLANNER_MODEL")
    analyst_model: str = Field(default="", alias="DRA_ANALYST_MODEL")
    writer_model: str = Field(default="", alias="DRA_WRITER_MODEL")

    # ===== 运行参数 =====
    runs_dir: Path = Field(default=PROJECT_ROOT / "runs", alias="DRA_RUNS_DIR")
    request_timeout_s: float = Field(default=180.0, alias="DRA_REQUEST_TIMEOUT_S")
    disable_thinking: bool = Field(default=True, alias="DRA_DISABLE_THINKING")

    # 规划/分析要稳定 JSON 且快；写作要质量。默认值可由 .env 覆盖。
    # 三个角色默认都用 qwen3.8-max，并关闭 thinking：
    # 实测开启 thinking 时写作 2600 字要 171s（其中 4995 token 是推理），
    # 关闭后同样质量约 50–70s，JSON 任务也从 30s 降到 6s。
    @property
    def plan_model(self) -> str:
        """拆题使用的模型。"""
        return self.planner_model or self.qwen_chat_model_1

    @property
    def analyze_model(self) -> str:
        """分析使用的模型。"""
        return self.analyst_model or self.qwen_chat_model_1

    @property
    def write_model(self) -> str:
        """写作使用的模型。"""
        return self.writer_model or self.qwen_chat_model_1

    @property
    def all_models(self) -> list[str]:
        """按 .env 里的顺序列出全部候选模型（去重），用于耗尽时逐个降级。"""
        ordered = [
            self.qwen_chat_model_1,
            self.qwen_chat_model_2,
            self.qwen_chat_model_3,
            self.qwen_chat_model_4,
            self.qwen_chat_model_5,
            self.qwen_chat_model_6,
            self.qwen_chat_model_7,
            self.qwen_chat_model_8,
        ]
        seen: list[str] = []
        for model in ordered:
            if model and model not in seen:
                seen.append(model)
        return seen

    def model_chain(self, primary: str) -> list[str]:
        """主模型优先，其余按 .env 顺序补齐。"""
        return [primary] + [m for m in self.all_models if m != primary]

    def require_llm(self) -> None:
        """缺少模型密钥时给出明确提示并退出。"""
        if not self.qwen_api_key:
            raise SystemExit("缺少 QWEN_API_KEY，请检查项目根目录的 .env")

    def require_search(self) -> None:
        """缺少检索密钥时给出明确提示并退出。"""
        if not self.tavily_api_key:
            raise SystemExit("缺少 TAVILY_API_KEY，请检查项目根目录的 .env")
