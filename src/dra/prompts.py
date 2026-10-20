"""提示词。0.1 的质量上限基本由这几个模板决定，改动请配套跑演示主题对比。"""

from __future__ import annotations

from dra.models import Source, SubQuestion


def plan_messages(topic: str, n: int = 4) -> list[dict[str, str]]:
    """构造拆题提示词。"""
    system = "你是深度研究规划器。你只输出 JSON，不输出任何解释或 Markdown 围栏。"
    user = f"""研究主题：{topic}

把主题拆成 {n} 个互补、可独立检索的子问题，覆盖：核心概念与现状、关键方案/玩家、对比维度、争议与风险。
子问题之间不要重叠。

要求：
1. 每个子问题给 1-2 条检索式；检索式写成人在搜索引擎里会输入的短语，不要写成疑问句。
2. 涉及"最新/现状/价格/版本"时，检索式里带年份 2026。
3. 中文主题用中文检索式，技术名词保留英文原名（如 Agent、RAG、MCP、MCP Server）。
4. id 从 1 开始连续编号。

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
            blocks.append(f"[{src.n}] {src.title} | {src.domain}{date}")
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
- gap：coverage 不是 enough 时，用一句话说明还缺什么（缺哪类来源或哪个维度）。

只输出 JSON：
{{"items":[{{"id":1,"key_points":["...[1][3]"],"evidence_ns":[1,3],"coverage":"partial","gap":"..."}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def replan_messages(topic: str, gaps: list[SubQuestion]) -> list[dict[str, str]]:
    """构造补充检索提示词。"""
    system = "你是研究检索策略专家。只输出 JSON，不输出解释。"
    lines = []
    for sq in gaps:
        lines.append(f"- 子问题 {sq.id}：{sq.question}")
        lines.append(f"  已有检索式：{sq.queries}")
        lines.append(f"  缺口：{sq.gap or '信息不足'}")
    user = f"""研究主题：{topic}

以下子问题证据不足：
{chr(10).join(lines)}

为每个子问题补 1-2 条**新的**检索式（必须与已有检索式不同），专门针对上面的缺口。
如果缺口是"缺官方/一手资料"，就换更具体的厂商名或文档关键词；缺对比就加"对比/benchmark/评测"。

只输出 JSON：
{{"queries":[{{"id":1,"queries":["...","..."]}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def write_messages(
    topic: str,
    subquestions: list[SubQuestion],
    sources: list[Source],
    *,
    words: str = "1800-3000",
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
        evidence.append(f"[{src.n}] {src.title} | {src.domain}{date}")
        evidence.append((src.snippet or "")[:600])
        evidence.append("")

    user = f"""研究主题：{topic}

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
5. 不要编造数字、日期、版本号；证据中没有的就不要写。
6. 覆盖度是 partial/gap 的部分，要明确写"现有资料未能证实…"或"缺少 X 维度的一手数据"。
7. 不要写"本文将……"这类开头，直接进入内容。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
