/* pagina Setări — separată de index.html, ca să poată crește cu mai multe opțiuni. */

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

// un toggle = {toggleId, stateId, settingsKey} — citește/scrie o cheie din /api/settings.
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
      window.alert(`Eroare la salvarea setărilor: ${e.message}`);
    }
  });

  return setState;
}

(async function init() {
  const setters = TOGGLES.map(bindToggle);
  try {
    const settings = await api("/api/settings");
    TOGGLES.forEach((t, i) => setters[i](!!settings[t.settingsKey]));
  } catch (e) {
    /* indisponibil — rămâne starea implicită a elementelor din HTML */
  }
})();
