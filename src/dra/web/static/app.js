const STAGES = [
  { key: "plan", name: "拆解问题", desc: "拆成可独立检索的子问题" },
  { key: "search", name: "检索资料", desc: "多源检索与 URL 去重" },
  { key: "read", name: "读取正文", desc: "批量提取正文，失败降级抓取" },
  { key: "analyze", name: "分析与收敛", desc: "要点、引用编号与覆盖度" },
  { key: "write", name: "生成报告", desc: "注入引用并复核链接" },
];

const state = {
  runId: null,
  source: null,
  poll: null,
  sources: 0,
  subqs: [],
  coverage: {},
  tab: "report",
  reportHtml: "",
  sourceList: [],
  trace: [],
  traceKeys: new Set(),
  evalTried: false,
  meta: null,
  followups: [],
  evalData: null,
};

const $ = (sel) => document.querySelector(sel);

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clock(iso) {
  if (!iso) return "--:--:--";
  const m = String(iso).match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : iso;
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json();
}

/* ---------------- stages ---------------- */
function renderStages() {
  const wrap = $("#stages");
  wrap.innerHTML = "";
  STAGES.forEach((stage, index) => {
    const row = el("div", "stage");
    row.dataset.index = index;
    row.appendChild(el("div", "node", String(index + 1)));
    const body = el("div", "body");
    body.appendChild(el("div", "name", stage.name));
    const sub = el("div", "sub", stage.desc);
    sub.dataset.sub = "1";
    body.appendChild(sub);
    row.appendChild(body);
    wrap.appendChild(row);
  });
}

function markStage(index, status) {
  const rows = document.querySelectorAll("#stages .stage");
  const row = rows[index];
  if (!row) return;
  if (status === "active") {
    for (let i = 0; i < index; i += 1) rows[i].className = "stage done";
  }
  row.className = `stage ${status}`;
}

function setStageNote(index, text) {
  const row = document.querySelectorAll("#stages .stage")[index];
  if (!row) return;
  const sub = row.querySelector(".sub");
  if (sub) sub.textContent = text;
}

function allDone() {
  document.querySelectorAll("#stages .stage").forEach((row) => {
    row.className = "stage done";
  });
}

/* ---------------- subquestions ---------------- */
function renderSubqs() {
  const wrap = $("#subqList");
  if (!wrap) return;
  wrap.innerHTML = "";
  state.subqs.forEach((sq) => {
    const row = el("div", "subq");
    const cov = state.coverage[sq.id];
    const badge = el("span", `cov ${cov || ""}`, cov || "待分析");
    row.appendChild(badge);
    row.appendChild(el("span", "", sq.question));
    wrap.appendChild(row);
  });
}

/* ---------------- logs ---------------- */
function logLine(ev) {
  const box = $("#logs");
  const row = el("div", "log");
  row.appendChild(el("span", "time", clock(ev.ts)));
  row.appendChild(el("span", "msg", describe(ev)));
  box.appendChild(row);
  while (box.childElementCount > 180) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function describe(ev) {
  const p = ev.payload || {};
  switch (ev.type) {
    case "plan.ready":
      return `拆出 ${(p.subquestions || []).length} 个子问题`;
    case "search.done":
      return p.ok ? `检索「${p.query}」→ ${p.hits} 条` : `检索失败「${p.query}」`;
    case "fetch.done":
      return `子问题 ${p.subquestion ?? "-"} 新增 ${(p.sources || []).length} 篇正文`;
    case "analysis.done":
      return `第 ${p.round} 轮分析：` + (p.items || []).map((i) => `${i.id}:${i.coverage}`).join("  ");
    case "replan":
      return `覆盖不足，为 ${(p.queries || []).length} 个子问题补检索`;
    case "write.started":
      return `开始写作（${p.model}）`;
    case "write.verify":
      return `数字校对：可核验率 ${Math.round((p.support_rate || 0) * 100)}% · ${p.action}`;
    case "write.verified":
      return `质量评估：${p.score} 分（${p.grade}）· 可核验率 ${Math.round((p.support_rate || 0) * 100)}%`;
    case "plan.reused":
      return `继承父报告，补 ${(p.questions || []).length} 个新问题`;
    case "run.finished":
      return `结束：${p.status} · ${p.sources} 篇 · ${p.chars} 字`;
    case "run.failed":
      return `失败：${(p.notes || []).slice(-1)[0] || "未知原因"}`;
    default:
      if (ev.type.endsWith(".llm")) {
        return `${p.model} · ${p.latency_ms}ms · ${p.tokens?.total_tokens || 0} tok`;
      }
      return ev.type;
  }
}

/* ---------------- event handling ---------------- */
function traceKey(ev) {
  const payload = JSON.stringify(ev.payload || {}).slice(0, 120);
  return `${ev.ts || ""}|${ev.type || ""}|${ev.seq ?? ""}|${payload}`;
}

function rememberTrace(ev) {
  const key = traceKey(ev);
  if (state.traceKeys.has(key)) return;
  state.traceKeys.add(key);
  state.trace.push({ ...ev, seq: ev.seq ?? state.trace.length + 1 });
  while (state.trace.length > 600) state.trace.shift();
  if (state.tab === "trace") renderTrace(true);
}

function handle(ev) {
  rememberTrace(ev);
  logLine(ev);
  const p = ev.payload || {};
  switch (ev.type) {
    case "run.started":
      setBadge("running", "研究中");
      markStage(0, "active");
      break;
    case "plan.ready":
      state.subqs = p.subquestions || [];
      renderSubqs();
      markStage(0, "done");
      markStage(1, "active");
      setStageNote(0, `${state.subqs.length} 个子问题`);
      break;
    case "search.done":
      markStage(1, "active");
      if (p.ok) setStageNote(1, `最近：${String(p.query).slice(0, 28)} → ${p.hits} 条`);
      break;
    case "fetch.done":
      if ((p.sources || []).length) {
        state.sources += p.sources.length;
        setStageNote(2, `已登记 ${state.sources} 篇来源`);
      }
      markStage(2, "active");
      break;
    case "analysis.done":
      (p.items || []).forEach((item) => { state.coverage[item.id] = item.coverage; });
      renderSubqs();
      markStage(2, "done");
      markStage(3, "active");
      break;
    case "replan":
      markStage(3, "active");
      break;
    case "write.started":
      markStage(3, "done");
      markStage(4, "active");
      break;
    case "write.verified":
      setStageNote(4, `质量 ${p.score ?? "-"} 分（${p.grade || "-"}）`);
      break;
    case "run.finished":
      finish(p);
      break;
    case "run.failed":
      fail(p);
      break;
    default:
      break;
  }
}

function statusLabel(status) {
  return {
    done: "完成",
    partial: "部分完成",
    failed: "失败",
    running: "研究中",
    queued: "排队中",
    cancelled: "已取消",
    interrupted: "已中断",
  }[status] || status;
}

function setBadge(cls, text) {
  const badge = $("#runBadge");
  badge.className = `badge ${cls}`;
  badge.textContent = text;
}

function closeStream() {
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  if (state.poll) {
    clearInterval(state.poll);
    state.poll = null;
  }
}

function finish(payload) {
  closeStream();
  allDone();
  state.sources = Array.isArray(payload.sources)
    ? payload.sources.length
    : (payload.sources ?? state.sources);
  const status = payload.status || "partial";
  setBadge(status, statusLabel(status));
  $("#startBtn").disabled = false;
  $("#hint").textContent = "本次研究已结束，可以继续开新主题。";
  if (state.runId) {
    loadRun(state.runId, { keepStages: true });
  }
}

function fail(payload) {
  closeStream();
  setBadge("failed", "失败");
  const note = (payload.notes || []).slice(-1)[0] || "未知错误";
  $("#panel").innerHTML = `<div class="empty">研究失败<br>${escapeHtml(note)}</div>`;
}

function follow(runId) {
  state.runId = runId;
  state.source = new EventSource(`/api/runs/${runId}/events`);
  state.source.onmessage = (event) => {
    try { handle(JSON.parse(event.data)); } catch (e) { /* 忽略半行 */ }
  };
  state.source.onerror = () => {
    if (state.source && state.source.readyState === EventSource.CLOSED) {
      pollUntilDone(runId);
    }
  };
}

function pollUntilDone(runId) {
  if (state.poll) return;
  state.poll = setInterval(async () => {
    try {
      const meta = await api(`/api/runs/${runId}`);
      if (meta.status && meta.status !== "running") {
        clearInterval(state.poll);
        state.poll = null;
        finish(meta);
      }
    } catch (e) { /* 继续等 */ }
  }, 2000);
}

/* ---------------- run loading & panels ---------------- */
async function loadRun(runId, options = {}) {
  closeStream();
  state.runId = runId;
  state.coverage = {};
  const [meta, sources, trace, followups, evaluation] = await Promise.all([
    api(`/api/runs/${runId}`),
    api(`/api/runs/${runId}/sources`).catch(() => []),
    api(`/api/runs/${runId}/trace`).catch(() => []),
    api(`/api/runs/${runId}/followups`).catch(() => ({ items: [] })),
    api(`/api/runs/${runId}/eval`).catch(() => null),
  ]);
  state.meta = meta;
  state.sourceList = sources;
  state.trace = trace;
  state.traceKeys = new Set(trace.map(traceKey));
  state.evalData = evaluation;
  state.evalTried = false;
  state.followups = followups.items || [];
  state.sources = sources.length;
  state.reportHtml = "";
  $("#viewTitle").textContent = meta.topic || "研究";
  const status = meta.status || "running";
  setBadge(status, statusLabel(status));

  if (!options.keepStages) {
    replayStages(trace, status);
  }
  $("#logs").innerHTML = "";
  if (status !== "running" && status !== "queued") {
    trace.forEach(logLine);
  }
  if (status !== "running") {
    state.reportHtml = await fetch(`/api/runs/${runId}/report`).then((r) => r.ok ? r.text() : "");
  }
  renderPanel();
  renderHistory();
  if (status === "running" || status === "queued") {
    follow(runId);
  }
}

function replayStages(trace, status = "") {
  renderStages();
  state.subqs = [];
  state.coverage = {};
  state.sources = 0;
  trace.forEach((ev) => {
    const p = ev.payload || {};
    switch (ev.type) {
      case "plan.ready":
        state.subqs = p.subquestions || [];
        markStage(0, "done");
        markStage(1, "active");
        setStageNote(0, `${state.subqs.length} 个子问题`);
        break;
      case "search.done":
        markStage(1, "active");
        if (p.ok) setStageNote(1, `最近：${String(p.query).slice(0, 28)} → ${p.hits} 条`);
        break;
      case "fetch.done":
        state.sources += (p.sources || []).length;
        markStage(2, "active");
        setStageNote(2, `已登记 ${state.sources} 篇来源`);
        break;
      case "analysis.done":
        (p.items || []).forEach((i) => { state.coverage[i.id] = i.coverage; });
        markStage(2, "done");
        markStage(3, "active");
        break;
      case "write.started":
        markStage(3, "done");
        markStage(4, "active");
        break;
      case "run.finished":
        allDone();
        break;
      default:
        break;
    }
  });
  renderSubqs();
  if (status && status !== "running" && status !== "queued") allDone();
}

function renderPanel() {
  const panel = $("#panel");
  if (state.tab === "report") {
    if (!state.reportHtml) {
      const status = state.meta?.status || "";
      const notes = (state.meta?.notes || []).map((note) => `· ${escapeHtml(note)}`).join("<br>");
      const text = status === "running" || status === "queued"
        ? "研究进行中，报告生成后自动显示。"
        : `状态：${escapeHtml(statusLabel(status))}`;
      panel.innerHTML = `<div class="empty">${text}${notes ? `<br>${notes}` : ""}</div>`;
      return;
    }
    const meta = state.meta || {};
    const actions = [];
    if (meta.status === "running" || meta.status === "queued") {
      actions.push(`<button id="cancelBtn" class="ghost-btn">取消</button>`);
    } else {
      actions.push(`<button id="retryBtn" class="ghost-btn">重试</button>`);
    }
    actions.push(`<button id="exportBtn" class="ghost-btn">导出 MD</button>`);
    actions.push(`<button id="sinkBtn" class="ghost-btn">沉淀到知识库</button>`);
    const head = `<div class="meta">
      <span>${escapeHtml(meta.started_at || "")}</span>
      <span>${state.sourceList.length} 篇来源</span>
      <span>${meta.usage?.total_tokens || 0} token</span>
      <span>${meta.duration_s || 0}s</span>
      <div class="meta-actions">${actions.join("")}</div>
    </div>`;
    panel.innerHTML = head + `<div class="report">${state.reportHtml}</div>`;
    const sinkBtn = $("#sinkBtn");
    if (sinkBtn) sinkBtn.onclick = sinkCurrent;
    const cancelBtn = $("#cancelBtn");
    if (cancelBtn) cancelBtn.onclick = cancelCurrent;
    const retryBtn = $("#retryBtn");
    if (retryBtn) retryBtn.onclick = retryCurrent;
    const exportBtn = $("#exportBtn");
    if (exportBtn) exportBtn.onclick = () => {
      if (state.runId) window.open(`/api/runs/${state.runId}/export?format=md`, "_blank");
    };
    return;
  }

  if (state.tab === "sources") {
    if (!state.sourceList.length) {
      panel.innerHTML = `<div class="empty">还没有来源。研究完成后，这里按引用编号列出原文链接。</div>`;
      return;
    }
    const rows = state.sourceList.map((s) => {
      const good = /^[23]/.test(String(s.link_status || ""));
      const pill = s.link_status && s.link_status !== "unchecked"
        ? `<span class="pill ${good ? "ok" : "bad"}">${escapeHtml(s.link_status)}</span>` : "";
      return `<tr>
        <td>${s.n}</td>
        <td><a href="${escapeHtml(s.url)}" target="_blank" rel="noreferrer">${escapeHtml(s.title)}</a><br>
            <span class="pill">${escapeHtml(s.domain)}</span> ${pill}</td>
      </tr>`;
    }).join("");
    panel.innerHTML = `<table class="src-table"><thead><tr><th>#</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table>`;
    return;
  }

  if (state.tab === "quality") {
    renderQuality();
    return;
  }

  if (state.tab === "followups") {
    renderFollowups();
    return;
  }

  if (state.tab === "trace") {
    renderTrace(false);
  }
}

function renderTrace(autoScroll = false) {
  const panel = $("#panel");
  const status = state.meta?.status || "";
  const live = status === "running" || status === "queued";
  if (!state.trace.length) {
    panel.innerHTML = `<div class="empty">${live ? "正在等待第一个事件…" : "这次运行没有留下轨迹。"}</div>`;
    return;
  }
  const rows = state.trace.map((ev) => `<tr>
      <td class="num">${ev.seq ?? ""}</td>
      <td class="time">${escapeHtml(clock(ev.ts))}</td>
      <td><span class="stage-pill">${escapeHtml(ev.stage || "-")}</span></td>
      <td class="ev">${escapeHtml(ev.type || "")}</td>
      <td>${escapeHtml(describe(ev))}</td>
      <td class="num">${ev.duration_ms ? (ev.duration_ms / 1000).toFixed(1) + "s" : ""}</td>
    </tr>`).join("");
  const liveBadge = live ? `<span class="live-pill"><i></i>实时</span>` : "";
  panel.innerHTML = `<div class="trace-head">
      <span>共 ${state.trace.length} 条事件</span>${liveBadge}
    </div>
    <table class="trace-table">
      <thead><tr><th>#</th><th>时间</th><th>阶段</th><th>事件</th><th>详情</th><th>耗时</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  if (autoScroll) panel.scrollTop = panel.scrollHeight;
}

/* ---------------- quality ---------------- */
async function refreshEval() {
  if (!state.runId) return;
  try {
    state.evalData = await api(`/api/runs/${state.runId}/eval`);
  } catch (e) {
    toast(`质量评估失败：${e.message}`, true);
  }
  renderPanel();
}

function renderQuality() {
  const panel = $("#panel");
  const data = state.evalData;
  if (!data) {
    const status = state.meta?.status || "";
    const running = status === "running" || status === "queued";
    if (running) {
      panel.innerHTML = `<div class="quality-wait">
        <div class="spinner"></div>
        <b>研究报告生成后自动计算质量</b>
        <span>引用覆盖 · 数字可核验 · 来源多样性</span>
      </div>`;
      return;
    }
    panel.innerHTML = `<div class="quality-wait">
      <b>还没有质量评估</b>
      <span>报告已生成，可以立即评估一次。</span>
      <button id="evalBtn" class="primary">立即评估</button>
    </div>`;
    const btn = $("#evalBtn");
    if (btn) btn.onclick = refreshEval;
    if (!state.evalTried) {
      state.evalTried = true;
      refreshEval();
    }
    return;
  }
  const gradeColor = { A: "#15803d", B: "#2563eb", C: "#b45309", D: "#b91c1c" }[data.grade] || "#334155";
  const warnings = (data.warnings || []).length
    ? `<div class="warn-box">${data.warnings.map((w) => `· ${escapeHtml(w)}`).join("<br>")}</div>`
    : "";
  const unverified = (data.numbers.unverified || []).length
    ? `<div class="unverified">${data.numbers.unverified.map((item) => `<div class="row">
        <span class="num">${escapeHtml(item.number)}</span>
        引用 ${escapeHtml((item.cited || []).join(",") || "无")} · ${escapeHtml(item.sentence)}
      </div>`).join("")}</div>`
    : `<div class="empty">所有抽检数字都能在引用来源中找到。</div>`;
  panel.innerHTML = `
    <div class="score-card">
      <div class="big" style="color:${gradeColor}">${data.score}</div>
      <div><div class="grade">等级 ${data.grade}</div></div>
      <div class="meta">${data.sources.total} 篇来源 · ${data.sources.domains} 个域名<br>
        ${escapeHtml(data.run_id)}</div>
    </div>
    <div class="metric-grid">
      <div class="metric"><div class="k">引用覆盖</div><div class="v">${Math.round(data.citations.citation_rate * 100)}%</div></div>
      <div class="metric"><div class="k">数字可核验</div><div class="v">${Math.round(data.numbers.support_rate * 100)}%</div></div>
      <div class="metric"><div class="k">一手来源占比</div><div class="v">${Math.round(data.sources.tier_avg * 100)}%</div></div>
      <div class="metric"><div class="k">无效引用</div><div class="v">${data.citations.invalid}</div></div>
    </div>
    ${warnings}
    <div class="sec">未在引用来源中找到的数字</div>
    ${unverified}`;
}

/* ---------------- follow-ups ---------------- */
function renderFollowups() {
  const panel = $("#panel");
  if (!state.runId) {
    panel.innerHTML = `<div class="empty">先打开一次研究，再基于它追问。</div>`;
    return;
  }
  const running = ["running", "queued"].includes(state.meta?.status);
  panel.innerHTML = `
    <div class="qa-box">
      <input id="qaInput" placeholder="基于这份报告追问，例如：那成本呢？" ${running ? "disabled" : ""}>
      <div class="qa-row">
        <span class="hint">只复用这份报告的证据，不重新检索</span>
        <button id="qaBtn" class="primary" ${running ? "disabled" : ""}>追问</button>
      </div>
    </div>
    <div id="qaAnswer"></div>
    <div class="sec">历史追问 · ${state.followups.length}</div>
    <div id="qaList"></div>`;
  renderFollowupList();
  const btn = $("#qaBtn");
  if (btn) btn.onclick = askFollowup;
  const input = $("#qaInput");
  if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") askFollowup(); });
}

function renderFollowupList() {
  const wrap = $("#qaList");
  if (!wrap) return;
  if (!state.followups.length) {
    wrap.innerHTML = `<div class="empty">还没有追问。</div>`;
    return;
  }
  wrap.innerHTML = state.followups.map((item) => `<div class="qa-item" data-fid="${item.id}">
      <div class="q">${escapeHtml(item.question)}</div>
      <div class="m">${item.chars} 字 · ${item.duration_s}s · ${(item.cited_ns || []).length} 篇引用</div>
    </div>`).join("");
  wrap.querySelectorAll(".qa-item").forEach((node) => {
    node.onclick = () => openFollowup(node.dataset.fid);
  });
}

async function askFollowup() {
  const input = $("#qaInput");
  const question = input ? input.value.trim() : "";
  if (!question || !state.runId) return;
  const btn = $("#qaBtn");
  if (btn) { btn.disabled = true; btn.textContent = "回答中…"; }
  const box = $("#qaAnswer");
  if (box) box.innerHTML = `<div class="empty">基于已有证据回答中，约 20–40 秒…</div>`;
  try {
    const created = await api(`/api/runs/${state.runId}/followups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await api(`/api/runs/${state.runId}/followups`).catch(() => ({ items: [] }));
    state.followups = data.items || state.followups;
    renderFollowups();
    await openFollowup(created.id);
  } catch (e) {
    if (box) box.innerHTML = `<div class="empty">追问失败：${escapeHtml(e.message)}</div>`;
    if (btn) { btn.disabled = false; btn.textContent = "追问"; }
  }
}

async function openFollowup(followupId) {
  if (!state.runId) return;
  try {
    const data = await api(`/api/runs/${state.runId}/followups/${followupId}`);
    const box = $("#qaAnswer");
    if (box) box.innerHTML = `<div class="report">${data.html}</div>`;
  } catch (e) { toast(`读取追问失败：${e.message}`, true); }
}

/* ---------------- history ---------------- */
async function renderHistory() {
  const wrap = $("#history");
  try {
    const data = await api("/api/runs");
    const runs = data.runs || [];
    if (!runs.length) {
      wrap.innerHTML = `<div class="empty" style="padding:20px 6px">还没有研究记录</div>`;
      return;
    }
    wrap.innerHTML = "";
    runs.forEach((run) => {
      const item = el("div", `hist${run.run_id === state.runId ? " active" : ""}`);
      item.onclick = () => {
        showView("research");
        loadRun(run.run_id);
      };
      const title = el("div", "t", run.topic || run.run_id);
      const meta = el("div", "m");
      const dot = el("span", `dot ${run.status || "running"}`);
      meta.appendChild(dot);
      meta.appendChild(el("span", "", run.status || "running"));
      meta.appendChild(el("span", "", `${(run.sources || []).length} 篇`));
      meta.appendChild(el("span", "", (run.started_at || "").slice(5, 16).replace("T", " ")));
      const del = el("button", "hist-del", "×");
      del.title = "删除这次研究";
      del.onclick = async (event) => {
        event.stopPropagation();
        if (!confirm(`删除「${run.topic || run.run_id}」及其产物？`)) return;
        try {
          await api(`/api/runs/${run.run_id}`, { method: "DELETE" });
          if (state.runId === run.run_id) resetView();
          else renderHistory();
          toast("已删除");
        } catch (e) { toast(`删除失败：${e.message}`, true); }
      };
      item.appendChild(title);
      meta.appendChild(del);
      item.appendChild(meta);
      wrap.appendChild(item);
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty" style="padding:20px 6px">读取历史失败</div>`;
  }
}

/* ---------------- actions ---------------- */
async function start() {
  const topic = $("#topic").value.trim();
  if (!topic) { $("#hint").textContent = "先输入一个研究主题。"; return; }
  const maxSources = Number($("#maxSources").value) || 12;
  closeStream();
  state.subqs = [];
  state.coverage = {};
  state.sources = 0;
  state.reportHtml = "";
  state.trace = [];
  state.traceKeys = new Set();
  state.evalData = null;
  state.evalTried = false;
  renderStages();
  renderSubqs();
  $("#logs").innerHTML = "";
  $("#panel").innerHTML = `<div class="empty">研究已启动，事件流会实时出现在左侧。</div>`;
  $("#startBtn").disabled = true;
  $("#hint").textContent = "研究进行中，通常 1–2 分钟。";
  try {
    const data = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, max_sources: maxSources }),
    });
    state.runId = data.run_id;
    $("#viewTitle").textContent = topic;
    setBadge("running", "研究中");
    follow(data.run_id);
    renderHistory();
  } catch (e) {
    setBadge("failed", "启动失败");
    $("#hint").textContent = e.message;
    $("#startBtn").disabled = false;
  }
}

function selectTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  renderPanel();
}

function resetView() {
  showView("research");
  closeStream();
  state.runId = null;
  state.subqs = [];
  state.coverage = {};
  state.sources = 0;
  state.reportHtml = "";
  state.trace = [];
  state.traceKeys = new Set();
  state.evalTried = false;
  state.sourceList = [];
  state.meta = null;
  state.followups = [];
  state.evalData = null;
  $("#topic").value = "";
  $("#logs").innerHTML = "";
  $("#hint").textContent = "Agent 会自动拆题、检索、交叉验证，并生成带引用的报告。";
  $("#viewTitle").textContent = "研究";
  setBadge("idle", "就绪");
  $("#startBtn").disabled = false;
  $("#panel").innerHTML = `<div class="empty">还没有报告。输入主题开始一次研究。</div>`;
  renderStages();
  renderSubqs();
  renderHistory();
}

/* ---------------- views / history ---------------- */
function showView(name) {
  const views = {
    research: ["研究", "输入主题，Agent 自动拆题、检索并生成可溯源报告"],
    history: ["研究历史", "每次研究自动记录；可以继续研究、追问、沉淀到知识库"],
    kb: ["知识库", "把报告沉淀进来，按关键词与向量混合检索"],
  };
  const meta = views[name] || views.research;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  $("#view-research").hidden = name !== "research";
  $("#view-history").hidden = name !== "history";
  $("#view-kb").hidden = name !== "kb";
  $("#viewTitle").textContent = meta[0];
  $("#viewSubtitle").textContent = meta[1];
  if (name === "kb") loadKb();
  if (name === "history") loadHistoryView();
}

function humanTime(iso) {
  const m = String(iso || "").match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}` : (iso || "");
}

function runCardHtml(run) {
  const status = run.status || "done";
  const followups = run.followup_count || 0;
  return `<article class="run-card" data-id="${run.run_id}">
    <div class="run-top">
      <span class="run-dot ${status}"></span>
      <h3>${escapeHtml(run.topic || run.run_id)}</h3>
      <span class="badge ${status}">${statusLabel(status)}</span>
    </div>
    <div class="run-meta">
      <span>${humanTime(run.started_at)}</span>
      <span>${(run.sources || []).length} 篇来源</span>
      <span>${run.chars || 0} 字</span>
      ${followups ? `<span>${followups} 条追问</span>` : ""}
    </div>
    <div class="run-actions">
      <button data-act="view">查看报告</button>
      <button data-act="continue">继续研究</button>
      <button data-act="ask">基于此追问</button>
      <button data-act="sink">沉淀到知识库</button>
      <button data-act="delete" class="danger">删除</button>
      ${followups ? `<button data-act="tree" class="link">▶ ${followups} 条追问</button>` : ""}
    </div>
    <div class="run-followups" hidden></div>
  </article>`;
}

async function loadHistoryView() {
  const wrap = $("#historyCards");
  wrap.innerHTML = `<div class="empty">读取中…</div>`;
  try {
    const data = await api("/api/runs?limit=100");
    $("#historyCount").textContent = `${data.total || 0} 次研究`;
    const runs = data.runs || [];
    if (!runs.length) {
      wrap.innerHTML = `<div class="empty"><b>还没有研究</b>回到「研究」输入一个主题开始。</div>`;
      return;
    }
    wrap.innerHTML = runs.map(runCardHtml).join("");
    wrap.querySelectorAll(".run-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("button[data-act]").forEach((btn) => {
        btn.onclick = () => historyAction(id, btn.dataset.act, card);
      });
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty">读取历史失败：${escapeHtml(e.message)}</div>`;
  }
}

async function openRun(runId, tab = "report") {
  showView("research");
  await loadRun(runId);
  selectTab(tab);
}

async function historyAction(runId, action, card) {
  if (action === "view") { await openRun(runId, "report"); return; }
  if (action === "ask") {
    await openRun(runId, "followups");
    const input = $("#qaInput");
    if (input) input.focus();
    return;
  }
  if (action === "delete") {
    if (!confirm("删除这次研究及其全部产物？")) return;
    try {
      await api(`/api/runs/${runId}`, { method: "DELETE" });
      toast("已删除");
      loadHistoryView();
      renderHistory();
    } catch (e) { toast(`删除失败：${e.message}`, true); }
    return;
  }
  if (action === "sink") {
    try {
      const res = await api("/api/kb/sink", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId }),
      });
      toast(res.skipped ? "这份报告已在知识库中" : `已沉淀 ${res.chunks} 块`);
    } catch (e) { toast(`沉淀失败：${e.message}`, true); }
    return;
  }
  if (action === "continue") {
    const raw = prompt("补充哪些新问题？每行一个（最多 4 个）");
    if (raw === null) return;
    const questions = raw.split("\n").map((q) => q.trim()).filter(Boolean);
    if (!questions.length) { toast("至少填一个新问题", true); return; }
    try {
      const res = await api(`/api/runs/${runId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ questions, max_sources: 6 }),
      });
      toast("已加入队列，研究完成后自动刷新");
      await openRun(res.run_id, "report");
    } catch (e) { toast(`继续研究失败：${e.message}`, true); }
    return;
  }
  if (action === "tree") {
    const box = card.querySelector(".run-followups");
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="empty">读取追问中…</div>`;
    try {
      const data = await api(`/api/runs/${runId}/followups`);
      const items = data.items || [];
      if (!items.length) { box.innerHTML = `<div class="empty">还没有追问。</div>`; return; }
      box.innerHTML = items.map((item) => `<div class="run-followup" data-fid="${item.id}">
          <span class="q">${escapeHtml(item.question)}</span>
          <span class="m">${item.chars || 0} 字 · ${(item.cited_ns || []).length} 篇引用</span>
        </div>`).join("");
      box.querySelectorAll(".run-followup").forEach((node) => {
        node.onclick = async () => {
          await openRun(runId, "followups");
          await openFollowup(node.dataset.fid);
        };
      });
    } catch (e) { box.innerHTML = `<div class="empty">读取失败：${escapeHtml(e.message)}</div>`; }
  }
}

/* ---------------- knowledge base ---------------- */
async function loadKb() {
  const statsBox = $("#kbStats");
  try {
    const data = await api("/api/kb");
    const stats = data.stats || {};
    statsBox.textContent = `${stats.documents || 0} 篇 · ${stats.chunks || 0} 块 · ${stats.embedded || 0} 已向量化`;
    const docs = data.documents || [];
    const wrap = $("#kbDocs");
    if (!docs.length) {
      wrap.innerHTML = `<div class="empty">知识库是空的。先在报告页点“沉淀到知识库”。</div>`;
      return;
    }
    wrap.innerHTML = docs.map((doc) => `<div class="kb-doc" data-doc="${doc.id}">
        <div class="main"><b>${escapeHtml(doc.title)}</b>
          <div class="m">${doc.chunk_count} 块 · ${doc.embedded} 已向量化 · ${escapeHtml(doc.run_id || "")}</div>
        </div>
        <button class="link-btn" data-del="${doc.id}">删除</button>
      </div>`).join("");
    wrap.querySelectorAll(".kb-doc").forEach((node) => {
      node.onclick = () => loadKbDoc(node.dataset.doc);
    });
    wrap.querySelectorAll("button[data-del]").forEach((btn) => {
      btn.onclick = async (event) => {
        event.stopPropagation();
        if (!confirm("从知识库删除这份文档？")) return;
        try {
          await api(`/api/kb/${btn.dataset.del}`, { method: "DELETE" });
          toast("已删除");
          $("#kbDetail").innerHTML = `<div class="empty">点击左侧文档查看分块与内容。</div>`;
          loadKb();
        } catch (e) { toast(`删除失败：${e.message}`, true); }
      };
    });
  } catch (e) {
    statsBox.textContent = `读取知识库失败：${e.message}`;
  }
}

async function loadKbDoc(docId) {
  const box = $("#kbDetail");
  box.innerHTML = `<div class="empty">读取中…</div>`;
  try {
    const data = await api(`/api/kb/${docId}`);
    const doc = data.document || {};
    const chunks = data.chunks || [];
    box.innerHTML = `<div class="kb-detail-head">
        <h3>${escapeHtml(doc.title)}</h3>
        <div class="kb-detail-meta">
          ${chunks.length} 块 · ${doc.embedded || 0} 已向量化
          ${doc.run_id ? ` · 来自 ${escapeHtml(doc.run_id)}` : ""}
        </div>
      </div>
      <div class="kb-chunks">
        ${chunks.map((c) => `<article class="kb-chunk">
          <div class="ch"><span>#${c.seq}</span><span>${c.chars} 字</span><span>${c.embedded ? "已向量化" : "未向量化"}</span></div>
          <pre>${escapeHtml(c.text)}</pre>
        </article>`).join("")}
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">读取文档失败：${escapeHtml(e.message)}</div>`;
  }
}

async function searchKb() {
  const query = $("#kbQuery").value.trim();
  const box = $("#kbResults");
  if (!query) return;
  box.innerHTML = `<div class="empty">检索中…</div>`;
  try {
    const data = await api(`/api/kb/search?q=${encodeURIComponent(query)}&k=6`);
    if (!data.hits || !data.hits.length) {
      box.innerHTML = `<div class="empty">没有命中。</div>`;
      return;
    }
    const mode = `mode=${data.mode} · 关键词 ${data.keyword_hits} · 向量 ${data.vector_hits}`;
    box.innerHTML = `<div class="kb-stats">${mode}</div>` + data.hits.map((hit) => `<div class="kb-hit" data-doc="${hit.doc_id}">
        <div class="h"><b>${escapeHtml(hit.title)}</b><span>chunk ${hit.seq}</span>
          <span class="score">${hit.score.toFixed(4)} (kw ${hit.keyword.toFixed(2)} / vec ${hit.vector.toFixed(2)})</span></div>
        <div class="txt">${escapeHtml(hit.text.slice(0, 320))}</div>
      </div>`).join("");
    box.querySelectorAll(".kb-hit").forEach((node) => {
      node.onclick = () => loadKbDoc(node.dataset.doc);
    });
  } catch (e) {
    box.innerHTML = `<div class="empty">检索失败：${escapeHtml(e.message)}</div>`;
  }
}

async function cancelCurrent() {
  if (!state.runId) return;
  try {
    const res = await api(`/api/runs/${state.runId}/cancel`, { method: "POST" });
    toast(res.result === "queued" ? "已取消排队任务" : "已请求取消，等待当前步骤结束");
    if (res.result !== "queued") pollUntilDone(state.runId);
  } catch (e) { toast(`取消失败：${e.message}`, true); }
}

async function retryCurrent() {
  if (!state.runId) return;
  const old = state.runId;
  try {
    const res = await api(`/api/runs/${old}/retry`, { method: "POST" });
    closeStream();
    state.subqs = [];
    state.coverage = {};
    state.sources = 0;
    state.reportHtml = "";
    state.evalData = null;
    renderStages();
    renderSubqs();
    $("#logs").innerHTML = "";
    $("#panel").innerHTML = `<div class="empty">已重新排队，等待执行。</div>`;
    state.runId = res.run_id;
    setBadge("queued", "排队中");
    follow(res.run_id);
    renderHistory();
    toast("已重新排队");
  } catch (e) { toast(`重试失败：${e.message}`, true); }
}

async function sinkCurrent() {
  if (!state.runId) return;
  const btn = $("#sinkBtn");
  if (btn) { btn.disabled = true; btn.textContent = "沉淀中…"; }
  try {
    const res = await api("/api/kb/sink", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: state.runId }),
    });
    if (btn) btn.textContent = res.skipped ? "已在知识库" : `已沉淀 · ${res.chunks} 块`;
    toast(res.skipped ? "这份报告已在知识库中" : `已沉淀 ${res.chunks} 块`);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "沉淀到知识库"; }
    toast(`沉淀失败：${e.message}`, true);
  }
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast"; }, 2600);
}

/* ---------------- init ---------------- */
function init() {
  const flow = document.querySelector(".flow");
  const list = el("div", "subq-list");
  list.id = "subqList";
  const logsWrap = document.querySelector(".logs-wrap");
  flow.insertBefore(list, logsWrap);
  renderStages();
  renderHistory();

  $("#startBtn").onclick = start;
  $("#newBtn").onclick = resetView;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.onclick = () => showView(btn.dataset.view);
  });
  $("#kbSearchBtn").onclick = searchKb;
  $("#kbQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchKb();
  });
  $("#topic").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") start();
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => selectTab(tab.dataset.tab);
  });

  const params = new URLSearchParams(location.search);
  const initialView = params.get("view");
  if (initialView) showView(initialView);
  const initialTab = params.get("tab");
  if (initialTab) selectTab(initialTab);
  const initialRun = params.get("run");
  if (initialRun) loadRun(initialRun);
  const initialDoc = params.get("doc");
  if (initialDoc) loadKbDoc(initialDoc);

  window.addEventListener("beforeunload", closeStream);
}

init();
