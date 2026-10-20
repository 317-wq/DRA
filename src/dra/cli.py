"""CLI：dra run / show / list。

终端里的进度只看事件流，和 0.2 的 SSE 消费同一份数据。
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import deque
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dra import __version__
from dra.agent import ResearchAgent
from dra.budget import Budget
from dra.config import ConfigWriteError, Settings, config_path
from dra.onboarding import PRESETS, collect_checks, failed_critical, save_config
from dra.store import RunStore

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Deep Research Agent —— 输入主题，产出带引用的研究报告。",
)
# Windows 的 GBK 控制台或管道无法编码 ✓ 等字符：降级为替换，避免直接崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

console = Console()

COVERAGE_ICON = {"enough": "●", "partial": "◐", "gap": "○", None: "·"}
COVERAGE_COLOR = {"enough": "green", "partial": "yellow", "gap": "red", None: "dim"}


class LiveView:
    """把事件流渲染成一块会自己刷新的面板。"""

    def __init__(self, topic: str, models: dict[str, str]) -> None:
        """初始化实时视图的初始状态。"""
        self.topic = topic
        self.models = models
        self.subquestions: list[dict] = []
        self.logs: deque[str] = deque(maxlen=7)
        self.sources = 0
        self.tokens = 0
        self.calls = 0
        self.status = "启动中"
        self.live: Live | None = None
        self.started = time.monotonic()

    def __call__(self, ev) -> None:
        """接收事件并刷新面板。"""
        p = ev.payload or {}
        if ev.type == "run.started":
            self.status = "拆题"
        elif ev.type == "plan.ready":
            self.subquestions = [
                {"id": s["id"], "question": s["question"], "coverage": None, "sources": 0}
                for s in p.get("subquestions", [])
            ]
            self.log(f"拆出 {len(self.subquestions)} 个子问题")
        elif ev.type == "search.done":
            if p.get("ok"):
                self.log(f"检索 · {p['query'][:34]} → {p['hits']} 条")
            else:
                self.log(f"检索失败 · {p['query'][:24]}（{str(p.get('error'))[:40]}）")
        elif ev.type == "fetch.done":
            ns = p.get("sources") or []
            if ns:
                self.sources += len(ns)
            self.log(f"读取 · 子问题 {p.get('subquestion')} 新增 {len(ns)} 篇")
        elif ev.type == "analysis.done":
            by_id = {it["id"]: it for it in p.get("items", [])}
            for sq in self.subquestions:
                item = by_id.get(sq["id"])
                if item:
                    sq["coverage"] = item.get("coverage")
            self.log(f"分析完成（第 {p.get('round')} 轮）")
            self.status = "写作" if all(s["coverage"] == "enough" for s in self.subquestions) else "补检索"
        elif ev.type == "replan":
            self.log("覆盖不足，重新规划检索式")
        elif ev.type in ("plan.llm", "analysis.llm", "replan.llm", "write.llm"):
            tok = p.get("tokens", {}).get("total_tokens", 0)
            self.tokens += tok
            self.calls += 1
            self.log(f"{p.get('model')} · {p.get('latency_ms', 0) / 1000:.1f}s · {tok} tok")
        elif ev.type == "run.finished":
            self.status = "完成" if p.get("status") == "done" else "部分完成"
        elif ev.type == "run.failed":
            self.status = "失败"
            self.log(f"失败：{(p.get('notes') or [''])[-1][:60]}")

        if self.live is not None:
            self.live.update(self.render())

    def log(self, line: str) -> None:
        """追加一行事件日志（保留最近若干条）。"""
        self.logs.append(line)

    def render(self):
        """渲染标题、子问题表与事件流面板。"""
        head = Text.assemble(
            ("主题  ", "dim"),
            (self.topic, "bold"),
            ("\n"),
            ("阶段  ", "dim"),
            (self.status, "cyan"),
            ("    来源  ", "dim"),
            (str(self.sources), "bold"),
            ("    LLM  ", "dim"),
            (f"{self.calls} 次 / {self.tokens} tok", ""),
            ("    用时  ", "dim"),
            (f"{time.monotonic() - self.started:.0f}s", ""),
        )

        table = Table(box=None, pad_edge=False, show_header=False, expand=True)
        table.add_column(width=3, no_wrap=True)
        table.add_column(ratio=1)
        table.add_column(width=10, no_wrap=True)
        for sq in self.subquestions:
            cov = sq["coverage"]
            table.add_row(
                Text(COVERAGE_ICON.get(cov, "·"), style=COVERAGE_COLOR.get(cov, "dim")),
                Text(sq["question"], overflow="ellipsis", no_wrap=True),
                Text(cov or "待分析", style=COVERAGE_COLOR.get(cov, "dim")),
            )

        body = Group(head, Text(""), table)
        log_panel = Panel(
            Text("\n".join(self.logs) or "等待事件…", style="dim"),
            title="事件流",
            border_style="bright_black",
            height=9,
        )
        return Group(
            Panel(body, title=f"Deep Research · {self.models.get('write', '')}", border_style="blue"),
            log_panel,
        )


def _print_run_summary(result, settings: Settings) -> None:
    """打印研究结束摘要面板。"""
    ok = result.status == "done"
    color = "green" if ok else ("yellow" if result.status == "partial" else "red")
    table = Table(box=None, show_header=False, pad_edge=False, expand=False)
    table.add_column(style="dim", width=10)
    table.add_column()
    table.add_row("run", result.run_id)
    table.add_row("状态", Text(result.status, style=color))
    table.add_row("耗时", f"{result.duration_s}s")
    table.add_row("来源", f"{len(result.sources)} 篇")
    table.add_row("引用", ", ".join(f"[{s.n}]" for s in result.sources) or "无")
    table.add_row("token", f"{result.usage.total_tokens}（{result.usage.calls} 次调用）")
    table.add_row("覆盖", "  ".join(f"{sq.id}:{sq.coverage or '-'}" for sq in result.subquestions))
    table.add_row("产物", str(Path(result.report_path).parent))
    for note in result.notes:
        table.add_row("提示", Text(note, style="yellow"))
    console.print(Panel(table, title="研究结束", border_style=color))
    if result.sources:
        console.print(f"\n[dim]查看报告：[/dim]dra show {result.run_id}")
        console.print(f"[dim]完整轨迹：[/dim]dra show {result.run_id} --trace\n")


@app.command()
def run(
    topic: str = typer.Argument(..., help="研究主题"),
    max_sources: int = typer.Option(12, "--max-sources", "-s", help="最多保留多少篇来源"),
    max_seconds: float = typer.Option(240, "--max-seconds", help="墙钟时间上限（秒）"),
    max_tool_calls: int = typer.Option(30, "--max-tool-calls", help="工具调用次数上限"),
    max_rounds: int = typer.Option(2, "--max-rounds", help="检索-分析轮次上限"),
    writer: str = typer.Option("", "--writer", help="覆盖写作模型"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="不渲染实时面板，只在结束时输出摘要"),
) -> None:
    """发起一次研究。"""
    settings = Settings()
    if writer:
        settings.writer_model = writer
    store = RunStore(settings.runs_dir)
    models = {
        "plan": settings.plan_model_resolved,
        "analyze": settings.analyze_model_resolved,
        "write": settings.write_model_resolved,
    }

    budget = Budget(
        max_tool_calls=max_tool_calls,
        max_seconds=max_seconds,
        max_rounds=max_rounds,
    )
    agent = ResearchAgent(settings, store=store)

    if quiet or not console.is_terminal:
        def on_event(ev) -> None:
            """非交互模式下的简化事件输出。"""
            if ev.type in ("plan.ready", "analysis.done", "run.finished", "run.failed"):
                console.print(f"[dim]{ev.type}[/dim] {json.dumps(ev.payload, ensure_ascii=False)[:220]}")

        result = agent.run(topic, budget=budget, max_sources=max_sources, on_event=on_event)
    else:
        view = LiveView(topic, models)
        with Live(view.render(), console=console, refresh_per_second=6, transient=False) as live:
            view.live = live
            result = agent.run(topic, budget=budget, max_sources=max_sources, on_event=view)
            live.update(view.render())

    _print_run_summary(result, settings)
    if result.status == "failed":
        raise typer.Exit(code=1)


@app.command()
def show(
    run_id: str = typer.Argument("", help="run id；留空则看最近一次"),
    trace: bool = typer.Option(False, "--trace", "-t", help="打印完整事件时间线"),
    raw: bool = typer.Option(False, "--raw", help="输出原始 Markdown 而不是渲染结果"),
) -> None:
    """查看某次研究的报告与轨迹。"""
    settings = Settings()
    store = RunStore(settings.runs_dir)
    if not run_id:
        runs = store.list()
        if not runs:
            console.print("[red]还没有任何研究记录[/red]")
            raise typer.Exit(code=1)
        run_id = runs[0]["run_id"]

    run = store.open(run_id)
    meta_path = run.dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    report_path = run.dir / "report.md"

    header = Table(box=None, show_header=False, pad_edge=False)
    header.add_column(style="dim", width=10)
    header.add_column()
    header.add_row("主题", meta.get("topic", run.topic))
    header.add_row("状态", str(meta.get("status", "?")))
    header.add_row("时间", f"{meta.get('started_at', '')} → {meta.get('finished_at', '')}")
    header.add_row("来源", str(len(meta.get("sources", []))))
    console.print(Panel(header, title=run_id, border_style="blue"))

    if report_path.exists():
        text = report_path.read_text(encoding="utf-8")
        if raw:
            console.print(text)
        else:
            console.print(Markdown(text))
    else:
        console.print("[yellow]这次运行没有生成报告[/yellow]")

    if meta.get("notes"):
        console.print("\n[bold]提示[/bold]")
        for note in meta["notes"]:
            console.print(f"  [yellow]· {note}[/yellow]")

    if trace:
        _print_trace(run.trace_path)


def _print_trace(path: Path) -> None:
    """把 trace.jsonl 渲染成时间线表格。"""
    if not path.exists():
        console.print("[yellow]没有 trace 文件[/yellow]")
        return
    table = Table(title="执行轨迹", box=None, pad_edge=False)
    table.add_column("seq", justify="right", style="dim", width=4)
    table.add_column("阶段", width=10)
    table.add_column("事件", width=16)
    table.add_column("详情", overflow="fold")
    table.add_column("耗时", justify="right", width=8)
    table.add_column("tok", justify="right", width=8)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        detail = _trace_detail(ev)
        table.add_row(
            str(ev.get("seq", "")),
            ev.get("stage", ""),
            ev.get("type", ""),
            detail,
            f"{ev['duration_ms'] / 1000:.1f}s" if ev.get("duration_ms") else "",
            str(ev.get("tokens") or ""),
        )
    console.print(table)


def _trace_detail(ev: dict) -> str:
    """把单个事件压缩成一行可读详情。"""
    p = ev.get("payload") or {}
    t = ev.get("type", "")
    if t == "plan.ready":
        return f"{len(p.get('subquestions', []))} 个子问题"
    if t == "search.done":
        return f"{p.get('query', '')[:40]} → {p.get('hits')} 条"
    if t == "fetch.done":
        return f"子问题 {p.get('subquestion')} 新增 {len(p.get('sources') or [])} 篇"
    if t == "analysis.done":
        return " ".join(f"{i['id']}:{i['coverage']}" for i in p.get("items", []))
    if t == "replan":
        return f"{len(p.get('queries', []))} 个子问题补检索"
    if t.endswith(".llm"):
        return f"{p.get('model')} {p.get('finish')} {p.get('tokens', {}).get('total_tokens', 0)} tok"
    if t == "run.finished":
        return f"{p.get('status')} · {p.get('sources')} 篇 · {p.get('chars')} 字"
    return json.dumps(p, ensure_ascii=False)[:80]


@app.command("list")
def list_runs() -> None:
    """列出历史研究。"""
    settings = Settings()
    items = RunStore(settings.runs_dir).list()
    if not items:
        console.print("[dim]还没有研究记录[/dim]")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("run", style="cyan")
    table.add_column("状态")
    table.add_column("主题", overflow="ellipsis", max_width=50)
    table.add_column("来源", justify="right")
    table.add_column("耗时", justify="right")
    table.add_column("时间", style="dim")
    for it in items:
        status = it.get("status", "?")
        table.add_row(
            it.get("run_id", ""),
            Text(status, style={"done": "green", "partial": "yellow", "failed": "red"}.get(status, "")),
            it.get("topic", ""),
            str(len(it.get("sources", []))),
            f"{it.get('duration_s', 0)}s",
            (it.get("started_at") or "")[:16],
        )
    console.print(table)


@app.command()
def ui(
    port: int = typer.Option(8765, "--port", help="起始端口（被占用会自动往后找）"),
    no_browser: bool = typer.Option(False, "--no-browser", help="不自动打开浏览器"),
) -> None:
    """启动本地界面（只监听 127.0.0.1）。"""
    from dra.web.server import serve

    serve(Settings(), port=port, open_browser=not no_browser)


@app.command()
def ask(
    question: str = typer.Argument(..., help="追问内容"),
    run_id: str = typer.Option("", "--run", "-r", help="父研究 run id；留空取最近一次"),
    k: int = typer.Option(6, "--k", help="从父研究里选多少篇证据"),
) -> None:
    """基于某次研究追问；默认复用父 run 证据，不重新检索、不消耗搜索额度。"""
    settings = Settings()
    settings.require_llm()
    store = RunStore(settings.runs_dir)
    if not run_id:
        runs = [item for item in store.list() if item.get("status") != "running"]
        if not runs:
            console.print("[red]还没有可追问的研究[/red]")
            raise typer.Exit(code=1)
        run_id = runs[0]["run_id"]

    from dra.followup import FollowupEngine

    engine = FollowupEngine(settings, store)
    try:
        if console.is_terminal:
            with console.status("基于已有证据回答…"):
                result = engine.ask(run_id, question, k=k)
        else:
            result = engine.ask(run_id, question, k=k)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    console.print(Markdown(result.answer))
    console.print(f"\n[dim]已保存：{result.path}（{result.duration_s}s）[/dim]")


kb_app = typer.Typer(help="本地知识库：导入报告、关键词 + 向量混合检索。", no_args_is_help=True)
app.add_typer(kb_app, name="kb")


def _open_kb(settings: Settings):
    from dra.kb import KnowledgeBase
    from dra.llm import ModelAdapter

    return KnowledgeBase(settings, adapter=ModelAdapter(settings))


@kb_app.command("add")
def kb_add(run_id: str = typer.Argument("", help="run id；留空取最近一次")) -> None:
    """把一次研究的报告导入知识库（需要 embedding，成本很低）。"""
    settings = Settings()
    settings.require_llm()
    store = RunStore(settings.runs_dir)
    if not run_id:
        runs = store.list()
        if not runs:
            console.print("[red]还没有研究记录[/red]")
            raise typer.Exit(code=1)
        run_id = runs[0]["run_id"]
    try:
        run = store.open(run_id)
    except FileNotFoundError:
        console.print(f"[red]找不到 run：{run_id}[/red]")
        raise typer.Exit(code=1) from None
    report = run.dir / "report.md"
    if not report.exists():
        console.print("[red]这次运行没有报告，无法导入[/red]")
        raise typer.Exit(code=1)
    meta_path = run.dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    kb = _open_kb(settings)
    try:
        doc_id, chunks = kb.add_report(
            run_id=run_id,
            title=meta.get("topic", run_id),
            text=report.read_text(encoding="utf-8"),
            source_path=str(report),
        )
    finally:
        kb.close()
    if chunks == 0:
        console.print(f"[yellow]内容已在知识库中（doc {doc_id}），未重复导入[/yellow]")
    else:
        console.print(f"[green]已导入[/green] doc {doc_id} · {chunks} 块")


@kb_app.command("import")
def kb_import(limit: int = typer.Option(0, "--limit", help="最多导入多少个 run；0 表示全部")) -> None:
    """批量导入历史研究（已导入的会按内容哈希跳过）。"""
    settings = Settings()
    settings.require_llm()
    store = RunStore(settings.runs_dir)
    runs = store.list()
    if limit > 0:
        runs = runs[:limit]
    if not runs:
        console.print("[dim]没有可导入的研究[/dim]")
        return
    kb = _open_kb(settings)
    added = skipped = failed = 0
    try:
        for meta in runs:
            run_id = meta.get("run_id", "")
            try:
                run = store.open(run_id)
                report = run.dir / "report.md"
                if not report.exists():
                    failed += 1
                    continue
                _, chunks = kb.add_report(
                    run_id=run_id,
                    title=meta.get("topic", run_id),
                    text=report.read_text(encoding="utf-8"),
                    source_path=str(report),
                )
                if chunks:
                    added += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1
    finally:
        kb.close()
    console.print(f"导入 {added} · 跳过 {skipped} · 失败 {failed}")


@kb_app.command("list")
def kb_list() -> None:
    """列出知识库里的文档。"""
    settings = Settings()
    kb = _open_kb(settings)
    try:
        docs = kb.list_documents()
    finally:
        kb.close()
    if not docs:
        console.print("[dim]知识库是空的。先用 dra kb add 或 dra kb import 导入报告。[/dim]")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("id", justify="right", width=4)
    table.add_column("标题", overflow="ellipsis", max_width=48)
    table.add_column("块数", justify="right")
    table.add_column("已向量化", justify="right")
    table.add_column("来源 run", style="dim")
    for doc in docs:
        table.add_row(
            str(doc["id"]),
            doc["title"],
            str(doc["chunk_count"]),
            str(doc["embedded"]),
            doc.get("run_id") or "-",
        )
    console.print(table)


@kb_app.command("search")
def kb_search(
    query: str = typer.Argument(..., help="检索词"),
    k: int = typer.Option(6, "--k", help="返回多少块"),
) -> None:
    """BM25 + 向量混合检索，RRF 融合排序。"""
    settings = Settings()
    kb = _open_kb(settings)
    try:
        hits, info = kb.search(query, k=k)
    finally:
        kb.close()
    if not hits:
        console.print(f"[dim]没有命中（mode={info.get('mode')}）。[/dim]")
        return
    console.print(
        f"[dim]mode={info.get('mode')} · 关键词命中 {info.get('keyword_hits')} · "
        f"向量命中 {info.get('vector_hits')}[/dim]"
    )
    for index, hit in enumerate(hits, start=1):
        snippet = re.sub(r"\s+", " ", hit.text)[:180]
        console.print(
            f"\n[bold]{index}. {hit.title}[/bold] [dim]· chunk {hit.seq} · "
            f"score {hit.score:.4f} (kw {hit.keyword:.2f} / vec {hit.vector:.2f})[/dim]"
        )
        console.print(f"   {snippet}")


@kb_app.command("show")
def kb_show(doc_id: int = typer.Argument(..., help="文档 id")) -> None:
    """查看一份文档的分块。"""
    settings = Settings()
    kb = _open_kb(settings)
    try:
        doc = kb.get_document(doc_id)
    finally:
        kb.close()
    if not doc:
        console.print(f"[red]找不到文档 {doc_id}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{doc['title']}[/bold]  [dim]{doc.get('source_path') or ''}[/dim]")
    console.print(f"[dim]{len(doc['chunks'])} 块[/dim]")
    for chunk in doc["chunks"]:
        console.print(f"\n[cyan]#{chunk['seq']}[/cyan] [dim]{chunk['chars']} 字[/dim]")
        console.print(chunk["text"][:400])


@kb_app.command("remove")
def kb_remove(
    doc_id: int = typer.Argument(..., help="文档 id"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """从知识库删除一份文档。"""
    if not yes and not typer.confirm(f"确认删除文档 {doc_id}？", default=False):
        raise typer.Exit(code=1)
    settings = Settings()
    kb = _open_kb(settings)
    try:
        ok = kb.remove(doc_id)
    finally:
        kb.close()
    console.print("[green]已删除[/green]" if ok else "[yellow]没有找到该文档[/yellow]")


@app.command()
def version() -> None:
    """版本号。"""
    console.print(f"dra {__version__}")


@app.command()
def init(
    base_url: str = typer.Option("", "--base-url", help="OpenAI 兼容端点"),
    api_key: str = typer.Option("", "--api-key", help="模型 API Key"),
    model: str = typer.Option("", "--model", help="默认模型名"),
    tavily_key: str = typer.Option("", "--tavily-key", help="Tavily API Key"),
    embedding_model: str = typer.Option("", "--embedding-model", help="向量模型（可选）"),
    runs_dir: str = typer.Option("", "--runs-dir", help="产物目录（默认用户数据目录）"),
    preset: str = typer.Option("", "--preset", help="dashscope / deepseek / openai / custom"),
    yes: bool = typer.Option(False, "--yes", "-y", help="非交互：用参数与默认值直接写入"),
    force: bool = typer.Option(False, "--force", help="覆盖已有配置"),
) -> None:
    """生成用户配置：模型端点、密钥与检索密钥（BYOK，密钥只存本机）。"""
    path = config_path()
    interactive = sys.stdin.isatty() and not yes

    if path.exists() and not force:
        if not interactive:
            console.print(f"[yellow]配置已存在：{path}；如需覆盖请加 --force[/yellow]")
            raise typer.Exit(code=1)
        if not typer.confirm(f"配置已存在（{path}），覆盖？", default=False):
            console.print("[yellow]已取消[/yellow]")
            raise typer.Exit(code=1)

    if preset and preset not in PRESETS:
        console.print(f"[red]未知预设：{preset}（可选：{', '.join(PRESETS)}）[/red]")
        raise typer.Exit(code=1)

    chosen = PRESETS.get(preset or "dashscope", PRESETS["dashscope"])

    if interactive and not preset:
        table = Table(title="选择模型服务", box=None, pad_edge=False)
        table.add_column("#", width=3)
        table.add_column("预设")
        table.add_column("端点", style="dim")
        for i, item in enumerate(PRESETS.values(), start=1):
            table.add_row(str(i), item["label"], item["base_url"] or "（手动填写）")
        console.print(table)
        raw = typer.prompt("选择", default="1")
        try:
            keys = list(PRESETS)
            chosen = PRESETS[keys[(int(raw) - 1) % len(keys)]]
        except (ValueError, IndexError):
            console.print("[red]无效选择[/red]")
            raise typer.Exit(code=1) from None

    default_base = base_url or chosen["base_url"]
    default_model = model or chosen["model"]

    if interactive:
        try:
            base_url = typer.prompt("模型端点 Base URL", default=default_base)
            api_key = typer.prompt(
                "模型 API Key（输入不回显）", hide_input=True, default=None
            ) or ""
            model = typer.prompt("默认模型", default=default_model)
            embedding_model = typer.prompt(
                "向量模型（可留空）", default=embedding_model or chosen.get("embedding_model", "")
            )
            tavily_key = typer.prompt(
                "Tavily API Key（输入不回显）", hide_input=True, default=tavily_key or ""
            )
            runs_dir = typer.prompt("产物目录", default=runs_dir or str(Settings().runs_dir))
        except (typer.Abort, EOFError, KeyboardInterrupt):
            console.print("\n[yellow]已取消，未写入任何配置[/yellow]")
            raise typer.Exit(code=1) from None
    else:
        base_url = base_url or default_base
        model = model or default_model
        embedding_model = embedding_model or chosen.get("embedding_model", "")

    if not base_url.startswith(("http://", "https://")):
        console.print("[red]Base URL 必须以 http:// 或 https:// 开头[/red]")
        raise typer.Exit(code=1)
    if not api_key:
        console.print("[red]缺少模型 API Key。非交互模式请加 --api-key，或去掉 --yes 交互填写[/red]")
        raise typer.Exit(code=1)

    summary = Table(box=None, show_header=False, pad_edge=False)
    summary.add_column(style="dim", width=10)
    summary.add_column()
    summary.add_row("配置", str(path))
    summary.add_row("端点", base_url)
    summary.add_row("模型", model)
    summary.add_row("密钥", "已填写")
    summary.add_row("检索", "已填写" if tavily_key else "未填写（dra run 会提示）")
    summary.add_row("产物", runs_dir or str(Settings().runs_dir))
    console.print(Panel(summary, title="即将写入", border_style="blue"))

    if interactive and not typer.confirm("确认写入？", default=True):
        console.print("[yellow]已取消[/yellow]")
        raise typer.Exit(code=1)

    try:
        written = save_config(
            base_url=base_url,
            api_key=api_key,
            model=model,
            tavily_api_key=tavily_key,
            embedding_model=embedding_model,
            runs_dir=runs_dir,
        )
    except ConfigWriteError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"[green]已写入[/green] {written}  [dim](权限 0600)[/dim]")
    console.print("\n下一步：")
    console.print("  dra doctor        [dim]检查 Key 与网络是否可用[/dim]")
    console.print('  dra run "研究主题"')


@app.command()
def doctor() -> None:
    """自检：配置文件、密钥、模型端点、对话、检索与数据目录。"""
    settings = Settings()
    if console.is_terminal:
        with console.status("检查中（会消耗极少量额度）..."):
            checks = collect_checks(settings)
    else:
        checks = collect_checks(settings)

    table = Table(title="dra doctor", box=None, pad_edge=False)
    table.add_column("检查", width=10)
    table.add_column("结果", width=4)
    table.add_column("详情", overflow="fold")
    for check in checks:
        if check.ok:
            icon = Text("✓", style="green")
        elif check.critical:
            icon = Text("✗", style="red")
        else:
            icon = Text("⚠", style="yellow")
        table.add_row(check.name, icon, check.detail)
    console.print(table)

    summary = Table(box=None, show_header=False, pad_edge=False)
    summary.add_column(style="dim", width=10)
    summary.add_column()
    for key, value in settings.public_summary().items():
        summary.add_row(key, str(value))
    console.print(Panel(summary, title="当前配置（已脱敏）", border_style="bright_black"))

    bad = failed_critical(checks)
    if bad:
        console.print(f"[red]{len(bad)} 项检查未通过，修复后再运行 dra run[/red]")
        raise typer.Exit(code=1)
    console.print("[green]全部关键检查通过[/green]")


if __name__ == "__main__":
    app()
