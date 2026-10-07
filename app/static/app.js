/* 条款草案协作台 —— 零依赖单页前端 */
"use strict";

const $view = document.getElementById("view");
const $toast = document.getElementById("toast");
const $author = document.getElementById("globalAuthor");
$author.value = localStorage.getItem("clause_author") || "";
$author.addEventListener("input", () => localStorage.setItem("clause_author", $author.value.trim()));

function author() {
  const a = $author.value.trim();
  if (!a) { toast("请先在右上角填写编辑者署名", true); throw new Error("no author"); }
  return a;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function toast(msg, isErr) {
  $toast.textContent = msg;
  $toast.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => $toast.classList.add("hidden"), 4000);
  $toast.classList.remove("hidden");
}
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(url, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.message || `HTTP ${res.status}`), { data });
  return data;
}
function kindLabel(k) { return { replace: "替换", delete: "删除", comment: "意见" }[k] || k; }
function resLabel(r) { return { keep: "保留当前", take: "采用提案", custom: "合并文本", applied: "直接生效" }[r] || r || "—"; }

// ---------------------------------------------------------------- 路由
window.addEventListener("hashchange", route);
function route() {
  const h = location.hash || "#/";
  if (h === "#/" || h === "") return renderHome();
  let m = h.match(/^#\/drafts\/(\d+)(?:\/(snapshots|diff|revisions))?/);
  if (m) return renderDraft(+m[1], m[2] || "clauses");
  return renderHome();
}

// ---------------------------------------------------------------- 首页
async function renderHome() {
  let drafts = [];
  try { drafts = (await api("GET", "/api/drafts")).drafts; } catch (e) { toast(e.message, true); }
  const rows = drafts.map(d => `
    <a class="draft-card" href="#/drafts/${d.id}">
      <div class="t">${esc(d.title)}</div>
      <div class="muted">创建者：${esc(d.author)} · 当前 v${d.head_version}</div>
      <div style="margin-top:6px">
        ${d.pending_count ? `<span class="badge pending">待决 ${d.pending_count}</span>` : `<span class="badge applied">无待决</span>`}
        <span class="badge v">已发布 ${d.snapshot_count}</span>
      </div>
    </a>`).join("") || `<p class="muted">还没有草案，在下方创建第一份。</p>`;

  $view.innerHTML = `
    <div class="panel">
      <h2>新建草案</h2>
      <p class="muted">条款编号一经创建即稳定不变；删除后编号作废、不复用、不转挂。每行一条，格式：<code>编号|正文</code></p>
      <label>草案标题</label>
      <input type="text" id="f-title" placeholder="例如：XX 接口规范 v1（草案）">
      <label>初始条款（每行 <code>编号|正文</code>）</label>
      <textarea id="f-clauses" rows="6" placeholder="1|术语定义……&#10;2|接口应以……&#10;A.1|附录条款……"></textarea>
      <div class="btn-row"><button id="f-submit">创建草案</button></div>
    </div>
    <h2>草案列表</h2><div class="cards">${rows}</div>`;

  document.getElementById("f-submit").onclick = async () => {
    try {
      const who = author();
      const title = document.getElementById("f-title").value.trim();
      const lines = document.getElementById("f-clauses").value.split("\n").map(s => s.trim()).filter(Boolean);
      const clauses = lines.map(line => {
        const idx = line.indexOf("|");
        if (idx <= 0) throw new Error(`行格式错误（应为 编号|正文）：${line.slice(0, 30)}`);
        return { stable_no: line.slice(0, idx).trim(), body: line.slice(idx + 1).trim() };
      });
      const d = await api("POST", "/api/drafts", { title, author: who, clauses });
      location.hash = `#/drafts/${d.id}`;
    } catch (e) { toast(e.data?.message || e.message, true); }
  };
}

// ---------------------------------------------------------------- 草案页
async function renderDraft(id, tab) {
  let d;
  try { d = await api("GET", `/api/drafts/${id}`); }
  catch (e) { $view.innerHTML = `<p class="muted">${esc(e.message)}</p><a class="back" href="#/">← 返回列表</a>`; return; }

  const tabs = [
    ["clauses", "当前草稿"],
    ["pending", `待决 ${d.pending_count ? `(${d.pending_count})` : ""}`],
    ["diff", "版本差异"],
    ["revisions", "修订来源"],
    ["snapshots", "发布快照"],
  ].map(([k, label]) =>
    `<a href="#/drafts/${id}/${k === "clauses" ? "" : k}" class="${tab === k ? "active" : ""}">${label}</a>`).join("");

  $view.innerHTML = `
    <a class="back" href="#/">← 草案列表</a>
    <div class="panel" style="margin-top:10px">
      <h1>${esc(d.title)}</h1>
      <div class="meta-grid">
        <span>版本 <b>v${d.head_version}</b></span>
        <span>创建者 ${esc(d.author)}</span>
        <span>创建于 ${esc(d.created_at)}</span>
        <span>${d.published_at ? `最近发布 ${esc(d.published_at)}` : "尚未发布"}</span>
      </div>
      <div class="btn-row">
        <button id="publish" ${d.pending_count ? "disabled" : ""}>${d.pending_count ? "有待决，禁止发布" : "发布不可变快照"}</button>
        <span class="muted" id="pub-hint">${d.pending_count ? "所有待决提案结案后才能发布" : "发布后快照冻结，后续修改只影响草稿"}</span>
      </div>
    </div>
    <div class="tabs">${tabs}</div>
    <div id="tab-body"></div>`;

  document.getElementById("publish").onclick = async () => {
    try {
      const who = author();
      const snap = await api("POST", `/api/drafts/${id}/publish`, { publisher: who });
      toast(`已发布 v${snap.version} 不可变快照（#${snap.snapshot_id}）`);
      renderDraft(id, "snapshots");
    } catch (e) { toast(e.data?.message || e.message, true); }
  };

  if (tab === "clauses") renderClauses(d);
  if (tab === "pending") renderPending(d);
  if (tab === "diff") renderDiffTab(d);
  if (tab === "revisions") renderRevisions(d);
  if (tab === "snapshots") renderSnapshotsTab(d);
}

function baselineCtl(d) {
  let opts = "";
  for (let v = 1; v <= d.head_version; v++) opts += `<option value="${v}" ${v === d.head_version ? "selected" : ""}>v${v}（当前）</option>`;
  return `<select class="baseline">${opts}</select>`;
}

function renderClauses(d) {
  const body = document.getElementById("tab-body");
  body.innerHTML = `<p class="muted">提交修改时请确认“所见版本”。你对其他条款的修改与之并行、自动合并；同一条款被他人先行改动时，你的提案将进入待决，不会覆盖前写。</p>` +
    d.clauses.map(c => clauseCard(d, c)).join("");

  body.querySelectorAll(".clause").forEach(el => {
    const no = el.dataset.no;
    const form = el.querySelector(".mini-form");
    const c = d.clauses.find(x => x.stable_no === no);
    el.querySelector("[data-act=replace]")?.addEventListener("click", () => {
      form.classList.add("open");
      form.dataset.mode = "replace";
      form.querySelector(".form-title").textContent = `替换 ${no}（当前 v${d.head_version}）`;
      form.querySelector("textarea").value = c.is_active ? c.body : "";
    });
    el.querySelector("[data-act=comment]")?.addEventListener("click", () => {
      form.classList.add("open");
      form.dataset.mode = "comment";
      form.querySelector(".form-title").textContent = `对 ${no} 提意见（附着稳定编号）`;
      form.querySelector("textarea").value = "";
    });
    el.querySelector("[data-act=delete]")?.addEventListener("click", async () => {
      if (!confirm(`确认删除条款 ${no}？编号将作废，原意见保留原引用并标记失效。`)) return;
      await submitProposal(d.id, { kind: "delete", stable_no: no, author: author(),
        baseline_version: +el.querySelector(".baseline").value });
    });
    form.querySelector("[data-act=cancel]").onclick = () => form.classList.remove("open");
    form.querySelector("[data-act=ok]").onclick = async () => {
      const mode = form.dataset.mode;
      const base = +el.querySelector(".baseline").value;
      const text = form.querySelector("textarea").value;
      if (mode === "replace" && !text.trim()) return toast("替换正文不能为空", true);
      if (mode === "comment" && !text.trim()) return toast("意见内容不能为空", true);
      const payload = mode === "comment"
        ? { kind: "comment", stable_no: no, author: author(), baseline_version: base, content: text }
        : { kind: "replace", stable_no: no, author: author(), baseline_version: base, body: text };
      await submitProposal(d.id, payload);
    };
  });
}

async function submitProposal(draftId, payload) {
  try {
    const r = await api("POST", `/api/drafts/${draftId}/proposals`, payload);
    if (r.pending) { toast("该条款已有并行改动，提案已进入待决等待裁决 →"); location.hash = `#/drafts/${draftId}/pending`; }
    else { toast("已生效"); location.hash = `#/drafts/${draftId}/`; }
    route();
  } catch (e) { toast(e.data?.message || e.message, true); }
}

function clauseCard(d, c) {
  const comments = c.comments.map(m => `
    <div class="comment ${m.is_active ? "" : "invalid"}">
      <div class="who">${esc(m.author)} · ${esc(m.created_at)}
        ${m.is_active ? "" : `<span class="badge invalid">条款已删除 · 意见失效（原引用 ${esc(m.ref_stable_no)}，未转挂）</span>`}
      </div>
      <div>${esc(m.content)}</div>
    </div>`).join("");
  const sources = c.sources.slice(1).map(s =>
    `<span class="src-line">v${s.draft_version} ${kindLabel(s.kind)} by ${esc(s.author || "—")}（提案 #${s.source_proposal_id || "初始"}）</span>`
  ).join("<br>");
  return `
  <div class="clause ${c.is_active ? "" : "deleted"}" data-no="${esc(c.stable_no)}">
    <div class="clause-head">
      <span class="clause-no">${esc(c.stable_no)}</span>
      ${c.is_active ? `<span class="badge v">rev ${c.current_rev}</span>` : `<span class="badge deleted">已删除 · 编号作废</span>`}
      <span class="spacer"></span>
      <label style="margin:0;font-size:12px">所见版本</label>
      ${baselineCtl(d)}
      <button class="secondary" data-act="replace" ${c.is_active ? "" : "disabled"}>替换</button>
      <button class="secondary" data-act="comment" ${c.is_active ? "" : "disabled"}>意见</button>
      <button class="danger" data-act="delete" ${c.is_active ? "" : "disabled"}>删除</button>
    </div>
    <div class="clause-body">${esc(c.body)}</div>
    ${sources ? `<div style="padding:6px 14px">${sources}</div>` : ""}
    <div class="comments">
      <div class="muted" style="font-size:12px">附着意见（${c.comments.length}）</div>
      ${comments}
      <div class="mini-form">
        <div class="form-title" style="font-weight:650;margin:8px 0 4px"></div>
        <textarea placeholder="正文…"></textarea>
        <div class="btn-row"><button data-act="ok">提交</button><button class="secondary" data-act="cancel">取消</button></div>
      </div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- 待决
function renderPending(d) {
  const body = document.getElementById("tab-body");
  if (!d.pending.length) {
    body.innerHTML = `<div class="panel"><p>✅ 当前没有待决提案，可以发布。</p></div>`;
    return;
  }
  body.innerHTML = `<p class="muted">同一基线上触及同一条款的并行改动在此并列保留。由编辑者选择<b>保留当前 / 采用提案 / 填写合并文本</b>结案；系统不会以后写覆盖前写。</p>` +
    d.pending.map(p => {
      const theirs = p.kind === "delete"
        ? `<span class="badge deleted">删除该条款（编号作废，意见保留失效标记）</span>`
        : esc(p.body);
      const current = p.current_active
        ? esc(p.current_body)
        : `<span class="badge deleted">当前条款已被删除</span>`;
      return `
      <div class="pending-item" data-pid="${p.id}">
        <div>
          <b>提案 #${p.id}</b> · 条款 <span class="clause-no">${esc(p.stable_no)}</span>
          <span class="badge pending">${kindLabel(p.kind)}</span>
          <span class="muted">${esc(p.author)} 基于 v${p.baseline_version} 提交于 ${esc(p.created_at)}${p.note ? "；" + esc(p.note) : ""}</span>
        </div>
        <div class="compare">
          <div class="box"><div class="cap">当前草稿（前写，受保护）</div>${current}</div>
          <div class="box"><div class="cap">本提案（后写）</div>${theirs}</div>
        </div>
        <label>合并文本（选“填写合并文本”时生效；若条款被删，此文本会以原编号恢复条款）</label>
        <textarea class="merge-text" rows="4" placeholder="人工合并后的最终条款正文…"></textarea>
        <div class="btn-row">
          <button class="secondary" data-res="keep">保留当前（放弃提案）</button>
          <button class="secondary" data-res="take">采用提案</button>
          <button data-res="custom">填写合并文本并结案</button>
          <span class="muted">结案编辑者：${esc($author.value || "（未署名）")}</span>
        </div>
      </div>`;
    }).join("");

  body.querySelectorAll(".pending-item").forEach(el => {
    const pid = +el.dataset.pid;
    el.querySelectorAll("[data-res]").forEach(btn => btn.onclick = async () => {
      try {
        const res = btn.dataset.res;
        const payload = { resolution: res, editor: author() };
        if (res === "custom") {
          const text = el.querySelector(".merge-text").value;
          if (!text.trim()) return toast("请填写合并文本", true);
          payload.body = text;
        }
        await api("POST", `/api/drafts/${d.id}/proposals/${pid}/resolve`, payload);
        toast("待决已结案");
        route();
      } catch (e) { toast(e.data?.message || e.message, true); }
    });
  });
}

// ---------------------------------------------------------------- 差异
function renderDiffTab(d) {
  const body = document.getElementById("tab-body");
  const opts = (sel) => {
    let s = `<option value="0">v0（空基线）</option>`;
    for (let v = 1; v <= d.head_version; v++) s += `<option value="${v}" ${v === sel ? "selected" : ""}>v${v}${v === d.head_version ? "（当前）" : ""}</option>`;
    return s;
  };
  body.innerHTML = `
    <div class="panel">
      <div class="row">
        <div><label>旧版本</label><select id="diff-from">${opts(Math.max(0, d.head_version - 1))}</select></div>
        <div><label>新版本</label><select id="diff-to">${opts(d.head_version)}</select></div>
      </div>
      <div class="btn-row"><button id="diff-go">生成差异</button></div>
    </div>
    <div id="diff-out"></div>`;
  const run = async () => {
    const frm = +document.getElementById("diff-from").value;
    const to = +document.getElementById("diff-to").value;
    if (frm >= to) return toast("旧版本必须小于新版本", true);
    try {
      const r = await api("GET", `/api/drafts/${d.id}/diff?from=${frm}&to=${to}`);
      const out = document.getElementById("diff-out");
      if (!r.changes.length) { out.innerHTML = `<p class="muted">两个版本之间无变化。</p>`; return; }
      out.innerHTML = r.changes.map(ch => {
        const badge = { added: "applied", deleted: "deleted", modified: "pending" }[ch.change];
        const label = { added: "新增", deleted: "删除", modified: "修改" }[ch.change];
        return `<div class="diff-file">${esc(ch.stable_no)} <span class="badge ${badge}">${label}</span></div>
          <pre class="diff">${colorDiff(ch.diff)}</pre>`;
      }).join("");
    } catch (e) { toast(e.data?.message || e.message, true); }
  };
  document.getElementById("diff-go").onclick = run;
  run();
}
function colorDiff(patch) {
  return esc(patch).split("\n").map(line => {
    if (line.startsWith("+++") || line.startsWith("---")) return `<span class="hunk">${line}</span>`;
    if (line.startsWith("@@")) return `<span class="hunk">${line}</span>`;
    if (line.startsWith("+")) return `<span class="add">${line}</span>`;
    if (line.startsWith("-")) return `<span class="del">${line}</span>`;
    return line;
  }).join("\n");
}

// ---------------------------------------------------------------- 修订来源
async function renderRevisions(d) {
  const body = document.getElementById("tab-body");
  let revs = [];
  try { revs = (await api("GET", `/api/drafts/${d.id}/revisions`)).revisions; } catch (e) { return toast(e.message, true); }
  const rows = revs.map(r => `
    <tr>
      <td><span class="clause-no">${esc(r.stable_no)}</span></td>
      <td><span class="badge ${r.kind === "delete" ? "deleted" : "v"}">${kindLabel(r.kind)}</span></td>
      <td>v${r.draft_version} / rev ${r.rev}</td>
      <td>${esc(r.author || "—")}${r.source_proposal_id ? ` <span class="muted">（提案 #${r.source_proposal_id}）</span>` : `<span class="muted">（建稿）</span>`}</td>
      <td class="muted">${esc(r.created_at)}</td>
    </tr>`).join("");
  body.innerHTML = `
    <div class="panel">
      <h2>逐次修订与来源</h2>
      <table><thead><tr><th>稳定编号</th><th>动作</th><th>版本/修订号</th><th>来源</th><th>时间</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="muted">暂无修订记录</td></tr>`}</tbody></table>
      <h3>提案/意见流水</h3>
      ${historyTable(d)}
    </div>`;
}
function historyTable(d) {
  const rows = d.history.map(p => `
    <tr>
      <td>#${p.id}</td>
      <td>${kindLabel(p.kind)}</td>
      <td><span class="clause-no">${esc(p.stable_no)}</span></td>
      <td>${esc(p.author)}</td>
      <td>v${p.baseline_version}</td>
      <td><span class="badge ${p.status}">${p.status === "applied" ? "生效" : "结案"}</span> <span class="muted">${resLabel(p.resolution)}</span></td>
      <td class="muted">${esc(p.applied_at || p.resolved_at || p.created_at)}</td>
    </tr>`).join("");
  return `<table><thead><tr><th>#</th><th>类型</th><th>编号</th><th>提交者</th><th>基线</th><th>状态</th><th>时间</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="7" class="muted">暂无记录</td></tr>`}</tbody></table>`;
}

// ---------------------------------------------------------------- 快照
async function renderSnapshotsTab(d) {
  const body = document.getElementById("tab-body");
  let snaps = [];
  try { snaps = (await api("GET", `/api/drafts/${d.id}/snapshots`)).snapshots; } catch (e) { return toast(e.message, true); }
  if (!snaps.length) {
    body.innerHTML = `<div class="panel"><p class="muted">尚未发布。待决清零后点击上方“发布不可变快照”。</p></div>`;
    return;
  }
  body.innerHTML = snaps.map(s => `
    <div class="panel">
      <div class="snapshot-hero">
        <b>快照 #${s.id}</b> · 版本 v${s.version}
        <span class="badge immutable">不可变</span>
        <div class="muted">${esc(s.title)} · 发布者 ${esc(s.published_by)} · ${esc(s.created_at)}</div>
      </div>
      <div id="snap-${s.id}"><p class="muted">加载中…</p></div>
    </div>`).join("");
  for (const s of snaps) {
    try {
      const snap = await api("GET", `/api/drafts/${d.id}/snapshots/${s.id}`);
      const rows = snap.clauses.map(c =>
        `<tr><td class="clause-no">${esc(c.stable_no)}</td><td style="white-space:pre-wrap">${esc(c.body)}</td><td>rev ${c.rev}</td></tr>`).join("");
      document.getElementById(`snap-${s.id}`).innerHTML =
        `<table><thead><tr><th>稳定编号</th><th>冻结正文</th><th>修订号</th></tr></thead><tbody>${rows}</tbody></table>`;
    } catch (e) { /* 单个快照失败不阻塞其余 */ }
  }
}

route();
