/* 条款草案协作系统 —— 前端逻辑（原生 JS，无构建步骤） */
const state = {
  drafts: [],
  draftId: null,
  detail: null,
  tab: "draft",
  versions: [],
  pending: [],
  comments: [],
  publications: [],
  viewingPub: null,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function author() {
  return $("#author").value.trim() || "anonymous";
}

function banner(msg, isError = false) {
  const el = $("#banner");
  el.textContent = msg;
  el.className = isError ? "error" : "";
  clearTimeout(banner._t);
  banner._t = setTimeout(() => el.classList.add("hidden"), 5000);
  el.classList.remove("hidden");
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

async function run(fn) {
  try {
    await fn();
  } catch (e) {
    banner(e.message, true);
  }
}

/* ---------------- 草案列表 ---------------- */

async function loadDrafts() {
  state.drafts = await api("/api/drafts");
  const ul = $("#draft-list");
  ul.innerHTML = state.drafts
    .map(
      (d) => `
      <li data-id="${d.id}" class="${d.id === state.draftId ? "active" : ""}">
        <div>${esc(d.title)}</div>
        <div class="meta">v${d.current_version}${
          d.pending_count
            ? ` <span class="badge warn">待决 ${d.pending_count}</span>`
            : ""
        }</div>
      </li>`
    )
    .join("");
  ul.querySelectorAll("li").forEach((li) =>
    li.addEventListener("click", () => selectDraft(Number(li.dataset.id)))
  );
}

async function selectDraft(id) {
  state.draftId = id;
  state.tab = "draft";
  state.viewingPub = null;
  await reloadDetail();
  await loadDrafts();
}

async function reloadDetail() {
  state.detail = await api(`/api/drafts/${state.draftId}`);
  await renderMain();
}

/* ---------------- 主区域 ---------------- */

const TABS = [
  ["draft", "当前草稿"],
  ["versions", "版本与差异"],
  ["pending", "待决冲突"],
  ["comments", "意见"],
  ["publications", "发布快照"],
];

async function renderMain() {
  const d = state.detail;
  const main = $("#main");
  main.innerHTML = `
    <div class="draft-head">
      <h2>${esc(d.title)}</h2>
      <span class="badge">当前 v${d.current_version}</span>
      ${
        d.pending_count
          ? `<span class="badge warn">待决冲突 ${d.pending_count}</span>`
          : `<span class="badge ok">无待决</span>`
      }
      <button class="primary" id="publish-btn" ${
        d.pending_count ? 'disabled title="有待决冲突，不能发布"' : ""
      }>发布快照</button>
    </div>
    <div class="tabs">
      ${TABS.map(
        ([k, label]) =>
          `<button data-tab="${k}" class="${state.tab === k ? "active" : ""}">${label}${
            k === "pending" && d.pending_count ? ` (${d.pending_count})` : ""
          }</button>`
      ).join("")}
    </div>
    <div id="tab-body"></div>`;

  $("#publish-btn").addEventListener("click", () =>
    run(async () => {
      const r = await api(`/api/drafts/${d.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ author: author() }),
      });
      banner(r.message);
      state.tab = "publications";
      await reloadDetail();
      await loadDrafts();
    })
  );
  main.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      state.tab = b.dataset.tab;
      renderMain();
    })
  );

  if (state.tab === "draft") renderDraftTab();
  else if (state.tab === "versions") await renderVersionsTab();
  else if (state.tab === "pending") await renderPendingTab();
  else if (state.tab === "comments") await renderCommentsTab();
  else if (state.tab === "publications") await renderPublicationsTab();
}

/* ---------------- 当前草稿 + 提交修订 ---------------- */

function renderDraftTab() {
  const d = state.detail;
  $("#tab-body").innerHTML = `
    <div class="card">
      ${d.clauses
        .map(
          (c) => `
        <div class="clause">
          <span class="num">${esc(c.number)}</span>
          <span class="text">${esc(c.text)}</span>
        </div>`
        )
        .join("")}
    </div>
    <div class="card">
      <h3>提交修订（基于所见版本）</h3>
      <form id="proposal-form" class="stack">
        <div class="row">
          <label>基线版本 <input name="base_version" type="number" min="1"
            value="${d.current_version}" required></label>
          <label>操作
            <select name="action">
              <option value="replace">替换条款</option>
              <option value="delete">删除条款</option>
            </select></label>
          <label>条款编号 <input name="clause_number" placeholder="如 3.1" required></label>
        </div>
        <label id="new-text-row">新文本 <textarea name="new_text" rows="3"></textarea></label>
        <button class="primary" type="submit">提交修订</button>
      </form>
      <p class="muted">并行改动触及不同条款会自动合并；触及同一条款则进入“待决冲突”，不会后写覆盖前写。</p>
    </div>`;

  const form = $("#proposal-form");
  form.action.addEventListener("change", () => {
    $("#new-text-row").style.display = form.action.value === "delete" ? "none" : "";
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const r = await api(`/api/drafts/${d.id}/proposals`, {
        method: "POST",
        body: JSON.stringify({
          base_version: Number(form.base_version.value),
          action: form.action.value,
          clause_number: form.clause_number.value,
          new_text: form.new_text.value,
          author: author(),
        }),
      });
      banner(r.message, r.status === "pending");
      if (r.status === "pending") state.tab = "pending";
      await reloadDetail();
      await loadDrafts();
    });
  });
}

/* ---------------- 版本与差异 ---------------- */

async function renderVersionsTab() {
  const d = state.detail;
  state.versions = await api(`/api/drafts/${d.id}/versions`);
  const opts = state.versions
    .map((v) => `<option value="${v.number}">v${v.number}</option>`)
    .join("");
  $("#tab-body").innerHTML = `
    <div class="card">
      <h3>版本历史（含每次修订来源）</h3>
      <table class="plain">
        <tr><th>版本</th><th>说明</th><th>作者</th><th>来源</th><th>时间 (UTC)</th></tr>
        ${state.versions
          .map(
            (v) => `<tr>
              <td>v${v.number}</td><td>${esc(v.message)}</td><td>${esc(v.author)}</td>
              <td>${esc(v.source_label)}</td><td>${esc(v.created_at)}</td>
            </tr>`
          )
          .join("")}
      </table>
    </div>
    <div class="card">
      <h3>版本差异</h3>
      <div class="row" style="max-width:340px">
        <label>从 <select id="diff-from">${opts}</select></label>
        <label>到 <select id="diff-to">${opts}</select></label>
      </div>
      <div id="diff-result"></div>
    </div>`;
  const from = $("#diff-from"), to = $("#diff-to");
  from.value = String(Math.max(1, d.current_version - 1));
  to.value = String(d.current_version);
  const showDiff = () =>
    run(async () => {
      const r = await api(`/api/drafts/${d.id}/diff?from=${from.value}&to=${to.value}`);
      const changed = r.changes.filter((c) => c.change !== "unchanged");
      $("#diff-result").innerHTML = changed.length
        ? changed
            .map(
              (c) => `
          <div class="diff-item diff-${c.change}">
            <span class="num">${esc(c.clause_number)}</span>
            ${
              c.change === "added"
                ? `<span class="badge ok">新增</span> <span class="new">${esc(c.new_text)}</span>`
                : c.change === "removed"
                ? `<span class="badge dead">删除</span> <span class="old">${esc(c.old_text)}</span>`
                : `<span class="badge warn">修改</span><br>
                   <span class="old">${esc(c.old_text)}</span><br>
                   <span class="new">${esc(c.new_text)}</span>`
            }
          </div>`
            )
            .join("")
        : `<p class="muted">两个版本之间没有差异。</p>`;
    });
  from.addEventListener("change", showDiff);
  to.addEventListener("change", showDiff);
  await showDiff();
}

/* ---------------- 待决冲突 ---------------- */

async function renderPendingTab() {
  const d = state.detail;
  state.pending = await api(`/api/drafts/${d.id}/proposals?status=pending`);
  const body = $("#tab-body");
  if (!state.pending.length) {
    body.innerHTML = `<p class="muted">没有待决冲突。</p>`;
    return;
  }
  body.innerHTML = state.pending
    .map(
      (p) => `
    <div class="card" data-pid="${p.id}">
      <h3>提案 #${p.id}：${p.action === "delete" ? "删除" : "替换"}条款
        ${esc(p.clause_number)} <span class="badge warn">待决</span></h3>
      <p class="muted">${esc(p.author)} 基于 v${p.base_version} 提交于 ${esc(p.created_at)}</p>
      <div class="pane-compare">
        <div><h4>基线 v${p.base_version} 内容</h4>${esc(p.base_text ?? "（不存在）")}</div>
        <div><h4>当前内容（ours）</h4>${esc(p.current_text ?? "（已删除）")}</div>
        <div><h4>提案内容（theirs）</h4>${
          p.action === "delete" ? "（删除该条款）" : esc(p.new_text)
        }</div>
      </div>
      <div class="row" style="margin-top:10px; max-width:520px">
        <button data-choice="ours">保留当前内容</button>
        <button data-choice="theirs">采用提案内容</button>
      </div>
      <div class="stack" style="margin-top:10px">
        <textarea rows="2" placeholder="或填写合并后的条款文本……"></textarea>
        <button class="primary" data-choice="merged" style="max-width:200px">以合并文本结案</button>
      </div>
    </div>`
    )
    .join("");

  body.querySelectorAll(".card").forEach((card) => {
    const pid = Number(card.dataset.pid);
    card.querySelectorAll("button").forEach((btn) =>
      btn.addEventListener("click", () =>
        run(async () => {
          const choice = btn.dataset.choice;
          const r = await api(`/api/drafts/${d.id}/proposals/${pid}/resolve`, {
            method: "POST",
            body: JSON.stringify({
              choice,
              merged_text: card.querySelector("textarea").value,
              author: author(),
            }),
          });
          banner(r.message);
          await reloadDetail();
          await loadDrafts();
        })
      )
    );
  });
}

/* ---------------- 意见 ---------------- */

async function renderCommentsTab() {
  const d = state.detail;
  state.comments = await api(`/api/drafts/${d.id}/comments`);
  $("#tab-body").innerHTML = `
    <div class="card">
      <h3>意见（附着于稳定条款编号）</h3>
      ${
        state.comments.length
          ? state.comments
              .map(
                (c) => `
        <div class="comment ${c.status === "invalid" ? "invalid" : ""}">
          <div class="head">
            条款 ${esc(c.clause_number)} · ${esc(c.author)} · 基于 v${c.base_version} · ${esc(c.created_at)}
            ${c.status === "invalid" ? '<span class="badge dead">已失效（原条款已删除）</span>' : ""}
          </div>
          <div class="text">${esc(c.text)}</div>
        </div>`
              )
              .join("")
          : '<p class="muted">暂无意见。</p>'
      }
    </div>
    <div class="card">
      <h3>提交意见</h3>
      <form id="comment-form" class="stack">
        <div class="row">
          <label>条款编号 <input name="clause_number" placeholder="如 3.1" required></label>
          <label>基线版本 <input name="base_version" type="number" min="1"
            value="${d.current_version}" required></label>
        </div>
        <label>意见内容 <textarea name="text" rows="2" required></textarea></label>
        <button class="primary" type="submit" style="max-width:160px">提交意见</button>
      </form>
    </div>`;
  const form = $("#comment-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      await api(`/api/drafts/${d.id}/comments`, {
        method: "POST",
        body: JSON.stringify({
          clause_number: form.clause_number.value,
          base_version: Number(form.base_version.value),
          text: form.text.value,
          author: author(),
        }),
      });
      banner("意见已提交");
      await renderCommentsTab();
    });
  });
}

/* ---------------- 发布快照 ---------------- */

async function renderPublicationsTab() {
  const d = state.detail;
  state.publications = await api(`/api/drafts/${d.id}/publications`);
  const body = $("#tab-body");
  if (state.viewingPub) {
    const pub = await api(`/api/drafts/${d.id}/publications/${state.viewingPub}`);
    body.innerHTML = `
      <div class="card">
        <h3>快照 #${pub.id}（基于 v${pub.version}）<span class="badge ok">不可变</span></h3>
        <p class="muted">发布人 ${esc(pub.author)} · ${esc(pub.created_at)} · 后续编辑不影响本快照</p>
        ${pub.clauses
          .map(
            (c) => `<div class="clause">
              <span class="num">${esc(c.number)}</span>
              <span class="text">${esc(c.text)}</span>
            </div>`
          )
          .join("")}
        <button id="back-to-pubs" style="margin-top:10px">返回列表</button>
      </div>`;
    $("#back-to-pubs").addEventListener("click", () => {
      state.viewingPub = null;
      renderPublicationsTab();
    });
    return;
  }
  body.innerHTML = `
    <div class="card">
      <h3>已发布快照</h3>
      ${
        state.publications.length
          ? `<table class="plain">
              <tr><th>快照</th><th>基于版本</th><th>发布人</th><th>时间 (UTC)</th><th></th></tr>
              ${state.publications
                .map(
                  (p) => `<tr>
                    <td>#${p.id}</td><td>v${p.version}</td>
                    <td>${esc(p.author)}</td><td>${esc(p.created_at)}</td>
                    <td><button data-pub="${p.id}">查看</button></td>
                  </tr>`
                )
                .join("")}
            </table>`
          : '<p class="muted">尚未发布。待决冲突清零后可在页首点击“发布快照”。</p>'
      }
    </div>`;
  body.querySelectorAll("button[data-pub]").forEach((b) =>
    b.addEventListener("click", () => {
      state.viewingPub = Number(b.dataset.pub);
      renderPublicationsTab();
    })
  );
}

/* ---------------- 新建草案 ---------------- */

function addClauseRow() {
  const row = document.createElement("div");
  row.className = "nd-clause";
  row.innerHTML = `
    <input class="num" placeholder="编号" required>
    <input class="text" placeholder="条款正文" required>
    <button type="button" class="danger">×</button>`;
  row.querySelector("button").addEventListener("click", () => row.remove());
  $("#nd-clauses").appendChild(row);
}

$("#new-draft-btn").addEventListener("click", () => {
  $("#new-draft-form").classList.toggle("hidden");
  if (!$("#nd-clauses").children.length) addClauseRow();
});
$("#nd-add-clause").addEventListener("click", addClauseRow);
$("#new-draft-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    const clauses = [...$("#nd-clauses").children].map((row) => ({
      number: row.querySelector(".num").value,
      text: row.querySelector(".text").value,
    }));
    const d = await api("/api/drafts", {
      method: "POST",
      body: JSON.stringify({
        title: $("#nd-title").value,
        clauses,
        author: author(),
      }),
    });
    banner(`草案已创建（v1）`);
    $("#new-draft-form").reset();
    $("#nd-clauses").innerHTML = "";
    $("#new-draft-form").classList.add("hidden");
    await selectDraft(d.id);
  });
});

/* ---------------- 启动 ---------------- */

$("#author").value = localStorage.getItem("author") || "";
$("#author").addEventListener("change", () =>
  localStorage.setItem("author", $("#author").value.trim())
);

run(async () => {
  await loadDrafts();
  if (state.drafts.length) await selectDraft(state.drafts[0].id);
});
