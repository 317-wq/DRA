# MVP-0.1 · 单主题端到端研究报告（CLI）

> 基线：PROJECT.md v1.1
> 前置版本：无（首个 MVP）
> 日期：2026-10-20
> 状态：**已完成**（实测数据见第 7 节）
> 已同步至 PROJECT.md v1.1（模型思考开关 + 引用复核）

## 1. 本版范围

**包含什么**（3–5 个核心能力）：

1. **拆题**：主题 → 3–5 个子问题，每个子问题带 1–2 条检索式（结构化 JSON，可校验）。
2. **检索与阅读**：子问题 → Tavily 搜索 → 正文提取 → 证据登记（URL 归一化去重、域名与抓取时间留痕）。
3. **分析与收敛**：每个子问题产出"要点 + 支撑证据编号 + 覆盖度（enough / partial / gap）"；
   覆盖不足且预算未尽时 → 重新规划检索式补检，否则收敛。
4. **成稿**：分节写作，输出带 `[n]` 引用的 Markdown 报告；引用编号由程序注入，模型不得自造 URL。
5. **可回放**：全程写 `trace.jsonl`，跑完能还原每一步的输入、输出、耗时与 token。

**不包含什么**（明确划掉，留到后续）：

- Web UI / 登录 / 多用户 / 权限（→ 0.2、1.0）
- 知识库、向量检索、RRF 融合（→ 0.3）
- 研究历史页、追问树、继续研究（→ 0.4）
- 定时任务、并发控制、监控指标（→ 1.0）
- LangGraph、任务队列、Docker 部署
- 邮件 / 通知 / 第三方数据源

## 2. 本版用户路径

> `uv run dra run "2026 年主流 Agent 记忆机制方案对比" --max-sources 8`
> → 终端滚动显示：拆题 → 逐个子问题检索、读取正文 → 覆盖度分析
> → 对 gap 补检一轮 → 收敛 → 分节写报告
> → 结束打印：run 目录、来源数、耗时、token、覆盖缺口
> → `uv run dra show <run_id>` 重新查看报告与调用轨迹

## 3. 相对基线的增量

- 架构 / 模块改动：只实现 Agent 循环主干（Plan / Search / Read / Analyze / Replan / Write）
  + 证据登记 + Trace。任务管理退化为单进程 `Run` 对象，不引入队列与状态机持久化。
- 技术选型改动：无。本版**不引入** LangGraph——单线程显式循环足够，且更利于控制预算。
- 接口改动（新增）：
  - `SearchTool.search(query, max_results)`
  - `FetchTool.extract(urls)`
  - `ModelAdapter.chat` / `ModelAdapter.embed`
  - 事件类型：`run.started / plan.ready / search.done / fetch.done / analysis.done / replan / write.started / run.finished / run.failed`
- 其他：无 Web 层；事件 schema 按 SSE 直出的形态设计，0.2 不返工。

## 4. 本版要实现的模块功能

- [x] **项目骨架 + 配置**：uv 初始化；pydantic-settings 读 `.env`；`dra --version`
- [x] **ModelAdapter**：chat（JSON 模式、思考开关、失败降级）+ embed
- [x] **工具层**：`search`（Tavily）、`extract`（Tavily extract，失败降级原始抓取，URL 安全校验）
- [x] **证据库**：Source 模型、URL 归一化去重、`sources.json` 读写、正文快照
- [x] **Plan 节点**：主题 → 子问题 + 检索式；JSON 结构校验
- [x] **检索 / 阅读节点**：每个子问题取 top-k → 提取正文 → 入库（并发 4）
- [x] **Analyze 节点**：子问题 → 要点 + 证据编号 + 覆盖度判断；截断自动重试、失败降级 gap
- [x] **Replan 节点**：gap 生成新检索式；预算检查（工具调用 / 墙钟时间 / token / 轮次）
- [x] **Write 节点**：分节写作；引用编号程序注入并重排；输出 `report.md`
- [x] **Trace + CLI 展示**：JSONL 事件、Rich 实时面板、结束摘要、`dra show --trace`
- [x] **演示脚本**：`scripts/demo.sh` 一键跑 3 个主题

## 5. 本版验收标准

- [x] 任选一个中文技术主题，**3 分钟内**产出 ≥1500 字、≥5 个来源的报告
      （实测 69–73s、1830–2596 字、6–8 篇）
- [x] 报告中每个 `[n]` 都能在 `sources.json` 找到对应 URL
      （程序强制：无效编号直接丢弃并重排；收尾复核写入 `link_status`——实测 5 篇中 3 篇可直连，
      YouTube / Wikipedia 在本机不可达但正文抓取成功，已在 run.json 提示）
- [x] 同一主题重复运行，URL 去重生效（无重复来源）
- [x] 搜索 0 结果 / 网络超时不崩溃：报错清晰，且已完成产物保留
      （提取失败逐条降级；全无来源时直接失败并写 trace）
- [x] 预算耗尽时输出"部分结论 + 缺口说明"，而不是硬编或空转
      （实测 `--max-tool-calls 3` 在 9.2s 干净中止）
- [x] `trace.jsonl` 能还原 5 个阶段的先后时序，含每步耗时与 token
- [x] 3 个演示主题全部跑通，且报告可读（技术选型 / 方案对比 / 现状调研）

## 6. 本版风险与待办

- **覆盖度偏保守**：多数子问题判 `partial`。原因是每篇只喂 800 字摘录，长文被截断；
  下一步要么提高摘录长度，要么做"分段摘要 + 证据压缩"。
- **Tavily 中文覆盖未压测**：3 个主题都拿到了 6–8 篇，但没有评估来源质量分布。
- **报告长度被硬约束在 1800–2600 字**：更长需要分节写作（计划放 0.2 与 Web 一起做）。
- **`partial` 是常态**：需要一次"全 enough"的样例来证明收敛路径，目前最好的主题是 1/4 enough。
- 事件 schema 尚未被 0.2 的 SSE 消费，字段可能还会动。

## 7. 实测记录（2026-10-20）

| 主题 | 耗时 | 来源 | 正文 | 覆盖度 |
|---|---|---|---|---|
| 2026 年主流 Agent 记忆机制方案对比 | 71.0s | 6 | 2002 字 | 0 enough / 4 partial |
| 2026 年 RAG 与长上下文方案的选择边界 | 68.8s | 8 | 1830 字 | 1 enough / 3 partial |
| 2026 年中国新能源汽车换电模式的现状与争议 | 72.9s | 7 | 2596 字 | 0 enough / 4 partial |

单次 token：9k–12k，无 reasoning token。

另测「MCP 协议」主题（5 篇）：89.3s、2235 字，其中约 11s 花在链接复核。

**关键决策：关闭 thinking。** 同一模型开启 thinking 时，写 2600 字要 171s（4995 reasoning token），
一次研究 245s；关闭后 60s 左右，且 JSON 不再被截断。`deepseek-v4.1-flash` 与 `kimi-k3`
在结构化输出上均不可靠（前者 reasoning 截断、后者不接受 `response_format`），因此默认值统一为
`qwen3.8-max` + `DRA_DISABLE_THINKING=true`。
