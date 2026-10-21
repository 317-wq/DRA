# MVP-1.3 · 后端补全（持久队列 / 热加载）

> 基线：PROJECT.md v1.12
> 前置版本：MVP-1.2（桌面便携版）
> 日期：2026-10-21
> 状态：**已完成**（104 个离线单测）

## 1. 本版范围

**包含什么**：

1. **持久任务队列**：排队任务写入 `runs/.jobs.sqlite3`，服务重启后自动重新入队，不再丢。
2. **重启语义明确**：
   - `queued` → 重新入队并继续执行；
   - `running` → 无法从中断处续跑，标为 `interrupted`，可手动重试。
3. **设置热加载**：`PUT /api/settings` 写入后立即作用于当前服务实例，不需要重启；
   路径类字段（`runs_dir` / `kb_path`）故意不热加载，避免运行中换目录。
4. **任务表状态**：`jobs` 表记录 `run_id / topic / max_sources / status / created_at / updated_at / error`。

**不包含什么**：

- 进程崩溃后的“续跑”（只能重新入队或重试）
- 多 worker 并发
- 调度器、通知、多用户权限（架构远期层）

## 2. 用户路径

> 连续发起多个研究 → 任务进入持久队列
> → 关掉服务/电脑重启 → 未开始的排队任务自动继续
> → 正在跑的那一条标为“已中断”，点重试即可
> → 在设置里改模型/Key → 下一个任务立即用新配置

## 3. 相对基线的增量

- 模块：`dra/jobs.py` 新增 `JobStore`；`RunManager` 启动时从 `.jobs.sqlite3` 恢复。
- 配置：`Settings.apply_hot()`；`HOT_RELOAD_FIELDS` 白名单。
- 接口：`PUT /api/settings` 返回 `reloaded`，`restart_required=false`。
- 其他：版本 1.3.0。

## 4. 本版要实现的模块功能

- [x] 任务持久化表与索引
- [x] 启动恢复：queued 重新入队
- [x] 启动恢复：running 标 interrupted
- [x] 删除任务时同步删任务表
- [x] 取消排队任务时同步任务表
- [x] 设置热加载（模型、Key、阈值等）
- [x] 兼容旧 run（没有 jobs 表记录的 queued/running）
- [x] 测试：重启恢复、状态持久化、热加载

## 5. 本版验收标准

- [x] 写入一条 queued 任务记录后，新建 manager 会自动执行它
- [x] running 任务在重启后变 interrupted，且不自动重跑
- [x] 正常跑完后任务表状态为 done
- [x] `PUT /api/settings` 后 `manager.settings` 立即变化
- [x] 路径类字段不会被热加载
- [x] 104 个离线单测通过

## 6. 本版风险与待办

- **崩溃续跑**：没有 checkpoint，跑一半只能重试；模型调用不可恢复。
- **旧数据兼容**：1.1 之前的 run 没有 jobs 记录，启动时会补建 queued 或标 interrupted。
- **多实例**：两个服务共享 runs 目录时，仍会互相把 running 标 interrupted（单用户假设）。
- **设置热加载范围**：只覆盖运行参数；换 `runs_dir`/`kb_path` 仍需重启或改环境变量。

## 7. 后端剩余 backlog（明确未做）

- 追问树（对追问再追问）
- “继续研究”（在已完成报告上追加子问题/来源）
- 知识库重建索引（换 embedding 模型后）
- 报告导出 PDF/Word（目前 MD/HTML）
- 本地 API token（目前仅监听 127.0.0.1）
- 多搜索源、调度器、通知、多用户（远期）

## 8. 实测记录（2026-10-21）

| 项 | 结果 |
|---|---|
| 单测 | 104 个通过 |
| 队列恢复 | queued 任务在“重启”后自动执行 |
| 中断恢复 | running → interrupted，不重复执行 |
| 热加载 | PUT 后 `manager.settings.model` 立即更新 |
| CI | 三平台矩阵（Windows flake 已修） |
