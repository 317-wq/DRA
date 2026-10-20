# MVP-0.4 · 本地知识库内核（CLI）

> 基线：PROJECT.md v1.4
> 前置版本：MVP-0.3（本地界面）
> 日期：2026-10-21
> 状态：**已完成**（CLI 验证；UI 页面留到 0.5）
> 已同步至 PROJECT.md v1.4（知识库存储与检索接口）

## 1. 本版范围

**包含什么**：

1. **导入**：把任意一次研究的 `report.md` 切块、向量化、写入本地 SQLite；
   内容哈希去重，同一份报告重复导入不会重复计费。
2. **混合检索**：中文 BM25（字 bigram）+ 向量余弦 + RRF 融合；没有 Key 或向量失败时自动退化为纯关键词。
3. **CLI**：`dra kb add / import / list / search / show / remove`。
4. **零搜索额度**：整个过程不调用 Tavily，只调用一次 embedding（导入时按块批量）。
5. **省额度**：同一轮/跨轮重复的检索式不再重复请求 Tavily。

**不包含什么**：

- 知识库的 Web 页面与“报告一键沉淀”按钮（→ 0.5）
- 分类、标签、rerank、引用回链（→ 0.5 之后）
- 多知识库 / 团队共享（工具是本地单用户）

## 2. 本版用户路径

> `dra kb import --limit 10`（把已有报告批量入库）
> → `dra kb list` 看文档与块数
> → `dra kb search "向量数据库 QPS 延迟"`
> → 输出：mode=hybrid、关键词/向量命中数、Top 块与分数
> → `dra kb show <doc_id>` 看某份报告的分块

## 3. 相对基线的增量

- 架构 / 模块改动：新增 `dra/kb.py`（纯本地，无外部服务依赖）。
- 技术选型改动：知识库用 **SQLite + Python BM25 + embedding BLOB**，不引入向量数据库；
  中文分词用字 bigram，避免 FTS5 对中文不友好的问题。
- 接口改动：
  - 新命令 `dra kb add|import|list|search|show|remove`
  - 新配置 `DRA_KB_PATH`（默认 `data_dir/kb.sqlite3`）
  - `RunContext` 增加 `searched_queries`，用于检索式去重
- 其他：版本 0.4.0。

## 4. 本版要实现的模块功能

- [x] **切块**：按小节/段落切到 ~700 字，跳过“参考资料”
- [x] **去重**：报告内容 SHA-256，重复导入返回已有 doc
- [x] **向量化**：批量 embedding，失败则仅存关键词索引
- [x] **BM25**：英文按词、中文按字 bigram
- [x] **RRF**：关键词与向量排名融合
- [x] **CLI 五个命令**：add / import / list / search / show / remove
- [x] **检索式去重**：同 run 内相同 query 只请求一次
- [x] **测试**：切块、分词、BM25、RRF、增删查、关键词退化

## 5. 本版验收标准

- [x] `dra kb add <run_id>` 能导入并显示块数
- [x] 重复导入同一报告返回 0 新增，不重复计费
- [x] `dra kb list` 显示标题、块数、已向量化数、来源 run
- [x] `dra kb search` 返回 mode / 命中数与 Top 块
- [x] 无 Key 时搜索仍可用（mode=keyword）
- [x] `dra kb remove` 能删除并级联清理 chunks
- [x] 71 个离线单测通过
- [x] `dra kb` 全程不调用 Tavily

## 6. 本版风险与待办

- **检索规模**：BM25 与余弦在每次查询时全量计算，几千块以内没问题；上万块需要倒排/近似检索。
- **向量噪声**：余弦没有阈值，语义不相关时也会进候选；目前靠 RRF 与关键词压制，后续可加阈值/rerank。
- **无 UI**：只能命令行；0.5 接页面与“沉淀”按钮。
- **不会自动入库**：研究结束后需要手动 `kb add`；0.5 做自动沉淀开关。

## 7. 实测记录（2026-10-21）

| 项 | 结果 |
|---|---|
| `kb add`（MCP 报告） | doc 1 · 9 块 · 全部向量化 |
| `kb import --limit 6` | 新增 4、跳过 1（已存在）、失败 1（该 run 无报告） |
| 库规模 | 5 篇 / 45 块 |
| `kb search "向量数据库 QPS 延迟"` | mode=hybrid，关键词 22 / 向量 45，Top1 命中 RAG 报告第 3 节 |
| 搜索额度消耗 | 0（仅 embedding） |
