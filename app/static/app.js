// auto-router admin UI. All /api/* calls send the router key as a bearer token,
// stored in localStorage. Phase 3: themed, responsive, mobile drawer.
const state = {
  key: localStorage.getItem("router_key") || "",
  models: [],
  disabled: new Set(),
  settings: null,
  presets: null,
  logOffset: 0,
  logLimit: 50,
  logTotal: 0,
};

const $ = (sel) => document.querySelector(sel[0] === "#" ? sel : "#" + sel);
const el = (tag, text) => { const e = document.createElement(tag); if (text != null) e.textContent = text; return e; };

// A table cell that carries its column name so the mobile card layout can show a label.
function cell(label, value, cls) {
  const td = el("td");
  if (label) td.dataset.label = label;
  if (cls) td.className = cls;
  if (value instanceof Node) td.appendChild(value);
  else td.textContent = value == null ? "" : String(value);
  return td;
}

function showError(msg) { $("#error").textContent = msg || ""; }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { Authorization: "Bearer " + state.key, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  const text = await res.text();
  if (!res.ok) throw new Error(res.status + " " + text.slice(0, 300));
  return text ? JSON.parse(text) : null;
}

const fmtCost = (c) => "$" + Number(c || 0).toFixed(6);
const fmtTime = (t) => (t ? new Date(t).toLocaleString() : "");
const fmtProb = (v) => (v == null ? "" : Number(v).toFixed(2));
const fmtPrice = (v) => {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  if (n === 0) return "$0";
  const perM = n * 1e6;
  return "$" + (perM >= 0.01 ? perM.toFixed(2) : perM.toFixed(4)) + "/M";
};
function fmtAgo(iso) {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return "just now";
  const s = Math.floor(diff / 1000);
  if (s < 60) return s + "s ago";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  return Math.floor(h / 24) + "d ago";
}

// --- theme toggle ---
function currentTheme() { return document.documentElement.dataset.theme || "dark"; }
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("ar-theme", t);
  const btn = $("#theme-toggle");
  if (btn) {
    const next = t === "dark" ? "light" : "dark";
    btn.textContent = t === "dark" ? "☀" : "🌙";
    btn.setAttribute("aria-pressed", String(t === "dark"));
    btn.setAttribute("aria-label", "Switch to " + next + " mode");
    btn.setAttribute("title", "Switch to " + next + " mode");
  }
}
applyTheme(currentTheme());
$("#theme-toggle").onclick = () => applyTheme(currentTheme() === "dark" ? "light" : "dark");

// --- mobile nav drawer (the whole sidebar slides in as the drawer below 900px) ---
const navEl = $("#sidebar");
const backdrop = $("#drawer-backdrop");
const navToggle = $("#nav-toggle");
function openDrawer() {
  navEl.classList.add("is-open");
  backdrop.classList.add("is-open");
  navToggle.setAttribute("aria-expanded", "true");
  // Focus the first drawer item so keyboard users land inside the drawer. Deferred: the
  // click that opened the drawer lands focus on the toggle button itself, and the drawer is
  // not focusable until its new visibility has been computed.
  const focusFirstItem = () => {
    const first = navEl.querySelector("#primary-nav button");
    if (first) first.focus();
  };
  requestAnimationFrame(focusFirstItem);
  setTimeout(focusFirstItem, 0);
}
function closeDrawer() {
  navEl.classList.remove("is-open");
  backdrop.classList.remove("is-open");
  navToggle.setAttribute("aria-expanded", "false");
}
navToggle.onclick = () => (navEl.classList.contains("is-open") ? closeDrawer() : openDrawer());
backdrop.onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// --- desktop sidebar collapse (icon-only rail) ---
const collapseBtn = $("#sidebar-collapse");
if (collapseBtn) {
  collapseBtn.onclick = () => {
    const collapsed = document.documentElement.classList.toggle("sidebar-collapsed");
    localStorage.setItem("ar-sidebar", collapsed ? "collapsed" : "expanded");
    collapseBtn.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    collapseBtn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  };
}

// --- auth bar ---
$("#save-key").onclick = () => {
  state.key = $("#router-key").value.trim();
  localStorage.setItem("router_key", state.key);
  $("#auth-status").textContent = state.key ? "key saved" : "key cleared";
  loadModels();
  loadHealth();
};

// --- tabs ---
document.querySelectorAll("nav button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((s) => (s.hidden = true));
    $("#tab-" + btn.dataset.tab).hidden = false;
    document.querySelectorAll(".nav-link").forEach((b) => b.classList.toggle("is-active", b === btn));
    closeDrawer();
    window.scrollTo(0, 0);
    if (btn.dataset.tab === "log") loadLog();
    if (btn.dataset.tab === "spend") { loadSpend(); loadHealth(); }
    if (btn.dataset.tab === "models") loadModelCatalog();
    if (btn.dataset.tab === "routing") loadRoutingEngine();
    if (btn.dataset.tab === "keys") loadKeys();
    if (btn.dataset.tab === "settings") loadSettings();
  };
});

// --- health indicator (Jev connection heartbeat) ---
async function loadHealth() {
  const box = $("#decision-health");
  try {
    const h = await fetch("/health").then((r) => r.json());
    const lastOk = h.last_successful_decision_at ? +new Date(h.last_successful_decision_at) : 0;
    const lastFb = h.last_fallback_at ? +new Date(h.last_fallback_at) : 0;
    const lastReq = h.last_request_at ? +new Date(h.last_request_at) : 0;
    if (!lastReq) {
      box.textContent = "Jev: idle";
      box.title = "No requests routed yet.";
      box.className = "health";
    } else if (lastFb > lastOk) {
      box.textContent = "Jev: fallback";
      box.title = "Falling back" + (h.last_fallback_at ? " (last " + fmtAgo(h.last_fallback_at) + ")" : "") +
        ". Jev couldn't make a confident decision recently.";
      box.className = "health danger";
    } else {
      box.textContent = "Jev: connected";
      box.title = "Working — last decision " + fmtAgo(h.last_successful_decision_at);
      box.className = "health ok";
    }
  } catch (e) {
    box.textContent = "Jev: down";
    box.title = "Health check failed: " + e.message;
    box.className = "health danger";
  }
}
setInterval(loadHealth, 15000);

// --- request log ---
async function loadLog() {
  showError("");
  const f = new FormData($("#log-filters"));
  const params = new URLSearchParams();
  for (const [k, v] of f.entries()) if (v !== "") params.set(k, v);
  params.set("limit", state.logLimit);
  params.set("offset", state.logOffset);
  try {
    const data = await api("/api/requests?" + params.toString());
    const tb = $("#log-table tbody");
    tb.innerHTML = "";
    for (const r of data.rows) {
      const tr = el("tr");
      tr.className = r.used_fallback ? "row-fallback" : "";
      const routing = el("span", r.used_fallback ? "FALLBACK" : (r.escalation_fired ? "escalated" : "normal"));
      routing.className = r.used_fallback ? "badge fallback" : (r.escalation_fired ? "badge escalated" : "badge normal");
      const cells = [
        ["time", fmtTime(r.timestamp)], ["caller", r.caller_id], ["tier", r.tier], ["model", r.model],
        ["conf", fmtProb(r.confidence), "num"], ["gap", fmtProb(r.probability_gap), "num"],
        ["esc", r.escalation_fired ? "yes" : "", "num"],
        ["in", r.input_tokens, "num"], ["out", r.output_tokens, "num"], ["cost", fmtCost(r.cost_usd), "num"],
        ["latency", r.latency_ms + "ms", "num"], ["ok", r.success ? "ok" : "FAIL"],
      ];
      cells.forEach(([label, c, cls]) => tr.appendChild(cell(label, c, cls)));
      const rt = cell("routing"); rt.appendChild(routing); tr.appendChild(rt);

      const dt = cell("");
      const btn = el("button", "details");
      btn.className = "btn btn-ghost";
      btn.onclick = () => { detail.hidden = !detail.hidden; };
      dt.appendChild(btn); tr.appendChild(dt);
      tb.appendChild(tr);

      // hidden detail row
      const detail = el("tr");
      detail.className = "row-detail";
      detail.hidden = !$("#log-show-prompts").checked;
      const dtd = el("td"); dtd.colSpan = 14;
      const prob = r.probabilities
        ? Object.entries(r.probabilities).map(([k, v]) => k + " " + fmtProb(v)).join("  ·  ")
        : "(none)";
      dtd.appendChild(el("div", "chosen model: " + (r.model || "(none)")));
      dtd.appendChild(el("div", "prompt: " + (r.prompt_preview || "(not stored — turn on 'preview' for that key)")));
      dtd.appendChild(el("div", "error: " + (r.error || "(none)")));
      dtd.appendChild(el("div", "jev_error: " + (r.jev_error || "(none)")));
      dtd.appendChild(el("div", "probabilities: " + prob));
      detail.appendChild(dtd);
      tb.appendChild(detail);
    }
    state.logTotal = data.total;
    $("#log-summary").textContent = `${data.total} total matching rows; showing ${data.total ? data.offset + 1 : 0}-${Math.min(data.offset + data.limit, data.total)}`;
    $("#log-page").textContent = ` page ${Math.floor(data.offset / data.limit) + 1} `;
    $("#log-prev").disabled = data.offset <= 0;
    $("#log-next").disabled = data.offset + data.limit >= data.total;
  } catch (e) { showError("Request log: " + e.message); }
}
$("#log-filters").onsubmit = (e) => { e.preventDefault(); state.logOffset = 0; loadLog(); };
$("#log-show-prompts").onchange = () => loadLog();
$("#log-reset").onclick = () => { $("#log-filters").reset(); state.logOffset = 0; loadLog(); };
$("#log-prev").onclick = () => { state.logOffset = Math.max(0, state.logOffset - state.logLimit); loadLog(); };
$("#log-next").onclick = () => { state.logOffset += state.logLimit; loadLog(); };

// --- spend ---
async function loadSpend() {
  showError("");
  try {
    const s = await api("/api/spend?bucket=" + $("#spend-bucket").value);
    $("#spend-totals").textContent =
      `App-calculated spend: ${fmtCost(s.total_cost)} across ${s.total_requests} requests ` +
      `(${s.input_tokens} in / ${s.output_tokens} out tokens).`;
    fillTable("#spend-periods tbody", ["period", "cost", "requests"], s.by_period, (r) => [fmtTime(r.period), fmtCost(r.cost), r.requests]);
    fillTable("#spend-tiers tbody", ["tier", "cost", "requests"], s.by_tier, (r) => [r.tier, fmtCost(r.cost), r.requests]);
    fillTable("#spend-models tbody", ["model", "cost", "requests"], s.by_model, (r) => [r.model, fmtCost(r.cost), r.requests]);
  } catch (e) { showError("Spend: " + e.message); }
  try {
    const c = await api("/api/openrouter/credits");
    $("#spend-balance").textContent =
      `OpenRouter: balance ${fmtCost(c.balance)} (credits ${fmtCost(c.total_credits)}, usage ${fmtCost(c.total_usage)}). ` +
      `Note: OpenRouter usage is account-wide and lifetime; the app total above is only what this router has logged.`;
  } catch (e) { $("#spend-balance").textContent = "OpenRouter balance unavailable: " + e.message; }
}
function fillTable(sel, labels, rows, mapFn) {
  const tb = $(sel); tb.innerHTML = "";
  for (const r of rows) {
    const tr = el("tr");
    mapFn(r).forEach((c, i) => tr.appendChild(cell(labels[i], c)));
    tb.appendChild(tr);
  }
}
$("#spend-bucket").onchange = loadSpend;
$("#spend-refresh").onclick = loadSpend;

// --- model catalog v3 (OpenRouter catalog + local routing table) ---

// OpenRouter catalog data
state.orModels = [];          // full catalog, sorted by the active sort key
state.orModelsLoaded = false;
state.orPage = 1;             // current catalog page (1-indexed)
state.orSort = "popular";     // active sort key (default: Most Popular)
const OR_PAGE_SIZE = 20;      // models per page

// Sort options for the OpenRouter catalog. `field` is the merged model key,
// `dir` is -1 (desc) / +1 (asc). Nulls always sort to the bottom.
const OR_SORTS = [
  { key: "popular",     label: "Most Popular",                  field: "weekly_tokens",      dir: -1 },
  { key: "newest",      label: "Newest",                        field: "created",            dir: -1 },
  { key: "oldest",      label: "Oldest",                        field: "created",            dir: 1 },
  { key: "topweekly",   label: "Top Weekly",                    field: "weekly_tokens",      dir: -1 },
  { key: "weekly-asc",  label: "Weekly Tokens: Low to High",    field: "weekly_tokens",      dir: 1 },
  { key: "discount",    label: "Discount: High to Low",         field: "discount",           dir: -1 },
  { key: "price-asc",   label: "Pricing: Low to High",          field: "price_usd",          dir: 1 },
  { key: "price-desc",  label: "Pricing: High to Low",          field: "price_usd",          dir: -1 },
  { key: "ctx-desc",    label: "Context: High to Low",          field: "context_length",     dir: -1 },
  { key: "ctx-asc",     label: "Context: Low to High",          field: "context_length",     dir: 1 },
  { key: "tput-desc",   label: "Throughput: High to Low",       field: "p50_throughput",     dir: -1 },
  { key: "tput-asc",    label: "Throughput: Low to High",       field: "p50_throughput",     dir: 1 },
  { key: "lat-asc",     label: "Latency: Low to High",          field: "p50_latency",        dir: 1 },
  { key: "lat-desc",    label: "Latency: High to Low",          field: "p50_latency",        dir: -1 },
  { key: "intel-desc",  label: "Intelligence: High to Low",     field: "intelligence_index", dir: -1 },
  { key: "intel-asc",   label: "Intelligence: Low to High",     field: "intelligence_index", dir: 1 },
  { key: "coding-desc", label: "Coding: High to Low",           field: "coding_index",       dir: -1 },
  { key: "coding-asc",  label: "Coding: Low to High",           field: "coding_index",       dir: 1 },
  { key: "agent-desc",  label: "Agentic: High to Low",          field: "agentic_index",      dir: -1 },
  { key: "agent-asc",   label: "Agentic: Low to High",          field: "agentic_index",      dir: 1 },
  { key: "design",      label: "Design Arena ELO: High to Low", field: "design_arena_elo",   dir: -1 },
];

function sortORCatalog() {
  const s = OR_SORTS.find(x => x.key === state.orSort) || OR_SORTS[0];
  const desc = s.dir < 0;
  state.orModels.sort((a, b) => {
    const av = a[s.field], bv = b[s.field];
    const aNull = av === null || av === undefined || av === "";
    const bNull = bv === null || bv === undefined || bv === "";
    if (aNull && bNull) return 0;
    if (aNull) return 1;   // nulls always sort last
    if (bNull) return -1;
    if (av === bv) return 0;
    return desc ? (av > bv ? -1 : 1) : (av < bv ? -1 : 1);
  });
}

async function loadModelCatalog() {
  $("models-error").style.display = "none";
  // Load both in parallel
  await Promise.all([loadORCatalog(), loadLocalModels()]);
  renderMyModels();
}

// --- OpenRouter catalog ---
async function loadORCatalog() {
  try {
    const data = await api("/api/openrouter/models");
    state.orModels = (data.data || []).filter(m => m.id && m.pricing);
    sortORCatalog();
    state.orModelsLoaded = true;
  } catch (e) {
    $("or-table").querySelector("tbody").innerHTML =
      '<tr><td colspan="5">OpenRouter catalog requires a valid router key. Enter your key in the top bar.</td></tr>';
  }
  renderORTable();
}

function renderORTable() {
  const q = ($("or-search").value || "").toLowerCase();
  const localIDs = new Set(state.myModels.map(m => m.openrouter_model_id));
  const tb = $("or-table").querySelector("tbody");
  tb.innerHTML = "";

  // Filter the full catalog by the search query.
  const matches = q
    ? state.orModels.filter(m => m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q))
    : state.orModels;

  // Paginate.
  const pageCount = Math.max(1, Math.ceil(matches.length / OR_PAGE_SIZE));
  if (state.orPage < 1) state.orPage = 1;
  if (state.orPage > pageCount) state.orPage = pageCount;
  const start = (state.orPage - 1) * OR_PAGE_SIZE;
  const pageModels = matches.slice(start, start + OR_PAGE_SIZE);

  if (pageModels.length === 0) {
    tb.innerHTML = '<tr><td colspan="5">No models match your search.</td></tr>';
  }

  for (const m of pageModels) {
    const tr = el("tr");
    const added = localIDs.has(m.id);
    if (added) tr.className = "row-disabled";
    const promptCost = m.pricing ? parseFloat(m.pricing.prompt || 0) * 1e6 : null;
    const compCost = m.pricing ? parseFloat(m.pricing.completion || 0) * 1e6 : null;
    const costStr = (promptCost !== null && compCost !== null)
      ? "$" + promptCost.toFixed(2) + " / $" + compCost.toFixed(2)
      : (m.pricing ? "$" + (parseFloat(m.pricing.prompt || 0) * 1e6).toFixed(2) : "—");
    [m.id, m.name || "—", m.context_length != null ? m.context_length.toLocaleString() : "—", costStr].forEach(c => {
      const td = el("td", c);
      tr.appendChild(td);
    });
    const actTd = el("td");
    if (added) {
      const badge = el("span", "Added");
      badge.className = "badge normal";
      actTd.appendChild(badge);
    } else {
      const btn = el("button", "+ Add");
      btn.className = "btn btn-primary";
      btn.style.fontSize = "var(--text-xs)";
      btn.onclick = async () => {
        btn.disabled = true;
        btn.textContent = "Adding...";
        try {
          await api("/api/models", {
            method: "POST",
            body: JSON.stringify({
              display_name: m.name || m.id,
              openrouter_model_id: m.id,
              context_length: m.context_length || 128000,
              cost_input_per_1m: promptCost,
              cost_output_per_1m: compCost,
              description: (m.name || m.id) + " — " + (m.context_length || "?") + " ctx",
            }),
          });
          await loadModelCatalog();
        } catch (e) {
          $("models-error").style.display = "block";
          $("models-error").textContent = "Add failed: " + e.message;
          btn.disabled = false;
          btn.textContent = "+ Add";
        }
      };
      actTd.appendChild(btn);
    }
    tr.appendChild(actTd);
    tb.appendChild(tr);
  }

  $("or-count").textContent =
    (matches.length === 0 ? "0" : (start + 1) + "–" + (start + pageModels.length)) +
    " of " + matches.length + " matching · " + state.orModels.length + " total";

  $("or-page").textContent = "Page " + state.orPage + " of " + pageCount;
  $("or-prev").disabled = state.orPage <= 1;
  $("or-next").disabled = state.orPage >= pageCount;
}

// Search on input (reset to page 1)
$("or-search").oninput = () => { state.orPage = 1; renderORTable(); };

// Pagination controls
$("or-prev").onclick = () => { state.orPage--; renderORTable(); };
$("or-next").onclick = () => { state.orPage++; renderORTable(); };

// Populate the sort dropdown from OR_SORTS (single source of truth)
(function populateSort() {
  const sel = $("or-sort");
  for (const s of OR_SORTS) {
    const opt = el("option", s.label);
    opt.value = s.key;
    sel.appendChild(opt);
  }
  sel.value = state.orSort;
})();

// Sort dropdown — resort + repaginate on change
$("#or-sort").onchange = () => { state.orPage = 1; sortORCatalog(); renderORTable(); };

// Models sub-tabs (OpenRouter models vs Your models)
function showModelsSubTab(which) {
  const isCatalog = which === "catalog";
  $("#models-sub-catalog").hidden = !isCatalog;
  $("#models-sub-yours").hidden = isCatalog;
  $("#models-tab-catalog").className = "btn " + (isCatalog ? "btn-primary" : "btn-ghost");
  $("#models-tab-yours").className = "btn " + (isCatalog ? "btn-ghost" : "btn-primary");
}
showModelsSubTab("catalog");
$("#models-tab-catalog").onclick = () => showModelsSubTab("catalog");
$("#models-tab-yours").onclick = () => showModelsSubTab("yours");

// --- Your models (local routing table) ---
state.myModels = [];

async function loadLocalModels() {
  try {
    const data = await api("/api/models");
    state.myModels = data.data || [];
  } catch (e) { state.myModels = []; }
  // Refresh OR table to update Added badges
  if (state.orModelsLoaded) renderORTable();
}

function renderMyModels() {
  const tb = $("models-table").querySelector("tbody");
  tb.innerHTML = "";
  if (state.myModels.length === 0) {
    tb.innerHTML = '<tr><td colspan="6">No models in your router yet. Browse the OpenRouter catalog above and click +Add.</td></tr>';
    return;
  }
  for (const m of state.myModels) {
    const tr = el("tr");
    if (!m.enabled) tr.className = "row-disabled";

    [m.display_name, m.openrouter_model_id, (m.context_length || "—").toLocaleString()].forEach(c => {
      tr.appendChild(el("td", String(c ?? "—")));
    });

    // Fallback badge
    const ftd = el("td");
    const fb = el("span", m.is_fallback_default ? "fallback" : "—");
    fb.className = m.is_fallback_default ? "badge escalated" : "badge normal";
    ftd.appendChild(fb);
    tr.appendChild(ftd);

    // Enabled toggle
    const etd = el("td");
    const toggle = el("button", m.enabled ? "on" : "off");
    toggle.className = "toggle " + (m.enabled ? "on" : "off");
    toggle.onclick = async () => {
      try {
        await api("/api/models/" + m.id, {
          method: "PUT",
          body: JSON.stringify({ enabled: !m.enabled }),
        });
        await loadModelCatalog();
      } catch (e) {
        $("models-error").style.display = "block";
        $("models-error").textContent = "Toggle: " + e.message;
      }
    };
    etd.appendChild(toggle);
    tr.appendChild(etd);

    // Actions
    const actTd = el("td");
    const editBtn = el("button", "Edit");
    editBtn.className = "btn btn-ghost";
    editBtn.onclick = () => openModelForm(m);
    actTd.appendChild(editBtn);
    tr.appendChild(actTd);

    tb.appendChild(tr);
  }
}

$("models-refresh").onclick = loadModelCatalog;

// --- Edit form ---
function openModelForm(model) {
  if (!model) return;
  $("models-form-card").style.display = "block";
  $("models-form-title").textContent = "Edit: " + (model.display_name || model.openrouter_model_id);
  $("models-form-id").value = model.id || "";
  $("models-form-name").value = model.display_name || "";
  $("models-form-slug").value = model.openrouter_model_id || "";
  $("models-form-ctx").value = model.context_length || "";
  $("models-form-cost-in").value = model.cost_input_per_1m ?? "";
  $("models-form-cost-out").value = model.cost_output_per_1m ?? "";
  $("models-form-desc").value = model.description || "";
  $("models-form-status").textContent = "";
}

$("models-form-save").onclick = async () => {
  const id = $("models-form-id").value;
  const body = {
    display_name: $("models-form-name").value,
    openrouter_model_id: $("models-form-slug").value,
    context_length: parseInt($("models-form-ctx").value) || 128000,
    cost_input_per_1m: parseFloat($("models-form-cost-in").value) || null,
    cost_output_per_1m: parseFloat($("models-form-cost-out").value) || null,
    description: $("models-form-desc").value,
  };
  try {
    if (id) {
      await api("/api/models/" + id, { method: "PUT", body: JSON.stringify(body) });
    } else {
      await api("/api/models", { method: "POST", body: JSON.stringify(body) });
    }
    $("models-form-card").style.display = "none";
    $("models-form-status").textContent = "";
    await loadModelCatalog();
  } catch (e) {
    $("models-form-status").textContent = "Error: " + e.message;
  }
};

$("models-form-cancel").onclick = () => {
  $("models-form-card").style.display = "none";
  $("models-form-status").textContent = "";
};
// Preload the OpenRouter catalog (used at init + after saving the API key).
async function loadModels() {
  if (!state.orModelsLoaded) await loadORCatalog();
}

// --- routing engine ---
function routingSetBias(name, val) {
  const pct = Math.round((val == null ? 0.5 : val) * 100);
  $("#routing-" + name).value = pct;
  $("#routing-" + name + "-val").textContent = pct + "%";
}
["speed", "accuracy", "cost"].forEach((name) => {
  $("#routing-" + name).oninput = () => {
    $("#routing-" + name + "-val").textContent = $("#routing-" + name).value + "%";
  };
});
async function loadRoutingEngine() {
  showError("");
  try {
    const data = await api("/api/settings");
    routingSetBias("speed", data.settings.speed_bias);
    routingSetBias("accuracy", data.settings.accuracy_bias);
    routingSetBias("cost", data.settings.cost_bias);
  } catch (e) { showError("Routing engine: " + e.message); }
}
async function saveRouting() {
  showError("");
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        speed_bias: Number($("#routing-speed").value) / 100,
        accuracy_bias: Number($("#routing-accuracy").value) / 100,
        cost_bias: Number($("#routing-cost").value) / 100,
      }),
    });
    $("#routing-status").textContent = " saved at " + new Date().toLocaleTimeString();
  } catch (e) { showError("Save routing: " + e.message); }
}
$("#routing-save").onclick = saveRouting;
document.querySelectorAll(".routing-preset").forEach((btn) => {
  btn.onclick = async () => {
    $("#routing-speed").value = btn.dataset.speed;
    $("#routing-accuracy").value = btn.dataset.accuracy;
    $("#routing-cost").value = btn.dataset.cost;
    $("#routing-speed-val").textContent = btn.dataset.speed + "%";
    $("#routing-accuracy-val").textContent = btn.dataset.accuracy + "%";
    $("#routing-cost-val").textContent = btn.dataset.cost + "%";
    await saveRouting();
  };
});

// --- keys ---
async function loadKeys() {
  showError("");
  try {
    const data = await api("/api/keys");
    const tb = $("#keys-table tbody"); tb.innerHTML = "";
    for (const k of data.data) {
      const tr = el("tr");
      tr.appendChild(cell("caller", k.caller_id));
      tr.appendChild(cell("key", k.api_key));
      const pvCell = cell("preview");
      const pvBtn = el("button", k.prompt_preview_enabled ? "on" : "off");
      pvBtn.className = "btn " + (k.prompt_preview_enabled ? "btn-primary" : "btn-ghost");
      pvBtn.title = "Toggle whether prompts are stored for this key";
      pvBtn.onclick = async () => {
        try {
          await api("/api/keys/" + k.id + "/preview", {
            method: "PUT", body: JSON.stringify({ prompt_preview_enabled: !k.prompt_preview_enabled }),
          });
          loadKeys();
        } catch (e) { showError("Toggle preview: " + e.message); }
      };
      pvCell.appendChild(pvBtn); tr.appendChild(pvCell);
      tr.appendChild(cell("created", fmtTime(k.created_at)));
      const td = cell("");
      const btn = el("button", "Revoke");
      btn.className = "btn btn-danger";
      btn.onclick = async () => { try { await api("/api/keys/" + k.id, { method: "DELETE" }); loadKeys(); } catch (e) { showError("Revoke: " + e.message); } };
      td.appendChild(btn); tr.appendChild(td);
      tb.appendChild(tr);
    }
  } catch (e) { showError("Keys: " + e.message); }
}
$("#key-create").onsubmit = async (e) => {
  e.preventDefault();
  showError("");
  const f = new FormData($("#key-create"));
  try {
    const row = await api("/api/keys", {
      method: "POST",
      body: JSON.stringify({ caller_id: f.get("caller_id"), prompt_preview_enabled: f.get("prompt_preview_enabled") === "on" }),
    });
    $("#key-created").textContent = "Created. Copy this key now: " + row.api_key;
    $("#key-create").reset();
    loadKeys();
  } catch (e) { showError("Create key: " + e.message); }
};

// --- settings ---
async function loadSettings() {
  showError("");
  try {
    const data = await api("/api/settings");
    state.settings = data.settings;
    state.presets = data.presets;
    $("#settings-preset").value = data.settings.routing_conservatism || "balanced";
    $("#settings-gap").value = data.settings.confidence_gap_threshold;
    $("#settings-preview-default").checked = !!data.settings.prompt_preview_default;
    $("#settings-preview-enabled").checked = !!data.settings.prompt_preview_enabled;
    $("#settings-preview-verbose").checked = !!data.settings.prompt_preview_verbose;
  } catch (e) { showError("Settings: " + e.message); }
}

$("#settings-preset").onchange = () => {
  const p = $("#settings-preset").value;
  if (p !== "custom" && state.presets && state.presets[p] != null) {
    $("#settings-gap").value = state.presets[p];
  }
};
$("#settings-gap").oninput = () => {
  $("#settings-preset").value = "custom";
};
$("#settings-form").onsubmit = async (e) => {
  e.preventDefault();
  showError("");
  const preset = $("#settings-preset").value;
  const body = {
    prompt_preview_default: $("#settings-preview-default").checked,
    prompt_preview_enabled: $("#settings-preview-enabled").checked,
    prompt_preview_verbose: $("#settings-preview-verbose").checked,
  };
  if (preset === "custom") {
    body.confidence_gap_threshold = Number($("#settings-gap").value);
  } else {
    body.routing_conservatism = preset;
  }
  try {
    const data = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    state.settings = data.settings;
    state.presets = data.presets;
    $("#settings-status").textContent = " saved at " + new Date().toLocaleTimeString();
    $("#settings-gap").value = data.settings.confidence_gap_threshold;
    $("#settings-preset").value = data.settings.routing_conservatism;
  } catch (e) { showError("Save settings: " + e.message); }
};

// --- init ---
$("#router-key").value = state.key;
if (state.key) loadModels();
loadHealth();
loadLog(); // the Request log tab is visible on load — populate it instead of showing an empty table
