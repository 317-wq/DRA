"""Agent 执行循环（0.1 主干）。

Plan → [Search → Read → Analyze → Replan]* → Write
所有过程通过 EventBus 输出；所有产物落到 runs/<run_id>/。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dra.budget import Budget, BudgetExceeded
from dra.citations import count_chinese_chars, finalize_report, strip_urls_for_check
from dra.config import Settings
from dra.jsonutil import JSONParseError, extract_json
from dra.llm import LLMError, ModelAdapter
from dra.models import ChatUsage, RunResult, Source, SubQuestion
from dra.prompts import analyze_messages, plan_messages, replan_messages, write_messages
from dra.store import Run, RunStore
from dra.tools import Reader, SearchHit, TavilySearch, ToolError, canonicalize, domain_of
from dra.trace import EventBus, TraceWriter, now_iso
from dra.verify import check_urls, is_ok

SNIPPET_CHARS = 1200
PER_QUESTION_SOURCES = 3
SEARCH_RESULTS = 6


_NUMERIC = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms|QPS|qps|recall|accuracy|倍|万|亿|GB|MB|tok)")
_QUANT_KEYS = (
    "benchmark", "latency", "qps", "recall", "throughput", "cost",
    "p95", "p99", "table", "延迟", "成本", "召回", "基准", "吞吐",
)


def best_excerpt(text: str, limit: int = SNIPPET_CHARS) -> str:
    """从正文里挑“数字和指标最密”的一段，而不是永远取开头。

    基准测试的数字通常在中后段的表格里；只取前 N 字会让分析模型看不到量化证据。
    """
    body = re.sub(r"[ \t]+", " ", text or "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) <= limit:
        return body

    stride = max(200, limit // 2)
    best_score, best_pos = -1, 0
    for pos in range(0, len(body) - limit + 1, stride):
        window = body[pos : pos + limit]
        score = len(re.findall(r"\d", window))
        score += 4 * len(_NUMERIC.findall(window))
        score += 2 * sum(window.lower().count(key) for key in _QUANT_KEYS)
        if score > best_score:
            best_score, best_pos = score, pos

    if best_pos == 0:
        return body[:limit]
    return "…" + body[best_pos : best_pos + limit]


def per_question_quota(remaining: int, targets_left: int, cap: int = PER_QUESTION_SOURCES) -> int:
    """把剩余来源名额均分给还没处理的子问题，避免前面的子问题吃光预算。"""
    if remaining <= 0 or targets_left <= 0:
        return 0
    return min(cap, max(1, remaining // targets_left))


@dataclass
class RunContext:
    run: Run
    topic: str
    budget: Budget
    bus: EventBus
    max_sources: int = 12
    sources: list[Source] = field(default_factory=list)
    usage: ChatUsage = field(default_factory=ChatUsage)
    notes: list[str] = field(default_factory=list)
    _by_url: dict[str, Source] = field(default_factory=dict)

    def remaining_sources(self) -> int:
        """还能再收录多少篇来源。"""
        return max(0, self.max_sources - len(self.sources))

    def has_url(self, url: str) -> bool:
        """该 URL 是否已登记（按规范化后的形式判断）。"""
        return canonicalize(url) in self._by_url

    def add_source(
        self,
        *,
        url: str,
        title: str,
        content: str,
        published_at: str | None,
        query: str,
        subq_id: int,
        via: str,
    ) -> Source | None:
        """登记一条证据；重复 URL 只追加子问题关联。"""
        canon = canonicalize(url)
        if not canon:
            return None
        if canon in self._by_url:
            src = self._by_url[canon]
            if query and query not in src.queries:
                src.queries.append(query)
            if subq_id not in src.subquestion_ids:
                src.subquestion_ids.append(subq_id)
            return None
        n = len(self.sources) + 1
        src = Source(
            n=n,
            url=canon,
            title=(title or canon)[:300],
            domain=domain_of(canon),
            published_at=published_at,
            snippet=best_excerpt(content),
            chars=len(content or ""),
            fetched_at=now_iso(),
            queries=[query] if query else [],
            subquestion_ids=[subq_id],
            via=via,
        )
        self.sources.append(src)
        self._by_url[canon] = src
        self.run.save_source(n, content or "")
        return src


class ResearchAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: ModelAdapter | None = None,
        searcher: TavilySearch | None = None,
        reader: Reader | None = None,
        store: RunStore | None = None,
        verify_links: bool = True,
    ) -> None:
        """初始化实例。"""
        self.settings = settings
        self.verify_links = verify_links
        self.llm = llm or ModelAdapter(settings)
        self.searcher = searcher or TavilySearch(settings)
        self.reader = reader or Reader(settings)
        self.store = store or RunStore(settings.runs_dir)

    # ------------------------------------------------------------------ run
    def run(
        self,
        topic: str,
        *,
        budget: Budget | None = None,
        max_sources: int = 12,
        on_event=None,
    ) -> RunResult:
        """执行一次完整研究并返回结果。"""
        budget = budget or Budget()
        run = self.store.create(topic)
        bus = EventBus(run.id)
        trace = TraceWriter(run.trace_path)
        bus.subscribe(trace)
        if on_event is not None:
            bus.subscribe(on_event)

        ctx = RunContext(
            run=run,
            topic=topic,
            budget=budget,
            bus=bus,
            max_sources=max_sources,
        )
        started_wall = datetime.now()
        status = "failed"
        report_md = ""
        cited: list[Source] = []
        subquestions: list[SubQuestion] = []

        bus.emit(
            "run.started",
            "start",
            {
                "topic": topic,
                "max_sources": max_sources,
                "budget": budget.snapshot(),
                "models": {
                    "plan": self.settings.plan_model_resolved,
                    "analyze": self.settings.analyze_model_resolved,
                    "write": self.settings.write_model_resolved,
                },
            },
        )

        try:
            self.settings.require_llm()
            self.settings.require_search()
            subquestions = self._plan(ctx)
            bus.emit(
                "plan.ready",
                "plan",
                {
                    "subquestions": [
                        {"id": sq.id, "question": sq.question, "queries": sq.queries}
                        for sq in subquestions
                    ]
                },
            )

            for round_no in range(1, budget.max_rounds + 1):
                if round_no > 1 and all(sq.coverage == "enough" for sq in subquestions):
                    break
                budget.check()
                budget.spend_round()
                added = self._collect(ctx, subquestions, round_no)
                if round_no > 1 and added == 0:
                    ctx.notes.append("补充检索未获得新来源，提前收敛")
                    break
                self._analyze(ctx, subquestions, round_no)
                if all(sq.coverage == "enough" for sq in subquestions):
                    break
                if not budget.can_continue():
                    ctx.notes.append(f"预算用尽，提前收敛：{budget.exhausted_reason()}")
                    break
                if round_no < budget.max_rounds:
                    self._replan(ctx, subquestions)

            report_md, cited, dropped = self._write(ctx, subquestions)
            self._finalize_files(ctx, subquestions, report_md, cited)
            status = "done" if all(sq.coverage == "enough" for sq in subquestions) else "partial"
            if dropped:
                ctx.notes.append(f"丢弃 {dropped} 处指向不存在来源的引用")
        except BudgetExceeded as exc:
            ctx.notes.append(f"预算中止：{exc.reason}")
            status = "partial"
        except (LLMError, ToolError, JSONParseError, FileNotFoundError) as exc:
            ctx.notes.append(f"{type(exc).__name__}: {exc}")
            status = "failed"
        except Exception as exc:  # 兜底：保留现场，别把 trace 丢了
            ctx.notes.append(f"{type(exc).__name__}: {exc}")
            status = "failed"

        finished = datetime.now()
        if not report_md:
            status = "failed"
            if not any("报告" in n for n in ctx.notes):
                ctx.notes.append("未生成报告（见上方失败原因）")
        result = RunResult(
            run_id=run.id,
            topic=topic,
            status=status,  # type: ignore[arg-type]
            started_at=started_wall.isoformat(timespec="seconds"),
            finished_at=finished.isoformat(timespec="seconds"),
            duration_s=round((finished - started_wall).total_seconds(), 1),
            report_path=str(run.report_path),
            sources=cited,
            cited_ns=[s.n for s in cited],
            subquestions=subquestions,
            usage=ctx.usage,
            budget=budget.snapshot(),
            model=self.settings.write_model_resolved,
            notes=ctx.notes,
        )
        run.write_json("run.json", result.model_dump())
        bus.emit(
            "run.finished" if status != "failed" else "run.failed",
            "done",
            {
                "status": status,
                "duration_s": result.duration_s,
                "sources": len(cited),
                "chars": count_chinese_chars(report_md),
                "notes": ctx.notes,
                "usage": ctx.usage.model_dump(),
            },
            tokens=ctx.usage.total_tokens,
        )
        trace.close()
        return result

    # ----------------------------------------------------------------- plan
    def _plan(self, ctx: RunContext) -> list[SubQuestion]:
        """拆题：主题 → 子问题与检索式。"""
        res = self._chat(
            ctx,
            plan_messages(ctx.topic, n=4),
            model=self.settings.plan_model_resolved,
            max_tokens=1800,
            json_mode=True,
            stage="plan",
        )
        data = extract_json(res.text)
        raw = data.get("subquestions") if isinstance(data, dict) else data
        if not isinstance(raw, list) or not raw:
            raise JSONParseError("规划结果缺少 subquestions")

        subs: list[SubQuestion] = []
        for item in raw[:5]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            queries = [str(q).strip() for q in (item.get("queries") or []) if str(q).strip()]
            if not question or not queries:
                continue
            subs.append(SubQuestion(id=len(subs) + 1, question=question[:200], queries=queries[:2]))
        if not subs:
            raise JSONParseError("规划结果没有可用子问题")
        return subs

    # --------------------------------------------------------------- collect
    def _collect(self, ctx: RunContext, subquestions: list[SubQuestion], round_no: int) -> int:
        """检索并读取正文，登记证据；返回本轮新增来源数。"""
        targets = subquestions if round_no == 1 else [sq for sq in subquestions if sq.coverage != "enough"]
        jobs: list[tuple[SubQuestion, str]] = [(sq, q) for sq in targets for q in sq.queries]
        if not jobs:
            return 0

        # 给读取环节留出一半预算
        room = ctx.budget.max_tool_calls - ctx.budget.tool_calls
        if room <= 0:
            return 0
        jobs = jobs[: max(1, room // 2)]

        results = self._search_many(jobs)
        ctx.budget.spend_tool(len(results))

        per_sq: dict[int, dict[str, tuple[SearchHit, str]]] = {}
        for sq, query, hits, error in results:
            ctx.bus.emit(
                "search.done",
                "search",
                {
                    "subquestion": sq.id,
                    "query": query,
                    "hits": len(hits),
                    "ok": error is None,
                    "error": error,
                },
            )
            if error:
                ctx.notes.append(f"搜索失败（子问题 {sq.id}）：{error}")
                continue
            bucket = per_sq.setdefault(sq.id, {})
            for hit in hits:
                canon = canonicalize(hit.url)
                if not canon or ctx.has_url(canon):
                    continue
                best = bucket.get(canon)
                if best is None or hit.score > best[0].score:
                    bucket[canon] = (hit, query)

        picked: list[tuple[SubQuestion, SearchHit, str]] = []
        seen: set[str] = set()
        remaining = ctx.remaining_sources()
        starved: list[int] = []
        for index, sq in enumerate(targets):
            quota = per_question_quota(remaining, len(targets) - index)
            if quota <= 0:
                starved.append(sq.id)
                continue
            bucket = sorted(per_sq.get(sq.id, {}).values(), key=lambda x: x[0].score, reverse=True)
            taken = 0
            for hit, query in bucket:
                if remaining <= 0 or taken >= quota:
                    break
                canon = canonicalize(hit.url)
                if not canon or canon in seen:
                    continue
                seen.add(canon)
                picked.append((sq, hit, query))
                taken += 1
                remaining -= 1

        if not picked:
            ctx.bus.emit("fetch.done", "read", {"subquestion": None, "sources": [], "failed": []})
            return 0

        affordable = max(0, ctx.budget.max_tool_calls - ctx.budget.tool_calls)
        picked = picked[:affordable]
        if not picked:
            return 0
        ctx.budget.spend_tool(len(picked))

        docs, failures = self.reader.read([hit.url for _, hit, _ in picked])
        by_url = {canonicalize(doc.url): doc for doc in docs}

        added: dict[int, list[int]] = {}
        for sq, hit, query in picked:
            doc = by_url.get(canonicalize(hit.url))
            if doc is None:
                continue
            src = ctx.add_source(
                url=hit.url,
                title=doc.title or hit.title,
                content=doc.content,
                published_at=hit.published_date,
                query=query,
                subq_id=sq.id,
                via=doc.via,
            )
            if src is not None:
                added.setdefault(sq.id, []).append(src.n)

        if starved:
            ctx.notes.append(
                "来源预算不足，子问题 "
                + "、".join(str(i) for i in starved)
                + " 未分配来源；可提高 --max-sources"
            )

        for sq in targets:
            ns = added.get(sq.id, [])
            sq.source_ns = sorted(set(sq.source_ns + ns))
            sq.rounds += 1
            ctx.bus.emit(
                "fetch.done",
                "read",
                {"subquestion": sq.id, "sources": ns, "failed": [list(f) for f in failures[:5]]},
            )
        for url, reason in failures[:5]:
            ctx.notes.append(f"读取失败：{url}（{reason}）")
        return sum(len(v) for v in added.values())

    def _search_many(self, jobs: list[tuple[SubQuestion, str]]):
        """并发执行多条检索，单条失败不影响整体。"""
        async def one(sq: SubQuestion, query: str, sem: asyncio.Semaphore):
            async with sem:
                try:
                    hits = await asyncio.to_thread(self.searcher.search, query, SEARCH_RESULTS)
                    return sq, query, hits, None
                except Exception as exc:  # 单条检索失败不影响整体
                    return sq, query, [], f"{type(exc).__name__}: {exc}"

        async def main():
            sem = asyncio.Semaphore(4)
            return await asyncio.gather(*[one(sq, q, sem) for sq, q in jobs])

        return asyncio.run(main())

    # --------------------------------------------------------------- analyze
    def _analyze(self, ctx: RunContext, subquestions: list[SubQuestion], round_no: int) -> None:
        """证据 → 要点、引用编号与覆盖度。"""
        evidence: dict[int, list[Source]] = {}
        for sq in subquestions:
            pool = [s for s in ctx.sources if sq.id in s.subquestion_ids]
            if not pool:
                pool = ctx.sources[-3:]
            evidence[sq.id] = sorted(pool, key=lambda s: s.n)[:5]

        if not any(evidence.values()):
            ctx.notes.append("本轮没有任何可用正文，跳过分析")
            for sq in subquestions:
                sq.coverage = "gap"
                sq.gap = sq.gap or "检索未获得正文"
            self._emit_analysis(ctx, subquestions, round_no, 0)
            return

        messages = analyze_messages(ctx.topic, round_no, subquestions, evidence, snippet_chars=800)
        res = None
        for attempt, limit in ((1, 4500), (2, 8000)):
            try:
                candidate = self._chat(
                    ctx,
                    messages
                    if attempt == 1
                    else messages
                    + [
                        {
                            "role": "user",
                            "content": "只输出最终 JSON，不要展开推理；每条 key_points 控制在 60 字内。",
                        }
                    ],
                    model=self.settings.analyze_model_resolved,
                    max_tokens=limit,
                    temperature=0.2 if attempt == 1 else 0.1,
                    json_mode=True,
                    stage="analysis" if attempt == 1 else "",
                )
            except LLMError as exc:
                ctx.notes.append(f"分析调用失败（第 {attempt} 次）：{str(exc)[:120]}")
                continue
            if candidate.finish_reason == "length" and attempt == 1:
                ctx.notes.append("分析输出被截断，加大 token 上限重试")
                continue
            res = candidate
            break

        if res is None:
            # 分析失败不拖垮整次研究：降级为 gap，报告仍由证据清单驱动
            ctx.notes.append("分析模型两次失败，按 gap 继续写作")
            for sq in subquestions:
                sq.coverage = "gap"
                sq.gap = sq.gap or "分析模型不可用"
            self._emit_analysis(ctx, subquestions, round_no, 0)
            return

        try:
            data = extract_json(res.text)
            items = data.get("items") if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise JSONParseError("分析结果缺少 items")
        except JSONParseError:
            raw_path = ctx.run.write_text(f"debug/analysis_raw_r{round_no}.txt", res.text)
            ctx.notes.append(f"分析结果解析失败，原始输出见 {raw_path.name}")
            for sq in subquestions:
                sq.coverage = "gap"
                sq.gap = "分析输出无法解析"
            self._emit_analysis(ctx, subquestions, round_no, res.usage.total_tokens)
            return

        by_id: dict[int, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict) and str(item.get("id", "")).strip().isdigit():
                by_id[int(item["id"])] = item

        valid_ns = {s.n for s in ctx.sources}
        for sq in subquestions:
            item = by_id.get(sq.id)
            if not item:
                sq.coverage = sq.coverage or "gap"
                continue
            coverage = str(item.get("coverage") or "partial")
            sq.coverage = coverage if coverage in ("enough", "partial", "gap") else "partial"  # type: ignore[assignment]
            points = [str(p).strip() for p in (item.get("key_points") or []) if str(p).strip()]
            sq.key_points = points[:4]
            gap_text = item.get("gap")
            sq.gap = str(gap_text).strip() if gap_text else None
            sq.source_ns = sorted({int(n) for n in (item.get("evidence_ns") or []) if n in valid_ns})
            if not sq.key_points and sq.coverage == "enough":
                sq.coverage = "partial"

        self._emit_analysis(ctx, subquestions, round_no, res.usage.total_tokens)

    def _emit_analysis(
        self, ctx: RunContext, subquestions: list[SubQuestion], round_no: int, tokens: int
    ) -> None:
        """统一发出 analysis.done 事件。"""
        ctx.bus.emit(
            "analysis.done",
            "analysis",
            {
                "round": round_no,
                "items": [
                    {
                        "id": sq.id,
                        "coverage": sq.coverage,
                        "key_points": sq.key_points,
                        "gap": sq.gap,
                    }
                    for sq in subquestions
                ],
            },
            tokens=tokens,
        )

    # ---------------------------------------------------------------- replan
    def _replan(self, ctx: RunContext, subquestions: list[SubQuestion]) -> None:
        """为覆盖不足的子问题补充检索式。"""
        gaps = [sq for sq in subquestions if sq.coverage != "enough" and sq.gap]
        if not gaps:
            return
        try:
            res = self._chat(
                ctx,
                replan_messages(ctx.topic, gaps),
                model=self.settings.plan_model_resolved,
                max_tokens=3000,
                json_mode=True,
                stage="replan",
            )
            data = extract_json(res.text)
            items = data.get("queries") if isinstance(data, dict) else data
            by_id = {
                int(it["id"]): it
                for it in (items or [])
                if isinstance(it, dict) and str(it.get("id", "")).isdigit()
            }
            updates = []
            for sq in gaps:
                item = by_id.get(sq.id)
                if not item:
                    continue
                new_queries = [
                    str(q).strip()
                    for q in (item.get("queries") or [])
                    if str(q).strip() and str(q).strip() not in sq.queries
                ]
                if new_queries:
                    sq.queries = sq.queries + new_queries[:2]
                    updates.append({"id": sq.id, "queries": new_queries[:2]})
            ctx.bus.emit("replan", "replan", {"queries": updates, "tokens": res.usage.total_tokens})
        except (JSONParseError, LLMError) as exc:
            ctx.notes.append(f"补充检索规划失败：{exc}")

    # ----------------------------------------------------------------- write
    def _write(self, ctx: RunContext, subquestions: list[SubQuestion]):
        """生成报告、注入并重排引用、复核链接。"""
        if not ctx.sources:
            raise LLMError("没有任何可用来源，无法生成报告")

        ctx.bus.emit(
            "write.started",
            "write",
            {"sources": len(ctx.sources), "model": self.settings.write_model_resolved},
        )
        res = self._chat(
            ctx,
            write_messages(ctx.topic, subquestions, ctx.sources),
            model=self.settings.write_model_resolved,
            max_tokens=6000,
            temperature=0.5,
            stage="write",
        )

        body = res.text.strip()
        body, cited, dropped = finalize_report(body, ctx.sources)

        # 引用复核：只影响 sources.json 的 link_status 与提示，不改报告
        statuses = check_urls([s.url for s in cited]) if self.verify_links else {}
        for src in cited:
            src.link_status = statuses.get(src.url, "unchecked")
        bad = [
            s
            for s in cited
            if self.verify_links and s.link_status != "unchecked" and not is_ok(s.link_status)
        ]
        if bad:
            ctx.notes.append(
                f"{len(bad)} 篇来源复核未通过（403/超时，多为反爬或已失效，需人工确认）："
                + ", ".join(s.domain for s in bad[:3])
            )
        header = (
            f"# {ctx.topic}\n\n"
            f"> 研究日期：{datetime.now().strftime('%Y-%m-%d')} ｜ "
            f"来源：{len(cited)} 篇 ｜ 模型：{res.model}\n\n"
        )
        report = header + body

        chars = count_chinese_chars(report)
        if chars < 800:
            ctx.notes.append(f"报告偏短（{chars} 字），建议检查提示词或证据量")
        if res.finish_reason == "length":
            ctx.notes.append("写作输出被 max_tokens 截断，报告可能不完整")
        leaked = strip_urls_for_check(report)
        if leaked:
            ctx.notes.append(f"正文出现 {len(leaked)} 个 URL（应由参考资料承载）")
        if not cited:
            ctx.notes.append("报告没有任何有效引用")
        return report, cited, dropped

    # ------------------------------------------------------------- plumbing
    def _chat(
        self,
        ctx: RunContext,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float = 0.3,
        json_mode: bool = False,
        stage: str = "",
    ):
        """统一的模型调用入口：预算检查、用量累计、事件上报。"""
        ctx.budget.check()
        res = self.llm.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        ctx.usage.merge(res.usage)
        ctx.budget.spend_tokens(res.usage.total_tokens)
        if stage:
            ctx.bus.emit(
                f"{stage}.llm",
                stage,
                {
                    "model": res.model,
                    "latency_ms": res.latency_ms,
                    "finish": res.finish_reason,
                    "tokens": res.usage.model_dump(),
                },
                duration_ms=res.latency_ms,
                tokens=res.usage.total_tokens,
            )
        return res

    def _finalize_files(
        self,
        ctx: RunContext,
        subquestions: list[SubQuestion],
        report: str,
        cited: list[Source],
    ) -> None:
        """落盘 report / sources / subquestions 三个产物。"""
        ctx.run.write_text("report.md", report)
        ctx.run.write_json("sources.json", [s.model_dump() for s in cited])
        ctx.run.write_json("subquestions.json", [sq.model_dump() for sq in subquestions])


__all__ = ["ResearchAgent", "RunContext", "best_excerpt"]
