// Minimal functional (unstyled) admin UI for auto-router.
// All /api/* calls send the router key as a bearer token, stored in localStorage.
const state = {
  key: localStorage.getItem("router_key") || "",
  models: [],
  logOffset: 0,
  logLimit: 50,
  logTotal: 0,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, text) => { const e = document.createElement(tag); if (text != null) e.textContent = text; return e; };

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

// --- auth bar ---
$("#save-key").onclick = () => {
  state.key = $("#router-key").value.trim();
  localStorage.setItem("router_key", state.key);
  $("#auth-status").textContent = state.key ? "key saved" : "key cleared";
  loadModels();
};

// --- tabs ---
document.querySelectorAll("nav button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((s) => (s.hidden = true));
    $("#tab-" + btn.dataset.tab).hidden = false;
    if (btn.dataset.tab === "log") loadLog();
    if (btn.dataset.tab === "spend") loadSpend();
    if (btn.dataset.tab === "tiers") loadTiers();
    if (btn.dataset.tab === "keys") loadKeys();
  };
});

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
      [fmtTime(r.timestamp), r.caller_id, r.tier, r.model, r.input_tokens, r.output_tokens,
       fmtCost(r.cost_usd), r.latency_ms + "ms", r.success ? "ok" : "FAIL", r.used_fallback ? "yes" : "",
       r.error || ""].forEach((c) => tr.appendChild(el("td", c)));
      tb.appendChild(tr);
    }
    state.logTotal = data.total;
    $("#log-summary").textContent = `${data.total} total matching rows; showing ${data.offset + 1}-${Math.min(data.offset + data.limit, data.total)}`;
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
    fillTable("#spend-periods tbody", s.by_period, (r) => [fmtTime(r.period), fmtCost(r.cost), r.requests]);
    fillTable("#spend-tiers tbody", s.by_tier, (r) => [r.tier, fmtCost(r.cost), r.requests]);
    fillTable("#spend-models tbody", s.by_model, (r) => [r.model, fmtCost(r.cost), r.requests]);
  } catch (e) { showError("Spend: " + e.message); }
  try {
    const c = await api("/api/openrouter/credits");
    $("#spend-balance").textContent =
      `OpenRouter: balance ${fmtCost(c.balance)} (credits ${fmtCost(c.total_credits)}, usage ${fmtCost(c.total_usage)}). ` +
      `Note: OpenRouter usage is account-wide and lifetime; the app total above is only what this router has logged.`;
  } catch (e) { $("#spend-balance").textContent = "OpenRouter balance unavailable: " + e.message; }
}
function fillTable(sel, rows, mapFn) {
  const tb = $(sel); tb.innerHTML = "";
  for (const r of rows) { const tr = el("tr"); mapFn(r).forEach((c) => tr.appendChild(el("td", c))); tb.appendChild(tr); }
}
$("#spend-bucket").onchange = loadSpend;
$("#spend-refresh").onclick = loadSpend;

// --- tier mapping ---
async function loadModels() {
  try {
    const data = await api("/api/openrouter/models");
    state.models = data.data || [];
    const dl = $("#model-list");
    dl.innerHTML = "";
    for (const m of state.models) {
      const o = el("option"); o.value = m.id; o.label = m.name || ""; dl.appendChild(o);
    }
  } catch (e) { /* models require a valid key; ignore silently until key set */ }
}

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
      [k.caller_id, k.api_key, k.prompt_preview_enabled ? "on" : "off", fmtTime(k.created_at)].forEach((c) => tr.appendChild(el("td", c)));
      const td = el("td"); const btn = el("button", "Revoke");
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

// --- init ---
$("#router-key").value = state.key;
if (state.key) loadModels();
