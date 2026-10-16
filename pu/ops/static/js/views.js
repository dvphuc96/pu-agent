// pu dashboard — subtab/db helpers, SQL console, Memory/Tools sub-views, VIEWS.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- sub-tabs: keep long pages short by splitting them into hash-routed tabs
// (#memory/semantic, #database/facts). Each tab is a plain link, so it's
// bookmarkable and the architecture cards can deep-link straight to one.
function subtabBar(view, tabs, active){
  return `<div class="subtabs">${tabs.map(([key,label,n]) =>
    `<a class="subtab ${key===active?"on":""}" href="#${view}/${key}">${esc(label)}${
      n!=null?`<span class="n">${n}</span>`:""}</a>`).join("")}</div>`;
}

// A raw SQLite table, scrollable, with the column names AS the (indigo) sticky
// headers so the schema lines up over its data instead of floating above it.
function dbTable(t){
  if (!t.sample.length) return `<div class="card empty">empty — no rows yet</div>`;
  const head = t.columns.map(c => `<th class="dbcol">${esc(c)}${
    t.types&&t.types[c]?`<small>${esc(t.types[c].toLowerCase())}</small>`:""}</th>`).join("");
  const body = t.sample.map(r => `<tr>${t.columns.map(c =>
    `<td class="dbcell">${esc(String(r[c]??"").slice(0,120))}</td>`).join("")}</tr>`).join("");
  return `<div class="scrolly"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="meta" style="margin-top:6px">showing ${t.sample.length} of ${t.count} row${t.count===1?"":"s"} (newest first)</div>`;
}
const DB_DESC = {
  calendar_events: "events the create_event tool wrote (the flagship task)",
  facts: "semantic memory — durable facts (Memory ▸ Semantic)",
  episodes: "episodic memory — dated summaries (Memory ▸ Episodic)",
  chat_log: "every message, tagged by session_id — consolidation reads from here",
  proposed_actions: "durable Human Approval queue — exact payload hash, decision and execution state",
  workflow_runs: "durable workflow lifecycle — request, status, retry attempt and report id",
  workflow_stage_checkpoints: "per-stage restart checkpoints; completed stages are reused after restart",
  agent_events: "structured AgentOps event envelope with workflow/tool/approval correlation",
  tool_audit_events: "queryable durable tool audit ledger (JSONL mirror also retained)",
  schema_migrations: "applied persistence schema versions",
};
const QUERY_EXAMPLES = [
  "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT 10",
  "SELECT subject, content FROM facts",
  "SELECT session_id, COUNT(*) FROM chat_log GROUP BY session_id",
];
function dbQueryView(){
  return `<div class="meta" style="margin-bottom:10px">A read-only SQL console over <code>state.db</code>
      Only <code>SELECT</code> runs — the file is opened read-only,
      so nothing here can change your data.</div>
    <textarea class="sqlbox" id="sqlbox" spellcheck="false" onfocus="markEditing()" oninput="markEditing()">${esc(QUERY_EXAMPLES[0])}</textarea>
    <div style="margin:8px 0"><button class="save" onclick="runQuery()">Run</button>
      <span class="meta" style="margin-left:12px">try: ${QUERY_EXAMPLES.map(q=>`<span class="qexample" onclick="qFill(this.textContent)">${esc(q)}</span>`).join(" &nbsp; ")}</span></div>
    <div id="qout"></div>`;
}

const TRACE_PAGE_SIZE = 18;
let tracePage = 1;
function tracePageNav(total, page, pages){
  if (pages <= 1) return `<div class="meta" style="margin-top:6px">${total} record${total===1?"":"s"}</div>`;
  return `<div style="display:flex;align-items:center;gap:8px;margin-top:8px">
    <button class="save ghost" onclick="tracePageGo(${page-1})" ${page===1?"disabled":""}>Previous</button>
    <span class="meta">page ${page} of ${pages} · ${total} records</span>
    <button class="save ghost" onclick="tracePageGo(${page+1})" ${page===pages?"disabled":""}>Next</button>
  </div>`;
}
function tracePageGo(page){
  tracePage = page;
  render();
}

// --- read-only SQL console
function qFill(sql){ const b=document.getElementById("sqlbox"); if(b){ b.value=sql; runQuery(); } }
async function runQuery(){
  editing = true;   // keep the 5s refresh from wiping the query + results
  const sql = (document.getElementById("sqlbox")||{}).value || "";
  const out = document.getElementById("qout");
  out.innerHTML = `<div class="meta">running…</div>`;
  const r = await postJSON("/api/query", {sql});
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}${r.recoverable&&r.workflow_id?`<div style="margin-top:10px"><button class="save" onclick="resumeResearch('${esc(r.workflow_id)}')">Resume from checkpoint</button></div>`:""}</div>`; await refresh(); return; }
  if (!r.rows.length){ out.innerHTML = `<div class="card empty">0 rows</div>`; return; }
  out.innerHTML = `<div class="scrolly"><table><thead><tr>${
    r.columns.map(c=>`<th class="dbcol">${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    r.rows.map(row=>`<tr>${row.map(v=>`<td class="dbcell">${esc(String(v).slice(0,120))}</td>`).join("")}</tr>`).join("")
    }</tbody></table></div><div class="meta" style="margin-top:6px">${r.rows.length} row(s)</div>`;
}

// --- Memory sub-tabs. Memory is the friendly, per-pillar view of what persists;
// the Data tab shows the SAME rows as raw SQLite tables (see the explainer).
function memOverview(d){
  const s = d.stats;
  const pillars = [
    ["Semantic","semantic",d.facts.length+" facts","durable, distilled facts about you and your people"],
    ["Episodic","episodic",d.episodes.length+" episodes","one dated summary per consolidation — stays small on purpose"],
    ["Knowledge","knowledge",(d.knowledge_sources||[]).length+" sources","documents you taught PU — playbooks, notes, Drive imports"],
    ["Procedural","skills",d.skills.length+" skills","SKILL.md files loaded only when relevant — how to act"],
  ].map(([t,sub,n,desc]) => `<div class="box" style="min-width:0" onclick="location.hash='memory/${sub}'">
      <b>${t} <span class="meta" style="font-weight:400">· ${n}</span></b><span>${desc}</span></div>`).join("");
  return `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>Memory vs Database — two views of one file.</b>
      <div class="r">This tab is the curated, per-pillar view of what PU remembers. The
      <a class="reveal" onclick="location.hash='database'">Database tab</a> shows the exact same
      thing as raw SQLite tables (plus the FTS5 keyword index). Same
      <code>.pu/state.db</code> — different altitude.
      <br><br>Some assistants (Hermes) keep memory as a single <code>MEMORY.md</code> file. PU keeps
      the queryable source in <code>state.db</code> (facts + episodes, FTS5-searchable) <b>and</b> writes a
      human-readable ${reveal("MEMORY.md","MEMORY.md")} mirror after every turn — so you get both: a real file
      you can open, backed by a sturdy database.</div></div>
    <h2>The three pillars</h2>
    <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${pillars}</div>
    <h2>Retrieval gate — does this turn even need memory?</h2>${gateSplit(s)}
    <div class="meta" style="margin-top:8px">A cheap model decides <b>if</b> a turn needs memory at all, before any lookup —
      this is memory <i>retrieval</i>, the hero decision. (The Ops tab charts the same skip/retrieve
      numbers as an operational metric; the decision itself is memory's.)</div>
    <div class="meta" style="margin-top:14px">Files: ${reveal("state.db","state.db")} · ${reveal("MEMORY.md","MEMORY.md")} · ${reveal("SOUL.md","SOUL.md")} · ${reveal("skills","skills/")}</div>`;
}
function memSemantic(d){
  let h = `<div class="meta" style="margin-bottom:12px">Durable facts distilled from what you tell PU —
    the smallest, most-reused store. Edit or forget any of them; changes are live next turn.</div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>subject</th><th>fact</th><th>source</th><th></th></tr>${
    d.facts.map(f => `<tr id="fact-${f.id}">
      <td><code>${esc(f.subject)}</code></td>
      <td class="fc">${esc(f.content)}</td>
      <td class="meta">${esc(f.source)}</td>
      <td style="white-space:nowrap"><a class="reveal" onclick="editFact(${f.id})">edit</a> · <a class="reveal del" onclick="delMem('delete_fact',${f.id})">delete</a></td>
    </tr>`).join("")}</table></div>`;
  return h;
}
function memUsage(d){
  const calls = (d.turns||[]).flatMap(t => t.llm_calls||[])
    .filter(call => call.provider && call.usage).slice(0,50);
  let h = `<div class="meta" style="margin-bottom:12px">LLM calls used while PU handled recent turns.
    This is the model/provider usage behind the memory and harness activity; the full raw trace remains in Ops.</div>`;
  if (!calls.length) return h + `<div class="card empty">no provider usage recorded yet</div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>when</th><th>provider</th><th>model</th><th>input tokens</th><th>output tokens</th></tr>${
    calls.map(call => `<tr><td class="meta">${esc((call.ts||"").replace("T"," ").slice(0,19))}</td>
      <td><code>${esc(call.provider)}</code></td><td class="meta">${esc(call.model||"")}</td>
      <td class="meta">${Number(call.usage.in||0).toLocaleString()}</td>
      <td class="meta">${Number(call.usage.out||0).toLocaleString()}</td></tr>`).join("")}</table></div>`;
  return h;
}
function memEpisodic(d){
  const src = d.episodes_source || "sqlite";
  let h = `<div class="meta" style="margin-bottom:8px">backend: <span class="srcpill">${esc(src)}</span></div>`;
  if (d.episodes_error) h += `<div class="card empty">Could not read episodes: ${esc(d.episodes_error)}</div>`;
  h += `<div class="card" style="background:var(--accent-soft);border-color:var(--line2)">
    <b>Why is this small?</b> <span class="r">Episodic memory holds one <i>distilled</i> summary per
    consolidation, not every message. The raw, blow-by-blow conversation lives in the
    <a class="reveal" onclick="location.hash='database/chat_log'"><code>chat_log</code> table</a>
    (the big one) on the Database tab — episodes are its highlights.</span></div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>date</th><th>episode</th><th></th></tr>${
    d.episodes.map(e => `<tr><td class="meta">${esc(e.happened_at)}</td><td>${esc(e.summary)}</td>
      <td><a class="reveal del" onclick="delMem('delete_episode','${e.id}')">delete</a></td></tr>`).join("")}</table></div>`;
  return h;
}
function memSkills(d){
  let h = `<div class="meta" style="margin-bottom:12px">Procedural memory — markdown instructions loaded
    only when a message matches. Add your own three ways: teach PU in chat (it calls
    <code>create_skill</code>), edit a skill below, or drop a <code>SKILL.md</code> into ${reveal("skills","the skills folder")}.</div>`;
  h += d.skills.map((sk,i) => {
    const full = `---
name: ${sk.name}
description: ${sk.description}
---

${sk.body}`;
    return `<div class="card">
      <div class="u"><code>${esc(sk.name)}</code> <span class="meta" style="font-weight:400">· ${esc(sk.description)}</span>
        <span class="srcpill ${sk.editable?"":"apple"}" style="margin-left:6px">${sk.editable?"home":"built-in"}</span></div>
      <textarea class="editor" id="sk-${i}" style="min-height:150px;margin-top:8px" data-path="${esc(sk.path)}"
        oninput="dirty('sksave-${i}')" onfocus="markEditing()">${esc(full)}</textarea>
      <div style="margin-top:8px"><button class="save" id="sksave-${i}" disabled onclick="saveSkill(${i})">Save SKILL.md</button>
        <span class="meta" id="skmsg-${i}" style="margin-left:10px">${esc(sk.rel)}</span></div></div>`;
  }).join("") || `<div class="card empty">no skills loaded</div>`;
  return h;
}
function memSoul(d){
  return `<div class="meta" style="margin-bottom:12px">SOUL.md is PU's persona — the system prompt it
    loads every turn. Editing it changes who your PU is. Changes are live next turn.</div>
    <div class="card"><textarea id="soul" class="editor" style="min-height:260px"
      oninput="dirty('soul-save')" onfocus="markEditing()">${esc(d.soul||"")}</textarea>
    <div style="margin-top:8px"><button class="save" id="soul-save" disabled onclick="saveSoul()">Save SOUL.md</button>
      <span class="meta" id="soul-msg" style="margin-left:10px"></span></div></div>
    <div class="meta" style="margin-top:10px">${reveal("SOUL.md","open SOUL.md in your editor")}</div>`;
}
function memConsolidation(d){
  const distilled = d.facts.filter(f => f.source==="consolidation");
  let h = `<div class="card"><b>How it works.</b> <span class="r">Every ${d.consolidate_every} exchanges,
    a cheap model reads the unconsolidated ${"<code>chat_log</code>"} and distills it into durable
    <b>facts</b> (semantic) plus one <b>episode</b> (episodic). Batching keeps it cheap and gives the
    summarizer enough context to pick what's worth keeping.</span></div>`;
  h += `<div class="tiles" style="margin-top:12px">
    <div class="tile"><b>${d.chat_pending}</b><span>messages queued</span></div>
    <div class="tile"><b>${d.consolidate_every*2}</b><span>trigger threshold</span></div>
    <div class="tile"><b>${distilled.length}</b><span>facts from consolidation</span></div>
    <div class="tile"><b>${d.episodes.length}</b><span>episodes total</span></div></div>`;
  h += `<h2>Facts it distilled</h2>`;
  h += table(["subject","fact","when"], distilled.map(f =>
    `<tr><td><code>${esc(f.subject)}</code></td><td>${esc(f.content)}</td><td class="meta">${esc((f.created_at||"").slice(0,10))}</td></tr>`));
  h += `<div class="meta" style="margin-top:10px">This is a memory operation, shown here. Each run is also
    <a class="reveal" onclick="location.hash='ops'">traced</a> (Ops) and can be scored by the judge evals.</div>`;
  return h;
}

// Tools ▸ Results: the artifacts tool calls produced (kept distinct from the
// tools themselves — the old tab conflated capability with output).
function toolsResults(d){
  let h = `<div class="meta" style="margin-bottom:10px">What tool calls actually wrote. These are results, not the tools.</div>`;
  h += `<h2>Calendar events <span class="meta" style="font-weight:400">· from create_event</span></h2>`;
  h += table(["event","start","end","with"], d.calendar.map(e =>
    `<tr><td>${esc(e.title)}</td><td class="meta">${esc(e.start)}</td><td class="meta">${esc(e.end)}</td><td>${esc(e.attendees)}</td></tr>`));
  h += `<div class="meta" style="margin-bottom:16px">also written to <code>calendar.ics</code> — ${reveal("calendar.ics","reveal calendar.ics in Finder")} (double-click to import into Calendar.app)</div>`;
  h += `<h2>Outbox — drafted messages <span style="font-weight:400;text-transform:none;letter-spacing:0">· ${reveal("outbox","open the outbox folder")}</span></h2>`;
  h += d.outbox.length ? d.outbox.map(o=>`<div class="card"><span class="u">${esc(o.name)}</span><div class="r">${esc(o.text)}</div></div>`).join("")
                       : `<div class="card empty">no drafted messages</div>`;
  return h;
}
// Connections: supported provider and integration configuration.

function connectionField(key, field, prefix="connection"){
  const id = `${prefix}-${key}-${field.name}`;
  const label = `${esc(field.label)}${field.required?" *":""}`;
  const help = field.help ? `<div class="conn-field-help">${esc(field.help)}</div>` : "";
  if (field.kind === "bool") return `<div class="conn-field">
    <label class="conn-check" for="${id}"><input id="${id}" data-field="${esc(field.name)}" type="checkbox" ${field.value?"checked":""}>
      <span>${label}</span></label>${help}</div>`;
  if (field.kind === "choice") return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}</span>
    <select id="${id}" data-field="${esc(field.name)}">${field.options.map(o=>`<option value="${esc(o)}" ${o===field.value?"selected":""}>${esc(o)}</option>`).join("")}</select>
    </label>${help}</div>`;
  const configured = field.secret && field.configured
    ? ` <span class="conn-secret-state">set ····${esc(field.last4)}</span>` : "";
  const clear = field.secret && field.configured
    ? `<label class="conn-clear"><input type="checkbox" data-clear="${esc(field.name)}"> Clear saved value</label>` : "";
  return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}${configured}</span>
    <input id="${id}" data-field="${esc(field.name)}" type="${field.secret?"password":"text"}"
      value="${field.secret?"":esc(field.value)}" placeholder="${field.secret?(field.configured?"Blank keeps the saved value":"Not configured"):""}">
    </label>${clear}${help}</div>`;
}
async function saveConnection(key, force){
  const modal = document.querySelector(`.connmodal[data-connection="${key}"]`), values = {}, clear = [];
  if (!modal) return;
  modal.querySelectorAll("[data-field]").forEach(el => values[el.dataset.field] = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value);
  modal.querySelectorAll("[data-clear]").forEach(el => { if (el.checked) clear.push(el.dataset.clear); });
  const msg = document.getElementById(`connection-msg-${key}`);
  msg.textContent = force ? "saving without a successful test…" : "saving…";
  const r = await postJSON("/api/connections", {key, values, clear, force:!!force});
  if (!r.ok && r.can_force) {
    msg.innerHTML = `${esc(r.error)} <button class="save ghost conn-force" onclick="saveConnection('${esc(key)}',true)">Save anyway</button>`;
  } else if (!r.ok) {
    msg.textContent = r.error || "failed";
  } else {
    closeConnectionModal();
    await refresh();
  }
}
async function testConnection(key){
  const msg = document.getElementById(`connection-msg-${key}`);
  if (msg) msg.textContent = "testing…";
  const r = await postJSON("/api/connections/test", {key});
  if (!r.status) {
    if (msg) msg.textContent = r.error || "failed";
    return;
  }
  const display = connectionStatusDisplay(r.status);
  const status = document.getElementById("connection-modal-status");
  if (status) {
    status.className = `connstatus ${display.className}`;
    status.innerHTML = `<span class="conndot"></span>${esc(display.label)}`;
  }
  const detail = document.getElementById("connection-modal-status-detail");
  if (detail) detail.textContent = r.status.message || "";
  const checked = document.getElementById("connection-modal-checked");
  if (checked) checked.textContent = r.status.checked_at ? `Last checked ${r.status.checked_at}` : "";
  if (msg) msg.textContent = r.status.message || display.label;
  await refresh();
}
async function saveProvider(provider){
  const info = (D.providers || []).find(x => x.key === provider);
  const field = info && info.fields[0] && document.getElementById(`provider-${provider}-${info.fields[0].name}`);
  const payload = {provider};
  if (field && field.value) payload.key = field.value;
  // Models are global fields for the *current* provider. Switching cards must
  // omit them so apply_provider selects the new provider's own default.
  if (provider === stProvider()) {
    const model = document.getElementById("provider-model"), small = document.getElementById("provider-small-model"), base = document.getElementById("provider-base-url"), custom = document.getElementById("provider-custom-key");
    if (model) payload.model = model.value;
    if (small) payload.small_model = small.value;
    if (base) payload.base_url = base.value;
    if (custom && custom.value) payload.custom_key = custom.value;
    if (document.getElementById("provider-clear-custom-key")?.checked) payload.custom_key = "";
  }
  const r = await postJSON("/api/providers", payload);
  if (!r.ok) alert(r.error || "Provider update failed"); else refresh();
}
function stProvider(){ return (D.settings || {}).provider || "anthropic"; }

const CONNECTION_GROUPS = ["Email", "Productivity", "Knowledge", "Research", "Observability"];
const CONNECTION_GROUP_MAP = {
  "Email": "Email",
  "Calendar & Productivity": "Productivity",
  "Knowledge": "Knowledge",
  "Research": "Research",
  "Observability": "Observability",
};

function connectionDisplayGroup(item){
  return CONNECTION_GROUP_MAP[item.group] || "Observability";
}

function connectionStatusDisplay(status){
  const state = (status && status.state) || "not_configured";
  if (state === "connected") return {label:"connected", className:"connected"};
  if (state === "error") return {label:"error", className:"error"};
  // "configured" means every required field is present; it simply has not
  // been probed in this process yet.
  if (state === "configured") return {label:"configured · not tested", className:"configured"};
  if (state === "installed_but_unconfigured") return {label:"needs setup", className:"needs-setup"};
  return {label:"not configured", className:"not-configured"};
}

function connectionCard(item){
  const display = connectionStatusDisplay(item.status);
  const action = item.status && item.status.state !== "not_configured" ? "Edit" : "Configure";
  // Show actionable detail for incomplete or failed connections.
  const why = (item.status && item.status.message
    && (item.status.state === "installed_but_unconfigured" || item.status.state === "error"))
    ? `<div class="connwhy">${esc(item.status.message)}</div>` : "";
  return `<article class="provcard conncard" data-connection-card="${esc(item.key)}">
    <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
    <div class="provname">${esc(item.name)}</div>
    <div class="connstatus ${display.className}"><span class="conndot"></span>${esc(display.label)}</div>
    ${why}
    <div class="conndesc">${esc(item.what)}</div>
    <div class="provactions connactions">
      <button class="save ghost" onclick="openConnectionModal('${esc(item.key)}')">${action}</button>
    </div>
  </article>`;
}

function connectionsGrid(items){
  const grouped = Object.fromEntries(CONNECTION_GROUPS.map(group => [group, []]));
  items.forEach(item => grouped[connectionDisplayGroup(item)].push(item));
  return CONNECTION_GROUPS.map(group => `<section class="connsection">
    <h2>${group}</h2>
    <div class="provgrid conngrid">${grouped[group].map(connectionCard).join("")}</div>
  </section>`).join("") + `<div id="connection-modal-root"></div>`;
}

function openConnectionModal(key){
  const item = ((D && D.connections) || []).find(connection => connection.key === key);
  const root = document.getElementById("connection-modal-root");
  if (!item || !root) return;
  markEditing();
  const display = connectionStatusDisplay(item.status);
  const status = item.status || {};
  const fields = item.fields.map(field => connectionField(item.key, field)).join("");
  root.innerHTML = `<div class="connmodal-back" onclick="closeConnectionModal()" onkeydown="connectionModalKeydown(event)">
    <section class="connmodal" data-connection="${esc(item.key)}" role="dialog" aria-modal="true" aria-labelledby="connection-modal-title" onclick="event.stopPropagation()">
      <header class="connmodal-head">
        <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
        <div class="connmodal-title">
          <h3 id="connection-modal-title">${esc(item.name)}</h3>
          <div class="connstatus ${display.className}" id="connection-modal-status"><span class="conndot"></span>${esc(display.label)}</div>
        </div>
        <button class="connmodal-close" type="button" onclick="closeConnectionModal()" aria-label="Close">Close</button>
      </header>
      <p class="conndesc connmodal-desc">${esc(item.what)}</p>
      <div class="connmodal-meta">
        <span id="connection-modal-status-detail">${esc(status.message || "")}</span>
        <span id="connection-modal-checked">${status.checked_at?`Last checked ${esc(status.checked_at)}`:""}</span>
      </div>
      ${(item.install_command || item.setup_url) ? `<div class="connsetup">
        ${item.install_command?`<code>${esc(item.install_command)}</code>`:""}
        ${item.setup_url?`<a href="${esc(item.setup_url)}" target="_blank" rel="noopener noreferrer">Setup guide ↗</a>`:""}
      </div>` : ""}
      <div class="connection-fields">${fields}</div>
      <footer class="connmodal-actions">
        <button class="save" onclick="saveConnection('${esc(item.key)}')">Save</button>
        <button class="save ghost" onclick="testConnection('${esc(item.key)}')">Test connection</button>
        <span class="connmodal-message" id="connection-msg-${esc(item.key)}" aria-live="polite"></span>
      </footer>
    </section>
  </div>`;
  setTimeout(() => {
    const target = root.querySelector(".connection-fields input, .connection-fields select")
      || root.querySelector(".connmodal-close");
    target?.focus();
  }, 0);
}

function closeConnectionModal(){
  editing = false;
  const root = document.getElementById("connection-modal-root");
  if (root) root.innerHTML = "";
  if (activeView === "connections") render();
}

function connectionModalKeydown(event){
  if (event.key === "Escape") closeConnectionModal();
}

// CRM tab actions. Sync/Connect are explicit clicks — the page itself only
// ever reads the local store (a page load must not make Gmail calls).
async function gmailSync(){
  const btn = document.getElementById("gmail-sync-btn");
  if (btn) btn.disabled = true;
  const msg = document.getElementById("gmail-sync-msg");
  if (msg) msg.textContent = "Syncing...";
  const r = await postJSON("/api/gmail/sync", {});
  if (msg) msg.textContent = r.message || r.error || "";
  render();
}
async function gmailConnect(){
  const msg = document.getElementById("gmail-sync-msg");
  if (msg) msg.textContent = "Opening Google sign-in...";
  const r = await postJSON("/api/gmail/connect", {});
  if (msg) msg.textContent = r.message || r.error || "";
  render();
}

// ---- Knowledge (Memory ▸ Knowledge) ---------------------------------------
// Sources the user taught PU: paste, upload, the watched folder, or a
// Google Drive import. The tab reads state.db only; add/import are explicit
// clicks. Search runs the same FTS index the agent uses. HTML is built as
// template strings with esc() on every dynamic value — the same convention
// as every other view in this file.
let kbSearchResults = null;
let kbOrigin = "paste";

async function kbDoSearch(){
  const q = (document.getElementById("kb-q") || {}).value || "";
  const r = await postJSON("/api/knowledge", {op: "search", query: q});
  kbSearchResults = r.results || [];
  render();
}

function kbOpenAdd(){
  kbOrigin = "paste";
  const groups = [...new Set(["Ungrouped", ...((D.knowledge_sources || []).map(s => s.group))])];
  openKbModal(`<h3>Add knowledge source</h3>
    <label class="fld">Title<input id="kb-title" placeholder="e.g. Sales playbook"></label>
    <label class="fld">Group<input id="kb-group" list="kb-group-opts" placeholder="Ungrouped">
      <datalist id="kb-group-opts">${groups.map(g => `<option value="${esc(g)}">`).join("")}</datalist></label>
    <label class="fld">File (.md / .txt — read in your browser, sent as text)
      <input type="file" id="kb-file" accept=".md,.markdown,.txt,text/plain,text/markdown" onchange="kbFilePicked(this)"></label>
    <label class="fld">…or paste text<textarea id="kb-text" rows="8" placeholder="Paste the document here"></textarea></label>
    <button class="save" onclick="kbAddSave()">Add source</button>
    <span class="connmodal-message" id="kb-msg"></span>`);
}

function kbFilePicked(input){
  const f = input.files && input.files[0];
  if (!f) return;
  const msg = document.getElementById("kb-msg");
  if (!/\.(md|markdown|txt)$/i.test(f.name)){
    if (msg) msg.textContent = "Only .md / .txt are supported for now (no PDF yet).";
    input.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    document.getElementById("kb-text").value = reader.result;
    const t = document.getElementById("kb-title");
    if (t && !t.value) t.value = f.name.replace(/\.(md|markdown|txt)$/i, "");
    kbOrigin = "upload";
  };
  reader.readAsText(f);
}

async function kbAddSave(){
  const text = (document.getElementById("kb-text") || {}).value || "";
  const msg = document.getElementById("kb-msg");
  const r = await postJSON("/api/knowledge", {op: "add",
    title: (document.getElementById("kb-title") || {}).value,
    group: (document.getElementById("kb-group") || {}).value,
    text, origin: kbOrigin});
  if (msg) msg.textContent = r.message || r.error || "";
  if (!r.error){ closeKbModal(); }
}

function kbOpenDrive(){
  if (!D.drive_connected){
    openKbModal(`<h3>Import from Google Drive</h3>
      <p class="conndesc">Not connected yet. Connecting opens your browser once,
      for a read-only Drive scope.</p>
      <button class="save" onclick="kbDriveConnect()">Connect Google Drive</button>
      <span class="meta">or run <code>pu connect drive</code></span>
      <span class="connmodal-message" id="kb-drive-msg"></span>`);
    return;
  }
  openKbModal(`<h3>Import from Google Drive</h3>
    <div class="meta" style="margin-bottom:8px">Docs, .md and .txt only (no PDF yet).</div>
    <div id="kb-drive-list" class="scrolly" style="max-height:300px"><div class="meta">Loading…</div></div>
    <div style="margin-top:10px"><button class="save ghost" onclick="kbDriveRefresh()">Refresh list</button>
    <button class="save" onclick="kbDriveImport()">Import selected</button>
    <span class="connmodal-message" id="kb-drive-msg"></span></div>`);
  kbDriveRefresh();
}

async function kbDriveConnect(){
  const msg = document.getElementById("kb-drive-msg");
  if (msg) msg.textContent = "Opening Google sign-in...";
  const r = await postJSON("/api/drive", {op: "connect"});
  if (msg) msg.textContent = r.message || r.error || "";
  D.drive_connected = true;  // token now exists — the picker can list
  kbOpenDrive();
}

async function kbDriveRefresh(){
  const box = document.getElementById("kb-drive-list");
  const r = await postJSON("/api/drive", {op: "list"});
  if (r.error){ if (box) box.innerHTML = `<div class="meta">${esc(r.error)}</div>`; return; }
  const files = r.files || [];
  if (box) box.innerHTML = files.length ? files.map(f =>
    `<label style="display:block;padding:4px 0">
       <input type="checkbox" class="kb-drive-file" value="${esc(f.id)}" data-name="${esc(f.name)}">
       ${esc(f.name)} <span class="meta">· ${esc(f.kind)}</span></label>`).join("")
    : `<div class="meta">No importable files found (docs, .md, .txt).</div>`;
}

async function kbDriveImport(){
  const ids = [...document.querySelectorAll(".kb-drive-file:checked")]
    .map(box => ({id: box.value, name: box.dataset.name}));
  const msg = document.getElementById("kb-drive-msg");
  if (!ids.length){ if (msg) msg.textContent = "Tick at least one file first."; return; }
  for (const f of ids){
    const r = await postJSON("/api/drive", {op: "import", file_id: f.id});
    if (msg) msg.textContent = r.message || r.error || "";
  }
  closeKbModal();
}

function kbView(id){
  postJSON("/api/knowledge", {op: "source", id}).then(r => {
    const s = r.source || {};
    const chunks = r.chunks || [];
    openKbModal(`<h3>${esc(s.title || "")}</h3>
      <div class="meta" style="margin-bottom:10px">${esc(s.origin || "")} · ${s.chars || 0} chars ·
        added ${esc((s.added_at || "").slice(0, 16).replace("T", " "))}</div>
      <label class="fld">Group<input id="kb-group-${s.id}" value="${esc(s.group || "")}"></label>
      <div class="card" style="max-height:260px;overflow:auto;white-space:pre-wrap">${esc(chunks.slice(0, 3).join("\n\n")).slice(0, 2000)}${chunks.length > 3 ? "\n\n… more chunks in the Database tab" : ""}</div>
      <div style="margin-top:10px">
        <button class="save" onclick="kbGroupSave(${s.id})">Save group</button>
        <button class="save ghost" onclick="kbToggle(${s.id}, ${s.enabled ? 0 : 1})">${s.enabled ? "Disable" : "Enable"}</button>
        <button class="save ghost" style="border-color:var(--bad);color:var(--bad)" onclick="kbDelete(${s.id})">Delete</button>
        <span class="connmodal-message" id="kb-msg-${s.id}"></span></div>`);
  });
}

async function kbToggle(id, enabled){
  await postJSON("/api/knowledge", {op: "toggle", id, enabled: !!enabled});
  closeKbModal();
}

async function kbGroupSave(id){
  const group = (document.getElementById(`kb-group-${id}`) || {}).value || "";
  await postJSON("/api/knowledge", {op: "group", id, group});
  closeKbModal();
}

async function kbDelete(id){
  await postJSON("/api/knowledge", {op: "delete", id});
  closeKbModal();
}

async function kbReindex(){
  const r = await postJSON("/api/knowledge", {op: "reindex"});
  kbSearchResults = null;
  render();
  if (r.message) setTimeout(() => {
    const el = document.getElementById("kb-note");
    if (el) el.textContent = r.message;
  }, 50);
}

function openKbModal(html){
  const root = document.getElementById("kb-modal-root");
  if (!root) return;
  markEditing();
  root.innerHTML = `<div class="connmodal-back" onclick="closeKbModal()" onkeydown="connectionModalKeydown(event)">
    <section class="connmodal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">${html}</section></div>`;
}

function closeKbModal(){
  editing = false;
  const root = document.getElementById("kb-modal-root");
  if (root) root.innerHTML = "";
  render();
}

function memKnowledge(d){
  const srcs = d.knowledge_sources || [];
  const groups = {};
  srcs.forEach(s => { (groups[s.group || "Ungrouped"] = groups[s.group || "Ungrouped"] || []).push(s); });
  const ORIGIN = {paste: "TEXT", upload: "TEXT", folder: "FILE", drive: "DRIVE"};
  let h = `<div class="card"><div class="cat-controls kb-tools">
      <input id="kb-q" type="text" placeholder="Search knowledge…" onkeydown="if(event.key==='Enter')kbDoSearch()">
      <button class="save" onclick="kbOpenAdd()">Add source</button>
      <button class="save ghost" onclick="kbOpenDrive()">Import from Google Drive</button>
      <button class="save ghost" onclick="kbReindex()">Sync folder</button>
      <span class="meta" id="kb-note"></span></div>
    <div class="meta" style="margin-top:10px">Indexed with FTS5; reaches PU through the retrieval gate
      and the <code>search_knowledge</code> tool. Watched folder: <code>.pu/knowledge/</code> (.md / .txt).</div></div>`;
  if (kbSearchResults !== null){
    h += `<h2>Search results</h2><div class="kbgroup">`;
    h += kbSearchResults.length
      ? kbSearchResults.map(r =>
          `<div class="kbsearch-hit"><b>${esc(r.title)}</b>
           <span class="meta">· ${esc(r.group || "")}</span>
           <div class="m">${esc(r.content.slice(0, 300))}</div></div>`).join("")
      : `<div class="meta" style="padding:8px 0">no match</div>`;
    h += `</div>`;
  }
  if (!srcs.length){
    h += `<div class="card empty">No sources yet — Add source, drop files in
      <code>.pu/knowledge/</code>, or import from Drive.</div>`;
  }
  for (const g of Object.keys(groups).sort()){
    h += `<div class="kbgroup"><h2>${esc(g)} <span class="meta" style="font-weight:400">· ${groups[g].length} source(s)</span></h2>`;
    h += table(["source", "type", "status", ""], groups[g].map(s =>
      `<tr><td><b>${esc(s.title)}</b><div class="meta">${s.chars || 0} chars ·
         ${esc((s.added_at || "").slice(5, 16).replace("T", " "))}</div></td>
       <td><span class="kbtype">${ORIGIN[s.origin] || (s.origin || "").toUpperCase()}</span></td>
       <td><span class="pill ${s.enabled ? "pass" : "skip"}">${s.enabled ? "READY" : "DISABLED"}</span></td>
       <td class="kbactions"><button class="save ghost" onclick="kbView(${s.id})">View</button>
           <button class="save ghost" onclick="kbToggle(${s.id}, ${s.enabled ? 0 : 1})">${s.enabled ? "Disable" : "Enable"}</button></td></tr>`));
    h += `</div>`;
  }
  return h + `<div id="kb-modal-root"></div>`;
}

function approvalStatusPill(status){
  const cls = status === "executed" || status === "approved" ? "pass" : status === "failed" || status === "rejected" ? "fail" : "";
  return `<span class="pill ${cls}">${esc(status||"")}</span>`;
}
function approvalJsonId(id){ return "approval-json-" + String(id||"").replace(/[^A-Za-z0-9_-]/g, ""); }
async function approvalAction(op, id){
  editing = true;
  const body = {op, id};
  if (op === "edit"){
    const box = document.getElementById(approvalJsonId(id));
    try { body.arguments = JSON.parse((box && box.value) || "{}"); }
    catch(e){ alert("Payload phải là JSON object hợp lệ: " + e); return; }
  }
  if (op === "reject"){
    const note = prompt("Lý do reject (tuỳ chọn):", "");
    if (note === null) return;
    body.note = note;
  }
  if (op === "approve_execute"){
    if (!confirm("Approve đúng payload đang hiển thị và thực thi action này?")) return;
  }
  const r = await postJSON("/api/approvals", body);
  if (r.error){ alert(r.error); return; }
  editing = false;
  await refresh();
  if (r.action) location.hash = "approvals/" + r.action.id;
}
function approvalsView(d, selected){
  const a = d.approvals || {items:[], counts:{}, pending:0};
  const items = a.items || [];
  if (selected){
    const x = items.find(v => v.id === selected);
    if (!x) return `<div class="card empty">approval not found</div>`;
    const editable = x.status === "pending" || x.status === "approved";
    return `<div class="meta" style="margin-bottom:10px"><a class="reveal" onclick="location.hash='approvals'">← approval queue</a></div>
      <div class="card" style="border-color:${x.status==='pending'?'var(--accent)':'var(--line)'}">
        <div class="tn" style="display:flex;justify-content:space-between;gap:12px"><span><code>${esc(x.tool_name)}</code> · revision ${x.revision}</span>${approvalStatusPill(x.status)}</div>
        <div class="meta" style="margin-top:8px">risk <b>${esc(x.risk)}</b> · ${x.external?'external':'local'} · requested by <code>${esc(x.agent_id||'agent')}</code></div>
        <div class="meta">principal <code>${esc(x.principal)}</code> · scope <code>${esc(x.required_scope)}</code> · capability <code>${esc(x.capability)}</code></div>
        <div class="meta">created ${esc((x.created_at||'').replace('T',' ').slice(0,19))} · expires ${esc((x.expires_at||'').replace('T',' ').slice(0,19))}</div>
        <div class="meta" style="word-break:break-all">action hash <code>${esc(x.action_hash)}</code></div>
        ${x.decided_by?`<div class="meta">decision by <code>${esc(x.decided_by)}</code>${x.decision_note?` · ${esc(x.decision_note)}`:''}</div>`:''}
        <h2>Exact payload</h2>
        <textarea class="sqlbox" id="${approvalJsonId(x.id)}" spellcheck="false" ${editable?'onfocus="markEditing()" oninput="markEditing()"':'readonly'}>${esc(JSON.stringify(x.arguments||{}, null, 2))}</textarea>
        ${editable?`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
          <button class="save ghost" onclick="approvalAction('edit','${esc(x.id)}')">Save edit → re-approve</button>
          <button class="save" onclick="approvalAction('approve_execute','${esc(x.id)}')">Approve &amp; execute</button>
          <button class="save ghost" onclick="approvalAction('reject','${esc(x.id)}')">Reject</button>
        </div>`:''}
        ${x.result_summary?`<h2>Execution result</h2><pre>${esc(x.result_summary)}</pre>`:''}
        ${x.error?`<div class="meta" style="color:var(--bad)">${esc(x.error)}</div>`:''}
      </div>`;
  }
  let h = `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)"><b>${a.pending||0} pending approval${a.pending===1?'':'s'}</b>
    <div class="meta" style="margin-top:6px">Agents can propose side effects, but only a human decision here can release the one-shot write capability. Editing the payload changes its hash and requires approval again.</div></div>`;
  if (!items.length) return h + `<div class="card empty">no proposed actions yet</div>`;
  const pending = items.filter(x=>x.status==='pending');
  const rest = items.filter(x=>x.status!=='pending');
  const cards = xs => xs.map(x=>`<div class="toolcard" style="cursor:pointer" onclick="location.hash='approvals/${esc(x.id)}'">
      <div class="tn" style="display:flex;justify-content:space-between;gap:8px"><span><code>${esc(x.tool_name)}</code> <span class="srcpill">${esc(x.risk)}</span></span>${approvalStatusPill(x.status)}</div>
      <div class="td">${esc(JSON.stringify(x.arguments||{}).slice(0,180))}</div>
      <div class="meta">${esc((x.created_at||'').replace('T',' ').slice(0,19))} · rev ${x.revision} · ${x.external?'external':'local'}</div></div>`).join('');
  if (pending.length) h += `<h2>Pending</h2>` + cards(pending);
  if (rest.length) h += `<h2>Recent decisions &amp; executions</h2>` + cards(rest.slice(0,50));
  return h;
}

async function runResearch(){
  const out = document.getElementById("research-out");
  const prompt = (document.getElementById("research-prompt")||{}).value || "";
  if (out) out.innerHTML = `<div class="meta">running Email → Company/Web/Calendar/Memory → Report…</div>`;
  const r = await postJSON("/api/research", {prompt});
  if (!out) return;
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}${r.recoverable&&r.workflow_id?`<div style="margin-top:10px"><button class="save" onclick="resumeResearch('${esc(r.workflow_id)}')">Resume from checkpoint</button></div>`:""}</div>`; await refresh(); return; }
  const ev = r.evaluation || {};
  const m = ev.metrics || {};
  const gateClass = ev.gate_status === "fail" ? "fail" : "pass";
  out.innerHTML = `<div class="card"><div class="tn">Report draft <span class="pill ${gateClass}">${esc(ev.gate_status||"not evaluated")}</span></div><div class="meta">workflow ${esc(r.workflow_id)} · overall ${esc(String(m.overall??"—"))} · source coverage ${m.source_coverage!=null?Math.round(m.source_coverage*100)+"%":"—"}</div><pre style="white-space:pre-wrap;margin-top:12px">${esc(r.report||"")}</pre></div>`;
}

async function resumeResearch(workflowId){
  const out = document.getElementById("research-out");
  if (out) out.innerHTML = `<div class="meta">resuming ${esc(workflowId)} from durable checkpoints…</div>`;
  const r = await postJSON("/api/research", {op:"resume", workflow_id:workflowId});
  if (!out) return;
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}</div>`; await refresh(); return; }
  const ev=r.evaluation||{}, m=ev.metrics||{};
  out.innerHTML = `<div class="card"><div class="tn">Recovered report <span class="pill ${ev.gate_status==="fail"?"fail":"pass"}">${esc(ev.gate_status||"done")}</span></div><div class="meta">workflow ${esc(r.workflow_id)} · source coverage ${m.source_coverage!=null?Math.round(m.source_coverage*100)+"%":"—"}</div><pre style="white-space:pre-wrap;margin-top:12px">${esc(r.report||"")}</pre></div>`;
  await refresh();
}

function reportQualityCard(r){
  const e = r.evaluation || {}, m = e.metrics || {};
  const cls = e.gate_status === "fail" ? "fail" : "pass";
  return `<div class="toolcard"><div class="tn">${esc(r.title||r.report_id)} <span class="pill ${cls}">${esc(e.gate_status||"unknown")}</span></div>
    <div class="meta"><code>${esc(r.report_id||"")}</code> · ${esc((r.generated_at||"").replace("T"," ").slice(0,19))}</div>
    <div class="td">${esc(r.executive_summary||"")}</div>
    <div class="meta">claims ${m.total_claims??0} · unsupported ${m.unsupported_claims??0} · sources ${m.source_count??0} · domains ${m.unique_domains??0} · coverage ${m.source_coverage!=null?Math.round(m.source_coverage*100)+"%":"—"} · overall ${m.overall??"—"}</div></div>`;
}

const VIEWS = {
  models(d){
    // Provider card grid (logo / status dot / edit / enable-disable). Editing
    // happens in a modal opened from a card; both live in js/models.js.
    return modelsGrid(d);
  },
  connections(d){
    const items = d.connections || [];
    return items.length ? connectionsGrid(items) : `<div class="card empty">No integrations registered.</div>`;
  },
  approvals(d, selected){
    return approvalsView(d, selected);
  },
  agents(d){
    const items = (d.agents&&d.agents.items)||[];
    let h = `<div class="meta" style="margin-bottom:14px">Each specialized agent has a least-privilege read-only catalog; write actions stay behind Human Approval.</div>`;
    h += `<div class="toolgrid">` + items.map(a => `<div class="toolcard"><div class="tn">${esc(a.name||a.agent_id)}</div><div class="meta"><code>${esc(a.agent_id)}</code></div><div class="td">${(a.responsibility?esc(a.responsibility):"bounded domain agent")}</div><div class="meta">tools: ${(a.tool_names||a.tools||[]).map(x=>`<code>${esc(x)}</code>`).join(" · ")||"none"}</div><div class="meta">capabilities: ${(a.capabilities||[]).map(x=>`<code>${esc(x)}</code>`).join(" · ")||"read-only / no tools"}</div></div>`).join("") + `</div>`;
    return h;
  },
  research(d, sub){
    return notebookView(d, sub);
  },
  workflows(d){
    const wf=d.workflows||{items:[],recoverable:0,completed:0};
    const recover=(wf.items||[]).filter(x=>["failed","running","interrupted"].includes(x.status));
    let h=`<div class="meta" style="margin-bottom:14px">Deterministic orchestration with durable checkpoints: Email → Company/Web/Calendar/Memory → Report. Completed stages are reused after restart.</div><div class="card"><label class="fld"><span>Research intent (optional)</span><textarea id="research-prompt" class="sqlbox" placeholder="Prepare me for the most important company/meeting today"></textarea></label><button class="save" onclick="runResearch()">Run research workflow</button></div><div class="metricrow"><div class="metric"><b>${wf.completed||0}</b><span>completed workflows</span></div><div class="metric"><b>${wf.recoverable||0}</b><span>recoverable</span></div></div>`;
    if(recover.length){ h+=`<h2>Recoverable after restart</h2>`+recover.slice(0,12).map(x=>`<div class="toolcard"><div class="tn"><code>${esc(x.id)}</code> <span class="pill fail">${esc(x.status)}</span></div><div class="meta">attempt ${x.attempt} · stage ${esc(x.current_stage||"—")} · ${esc((x.updated_at||"").replace("T"," ").slice(0,19))}</div><div class="td">${esc(x.error||"")}</div><button class="save" onclick="resumeResearch('${esc(x.id)}')">Resume from checkpoint</button></div>`).join(""); }
    return h+`<div id="research-out"></div>`;
  },
  reports(d, sub){
    return notebookView(d, sub);
  },
  artifacts(d){
    const rows = d.reports || [];
    if (!rows.length) return `<div class="card empty">No structured reports yet — run the Research workflow.</div>`;
    return `<div class="meta" style="margin-bottom:14px">Structured report artifacts are persisted under <code>.pu/reports/</code>. Sources are normalized before generation; claims may only cite known source IDs.</div><div class="toolgrid">${rows.map(reportQualityCard).join("")}</div>`;
  },
  evaluations(d){
    const rows = d.reports || [];
    let h = `<div class="card"><b>Report Quality Gate</b><div class="r">Deterministic validators are authoritative: completeness, source coverage, invalid/unsupported claims, source diversity and freshness. LLM-as-Judge is optional and cannot override a deterministic failure or authorize a side effect.</div></div>`;
    if (!rows.length) return h + `<div class="card empty">No report evaluation runs yet.</div>`;
    h += table(["report","gate","overall","coverage","unsupported","sources","freshness"], rows.map(r=>{ const e=r.evaluation||{},m=e.metrics||{}; const cls=e.gate_status==="fail"?"fail":"pass"; return `<tr><td>${esc(r.title||r.report_id)}</td><td><span class="pill ${cls}">${esc(e.gate_status||"unknown")}</span></td><td class="meta">${esc(String(m.overall??"—"))}</td><td class="meta">${m.source_coverage!=null?Math.round(m.source_coverage*100)+"%":"—"}</td><td class="meta">${m.unsupported_claims??"—"}</td><td class="meta">${m.source_count??"—"}</td><td class="meta">${m.freshness!=null?Math.round(m.freshness*100)+"%":"—"}</td></tr>`; }));
    return h;
  },
  // Chat history for the supported local surfaces (dashboard and CLI).
  // Each conversation can be opened in the persistent dock on the right.
  gateway(d){
    const sessions = d.sessions || [];
    let h = `<div class="meta" style="margin-bottom:14px">Dashboard and CLI conversations in one place.
      Click one to open it in the chat dock &rarr;.</div>`;
    if (!sessions.length)
      return h + `<div class="card empty">no conversations yet — say something in the chat dock &rarr;</div>`;
    h += sessions.map(s => {
      const tags = gwTags(s);
      const on = s.id === SESSION;
      return `<div class="toolcard" style="cursor:pointer${on?';border-color:var(--accent)':''}" onclick="openConversation('${esc(s.id)}')">
        <div class="tn" style="display:flex;justify-content:space-between;align-items:baseline;gap:10px">
          <span>${esc(s.title||s.id)} ${tags}</span>
          <span class="meta" style="font-weight:400;white-space:nowrap">${sessionMeta(s)}</span></div>
        <div class="td">${esc(s.last||"")}</div></div>`;
    }).join("");
    return h;
  },
  // CRM: email pulled read-only from Gmail into crm_emails (pu/tools/gmail.py).
  // Reads the LOCAL store only — pulling happens when you click Sync or ask
  // PU in chat, never on a page load.
  crm(d){
    const emails = d.crm || [];
    let h = `<div class="meta" style="margin-bottom:14px">Email pulled read-only from Gmail into
      <code>crm_emails</code> (state.db). Sync runs on demand only — the button below or asking
      PU in chat. Nothing is ever sent back to Gmail.</div>`;
    h += `<div class="card"><span class="r">
      ${d.gmail_connected
        ? `<button class="save" id="gmail-sync-btn" onclick="gmailSync()">Sync now</button>`
        : `<button class="save ghost" onclick="gmailConnect()">Connect Gmail</button>
           <span class="meta">or run <code>pu connect gmail</code> — the browser opens once,
           for a read-only scope.</span>`}
      ${d.gmail_enabled ? "" : `<span class="meta">The chat tool is off — flip <b>Enable Gmail
        inbox</b> on the <a class="reveal" onclick="location.hash='connections'">Gmail card</a>
        so PU can read mail in conversation.</span>`}
      <span class="connmodal-message" id="gmail-sync-msg"></span></span></div>`;
    h += emails.length
      ? table(["from", "subject", "received", ""], emails.map(e =>
          `<tr><td>${esc(e.sender_name || "(unknown)")}
             <div class="meta">${esc(e.sender_email || "")}</div></td>
           <td>${esc(e.subject || "(no subject)")}
             <div class="meta">${esc((e.snippet || "").slice(0, 90))}</div></td>
           <td class="meta">${esc((e.internal_date || "").replace("T", " ").slice(0, 16))}</td>
           <td>${e.unread ? `<span class="pill pass">unread</span>` : ""}</td></tr>`))
      : `<div class="card empty">${d.gmail_connected
          ? "nothing synced yet — hit Sync now"
          : "not connected yet — Connect Gmail above, then Sync"}</div>`;
    return h;
  },
  overview(d){
    const s = d.stats;
    const u = d.usage || {total_cost:0};
    const tiles = [
        [money(u.total_cost),"spent · all-time","money"],[secs(s.latency_avg),"avg turn",""],
        [s.turns,"turns",""],[s.tool_calls,"tool calls",""],
        [d.facts.length,"facts",""],[d.calendar.length,"events",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("");
    return `<div class="tiles">${tiles}</div>
    <h2>Retrieval gate — the hero decision</h2>${gateSplit(s)}
    <h2 style="margin-top:26px">Architecture — click any box <span class="arch-status"></span></h2>
    ${archSVG(d)}
    <h2>Graph workflows — when a turn needs shape</h2>
    ${graphPanel(d)}
    <h2>Latest turn</h2>${d.turns.length?turnCard(d.turns[0]):'<div class="card empty">no turns yet — talk to PU first</div>'}`;
  },
  loop(d){
    return d.turns.length ? d.turns.map(turnCard).join("") : `<div class="card empty">no turns yet</div>`;
  },
  // Graph workflows: the loop's sibling. The chart is rendered from the
  // engine's own describe() (served in d.graph.workflows) so it can never
  // show a shape the engine doesn't run. Nothing here is a mode switch —
  // the harness routes every message itself; this tab just tells the story.
  graph(d){
    const g = d.graph || {enabled:false, workflows:[], stats:{quick:0, full:0}};
    let h = `<div class="meta" style="margin-bottom:14px">The loop is one agent turn: the model picks tools until
      it stops. Some work has <b>shape</b> — steps that can run at the same time, and explicit "if this, go
      there" routing. A <b>graph workflow</b> makes that shape first-class: nodes (each does one job) connected
      by edges (what happens next). The loop did not change one line — the <code>full_agent</code> node below
      IS the same loop, running as one step. The harness routes every message itself — and workflows you
      can also call BY NAME from the chat box: type <code>/graphs</code> to see them.</div>`;
    if (!g.enabled)
      h += `<div class="card"><b>Off</b> — every turn currently runs the classic loop.
        <div class="meta" style="margin-top:6px">Switch on <b>graph workflows</b> in
        <a class="reveal" onclick="location.hash='settings'">Behaviour</a>, or set
        <code>PU_GRAPH_WORKFLOWS=1</code> in <code>.env</code>. Any failure anywhere fails open to the
        plain loop — this can never lose a reply, only save time and tokens.</div></div>`;
    const NOTE = {
      triage: `<b>Runs itself on every message when graph workflows are enabled.</b> Solid arrows = always, dashed = the router's choice. <code>full_agent</code> is the ordinary loop running as one node.`,
    };
    (g.workflows || []).forEach(w => {
      if (!w) return;
      h += `<h2>${esc(w.name)} — live topology <span class="arch-status"></span></h2>`;
      const tot = g.stats.quick + g.stats.full;
      const extra = w.name === "triage" && tot
        ? ` · ${g.stats.quick} quick / ${g.stats.full} full so far` : "";
      h += `<div class="card">${graphSVG(w)}
        <div class="meta" style="margin-top:8px">${NOTE[w.name] || ""}${extra} ·
        drawn from the engine's own <code>describe()</code>, so this picture cannot drift from the code</div></div>`;
    });
    const gturns = (d.turns||[]).filter(t => t.graph && t.graph.route);
    h += `<h2>Graph turns</h2>`;
    h += gturns.length
      ? gturns.slice(0,20).map(t => `<div class="card">
          <div class="u">${esc(t.user_message)}</div>
          <div class="meta" style="margin-top:4px"><span class="badge ${t.graph.route==="quick"?"":"retrieve"}">graph · ${esc(t.graph.route)}</span>
            <span class="meta" style="margin:0">${esc(t.graph.reason||"")}</span></div>
          <div class="r">${renderMarkdown(t.reply||"")}</div></div>`).join("")
      : `<div class="card empty">no graph turns yet — ${g.enabled
          ? 'say "thanks!" in the chat and watch it take the quick door'
          : "switch the flag on first"}</div>`;
    return h;
  },
  memory(d, sub){
    sub = sub || "overview";
    const tabs = [["overview","Overview"],["semantic","Semantic",d.facts.length],
      ["episodic","Episodic",d.episodes.length],["knowledge","Knowledge",(d.knowledge_sources||[]).length],
      ["skills","Skills",d.skills.length],
      ["soul","SOUL"],["consolidation","Consolidation",d.chat_pending],
      ["usage","LLM usage",(d.turns||[]).flatMap(t => t.llm_calls||[]).filter(call => call.provider && call.usage).length]];
    let h = subtabBar("memory", tabs, sub);
    if (sub==="semantic") return h + memSemantic(d);
    if (sub==="usage") return h + memUsage(d);
    if (sub==="episodic") return h + memEpisodic(d);
    if (sub==="knowledge") return h + memKnowledge(d);
    if (sub==="skills") return h + memSkills(d);
    if (sub==="soul") return h + memSoul(d);
    if (sub==="consolidation") return h + memConsolidation(d);
    return h + memOverview(d);
  },
  settings(d){
    const st = d.settings || {providers:[]};
    return `<h2>Graph workflows</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">Off by default. When on, every message is triaged
        through a graph first: trivial messages can get a fast small-model reply, while real tasks run
        the same loop as a graph node. Any graph failure falls back to the plain loop.</div>
      <label class="fld">Triage-first turns
        <select id="set-graph-workflows" onfocus="markEditing()">
          <option value="" ${!st.graph_workflows?"selected":""}>off — classic loop</option>
          <option value="1" ${st.graph_workflows?"selected":""}>on — triage graph</option>
        </select></label>
      <div style="margin-top:12px"><button class="save" onclick="saveSettings()">Save &amp; switch</button>
        <span class="meta" style="margin-left:10px">rebuilds the agent in-process</span></div>
    </div>`;
  },
  tools(d, sub){
    const t = d.tools || {catalog:[]};
    sub = sub || "available";
    const tabs = [["available","Available",t.catalog.length],["results","Results"]];
    let h = subtabBar("tools", tabs, sub);
    if (sub === "results") return h + toolsResults(d);
    h += `<div class="meta" style="margin-bottom:12px">Read capabilities execute through Tool + Security Harness. Side-effect tools are exposed to the agent as <code>propose_*</code>; the agent has <code>action:propose</code>, never the underlying write capability.</div>`;
    const SRC = [["flagship","Scheduling & local drafts"],["email","Email"],["web","Web research"],
      ["knowledge","Knowledge"],["self-management","Memory self-management"],["other","Other"]];
    SRC.forEach(([key,label]) => {
      const items = t.catalog.filter(c => c.source === key);
      if (!items.length) return;
      h += `<h2>${label}</h2>`;
      h += items.map(c => `<div class="toolcard"><div class="tn">${esc(c.agent_name||c.name)}<span class="srcpill">${esc(key)}</span></div>
        <div class="td">${esc(c.description)}</div>
        <div class="meta" style="margin-top:6px">agent capability <code>${esc(c.agent_capability||c.capability||"")}</code> · target <code>${esc(c.capability||"")}</code> · scope <code>${esc(c.scope||"")}</code> · ${c.side_effect?"approval-gated write":"read-only"} · ${c.external?"external boundary":"local"}</div></div>`).join("");
    });
    return h;
  },
  security(d){
    const s = d.security || {enforced:false, blocked:0, events:0, policies:{}, recent:[]};
    let h = `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>${s.enforced?"Tool + Security Harness enforced":"Security harness unavailable"}</b>
      <div class="meta" style="margin-top:6px">schema → authentication → user scope → durable audit → Security Harness → proposal/approval binding → handler. Approved actions are one-shot and security is re-checked at execution time.</div></div>`;
    h += `<div class="metricrow">
      <div class="metric"><b>${s.events||0}</b><span>audit events</span></div>
      <div class="metric"><b>${s.blocked||0}</b><span>blocked</span></div>
      <div class="metric"><b>${Object.keys(s.policies||{}).length}</b><span>blocking policies</span></div></div>`;
    const policyRows = Object.entries(s.policies||{}).map(([k,v])=>`<tr><td><code>${esc(k)}</code></td><td>${v}</td></tr>`);
    h += `<h2>Policy blocks</h2>` + (policyRows.length ? table(["policy","blocks"], policyRows) : `<div class="card empty">no blocked tool executions yet</div>`);
    const rows = (s.recent||[]).map(r=>`<tr><td class="meta">${esc((r.timestamp||"").replace("T"," ").slice(0,19))}</td><td><code>${esc(r.tool||"")}</code></td><td>${esc(r.status||"")}</td><td><code>${esc(r.policy||"—")}</code></td><td class="meta">${esc(r.reason||"")}</td></tr>`);
    h += `<h2>Recent audit</h2>` + (rows.length ? table(["time","tool","status","policy","reason"], rows) : `<div class="card empty">no tool audit events yet</div>`);
    return h;
  },
  database(d, sub){
    // The persistence layer itself — one SQLite file, real tables, FTS5 index.
    // "Data" in the nav (plainer than "state.db"), but we keep saying state.db
    // because that's literally the filename you can open.
    const db = d.db || {tables:[], all_tables:[], fts:[], size:0, path:""};
    const tables = db.tables || [];
    sub = sub || "overview";
    const tabs = [["overview","Overview"],
      ...tables.map(t => [t.name, t.name, t.count]),
      ["query","SQL console"]];
    let h = subtabBar("database", tabs, sub);
    if (sub === "query") return h + dbQueryView();
    if (sub !== "overview"){
      const t = tables.find(x => x.name === sub);
      if (!t) return h + `<div class="card empty">no such table</div>`;
      return h + `<div class="meta" style="margin-bottom:10px">${DB_DESC[t.name]||""}</div>` + dbTable(t);
    }
    const kb = (db.size/1024).toFixed(1);
    h += `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>Database vs Memory.</b> <span class="r">This is the raw persistence layer — the literal SQLite
      tables. The <a class="reveal" onclick="location.hash='memory'">Memory tab</a> is the friendly
      view of the same rows (facts, episodes, skills, persona). One file, two altitudes. Where Hermes
      uses a <code>MEMORY.md</code> file, PU uses these queryable tables — and mirrors them to a
      readable <code>MEMORY.md</code> too.</span></div>`;
    h += `<div class="card">
      <div class="u" style="font-family:var(--mono);font-size:12.5px;word-break:break-all">${esc(db.path)}</div>
      <div class="meta">${kb} KB on disk · SQLite + FTS5 · open it yourself: <code>sqlite3 .pu/state.db</code></div>
      <div class="meta" style="margin-top:8px">${reveal("state.db","reveal state.db in Finder")} &nbsp;·&nbsp; ${reveal("","open the .pu folder")}</div></div>`;
    h += `<h2>Tables — click a tab above, or a row here</h2>`;
    h += table(["table","rows","what it holds"], tables.map(t =>
      `<tr><td><a class="reveal" onclick="location.hash='database/${esc(t.name)}'"><code>${esc(t.name)}</code></a></td>
        <td class="meta">${t.count}</td><td class="meta">${DB_DESC[t.name]||""}</td></tr>`));
    h += `<h2>FTS5 — the keyword index</h2><div class="card">The <code>*_fts</code> virtual tables (and their
      <code>*_fts_data</code>/<code>*_fts_idx</code> shadows) make memory searchable by keyword — no embeddings,
      no vector DB. This is the "keyword top-k" the retrieval gate queries.
      <div class="meta" style="margin-top:8px">all ${db.all_tables.length} tables: ${db.all_tables.map(t=>`<code>${esc(t)}</code>`).join(" ")}</div></div>`;
    return h;
  },
  ops(d){
    const s = d.stats;
    const u = d.usage || {calls:0,total_in:0,total_out:0,total_cost:0,by_day:[],by_provider:[]};
    const ao=d.agentops||{metrics:{},slo:{healthy:true,checks:[],alerts:[]},recent:[]}, am=ao.metrics||{}, slo=ao.slo||{};
    let h = `<div class="card" style="border-color:${slo.healthy?"var(--good)":"var(--bad)"}"><b>AgentOps SLOs · ${slo.healthy?"healthy":"attention required"}</b><div class="meta">durable events ${am.events||0} · LLM p95 ${am.llm_p95_ms||0} ms · tool error ${Math.round((am.tool_error_rate||0)*100)}% · workflow success ${Math.round((am.workflow_success_rate??1)*100)}% · approval failure ${Math.round((am.approval_failure_rate||0)*100)}%</div></div>`;
    h += `<div class="toolgrid">${(slo.checks||[]).map(c=>`<div class="toolcard"><div class="tn">${esc(c.name)} <span class="pill ${c.ok?"pass":"fail"}">${c.ok?"ok":"alert"}</span></div><div class="meta">${esc(String(c.value))} ${esc(c.unit)} · target ${esc(c.comparator)} ${esc(String(c.target))}</div></div>`).join("")}</div>`;
    h += `<div class="tiles">${[
        [money(u.total_cost),"spent · all-time","money"],[u.total_in.toLocaleString(),"tokens in · all-time",""],
        [u.total_out.toLocaleString(),"tokens out · all-time",""],[u.calls.toLocaleString(),"LLM calls",""],
        [secs(s.latency_avg),"avg turn",""],[`${s.tool_errors}`,"tool errors",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("")}</div>`;

    h += `<h2>Spend <span class="meta" style="font-weight:400">· permanent ledger — survives a demo reset</span></h2>`;
    h += `<div class="card"><span class="r">Every LLM call's tokens are logged to
      <code>.pu/usage.jsonl</code> (append-only, never wiped). Dollar cost is estimated from tokens
      × current pricing — the tokens are the ground truth. ${reveal("usage.jsonl","open usage.jsonl")}</span></div>`;
    if ((u.by_provider||[]).length){
      h += table(["provider","LLM calls","tokens in","tokens out","cost (est)"], u.by_provider.map(p =>
        `<tr><td><code>${esc(p.provider)}</code></td><td class="meta">${p.calls}</td>
          <td class="meta">${p.in.toLocaleString()}</td><td class="meta">${p.out.toLocaleString()}</td>
          <td class="meta">${money(p.cost)}</td></tr>`));
    }
    if ((u.by_day||[]).length){
      h += `<h2>Spend per day</h2>`;
      h += table(["day","LLM calls","tokens in","tokens out","cost (est)"], u.by_day.map(r =>
        `<tr><td class="meta">${esc(r.date)}</td><td class="meta">${r.calls}</td>
          <td class="meta">${r.in.toLocaleString()}</td><td class="meta">${r.out.toLocaleString()}</td>
          <td class="meta">${money(r.cost)}</td></tr>`));
    }

    h += `<h2>Retrieval gate — which turns used memory</h2>${gateSplit(s)}`;
    const decided = d.turns.filter(t => t.gate);
    if (decided.length){
      h += `<div class="meta" style="margin:8px 0">The actual decisions (what was skipped vs retrieved), most recent first:</div>`;
      h += table(["turn","decision","why"], decided.slice(0,10).map(t =>
        `<tr><td>${esc((t.user_message||"").slice(0,44))} </td>
          <td><span class="pill ${t.gate.decision==="skip"?"skip":"pass"}">${esc(t.gate.decision)}</span></td>
          <td class="meta">${esc(t.gate.reason||"")}</td></tr>`));
    }

    h += `<h2>Release gate <span class="meta" style="font-weight:400">· the ship/no-ship check</span></h2>`;
    h += `<div class="card"><span class="r">Before you ship a change (new prompt, swapped model, tuned
      retrieval), <code>make gate</code> runs both eval suites: deterministic must pass 100%, the judge must
      clear its threshold. It's manual — you run it — so there's one record per run. The history below grows
      each time you run it.</span></div>`;
    h += d.eval_report ? `<div class="card">
        <span class="pill ${d.eval_report.deterministic}">deterministic · ${d.eval_report.deterministic}</span>
        <span class="pill ${d.eval_report.judge==="pass"?"pass":d.eval_report.judge==="fail"?"fail":"skip"}" style="margin-left:8px">llm-judge · ${d.eval_report.judge}</span>
        <div class="meta">last run ${esc(d.eval_report.ran_at)} — re-run with <code>make gate</code></div></div>`
      : `<div class="card empty">never run yet — run <code>make gate</code> to populate this</div>`;

    if ((d.eval_history||[]).length){
      const cnt = s => s ? `${s.passed||0} pass · ${s.failed||0} fail` : "—";
      h += `<h2>Eval history</h2>`;
      h += table(["when","deterministic","llm-judge","counts"], d.eval_history.map(r =>
        `<tr><td class="meta">${esc((r.ran_at||"").replace("T"," ").slice(0,19))}</td>
         <td><span class="pill ${r.deterministic}">${esc(r.deterministic)}</span></td>
         <td><span class="pill ${r.judge==="pass"?"pass":r.judge==="fail"?"fail":"skip"}">${esc(r.judge)}</span></td>
         <td class="meta">det ${cnt(r.suites&&r.suites.deterministic)} · judge ${cnt(r.suites&&r.suites.judge)}</td></tr>`));
    }

    h += `<h2>Slowest turns</h2>`;
    const slow = [...d.turns].filter(t=>t.latency_ms!=null).sort((a,b)=>b.latency_ms-a.latency_ms).slice(0,6);
    h += table(["turn","latency","cost","tools"], slow.map(t =>
      `<tr><td>${esc((t.user_message||"").slice(0,48))}</td><td class="meta">${secs(t.latency_ms)}</td><td class="meta">${money(t.cost||0)}</td><td class="meta">${(t.tools||[]).map(x=>x.tool).join(", ")||"—"}</td></tr>`));

    h += `<h2>Tracing <span class="meta" style="font-weight:400">· every turn as JSONL, always on</span></h2>`;
    if ((d.trace_errors||[]).length){
      h += d.trace_errors.map(e => `<div class="card"><span class="pill fail">trace encoding error</span>
        <div class="meta" style="margin-top:8px"><code>${esc(e.file)}</code> — ${esc(e.error)}</div></div>`).join("");
    }
    h += `<div class="card"><span class="r">${s.trace_files} trace file(s) in <code>traces/</code>${
      d.trace_file?` (newest: <code>${esc(d.trace_file)}</code>)`:""}. ${reveal("traces","open the traces folder")}.
      A trace is just "what happened, in order" — here are the most recent lines:</span></div>`;
    const traceRows = d.trace_tail||[];
    const tracePages = Math.max(1, Math.ceil(traceRows.length / TRACE_PAGE_SIZE));
    tracePage = Math.min(Math.max(tracePage, 1), tracePages);
    const traceStart = (tracePage - 1) * TRACE_PAGE_SIZE;
    const visibleTraceRows = traceRows.slice(traceStart, traceStart + TRACE_PAGE_SIZE);
    h += traceRows.length ? table(["event","detail","usage","when"], visibleTraceRows.map(e => {
      const usage = e.provider && e.usage
        ? `${e.provider}${e.model?` · ${e.model}`:""} · ${e.usage.in||0} in / ${e.usage.out||0} out`
        : "";
      return `<tr><td><code>${esc(e.type)}</code></td><td class="meta">${esc(String(e.detail).slice(0,60))}</td>
         <td class="meta">${esc(usage)}</td>
          <td class="meta">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td></tr>`;
    })) + tracePageNav(traceRows.length, tracePage, tracePages)
      : `<div class="card empty">no trace lines yet — talk to PU</div>`;
    h += `<div class="meta" style="margin-top:8px">Span waterfalls: <code>make trace</code> + <code>OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317</code>.</div>`;

    return h;
  },
};
