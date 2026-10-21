# PROJECT · 深度研究代理（Deep Research Agent）

> 基线版本：v1.14
> 最后更新：2026-10-20
> 说明：本文档只放跨版本稳定的内容（目标架构、技术原则、稳定接口）。
> 版本范围、依赖与验收见对应的 `MVP-x.y.md`。

## 0. 文档约定

- 本文档是"当前真相"，描述**完整目标架构**，不等于当前已实现的功能。
- 结构性变更（架构、技术原则、稳定接口）才改这里并升版本号。
- 各 `MVP-x.y.md` 只写"相对基线/上一版的增量"，相同内容引用本文档，不重复粘贴。
- 目标架构图：`diagram/深度研究代理系统架构图.png`（含远期模块）。
- 交互原型：`docs/prototype.html`（只作视觉与交互参考，不是实现）。
- 变更记录写在文末第 6 节。

## 1. 项目概述

- 一句话描述：输入一个研究主题，Agent 自主拆解子问题、多源检索与阅读、核对证据，输出一份**每段结论都能溯源到具体 URL** 的 Markdown 研究报告，且全过程可回放。
- 解决什么问题：把"查资料 → 读 → 比对 → 写"这条链路自动化。人只负责定题、看过程、判断结论可信度，不负责搬运和摘抄。
- 目标用户 / 使用场景：需要快速形成技术选型、方案对比、行业现状类调研结论的开发者 / 研究员 / 产品经理。单次研究 3–15 分钟，产出 2000–5000 字。
- 与"RAG 问答"的区别：多轮自主检索与重新规划、引用可核验（到 URL 级）、过程与预算全程可见可控。

## 2. 总体架构

- 架构图：见 `diagram/深度研究代理系统架构图.png`（完整目标架构，含远期模块）。
- 模块清单：

| 模块 | 职责 |
|---|---|
| 前端应用（Web UI） | 发起研究，查看报告 / 来源 / 执行轨迹 / 历史 / 知识库 / 设置 |
| API 层（HTTP + SSE） | 前后端边界，把研究事件流实时推给前端 |
| 任务管理器 | 任务创建、状态机、调度、结果汇总 |
| Agent 执行循环 | 规划 → 工具调用 → 结果分析 → 计划调整，按"证据质量 + 执行预算"决定继续或收敛 |
| 证据评估 | 可信度评分、信息完整性、跨来源交叉验证（远期） |
| 记忆管理 | 短期（本次运行）、任务上下文（同一研究线的追问）、长期（知识库，远期） |
| 策略控制 | 工具权限、风险识别（危险 URL / 敏感操作）、异常处理 |
| 工具执行层 | 网页搜索、网页读取、文件读写、数据处理、命令执行、扩展工具 |
| 外部服务适配 | LLM API、搜索引擎、网页提取、第三方数据源 |
| 数据存储 | 任务数据、执行记录、报告、元数据、向量索引 |
| 消息与事件系统 | 任务事件、通知、状态更新 |
| 监控与可观测 | 日志、指标、链路追踪 |
| 调度器 | 定时任务、资源限制、并发控制（远期） |
| 基础设施 | Linux / Docker / 负载均衡 / 集群 / 网络安全 |
| 安全与合规 | 认证授权、加密、敏感信息保护、审计、合规 |

- 关键数据流（主干）：
  主题 → 任务管理器创建 Run → Agent 循环（Plan → Search/Read → Analyze → Replan）→ 证据库累积
  → 报告合成（引用由程序注入）→ Run 产物落盘 + 事件流推送 → Web UI 展示 / 沉淀知识库

> 完整架构图包含远期模块，各版本只实现当前需要的部分。

## 3. 技术选型（核心骨架）

> 只写长期技术原则与类别，具体包、版本、配置放 MVP 文档。

| 类别 | 基线选型 | 一句话理由 |
|---|---|---|
| 开发语言 | Python 3.12 | Agent 逻辑与生态最省事 |
| 环境与依赖 | uv | 锁定依赖、跑得快，`uv run` 即入口 |
| CLI 交互 | Typer + Rich | 0.1 阶段的验证入口，Rich 做实时进度 |
| Web / API | FastAPI + SSE（uvicorn） | 研究过程天然是事件流，SSE 比 WebSocket 简单；只绑 127.0.0.1 |
| 前端 | 原生 HTML/CSS/JS 单页 | 0.2 复用原型视觉，先不上构建链 |
| 模型调用 | openai SDK + 兼容端点，统一 ModelAdapter | 换模型 / 换供应商不动业务代码；对 Qwen 默认关闭 thinking 以控时延 |
| Agent 核心 | 手写 Agent Loop（显式状态 + 预算） | 研究流程的可控性优先于框架抽象 |
| 复杂编排 | LangGraph（条件性使用） | 仅当多分支、可恢复、人机中断成为瓶颈时引入 |
| 检索 | Tavily Search + Extract | 一步拿到清洗后的正文，省掉自建爬虫 |
| 存储 | 文件系统 `runs/<run_id>/` + SQLite | 产物可读、可 diff；元数据可查询 |
| 向量检索 | 百炼 Embedding（1024 维）+ SQLite BLOB + Python 余弦 | 规模小，不引向量数据库；中文 BM25 用字 bigram |
| 配置管理 | `config.toml`（系统配置目录）+ 环境变量 + 开发用 `.env` | BYOK；优先级 参数 > 环境 > config.toml > .env |
| 运行数据 | 系统数据目录（platformdirs） | 安装成工具后不污染仓库；可按用户覆盖 |
| 测试 | pytest（单测 + 1 条真实链路 smoke） | 关键路径可回归 |
| 执行追踪 | JSONL 事件（`trace.jsonl`） | 直接喂给"执行轨迹"页与后续评测 |
| 代码规范 | Ruff | 检查与格式统一 |
| 分发 | PyPI + GitHub Release（tag 触发 CI） | 一条命令安装；产物可复现 |

> 具体版本、SDK、包选择、配置项见对应 `MVP-x.y.md`。
> 选型变更属于结构性变更，改这里并升版本号，并在对应版本文档记一笔。

## 4. 长期接口约定

> 只定边界，具体字段与参数放 MVP。字段名一旦写进这里，就按稳定接口对待。

- CLI 入口：`dra init` 配置、`dra doctor` 自检、`dra run "<主题>"` 发起研究、`dra show <run_id>` 查看报告与轨迹、`dra list` 列出历史 Run、`dra ui` 打开本地界面。
- 本地界面：只监听 `127.0.0.1`，自动开浏览器；不提供远程访问、不做多用户。
- HTTP 接口：`GET /`、`GET/POST /api/runs`、`GET /api/runs/{id}`、`/report`、`/sources`、`/trace`、`/events`（SSE）。
- 任务模型：本地单用户、单 worker 串行；连续发起进入持久队列（`runs/.jobs.sqlite3`），
  重启后 queued 自动重新入队、running 标 interrupted；运行中返回 409 仅用于严格互斥调用。
- Run 状态：`queued / running / done / partial / failed / cancelled / interrupted`；重启时遗留的 queued/running 标记为 interrupted。
- 管理接口：`GET /healthz`、分页 `GET /api/runs`、`DELETE /api/runs/{id}`、`/export`、`/cancel`、`/retry`、`GET/PUT /api/settings`。
- 知识库：`dra kb add|import|list|search|show|remove`；存储为单个 `kb.sqlite3`（默认 `data_dir/kb.sqlite3`，可用 `DRA_KB_PATH` 覆盖）。
- 检索契约：关键词 BM25 + 向量余弦，RRF 融合；无 Key 时退化为纯关键词，不阻塞。
- 知识库接口：`GET /api/kb`、`GET /api/kb/search`、`POST /api/kb/sink`、`DELETE /api/kb/{doc_id}`；沉淀与检索只调用 embedding。
- 追问接口：`GET/POST /api/runs/{id}/followups`、`GET /api/runs/{id}/followups/{fid}`；CLI `dra ask`。
- 追问存储：`runs/<parent>/followups/<id>/{answer.md, meta.json, trace.jsonl}`，父目录 `followups.json` 维护索引；引用沿用父报告编号。
- 追问树：元数据 `parent_run_id / parent_followup`；可对追问再追问，证据选择结合父问题。
- 继续研究：`POST /api/runs/{id}/continue` 创建子 run，继承父来源与报告，`mode=continue`；
  旧来源编号不变，新来源从 `max(n)+1` 开始，报告用更新版语义。
- 证据质量：来源带 `tier`（3 一手 / 2 专业媒体 / 1 一般 / 0 自媒体）与 `quality`（0–100）；
  `GET /api/runs/{id}/eval` 与 `dra eval` 输出引用覆盖、数字可核验率、来源多样性，纯本地计算。
- 写作约束：具体数字必须逐字来自证据；写后本地核验，低于 `DRA_MIN_NUMBER_SUPPORT`（默认 0.75）
  时带证据校对一次；修复稿需同时满足“可核验率 +5pp、引用 ≥60%、长度 ≥60%”才采纳，否则保留原稿。
- 安装与分发：PyPI 发行名 `dra-research`、命令 `dra`（`uv tool install dra-research` / `uvx --from dra-research dra`）为主，GitHub Release 为备；安装后不依赖仓库目录，配置与数据都在用户目录。
- 桌面形态：`dra.desktop` 启动本地服务 + Tk 控制窗口；PyInstaller 打成 Windows 便携版，
  配置与数据固定在 exe 同级 `data/`；Release 的 `portable-windows` job 产出 zip。
- 发布流程：`v*` tag 触发 `.github/workflows/release.yml`；版本号目前需同步 `pyproject.toml` 与 `src/dra/__init__.py`，清单见 `RELEASE.md`。
- 配置优先级：初始化参数 > 环境变量（`DRA_*`，兼容 `QWEN_*` / `TAVILY_*`）> 用户 `config.toml` > 项目 `.env` > 默认值。
- 模型服务：任意 OpenAI 兼容端点；`model` 为主模型，`fallback_models` 为降级链，某个模型不可用时按序切换。
- Model Adapter：
  - `chat(messages, *, model, temperature, response_format) -> ChatResult`
  - `embed(texts) -> list[list[float]]`
  - 必须屏蔽不同供应商差异（含 reasoning 模型返回的思考字段、JSON 输出重试）。
- 工具接口：统一签名 `tool(name, args) -> ToolResult`；**所有工具调用必须经过注册、参数校验、策略检查**。
- 检索工具：`search(query, max_results) -> list[SearchHit]`；读取工具：`extract(urls) -> list[Document]`。
- Agent 核心：`run(topic, budget, emit) -> RunResult`；`emit(event)` 是唯一的过程输出通道。
- 事件 schema（稳定字段）：`{ts, run_id, seq, type, stage, payload}`；
  `type ∈ run.started | plan.ready | search.done | fetch.done | analysis.done | replan | write.started | run.finished | run.failed`。
- 证据记录：`{n, url, title, domain, published_at, snippet, fetched_at, link_status}`；报告里的 `[n]` 与 `sources.json` 的 `n` 严格对齐，
  **引用编号只能由程序分配，禁止模型自己生成 URL**。
- 产物布局：`runs/<run_id>/{run.json, report.md, sources.json, trace.jsonl}`。
- 安全边界：文件写入限于 `runs/` 与显式工作区；抓取前做 URL 白名单与内网地址拦截。

## 5. 长期风险与待办

- **报告质量上限由检索质量决定**：中文源覆盖、付费墙、反爬都会直接影响结论完整度。
- **引用幻觉**：模型会编造来源。对策是"证据先行"——引用只能从证据库注入，写作阶段模型只能引用已登记编号。
- **交叉验证成本高**：MVP 只做"多来源一致性提示"，不做全量事实核查。
- **上下文膨胀**：长报告要分节写作 + 证据压缩，不能把全文塞进一次调用。
- **预算失控**：必须有硬上限（工具调用数 / 时间 / token）和明确的收敛条件。
- 远期能力：知识库与长期记忆、回滚、评测集与指标、定时监控、多用户与权限。

## 6. 变更记录

| 版本 | 日期 | 变更内容 |
|---|---|---|
| v1.0 | 2026-10-20 | 初版：从架构图与 `5.html` 原型提取目标架构、技术原则与稳定接口 |
| v1.1 | 2026-10-20 | 模型适配层增加思考开关（默认关闭）：单次研究 245s → 70s，三角色默认 `qwen3.8-max`；报告收尾对引用 URL 做可达性复核（`link_status`） |
| v1.14 | 2026-10-21 | 研究历史页、知识库分栏与文档详情 |
| v1.13 | 2026-10-21 | 追问树与继续研究 |
| v1.12 | 2026-10-21 | 后端补全：持久任务队列、重启恢复、设置热加载 |
| v1.11 | 2026-10-21 | 桌面便携版：desktop 入口、Tk 控制窗口、PyInstaller 打包与 Release 自动构建 |
| v1.10 | 2026-10-21 | 后端加固：任务队列、取消、重试、重启恢复、原子写、管理 API 与设置读写 |
| v1.9 | 2026-10-21 | 1.0 发布基线：PyPI 元数据、MIT License、Release 工作流、安装与升级文档 |
| v1.8 | 2026-10-21 | 写作数字约束 + 写后核验/校对/验收闸门；自动生成 eval.json |
| v1.7 | 2026-10-21 | 新增证据质量：来源分级、引用/数字核验评估、基线；提示词标注来源等级 |
| v1.6 | 2026-10-21 | 新增追问：基于父研究证据回答，复用原编号，默认不重新检索；CLI 与界面入口 |
| v1.5 | 2026-10-21 | 知识库接入本地界面：文档列表、页面检索、报告一键沉淀与删除 |
| v1.4 | 2026-10-21 | 新增本地知识库：报告切块入库、BM25 + 向量 + RRF 检索、`dra kb` 命令；检索式去重省额度 |
| v1.3 | 2026-10-20 | 新增本地界面：`dra ui`（FastAPI + SSE，只绑 127.0.0.1）、后台任务管理与 HTTP 接口 |
| v1.2 | 2026-10-20 | 面向"本地工具"重构配置与分发：`config.toml` + 用户数据目录 + BYOK + 任意 OpenAI 兼容端点 + 模型降级链；新增 `dra init` / `dra doctor` |
