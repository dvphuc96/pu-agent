// pu dashboard — render/refresh loop, resizers/chrome, bootstrap (LOADS LAST).
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

let activeView = null, activeSub = null;
// The hash keys stay as they are — #settings is linked from graph.js, views.js,
// the README and DEMO-CHECKLIST, and from anyone's bookmark. Only the LABEL
// moved: after the Connections registry took keys, providers and integrations
// out of that page, what remained was two switches that change how a turn runs,
// which is a behaviour, not a setting.
const TITLES = {today:"Hôm nay", review:"Chờ phê duyệt", chat:"Trò chuyện", ops:"LLM Ops",
                crm:"CRM — inbox synced read-only from Gmail",
                graph:"Graph workflows — structure around the loop",
                agents:"Agents — specialized least-privilege roles",
                research:"Research — multi-agent morning workflow",
                reports:"Reports — grounded artifacts and provenance",
                evaluations:"Evaluations — report quality and regression gates",
                settings:"Behaviour — how a turn runs",
                database:"Database — everything PU stores (state.db)",
                approvals:"Approvals — human review before side effects",
                security:"Security — enforced tool boundary"};
function render(){
  if (!D) return;
  const [v, subRaw] = (location.hash||"#chat").slice(1).split("/");
  const sub = subRaw || null;
  const view = VIEWS[v] ? v : "overview";
  const notebook = ["chat","today","review","research","reports"].includes(view);
  document.body.classList.toggle("notebook-mode", notebook);
  const subChanged = sub !== activeSub || view !== activeView;
  const effSub = sub;
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",
    a.dataset.v === view && (!a.dataset.sub || a.dataset.sub === effSub)));
  document.getElementById("title").textContent =
    TITLES[`${view}/${effSub}`] || TITLES[view] || view[0].toUpperCase()+view.slice(1);
  if (view==='chat'){
    if(subChanged||!document.querySelector('.chat-main'))document.getElementById('view').innerHTML=VIEWS.chat(D);
  } else if (notebook){
    // Polling general telemetry must not reset source selection, questions or dialogs.
    if ((subChanged || !document.querySelector('.nb-root') || ["today","review"].includes(view)) && !(document.activeElement && document.activeElement.closest && document.activeElement.closest('#brief-settings'))){
      if (subChanged){NB.source=null;NB.availability=null;NB.selectedSlot=null;NB.error='';NB.loadedId=null;}
      document.getElementById("view").innerHTML = VIEWS[view](D, sub);
    }
  } else if (view === "overview" || view === "graph"){
    // don't rebuild mid-animation or the glowing SVG gets wiped
    if (activeView !== view || !animating){ document.getElementById("view").innerHTML = VIEWS[view](D); }
  } else if ((view === "memory" || view === "settings" || view === "database" || view === "models" || view === "connections" || view === "approvals") && editing && !subChanged){
    // don't wipe an in-progress edit on the 5s refresh — but DO switch sub-tabs
  } else {
    editing = false;
    // Rebuilding #view innerHTML resets the scroll. On a same-view refresh (the
    // 5s poll, a sort click) keep the reader where they were — only jump to top
    // on an actual navigation (subChanged), where top is correct.
    const main = document.querySelector("main");
    const keepScroll = !subChanged && main;
    const y = keepScroll ? main.scrollTop : 0;
    document.getElementById("view").innerHTML = VIEWS[view](D, sub);
    if (keepScroll) main.scrollTop = y;
  }
  activeView = view; activeSub = sub;
  document.getElementById("model").textContent = `${D.provider} · ${D.model}`;
  document.getElementById("n-gw").textContent = (D.chat_log||[]).length;
  document.getElementById("n-loop").textContent = D.stats.turns;
  document.getElementById("n-graph").textContent =
    (D.graph && (D.graph.stats.quick + D.graph.stats.full)) || "";
  document.getElementById("n-mem").textContent = D.facts.length + D.episodes.length;
  document.getElementById("n-tools").textContent = (D.tools && D.tools.catalog.length) || "";
  document.getElementById("n-approvals").textContent = (D.approvals && D.approvals.pending) || "";
  document.getElementById("n-security").textContent = (D.security && D.security.blocked) || "";
  document.getElementById("n-crm").textContent = (D.crm || []).filter(e => e.unread).length || "";
  document.getElementById("n-db").textContent = (D.db && D.db.all_tables.length) || "";
  const sloAlerts = (((D.agentops||{}).slo||{}).alerts||[]).length;
  document.getElementById("n-ops").textContent = sloAlerts || D.stats.tool_errors || (D.eval_report ? "" : "!");
}
let lastFetch = Date.now();
function tickLive(){
  if (!D) return;
  const ago = Math.round((Date.now()-lastFetch)/1000);
  document.getElementById("sub").innerHTML =
    `<span class="live"><span class="dot"></span>live</span> · updated ${ago}s ago · ${esc(D.home)}`;
}
let dockRestored = false;
async function restoreDock(){
  // On page load the dock is empty even though the current thread has messages
  // — restore them so a refresh never looks like it lost the chat.
  dockRestored = true;
  const sid = D && D.current_session;
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}
async function refresh(){
  try {
    const next = await (await fetch("/api/data")).json();
    if(D && (D.data_epoch||'')!==(next.data_epoch||'')){
      CHAT.length=0;
      Object.assign(NB,{items:null,detail:null,requested:null,source:null,availability:null,loadedId:null});
      document.getElementById('chat-approval-dialog')?.remove();
      activeView=null;activeSub=null;dockRestored=false;
    }
    D = next; lastFetch = Date.now();
    render(); tickLive();
    if(typeof briefRefresh==='function')briefRefresh();
    if(typeof syncChatApprovals==='function')syncChatApprovals();
    syncModelChip();  // keep the dock's model pill in sync with the active brain
    applyTele();      // reflect the stats on/off choice (default on)
    syncLiveView();   // live-update an opened conversation
    if (!dockRestored) restoreDock();
  } catch(e){ /* server restarting — keep showing last data */ }
}
// --- resizable columns: drag the thin handle between nav|main and main|dock.
// Width lives in a CSS var + localStorage, so it survives refreshes.
function wireResizer(id, cssVar, key, fromRight, min, max){
  const el = document.getElementById(id);
  if (!el) return;
  el.onmousedown = e => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const move = ev => {
      let w = fromRight ? (window.innerWidth - ev.clientX) : ev.clientX;
      w = Math.max(min, Math.min(max, w));
      document.documentElement.style.setProperty(cssVar, w + "px");
      localStorage.setItem(key, w);
    };
    const up = () => { document.body.classList.remove("resizing");
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };
}
function wireChrome(){
  // restore saved widths
  const nw = localStorage.getItem("navW"); if (nw) document.documentElement.style.setProperty("--nav-w", nw+"px");
  const dw = localStorage.getItem("dockW"); if (dw) document.documentElement.style.setProperty("--dock-w", dw+"px");
  wireResizer("nav-resizer", "--nav-w", "navW", false, 150, 380);
  wireResizer("dock-resizer", "--dock-w", "dockW", true, 260, 680);
  // hide / show the sidebar
  const setNav = v => { document.body.classList.toggle("nav-hidden", v); localStorage.setItem("navHidden", v?"1":"0"); };
  const nt = document.getElementById("nav-toggle"), nr = document.getElementById("nav-reopen");
  if (nt) nt.onclick = () => setNav(true);
  if (nr) nr.onclick = () => setNav(false);
  setNav(localStorage.getItem("navHidden") === "1" || (!localStorage.getItem("navHidden") && window.innerWidth < 900));
}

window.addEventListener("hashchange", render);
window.__hold = (v)=>{ animating = v; };   // test hook: freeze the diagram
wireDock(); wireChrome();
refresh(); setInterval(refresh, 5000); setInterval(tickLive, 1000);
pollEvents(); setInterval(pollEvents, 450);   // live harness animation
