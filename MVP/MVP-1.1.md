# MVP-1.1 · 后端加固（队列 / 取消 / 恢复 / 管理接口）

> 基线：PROJECT.md v1.10
> 前置版本：MVP-1.0（分发）
> 日期：2026-10-21
> 状态：**已完成**（101 个离线单测；UI 与 API 均已接上）
> 已同步至 PROJECT.md v1.10

## 1. 本版范围

**包含什么**：

1. **任务队列**：单 worker 串行执行；连续发起不再被 409 拒绝，而是排队。
2. **取消**：排队中可立即取消；运行中在“工具/模型边界”协作式退出，保留已完成产物。
3. **重试**：用原主题与原来源上限重新排队；`requested_max_sources` 不再被最终 `run.json` 冲掉。
4. **重启恢复**：服务启动时把遗留的 `queued` / `running` 标记为 `interrupted`，不会永远卡住。
5. **原子写**：`run.json` 等文本先写临时文件再 `os.replace`，避免读到半截 JSON。
6. **管理 API**：分页、状态过滤、删除、导出（MD/HTML）、健康检查、设置读写、取消、重试。
7. **CLI**：`dra delete`、`dra export`。
8. **界面**：取消 / 重试 / 删除历史 / 导出报告；新增 `queued` / `cancelled` / `interrupted` 状态。

**不包含什么**：

- 多 worker 并发（本地单用户，单 worker 足够）
- 任务持久队列（进程重启后排队任务不恢复，只标记 interrupted）
- 定时调度、通知（架构远期）
- 多用户与权限

## 2. 用户路径

> `dra ui` → 连续发起两个主题 → 第二个显示“排队中”
> → 取消排队任务 / 取消运行中任务
> → 失败后点“重试” → 新任务沿用原主题与来源上限
> → 历史里删除不再需要的 run
> → 报告页“导出 MD”，或 `dra export <run_id> --format html`

## 3. 状态机

```
queued → running → done | partial | failed | cancelled
queued → cancelled
running → cancelled（在边界检查）
queued/running → interrupted（服务重启）
```

## 4. 本版要实现的模块功能

- [x] 任务队列（`queue.Queue` + 单 worker 线程）
- [x] 取消（`threading.Event` + Agent 边界检查）
- [x] 重试（保留主题与 `requested_max_sources`）
- [x] 重启恢复（`_recover_stale`）
- [x] 原子写（`os.replace`）
- [x] 分页与状态过滤（`GET /api/runs?limit&offset&status`）
- [x] 删除（`DELETE /api/runs/{id}`，运行中拒绝）
- [x] 导出（`GET /api/runs/{id}/export?format=md|html`）
- [x] 健康检查（`GET /healthz`）
- [x] 设置读写（`GET/PUT /api/settings`）
- [x] CLI `delete` / `export`
- [x] UI 取消 / 重试 / 删除 / 导出
- [x] 测试：队列顺序、取消排队/运行、重试、恢复、原子写、管理 API

## 5. 本版验收标准

- [x] 连续发起两个任务，第二个状态为 `queued`，执行顺序与发起顺序一致
- [x] 取消排队任务立即变 `cancelled`
- [x] 取消运行中任务，在边界退出并写 `cancelled`
- [x] 重试生成新 run，主题与来源上限与原任务一致
- [x] 模拟重启后，遗留 `running` 变 `interrupted`
- [x] 原子写不留下临时文件，重复写后内容正确
- [x] 分页返回 `total` / `limit` / `offset`；状态过滤生效
- [x] 导出 MD/HTML 带下载头；删除后 404
- [x] 设置 PUT 后 GET 能读到新值（配置文件优先级修复）
- [x] 101 个离线单测通过

## 6. 本版风险与待办

- **单进程假设**：两个服务实例共享同一个数据目录时，启动恢复会误伤另一个实例的任务。
- **取消延迟**：模型调用不可中断，取消会在当前调用返回后生效（最长约 40–60 秒）。
- **排队不持久**：进程崩溃后排队任务丢失，只标记 interrupted；1.2 可做持久队列。
- **设置生效**：`PUT /api/settings` 写配置后，已启动的服务实例不会热加载，需要重启（响应里已标注）。
- **无并发**：单 worker 是刻意的；如果以后要多任务并发，需要按 run 隔离资源与 SQLite 连接。

## 7. 实测记录（2026-10-21）

| 项 | 结果 |
|---|---|
| 单测 | 101 个通过 |
| 队列 | 第二个任务稳定 `queued`，顺序执行 |
| 取消 | 排队/运行两条路径均落到 `cancelled` |
| 恢复 | 遗留 `running` → `interrupted` |
| 导出 | MD/HTML 均带 `Content-Disposition` |
| 设置 | PUT 后 GET 读到新 model 与阈值 |
