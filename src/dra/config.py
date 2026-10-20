"""配置：优先级 初始化参数 > DRA_* 环境变量 > 用户 config.toml > 旧变量/.env > 默认值。

面向"本地工具"的设计：
- 用户配置放系统配置目录（Linux `~/.config/dra`，Windows `%APPDATA%\\dra`）；
- 运行数据放系统数据目录（Linux `~/.local/share/dra`，Windows `%LOCALAPPDATA%\\dra`）；
- 仓库里的 .env 只作为开发时的便捷覆盖，不要求使用者创建。
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from platformdirs import user_config_dir, user_data_dir
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

APP_NAME = "dra"

# 开发者的仓库根目录；安装成工具后这里是 site-packages 的上级，通常没有 .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 旧变量 → 字段名；优先级低于 config.toml，仅用于兼容已有 .env
LEGACY_ENV = {
    "QWEN_BASE_URL": "base_url",
    "QWEN_API_KEY": "api_key",
    "QWEN_CHAT_MODEL_1": "model",
    "QWEN_EMBEDDING_MODEL": "embedding_model",
    "TAVILY_API_KEY": "tavily_api_key",
    "TAVILY_BASE_URL": "tavily_base_url",
}


def config_path() -> Path:
    """用户配置文件路径，可用 DRA_CONFIG_FILE 覆盖（测试与多环境常用）。"""
    override = os.environ.get("DRA_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True)) / "config.toml"


def data_dir() -> Path:
    """用户数据目录，可用 DRA_DATA_DIR 覆盖。"""
    override = os.environ.get("DRA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(APP_NAME, appauthor=False))


def default_runs_dir() -> Path:
    """默认产物目录：用户数据目录下的 runs。"""
    return data_dir() / "runs"


def load_toml(path: Path | None = None) -> dict[str, Any]:
    """读取用户配置文件；不存在或格式错误时返回空字典。"""
    target = path or config_path()
    if not target.exists():
        return {}
    try:
        with target.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _toml_value(value: Any) -> str:
    """把 Python 标量/列表渲染成 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class ConfigWriteError(RuntimeError):
    """配置目录不可写。把系统错误翻译成用户能执行的下一步。"""


def write_toml_config(values: dict[str, Any], path: Path | None = None) -> Path:
    """写入用户配置（权限 0600），只写传入的键。"""
    target = path or config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigWriteError(
            f"无法创建配置目录：{target.parent}\\n"
            f"  系统错误：{exc}\\n"
            "  常见原因：权限限制、杀毒/EDR 拦截，或当前终端被限制为只能写工作区。\\n"
            "  解决办法：把配置与数据放到可写目录后重试，例如在项目目录内：\\n"
            '    $env:DRA_CONFIG_FILE = "$PWD\\\\config.toml"\\n'
            '    $env:DRA_DATA_DIR    = "$PWD\\\\data"\\n'
            "    dra init"
        ) from exc
    lines = [
        "# Deep Research Agent 配置",
        "# 由 `dra init` 生成，可直接编辑；密钥以明文保存在本机，请勿提交到仓库。",
        "",
    ]
    for key, value in values.items():
        if value is None or value == "":
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    text = "\n".join(lines) + "\n"
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def mask_secret(value: str) -> str:
    """只露头尾，用于日志与 doctor 输出。"""
    if not value:
        return "(未配置)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-4:]}"


class TomlConfigSource(PydanticBaseSettingsSource):
    """把 config.toml 接入 pydantic-settings，优先级低于环境变量。"""

    def __init__(self, settings_cls: type[BaseSettings], data: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._data = data

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        value = self._data.get(field_name)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v not in (None, "")}


class LegacyEnvSource(PydanticBaseSettingsSource):
    """兼容旧 .env：QWEN_* / TAVILY_* 映射到字段名，优先级低于 config.toml。"""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        data: dict[str, str] = {}
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            data.update({k: v for k, v in dotenv_values(env_file).items() if v})
        data.update(os.environ)
        return {
            field: data[name]
            for name, field in LEGACY_ENV.items()
            if data.get(name)
        }


class Settings(BaseSettings):
    """全部运行参数。字段名同时是 config.toml 的键名。"""

    model_config = SettingsConfigDict(
        env_prefix="DRA_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ===== 模型服务（任意 OpenAI 兼容端点）=====
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen3.8-max"
    plan_model: str = ""
    analyze_model: str = ""
    write_model: str = ""
    embedding_model: str = "qwen3.7-text-embedding-flash"
    # 主模型不可用时按顺序降级（config.toml 里可写成数组）
    fallback_models: list[str] = Field(default_factory=list)
    # 仅对 Qwen 系列生效：关闭思考模式，显著降时延
    disable_thinking: bool = True

    # ===== 检索服务 =====
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"

    # ===== 运行参数 =====
    runs_dir: Path = Field(default_factory=default_runs_dir)
    request_timeout_s: float = 180.0
    kb_path: Path = Field(default_factory=lambda: data_dir() / "kb.sqlite3")
    # 报告里数字的可核验率低于该值时，触发一次“带证据的重写”
    min_number_support: float = 0.75

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """init > DRA_* 环境变量 > config.toml > 旧变量/.env > secrets。"""
        return (
            init_settings,
            env_settings,
            TomlConfigSource(settings_cls, load_toml()),
            LegacyEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    # 三个角色默认都走 model；单独指定时才覆盖。
    @property
    def plan_model_resolved(self) -> str:
        """拆题使用的模型。"""
        return self.plan_model or self.model

    @property
    def analyze_model_resolved(self) -> str:
        """分析使用的模型。"""
        return self.analyze_model or self.model

    @property
    def write_model_resolved(self) -> str:
        """写作使用的模型。"""
        return self.write_model or self.model

    @property
    def all_models(self) -> list[str]:
        """主模型 + 降级链，去重且保持顺序。"""
        ordered = [self.model, *self.fallback_models]
        seen: list[str] = []
        for item in ordered:
            if item and item not in seen:
                seen.append(item)
        return seen

    def model_chain(self, primary: str) -> list[str]:
        """主模型优先，其余按配置顺序补齐。"""
        return [primary] + [m for m in self.all_models if m != primary]

    def public_summary(self) -> dict[str, str]:
        """doctor / 日志用的脱敏摘要。"""
        return {
            "config": str(config_path()),
            "base_url": self.base_url,
            "api_key": mask_secret(self.api_key),
            "model": self.model,
            "fallback": ", ".join(self.fallback_models) or "(无)",
            "embedding": self.embedding_model,
            "tavily": mask_secret(self.tavily_api_key),
            "runs_dir": str(self.runs_dir),
        }

    def require_llm(self) -> None:
        """缺少模型密钥时给出可执行的下一步。"""
        if not self.api_key:
            raise SystemExit(
                "尚未配置模型 API Key。\n"
                "  先运行：dra init\n"
                "  或设置环境变量：DRA_API_KEY=<key>"
            )

    def require_search(self) -> None:
        """缺少检索密钥时给出可执行的下一步。"""
        if not self.tavily_api_key:
            raise SystemExit(
                "尚未配置检索 API Key（Tavily）。\n"
                "  先运行：dra init\n"
                "  或设置环境变量：DRA_TAVILY_API_KEY=<key>"
            )
