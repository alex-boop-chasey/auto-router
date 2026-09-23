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

const $ = (sel) => document.querySelector(sel);
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
function currentTheme() { return document.documentElement.dataset.theme || "light"; }
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

// --- mobile nav drawer ---
const navEl = $("#primary-nav");
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
    const first = navEl.querySelector("button");
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
    if (btn.dataset.tab === "tiers") loadTiers();
    if (btn.dataset.tab === "keys") loadKeys();
    if (btn.dataset.tab === "settings") loadSettings();
  };
});

// --- health indicator ---
async function loadHealth() {
  const box = $("#decision-health");
  try {
    const h = await fetch("/health").then((r) => r.json());
    box.textContent = "Decision layer last confirmed working: " + fmtAgo(h.last_successful_decision_at);
    box.className = "health " + (h.last_successful_decision_at ? "ok" : "warn");
  } catch (e) {
    box.textContent = "Decision layer status unavailable: " + e.message;
    box.className = "health warn";
  }
}

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
      detail.hidden = true;
      const dtd = el("td"); dtd.colSpan = 14;
      const prob = r.probabilities
        ? Object.entries(r.probabilities).map(([k, v]) => k + " " + fmtProb(v)).join("  ·  ")
        : "(none)";
      dtd.appendChild(el("div", "error: " + (r.error || "(none)")));
      dtd.appendChild(el("div", "jev_error: " + (r.jev_error || "(none)")));
      dtd.appendChild(el("div", "preview: " + (r.prompt_preview || "(not enabled)")));
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

// --- model catalog data (shared) ---
async function loadModels() {
  try {
    const data = await api("/api/openrouter/models");
    state.models = data.data || [];
  } catch (e) { /* models require a valid key; ignore silently until key set */ }
  try {
    const d = await api("/api/models/enabled");
    state.disabled = new Set((d && d.disabled) || []);
  } catch (e) { /* same */ }
  populateModelDatalist();
}

function isEnabled(slug) { return !state.disabled.has(slug); }

function populateModelDatalist() {
  const dl = $("#model-list");
  if (!dl) return;
  const showAll = $("#tiers-show-all") && $("#tiers-show-all").checked;
  dl.innerHTML = "";
  for (const m of state.models) {
    if (!showAll && !isEnabled(m.id)) continue;
    const o = el("option"); o.value = m.id; o.label = m.name || ""; dl.appendChild(o);
  }
}

// --- model catalog page ---
async function loadModelCatalog() {
  showError("");
  await loadModels();
  renderModelCatalog();
}
function renderModelCatalog() {
  const q = ($("#models-search").value || "").toLowerCase();
  const onlyEnabled = $("#models-only-enabled").checked;
  const tb = $("#models-table tbody");
  tb.innerHTML = "";
  let shown = 0;
  for (const m of state.models) {
    const enabled = isEnabled(m.id);
    if (onlyEnabled && !enabled) continue;
    if (q && !(m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q))) continue;
    shown++;
    const tr = el("tr");
    tr.className = enabled ? "" : "row-disabled";
    const price = m.pricing || {};
    const labels = ["model", "name", "context", "prompt", "completion"];
    [m.id, m.name || "", m.context_length != null ? m.context_length : "—",
     fmtPrice(price.prompt), fmtPrice(price.completion)].forEach((c, i) => tr.appendChild(cell(labels[i], c)));
    const etd = cell("enabled");
    const toggle = el("button", enabled ? "enabled" : "disabled");
    toggle.className = enabled ? "toggle on" : "toggle off";
    toggle.onclick = async () => {
      try {
        await api("/api/models/enabled", { method: "PUT", body: JSON.stringify({ model_slug: m.id, enabled: !enabled }) });
        if (enabled) state.disabled.add(m.id); else state.disabled.delete(m.id);
        renderModelCatalog();
        populateModelDatalist();
      } catch (e) { showError("Toggle model: " + e.message); }
    };
    etd.appendChild(toggle); tr.appendChild(etd);
    tb.appendChild(tr);
  }
  $("#models-summary").textContent = `${shown} of ${state.models.length} models shown`;
}
$("#models-search").oninput = renderModelCatalog;
$("#models-only-enabled").onchange = renderModelCatalog;

// --- tier mapping ---
async function loadTiers() {
  showError("");
  await loadModels();
  try {
    const tiers = await api("/api/tiers");
    const box = $("#tiers-editor");
    box.innerHTML = "";
    for (const name of ["simple", "medium", "complex"]) {
      const t = tiers[name] || {};
      const wrap = el("div");
      wrap.className = "tier-row";
      wrap.appendChild(el("label", name + ": "));
      const input = el("input"); input.setAttribute("list", "model-list"); input.value = t.model || "";
      input.dataset.tier = name; input.size = 40; input.className = "tier-model";
      const ctx = el("input"); ctx.type = "number"; ctx.value = t.context_length || ""; ctx.dataset.tier = name; ctx.className = "tier-ctx";
      input.onchange = () => {
        const found = state.models.find((m) => m.id === input.value);
        if (found && found.context_length) ctx.value = found.context_length;
      };
      wrap.appendChild(input);
      wrap.appendChild(el("label", " context_length: "));
      wrap.appendChild(ctx);
      box.appendChild(wrap);
    }
  } catch (e) { showError("Tier mapping: " + e.message); }
}
$("#tiers-show-all").onchange = populateModelDatalist;
$("#tiers-save").onclick = async () => {
  showError("");
  const tiers = {};
  document.querySelectorAll(".tier-model").forEach((i) => {
    const t = i.dataset.tier;
    tiers[t] = { model: i.value.trim(), context_length: Number(document.querySelector(`.tier-ctx[data-tier="${t}"]`).value) };
  });
  try {
    await api("/api/tiers", { method: "PUT", body: JSON.stringify({ tiers }) });
    $("#tiers-status").textContent = " saved at " + new Date().toLocaleTimeString();
  } catch (e) { showError("Save tiers: " + e.message); }
};

// --- keys ---
async function loadKeys() {
  showError("");
  try {
    const data = await api("/api/keys");
    const tb = $("#keys-table tbody"); tb.innerHTML = "";
    for (const k of data.data) {
      const tr = el("tr");
      const labels = ["caller", "key", "preview", "created"];
      [k.caller_id, k.api_key, k.prompt_preview_enabled ? "on" : "off", fmtTime(k.created_at)]
        .forEach((c, i) => tr.appendChild(cell(labels[i], c)));
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
  const body = { prompt_preview_default: $("#settings-preview-default").checked };
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
