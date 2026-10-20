# Deep Research Agent

输入一个研究主题，Agent 自己拆题、检索、读正文、判断证据够不够、补检索，最后写一份**每个结论都能溯源到 URL** 的 Markdown 报告。

当前版本：**MVP 0.1（CLI）**。范围与验收见 [MVP/MVP-0.1.md](MVP/MVP-0.1.md)，目标架构见 [MVP/PROJECT.md](MVP/PROJECT.md)，路线图见 [MVP/ROADMAP.md](MVP/ROADMAP.md)。

## 实测（2026-10-20，8 核 / 3GB 单机）

| 主题 | 耗时 | 来源 | 正文 | 结果 |
|---|---|---|---|---|
| 2026 年主流 Agent 记忆机制方案对比 | 71.0s | 6 篇 | 2002 字 | partial（多数子问题证据只到 partial） |
| 2026 年 RAG 与长上下文方案的选择边界 | 68.8s | 8 篇 | 1830 字 | partial（1 个子问题 enough） |
| 2026 年中国新能源汽车换电模式的现状与争议 | 72.9s | 7 篇 | 2596 字 | partial |

三轮合计约 9k–12k token/次（无 reasoning token）。`partial` 不是失败：它表示有子问题没拿到足够证据，报告里会明确写出缺口。

## 快速开始

```bash
cd ~/ljt/DRA

# 依赖（uv 不在 PATH 里，用绝对路径）
~/.local/bin/uv sync

# 配置：复制后填 key
cp .env.example .env

# 跑一个主题
~/.local/bin/uv run dra run "2026 年主流 Agent 记忆机制方案对比" --max-sources 8

# 看报告 / 看完整轨迹
~/.local/bin/uv run dra show <run_id>
~/.local/bin/uv run dra show <run_id> --trace

# 历史
~/.local/bin/uv run dra list

# 三个主题一键演示
./scripts/demo.sh
```

也可以 `source .venv/bin/activate` 后直接用 `dra`。

## 一次研究发生了什么

```
主题
 ├─ Plan      拆成 3–5 个可独立检索的子问题（每个带检索式）
 ├─ Search    Tavily 并发检索（并发 4），URL 归一化去重
 ├─ Read      Tavily Extract 批量取正文；失败降级原始抓取
 ├─ Analyze   证据 → 要点 + 引用编号 + 覆盖度（enough/partial/gap）
 ├─ Replan    有 gap 且预算未尽 → 补 1–2 条检索式，再来一轮
 └─ Write     分节写报告，引用编号由程序注入并重排
```

终止条件：所有子问题 `enough`，或预算（工具调用 / 墙钟时间 / token / 轮次）用尽。
补充检索没拿到新来源时会提前收敛，不再空跑一轮分析。

## 产物

```
runs/<run_id>/
  run.json          # 元数据、覆盖度、预算、token、提示
  report.md         # 报告（含参考资料，URL 由程序生成）
  sources.json      # 实际引用的来源，n 与 [n] 对齐，含 link_status 复核结果
  subquestions.json # 拆题与每轮覆盖度
  trace.jsonl       # 事件流：可直接喂给 0.2 的 SSE
  sources/001.md    # 每篇来源的正文快照（排查用）
  debug/            # 仅在分析输出解析失败时出现，存原始返回
```

## 配置

`.env` 里的 `QWEN_*` / `TAVILY_*` 直接复用。角色模型可覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DRA_PLANNER_MODEL` | `qwen3.8-max` | 拆题 + 补检索 |
| `DRA_ANALYST_MODEL` | `qwen3.8-max` | 证据分析 |
| `DRA_WRITER_MODEL` | `qwen3.8-max` | 写报告（也可 `--writer` 临时覆盖） |
| `DRA_DISABLE_THINKING` | `true` | 对 Qwen 模型关闭思考模式（关键，见下） |
| `DRA_RUNS_DIR` | `./runs` | 产物目录 |
| `DRA_REQUEST_TIMEOUT_S` | `180` | 单次模型调用超时 |

### 模型实测记录（这些数字决定了默认值）

| 配置 | 结果 |
|---|---|
| `qwen3.8-max` + 关闭 thinking | 拆题 5.7s、写 2600 字约 60s，JSON 稳定 ✅ 默认 |
| `qwen3.8-max` + 开启 thinking | 写 2600 字 171s（其中 4995 token 是推理），一次研究 245s |
| `qwen3.8-flash` | 能写对，但同样爱推理：一次分析 102s / 4628 reasoning token |
| `deepseek-v4.1-flash` | 快，但 50% 概率把 4000+ token 花在推理上，JSON 被截断；作写作模型单次 5368 reasoning/6000 上限，报告写一半 |
| `kimi-k3` | 不接受 `response_format`（HTTP 400） |
| `glm-5.3` / `deepseek-v4-pro-0813` | JSON 模式下 reasoning 吃掉输出额度，容易截断 |

结论：**结构化任务和写作都用 qwen3.8-max，并关闭 thinking**。收益不是一点——单次研究从 245s 降到 70s，且没有截断。

## 设计取舍（0.1）

- **不用 LangGraph**：单线程显式循环 + 预算检查就够，调试成本低。
- **引用只由程序注入**：模型写 `[n]`，程序校验编号、按首次出现重排、生成参考资料。模型编的 URL 进不了报告。
- **证据先行**：先登记来源再分析，分析阶段只能看已登记证据。
- **分析失败不拖垮研究**：截断会加大额度重试一次；仍失败就降级为 gap，报告继续由证据清单驱动。
- **引用收尾复核**：写完后并发检查引用 URL，状态写进 `sources.json`；403/超时只提示，不改写报告。
- **不追求"全知"**：覆盖度不到就写"证据不足"，比编一段像样的假话有价值。

## 测试

```bash
~/.local/bin/uv run pytest                      # 34 个单测（离线，约 2s）
DRA_LIVE=1 ~/.local/bin/uv run pytest -m live   # 真实链路 smoke（会花钱）
```

## 已知限制

- 中文源覆盖受 Tavily 影响；未实测是否需要补 Bing / Brave。
- 分析阶段每个子问题最多看 5 篇、每篇 800 字摘录，长文会被截断——这是 `partial` 偏多的主要原因。
- 报告 1800–2600 字，来自提示词里的硬约束；要更长需要分节写作（0.2 再做）。
- 事件 schema 已按 SSE 直出的形态设计，但尚未被真实消费者验证。
- 部分站点（YouTube / Wikipedia）抓取成功但服务器直连不可达，`link_status` 会标 `unreachable`。
- 无 Web、无知识库、无追问——见 ROADMAP 0.2–0.4。
