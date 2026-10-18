/* Settings page — separate from index.html, so it can grow with more options. */

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

// a toggle = {toggleId, stateId, settingsKey} — reads/writes a key in /api/settings.
const TOGGLES = [
  { toggleId: "verbose-toggle", stateId: "verbose-state", settingsKey: "verbose" },
  { toggleId: "notify-telegram-toggle", stateId: "notify-telegram-state", settingsKey: "notify_telegram" },
  { toggleId: "notify-sms-toggle", stateId: "notify-sms-state", settingsKey: "notify_sms" },
];

function bindToggle({ toggleId, stateId, settingsKey }) {
  const toggle = document.getElementById(toggleId);
  const state = document.getElementById(stateId);

  function setState(on) {
    toggle.checked = on;
    state.textContent = on ? "ON" : "OFF";
  }

  toggle.addEventListener("change", async () => {
    const on = toggle.checked;
    setState(on);
    try {
      await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [settingsKey]: on }),
      });
    } catch (e) {
      setState(!on);
      window.alert(`Error saving settings: ${e.message}`);
    }
  });

  return setState;
}

function formatLatency(ms) {
  if (ms === null || ms === undefined) return "—";
  return `${Math.round(ms)} ms`;
}

function formatCost(usd) {
  if (!usd) return "$0";
  return usd < 0.0001 ? "<$0.0001" : `$${usd.toFixed(4)}`;
}

async function loadUsageTable() {
  const model = document.getElementById("usage-model-filter").value;
  const since = document.getElementById("usage-since").value;
  const until = document.getElementById("usage-until").value;
  const params = new URLSearchParams();
  if (model) params.set("model", model);
  if (since) params.set("since", String(Date.parse(since) / 1000));
  if (until) params.set("until", String(Date.parse(until) / 1000 + 86400));

  const tbody = document.getElementById("usage-table-body");
  try {
    const { rows } = await api(`/api/usage/stats?${params}`);
    tbody.innerHTML = "";
    for (const row of rows) {
      const tr = document.createElement("tr");
      const tokensIn = row.prompt_tokens + row.cache_hit_tokens + row.cache_miss_tokens;
      tr.innerHTML = `
        <td>${row.model}</td>
        <td>${row.calls}</td>
        <td>${tokensIn}</td>
        <td>${row.completion_tokens}</td>
        <td>${formatLatency(row.avg_latency_ms)}</td>
        <td>${formatCost(row.cost_usd)}</td>
      `;
      tbody.appendChild(tr);
    }
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6">No usage in the selected period.</td></tr>';
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6">Error: ${e.message}</td></tr>`;
  }
}

function bindModelSelect() {
  const select = document.getElementById("default-model-select");
  select.addEventListener("change", async () => {
    const value = select.value;
    try {
      await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ default_model: value }),
      });
    } catch (e) {
      window.alert(`Error saving default model: ${e.message}`);
    }
  });
  return select;
}

(async function init() {
  const setters = TOGGLES.map(bindToggle);
  const modelSelect = bindModelSelect();
  try {
    const models = await api("/api/models");
    const usageModelFilter = document.getElementById("usage-model-filter");
    for (const { id, label } of models) {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = label;
      modelSelect.appendChild(opt);
      const filterOpt = opt.cloneNode(true);
      usageModelFilter.appendChild(filterOpt);
    }
  } catch (e) {
    /* unavailable — select stays empty */
  }
  document.getElementById("usage-filter-btn").addEventListener("click", loadUsageTable);
  document.getElementById("usage-model-filter").addEventListener("change", loadUsageTable);
  loadUsageTable();
  try {
    const settings = await api("/api/settings");
    TOGGLES.forEach((t, i) => setters[i](!!settings[t.settingsKey]));
    if (settings.default_model) modelSelect.value = settings.default_model;
  } catch (e) {
    /* unavailable — keep the HTML elements' default state */
  }
})();
