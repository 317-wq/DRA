# Deep Research Agent

输入一个研究主题，Agent 自己拆题、检索、读正文、判断证据够不够、补检索，最后写一份**每个结论都能溯源到 URL** 的 Markdown 报告。

它是一个**本地工具**：数据在你自己电脑上，不需要注册登录，也不需要有人维护服务器。你只需要准备两个 API Key（模型 + 搜索）。

当前版本：**1.9.0**。
- 目标架构与接口：`MVP/PROJECT.md`（v1.9）
- 本版范围与验收：`MVP/MVP-1.0.md`
- 后续路线：`MVP/ROADMAP.md`
- 发布流程：`RELEASE.md`

## 安装（使用者）

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。uv 会顺手装好 Python，不用单独配环境。

### 方式 A：安装成命令（推荐）

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install dra-research   # 从 PyPI 安装

dra init                     # 选服务商、填两个 Key
dra doctor                   # 检查 Key、端点、检索是否真的能用
dra run "2026 年主流 Agent 记忆机制方案对比"
```

Windows（PowerShell）：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install dra-research

dra init
dra doctor
dra run "2026 年主流 Agent 记忆机制方案对比"
```

> 如果提示 `dra` 找不到，把 `~/.local/bin`（Windows 是 `%USERPROFILE%\.local\bin`）加进 PATH，
> 或执行 `uv tool update-shell` 后重开终端。

#### Windows 便携版（不装 Python）

下载 Release 里的 `DRA-portable-win64.zip`，解压到可写目录，双击 `DRA.exe`。
首次运行会弹配置窗口填两个 Key；数据都在同目录 `data/` 里，换机整个文件夹拷走。

## 方式 B：不安装，直接跑

```bash
uvx --from dra-research dra run "2026 年主流 Agent 记忆机制方案对比"
```

### 方式 C：从 GitHub 安装（PyPI 不可用时）

```bash
uv tool install git+https://github.com/317-wq/DRA.git
```

> Windows 从 GitHub 安装需要先装 Git：`winget install --id Git.Git -e`。

### 升级与回滚

```bash
uv tool upgrade dra-research # 升级到最新
uv tool install dra-research==1.0.1 --force   # 回滚到指定版本
```

PyPI 发布状态见 <https://pypi.org/project/dra-research/>。命令仍是 `dra`。维护者流程见 `RELEASE.md`。

## 在 Windows 上跑演示

程序本身是跨平台的（CI 覆盖 `windows-latest`），Linux 只是这次的开发机。
唯一有平台差异的是演示脚本，Windows 用 PowerShell 版：

```powershell
# 仓库目录内，依次跑三个主题
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1

# 或安装后直接跑单个主题
dra run "2026 年主流 Agent 记忆机制方案对比" --max-sources 8
```

产物在 `%LOCALAPPDATA%\dra\runs\`。建议用 Windows Terminal；旧版 cmd 的代码页可能让中文排版异常。

## 从源码运行（开发者）

```bash
git clone https://github.com/317-wq/DRA.git && cd DRA
uv sync
uv run dra init
uv run dra doctor
uv run dra run "2026 年 RAG 与长上下文方案的选择边界"
```

`uv run dra ...` 与安装后的 `dra ...` 等价。

## 配置

`dra init` 会写到系统配置目录，密钥权限 0600：

| 平台 | 配置 | 运行数据 |
|---|---|---|
| Linux | `~/.config/dra/config.toml` | `~/.local/share/dra/runs/` |
| macOS | `~/Library/Application Support/dra/config.toml` | `~/Library/Application Support/dra/runs/` |
| Windows | `%APPDATA%\dra\config.toml` | `%LOCALAPPDATA%\dra\runs\` |

优先级：**命令行参数 > 环境变量 > `config.toml` > 项目 `.env` > 默认值**。
所以临时换 Key 不用改文件：

```bash
DRA_API_KEY=sk-xxx dra doctor
```

`config.toml` 示例（`dra init` 生成的就是这些键）：

```toml
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-..."
model = "qwen3.8-max"
fallback_models = ["deepseek-v4.1-flash"]
embedding_model = "qwen3.7-text-embedding-flash"
tavily_api_key = "tvly-..."
runs_dir = "/home/you/.local/share/dra/runs"
```

- **BYOK**：模型和搜索都由用户自己的 Key 付费，项目不代管额度。
- **换供应商**：只要兼容 OpenAI 接口，改 `base_url` / `model` / `api_key` 即可（DeepSeek、OpenAI、自建代理、Ollama 都行）。
- **降级链**：`fallback_models` 里的模型会在主模型不可用（额度耗尽 / 不存在 / 429）时按序接管。

### 配置目录不可写时（便携模式）

有的机器（公司策略、杀软、或在受限终端里）不允许写 `%APPDATA%`。把配置和数据指到可写目录即可：

```powershell
$env:DRA_CONFIG_FILE = "$PWD\config.toml"
$env:DRA_DATA_DIR    = "$PWD\data"
dra init
```

Linux / macOS 同理。想让设置长期生效，把这两行写进 PowerShell profile（或 shell 的 rc 文件）。
`dra doctor` 会显示它实际读到的是哪个配置路径。

## 证据质量

```bash
dra eval                 # 最近一次
dra eval --all           # 全部历史报告的基线
```

评估是**纯本地**的：引用覆盖、数字能否在引用来源中找到、来源域名与一手占比。
来源在采集时就会标 `tier`（一手 / 专业媒体 / 一般 / 自媒体）与 `quality` 分数。

当前基线（26 份，修正评估器后）：均分 68.8，区间 37–90，A×2 / B×13 / C×7 / D×4。
主要失分项仍是“数字与来源对不上”。0.8 起，写完后会自动核验数字，不达标带证据校对一次。

## 追问

```bash
dra ask "那成本呢？" --run <run_id>
```

或直接 `dra ui` → 打开一次研究 → 「追问」tab。

- 只复用父报告的证据，**默认不重新检索**，几乎不消耗搜索额度。
- 引用沿用父报告编号（[3] 在两份文档里指同一来源）。
- 证据不够时直接说“现有证据无法回答”，不会偷偷补搜或编造。

## 本地知识库

```bash
dra kb import --limit 10      # 把已有报告批量入库（只花 embedding）
dra kb list                   # 文档 / 块数 / 是否已向量化
dra kb search "向量数据库 QPS 延迟"
dra kb show 1                 # 看某份报告的分块
```

- 存储是一个本地 `kb.sqlite3`，不引向量数据库。
- 中文检索用字 bigram 的 BM25，配合向量余弦与 RRF。
- **不调用 Tavily**；没有模型 Key 时自动退化为纯关键词检索。
- 同一份报告按内容哈希去重，重复导入不重复计费。

## 本地界面

```bash
dra ui
```

只在 `127.0.0.1` 起服务并自动开浏览器。页面里能连续发起研究（排队执行）、取消运行中任务、失败后重试、删除历史、导出报告；结束后切报告 / 来源 / 轨迹 / 追问 / 质量，报告页可一键沉淀到知识库。刷新页面不会丢进度——事件流直接读 `runs/<id>/trace.jsonl`。

> 不做远程访问、不做多用户；一次只跑一个研究。

## 命令

| 命令 | 作用 |
|---|---|
| `dra init` | 生成/更新配置（`--yes` 非交互，`--force` 覆盖） |
| `dra doctor` | 8 项自检：配置、密钥、端点、对话、向量、检索、数据目录 |
| `dra run "<主题>"` | 发起研究；`--max-sources` / `--max-seconds` / `--max-tool-calls` / `--max-rounds` 控预算 |
| `dra ui` | 打开本地界面（127.0.0.1，自动开浏览器） |
| `dra kb add/import` | 把报告导入本地知识库（不消耗搜索额度） |
| `dra kb search "查询"` | 关键词 + 向量混合检索（RRF 融合） |
| `dra ask "问题" --run <id>` | 基于某次研究追问（默认不重新检索） |
| `dra eval [run_id]` | 本地评估报告证据质量；`--all` 看基线 |
| `dra delete <run_id>` | 删除一次研究及其产物 |
| `dra export <run_id>` | 导出报告；`--format html --out report.html` |
| `dra show <run_id>` | 看报告；`--trace` 看完整事件时间线 |
| `dra list` | 列出历史研究 |
| `dra version` | 版本号 |

## 一次研究发生了什么

```
主题
 ├─ Plan      拆成 3–5 个可独立检索的子问题（每个带检索式）
 ├─ Search    Tavily 并发检索（并发 4），URL 归一化去重
 ├─ Read      Tavily Extract 批量取正文；失败降级原始抓取
 ├─ Analyze   证据 → 要点 + 引用编号 + 覆盖度（enough / partial / gap）
 ├─ Replan    有 gap 且预算未尽 → 补 1–2 条检索式，再来一轮
 └─ Write     分节写报告，引用编号由程序注入并重排
```

终止条件：所有子问题 `enough`，或预算（工具调用 / 墙钟时间 / token / 轮次）用尽。
补充检索没拿到新来源会提前收敛，不再空跑一轮分析。

## 产物

```
<runs_dir>/<run_id>/
  run.json          # 元数据、覆盖度、预算、token、提示
  report.md         # 报告（含参考资料，URL 由程序生成）
  sources.json      # 实际引用的来源，n 与 [n] 对齐，含 link_status 复核结果
  subquestions.json # 拆题与每轮覆盖度
  trace.jsonl       # 事件流：可直接喂给 0.3 的本地 UI
  sources/001.md    # 每篇来源的正文快照（排查用）
  debug/            # 仅在分析输出解析失败时出现，存原始返回
```

## 实测（2026-10-20，8 核 / 3GB 单机）

| 主题 | 耗时 | 来源 | 正文 | 结果 |
|---|---|---|---|---|
| 2026 年主流 Agent 记忆机制方案对比 | 71.0s | 6 | 2002 字 | partial |
| 2026 年 RAG 与长上下文方案的选择边界 | 68.8s | 8 | 1830 字 | partial（1 个子问题 enough） |
| 2026 年中国新能源汽车换电模式的现状与争议 | 72.9s | 7 | 2596 字 | partial |
| 2026 年向量数据库的选型边界 | 79.8s | 5 | 8 节 | partial |
| MCP 协议核心概念与生态 | 73.0s | 3 | — | 配置驱动运行 |

单次 9k–12k token。`partial` 不是失败：它表示有子问题没拿到足够证据，报告里会明确写出缺口。

### 模型实测记录（决定默认值的就是这些数字）

| 配置 | 结果 |
|---|---|
| `qwen3.8-max` + 关闭 thinking | 拆题 5.7s、写 2600 字约 60s，JSON 稳定 ✅ 默认 |
| `qwen3.8-max` + 开启 thinking | 写 2600 字 171s（其中 4995 token 是推理），一次研究 245s |
| `qwen3.8-flash` | 能写对，但同样爱推理：一次分析 102s / 4628 reasoning token |
| `deepseek-v4.1-flash` | 快，但约 50% 概率把 4000+ token 花在推理上，JSON 被截断；作写作模型会把报告写一半 |
| `kimi-k3` | 不接受 `response_format`（HTTP 400） |
| `glm-5.3` / `deepseek-v4-pro-0813` | JSON 模式下 reasoning 吃掉输出额度，容易截断 |

## 设计取舍

- **不用 LangGraph**：单线程显式循环 + 预算检查就够，调试成本低。
- **引用只由程序注入**：模型写 `[n]`，程序校验编号、按首次出现重排、生成参考资料。模型编的 URL 进不了报告。
- **证据先行**：先登记来源再分析，分析阶段只能看已登记证据。
- **主动找一手来源**：性能/成本/对比类子问题会追加 `site:arxiv.org` / `site:github.com` 检索式，避免被选型榜单淹没。
- **分析失败不拖垮研究**：截断会加大额度重试一次；仍失败就降级为 gap，报告继续由证据清单驱动。
- **引用收尾复核**：写完后并发检查引用 URL，状态写进 `sources.json`；403/超时只提示，不改写报告。
- **本地优先**：配置与数据都在用户目录，未来加 UI 也只在 127.0.0.1 起服务。

## 测试

```bash
uv run pytest                          # 126 个离线单测，约 8s
DRA_LIVE=1 uv run pytest -m live       # 真实链路 smoke（会花钱）
```

CI 在 `.github/workflows/ci.yml`，覆盖 ubuntu / windows / macOS。

## 已知限制

- 中文源覆盖受 Tavily 影响；未实测是否需要补 Bing / Brave。
- 证据摘要取每篇“数字最密”的 1400 字（而不是开头），但分析阶段仍只读前 800 字；超长文更靠后的表格可能读不到。
- 报告 1800–2600 字，来自提示词里的硬约束；要更长需要分节写作。
- 密钥以明文存本机（0600），尚未接系统 keyring。
- 部分站点（YouTube / Wikipedia）抓取成功但服务器直连不可达，`link_status` 会标 `unreachable`。
- 暂无本地 UI、知识库、追问——见 ROADMAP 0.3–0.5。
