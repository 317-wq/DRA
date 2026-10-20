# MVP-0.2 · 可安装可配置（BYOK）

> 基线：PROJECT.md v1.2
> 前置版本：MVP-0.1（引擎跑通）
> 日期：2026-10-20
> 状态：**已完成**（GitHub CI 待首次 push 验证）
> 已同步至 PROJECT.md v1.2（配置优先级、provider 抽象、用户目录）

## 1. 本版范围

**包含什么**：

1. **BYOK 配置向导**：`dra init` 交互/非交互两种模式，密钥只写本机 `config.toml`（0600）。
2. **用户目录**：配置走系统配置目录、产物走系统数据目录，安装成工具后不再往仓库里写文件。
3. **自检**：`dra doctor` 依次检查配置文件、两个密钥、模型端点、真实对话、向量模型、Tavily、数据目录；
   关键项失败返回非零退出码。
4. **Provider 抽象**：任意 OpenAI 兼容端点（百炼 / DeepSeek / OpenAI / 自建），换供应商只改配置；
   `model` + `fallback_models` 组成降级链，某个模型耗尽时自动换下一个。
5. **分发基础**：`uv build` 产出 wheel、`uv tool install` 后 `dra` 可直接用；三平台 CI 跑 lint + 单测。

**不包含什么**：

- 本地 UI / SSE（→ 0.3）
- PyPI 发布与 GitHub Release 安装包（→ 1.0）
- 系统 keyring、加密存储（远期）
- 非 Tavily 的检索供应商（0.3 之后再评估）

## 2. 本版用户路径

> `uv tool install git+https://github.com/<仓库>/dra.git`
> → `dra init`（选预设 → 填 Key → 确认写入）
> → `dra doctor`（8 项检查全绿）
> → `dra run "2026 年主流 Agent 记忆机制方案对比"`
> → 产物落在 `~/.local/share/dra/runs/<run_id>/`

## 3. 相对基线的增量

- 架构 / 模块改动：新增 `onboarding.py`（init/doctor 逻辑，与 CLI 解耦，0.3 的本地 UI 可复用）。
- 技术选型改动：配置源改为 `config.toml` + `platformdirs`；模型服务从"百炼专用字段"改为
  "OpenAI 兼容端点 + 模型降级链"。
- 接口改动：
  - `Settings` 字段改名：`base_url` / `api_key` / `model` / `embedding_model` / `fallback_models`
  - 新增 `config_path()` / `data_dir()` / `write_toml_config()` / `model_chain()`
  - 新增命令：`dra init`、`dra doctor`
  - 环境变量统一 `DRA_*`，保留 `QWEN_*` / `TAVILY_*` 作为兼容别名
- 其他：版本号 0.2.0。

## 4. 本版要实现的模块功能

- [x] **配置层**：TOML 配置源接入 pydantic-settings；优先级 参数 > 环境变量 > config.toml > .env
- [x] **路径**：`platformdirs` 的配置目录与数据目录；`DRA_CONFIG_FILE` / `DRA_DATA_DIR` / `DRA_RUNS_DIR` 可覆盖
- [x] **`dra init`**：预设选择、交互填写、脱敏确认、0600 写入、非交互参数
- [x] **`dra doctor`**：8 项检查、脱敏摘要、关键项失败非零退出
- [x] **Provider 抽象**：`base_url` 可换；qwen 才启用 thinking 开关；模型降级链
- [x] **首次运行引导**：缺 Key 时提示 `dra init`，不再抛 SDK 原始异常
- [x] **打包**：`uv build` 通过、wheel 含全部模块、`uv tool install` 后命令可用
- [x] **CI**：`.github/workflows/ci.yml`，ubuntu / windows / macOS 矩阵

## 5. 本版验收标准

- [x] 未配置时 `dra run` 输出可执行提示（`dra init`），退出干净
- [x] `dra init` 生成的配置权限 0600，密钥不出现在任何日志里
- [x] `dra doctor` 8 项全绿；删掉 Key 后对应项变红且退出码非 0
- [x] 配置优先级符合预期（单测覆盖 env > toml、kwargs > env）
- [x] 产物写入数据目录，仓库 `runs/` 不再新增
- [x] 只改 `base_url` / `model` / `api_key` 即可切换到其他 OpenAI 兼容供应商
- [x] `uv tool install` 后在仓库外目录可执行 `dra version` / `dra list`
- [ ] GitHub CI 三平台首次运行通过（需 push 后确认）

## 6. 本版风险与待办

- **密钥明文**：`config.toml` 权限 0600，但仍是明文；Windows 上无 chmod 语义，后续可接系统 keyring。
- **供应商差异**：非 Qwen 端点不支持 `enable_thinking`，代码已按模型名前缀跳过；embedding 能力也因供应商而异，
  `doctor` 里向量检查是"警告级"。
- **CI 未实跑**：workflow 已写好，但仓库还没有 remote；首次 push 后要确认 Windows runner 的中文输出与权限测试。
- **PATH**：`uv tool install` 会提示 `~/.local/bin` 不在 PATH，README 需要写清楚。

## 7. 实测记录（2026-10-20）

| 项 | 结果 |
|---|---|
| `dra init --yes` | 写入 0600，密钥脱敏显示 |
| `dra init` 交互模式（pty） | 6 个提示 → 脱敏确认 → 0600 写入，退出码 0 |
| `dra doctor` | 8/8 通过；对话 822ms / 16 tok；embedding 1024 维；Tavily 1 条 |
| 配置驱动 `dra run` | 73.0s、3 篇来源，产物写入 `/tmp/dra-home/runs`，仓库 `runs/` 未新增 |
| 首次运行（缺 Key） | 输出 `dra init` 引导，无堆栈 |
| `uv build` | 产出 `dra-0.2.0-py3-none-any.whl`，含 18 个模块 |
| `uv tool install` | `~/.local/bin/dra version` → `dra 0.2.0`，`dra list` 能读到配置目录 |
