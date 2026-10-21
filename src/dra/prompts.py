"""提示词。0.1 的质量上限基本由这几个模板决定，改动请配套跑演示主题对比。"""

from __future__ import annotations

from dra.models import Source, SubQuestion


def plan_messages(topic: str, n: int = 4, parent_report: str = "") -> list[dict[str, str]]:
    """构造拆题提示词；有父报告时优先补缺口。"""
    system = "你是深度研究规划器。你只输出 JSON，不输出任何解释或 Markdown 围栏。"
    parent_block = ""
    if parent_report:
        parent_block = f"""

上一版报告（只用于找缺口，不要重复已有结论）：
{parent_report[:2500]}
"""
    user = f"""研究主题：{topic}
{parent_block}
把主题拆成 {n} 个互补、可独立检索的子问题，覆盖：核心概念与现状、关键方案/玩家、对比维度、争议与风险。
子问题之间不要重叠。

要求：
1. 每个子问题给 1-2 条检索式；检索式写成人在搜索引擎里会输入的短语，不要写成疑问句。
2. 涉及"最新/现状/价格/版本"时，检索式里带年份 2026。
3. 中文主题用中文检索式，技术名词保留英文原名（如 Agent、RAG、MCP、MCP Server）。
4. id 从 1 开始连续编号。
5. 涉及性能、成本、对比、安全、争议的子问题，至少给一条指向一手来源的检索式：
   用 `site:arxiv.org`、`site:github.com`，或该领域官方文档域名。不要只给"对比/推荐/2026"。
6. 量化/基准类子问题再补一条指向基准项目的检索式：ANN-Benchmarks、VectorDBBench，
   或厂商官方 benchmark 文档（如 `docs.weaviate.io/weaviate/benchmarks`、`qdrant.tech/benchmarks`）。
7. 如果给了上一版报告，子问题要优先补它没覆盖或证据不足的部分，不要重复已有结论。

只输出 JSON：
{{"subquestions":[{{"id":1,"question":"...","queries":["...","..."]}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def analyze_messages(
    topic: str,
    round_no: int,
    subquestions: list[SubQuestion],
    evidence: dict[int, list[Source]],
    snippet_chars: int = 900,
) -> list[dict[str, str]]:
    """构造证据分析提示词。"""
    system = (
        "你是严谨的研究分析员。只依据给定证据下结论，证据不足就明确承认。"
        "只输出 JSON，不输出解释。"
    )
    blocks: list[str] = []
    for sq in subquestions:
        blocks.append(f"### 子问题 {sq.id}：{sq.question}")
        sources = evidence.get(sq.id, [])
        if not sources:
            blocks.append("（本轮没有抓到证据）")
        for src in sources:
            date = f" | {src.published_at[:10]}" if src.published_at else ""
            blocks.append(f"[{src.n}] {src.title} | {src.domain}{date}（{src.tier_label}）")
            blocks.append((src.snippet or "")[:snippet_chars])
        blocks.append("")

    user = f"""研究主题：{topic}
当前轮次：第 {round_no} 轮

子问题与证据（[n] 为证据编号）：
{chr(10).join(blocks)}

对每个子问题输出：
- key_points：2-4 条要点，每条一句话，必须带 [n] 引用；没有证据支撑的推断不要写。
- evidence_ns：用到的证据编号列表。
- coverage：enough（足以支撑该子问题的核心结论）/ partial（有信息但缺关键维度）/ gap（几乎没有可用信息）。
  enough 不要求"存在一张统一对比表"：两个以上独立来源覆盖核心维度、且给出具体数值或事实，就判 enough；
  只有核心维度仍然缺数值（例如全是定性描述）时才判 partial。
- gap：coverage 不是 enough 时，用一句话说明还缺什么（缺哪类来源或哪个维度）。
  只描述本次证据："本次检索到的资料中未见……"，禁止写"行业完全空白/没有研究"这类绝对判断。

注意：摘录里可能出现 Markdown 表格（`| 模型 | 0.09 | 5.2 |`）。表格中的数字要连同口径
写进 key_points（例如"512 上下文下召回 7.2"）。只有确实没有任何量化数字时，
才能以"缺量化数据"为由判 partial/gap。

只输出 JSON：
{{"items":[{{"id":1,"key_points":["...[1][3]"],"evidence_ns":[1,3],"coverage":"partial","gap":"..."}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def replan_messages(
    topic: str, gaps: list[SubQuestion], source_counts: dict[int, int] | None = None
) -> list[dict[str, str]]:
    """构造补充检索提示词；带上每个子问题已有的来源数。"""
    system = "你是研究检索策略专家。只输出 JSON，不输出解释。"
    counts = source_counts or {}
    lines = []
    for sq in gaps:
        lines.append(f"- 子问题 {sq.id}：{sq.question}（已有 {counts.get(sq.id, 0)} 篇来源）")
        lines.append(f"  已有检索式：{sq.queries}")
        lines.append(f"  缺口：{sq.gap or '信息不足'}")
    user = f"""研究主题：{topic}

以下子问题证据不足：
{chr(10).join(lines)}

为每个子问题补 1-2 条**新的**检索式（必须与已有检索式不同），按缺口类型来换：
- 缺量化/基准/延迟/成本 → 用具体基准名（LongMemEval、LOCOMO、ANN-Benchmarks、VectorDBBench 等），
  或 `site:arxiv.org`、`site:github.com`、官方 benchmark 文档域名；
- 缺正文/只有标题 → 换官方文档 `site:docs.<厂商>.com` 或 `site:github.com` 仓库 README；
- 缺争议/监管 → `site:gov.cn`、`site:arxiv.org` 或行业媒体。
已有来源少于 2 篇的子问题，必须给一条带 `site:` 或具体基准项目名的检索式，
不要只改措辞或年份；宽泛查询只会拿回同一批页面。

只输出 JSON：
{{"queries":[{{"id":1,"queries":["...","..."]}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def write_messages(
    topic: str,
    subquestions: list[SubQuestion],
    sources: list[Source],
    *,
    words: str = "1800-3000",
    parent_report: str = "",
) -> list[dict[str, str]]:
    """构造报告写作提示词。"""
    system = (
        "你是资深研究报告撰写者。写作克制、具体、可核验；宁可写'证据不足'，也不编造。"
    )
    analyses: list[str] = []
    for sq in subquestions:
        analyses.append(f"### 子问题 {sq.id}：{sq.question}")
        analyses.append(f"覆盖度：{sq.coverage or 'unknown'}")
        for point in sq.key_points:
            analyses.append(f"- {point}")
        if sq.gap:
            analyses.append(f"- 证据缺口：{sq.gap}")
        analyses.append("")

    evidence: list[str] = []
    for src in sources:
        date = f" | {src.published_at[:10]}" if src.published_at else ""
        evidence.append(f"[{src.n}] {src.title} | {src.domain}{date}（{src.tier_label}）")
        evidence.append((src.snippet or "")[:600])
        evidence.append("")

    parent_block = ""
    if parent_report:
        parent_block = f"""
上一版报告（继承上下文，不要照抄；只保留仍然成立的结论）：
{parent_report[:3000]}
"""
    user = f"""研究主题：{topic}
{parent_block}
研究结论（按子问题整理）：
{chr(10).join(analyses)}

证据清单（只能引用这些编号）：
{chr(10).join(evidence)}

写作要求：
1. 中文，Markdown，正文 {words} 字。
2. 结构固定为：
   ## 摘要（150 字内，直接给结论）
   ## 1. 研究背景与问题界定
   ## 2. …（按主题自拟 2-4 个小节，逐个子问题展开）
   ## 结论与建议（可执行、有取舍，不要列空话）
   ## 证据缺口与不确定性
3. 每个事实性论断后用 [n] 标注来源；只能使用上面出现过的编号，不得新造编号。
4. 不要输出 URL，不要输出"参考资料"章节（由程序生成）。
5. 不要编造数字、日期、版本号。**所有具体数字必须逐字出现在给定证据摘录中**；
   找不到就写"资料未给出具体数值"或改用定性表述，禁止自己换算、估算或四舍五入后再写。
6. 覆盖度是 partial/gap 的部分，要明确写"本次检索的资料中未见…"或"缺少 X 维度的一手数据"；
   不要把"本次检索未覆盖"写成"行业不存在/完全空白"。
7. 不要写"本文将……"这类开头，直接进入内容。
8. 如果给了上一版报告：这是续写版，保留仍然成立的结论，重点补充新证据；
   旧来源编号必须沿用，不得改变 [n] 的含义。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def followup_messages(
    topic: str,
    report_excerpt: str,
    sources: list[Source],
    question: str,
) -> list[dict[str, str]]:
    """构造追问提示词：只给父研究的证据，引用沿用原编号。"""
    system = "你是研究助手。只依据已有研究的证据回答追问，引用沿用原编号，不编造。"
    evidence = []
    for src in sources:
        evidence.append(f"[{src.n}] {src.title} | {src.domain}")
        evidence.append((src.snippet or "")[:900])
        evidence.append("")
    user = f"""原研究主题：{topic}

原报告开头（用于理解上下文）：
{report_excerpt[:1500]}

可用证据（编号与原报告一致）：
{chr(10).join(evidence)}

追问：{question}

要求：
1. 中文，Markdown，300–800 字，直接回答问题，不要复述原报告。
2. 只使用上面给出的证据；每个事实性论断用 [n] 标注，编号只能来自上面。
3. 证据不足时明确写"现有证据无法回答……"，并说明还缺什么；不要编造数字或结论。
4. 不要输出"参考资料"章节，不要输出 URL。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_messages(
    topic: str,
    draft: str,
    unverified: list[dict],
    sources: list[Source],
) -> list[dict[str, str]]:
    """构造“数字校对”提示词：只修数字，不改变结论与结构。"""
    system = "你是研究报告的校对者。只修正数字与对应引用，不改变结论和结构。"
    needed = {n for item in unverified for n in (item.get("cited") or [])}
    evidence = []
    for src in sources:
        if src.n not in needed:
            continue
        evidence.append(f"[{src.n}] {src.title} | {src.domain}")
        evidence.append((src.snippet or "")[:900])
        evidence.append("")
    items = []
    for item in unverified[:12]:
        items.append(
            f"- 数字 {item['number']} 出现在句子：{item['sentence'][:100]}"
            f"（引用 {item.get('cited') or '无'}）"
        )
    user = f"""研究主题：{topic}

以下句子里的数字，没有在所引用的来源中找到：
{chr(10).join(items)}

这些来源的摘录（数字只能来自这里）：
{chr(10).join(evidence)}

请重写整篇报告：
1. 保留原有章节结构与**全部 [n] 引用**；每个事实性句子仍必须带引用。
2. 只改涉及上述数字的句子，其余文字尽量原样保留，输出长度不得明显短于原文。
3. 数字必须逐字来自上面的来源摘录；找不到就删掉数字、保留句子与引用，
   或把该句改成定性表述。
4. 不要新增来源、不要新增编号、不要改变结论。
5. 直接输出完整的 Markdown 正文，不要解释，不要输出"参考资料"。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
