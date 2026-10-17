/* pagina Setări — separată de index.html, ca să poată crește cu mai multe opțiuni. */

const verboseToggle = document.getElementById("verbose-toggle");
const verboseState = document.getElementById("verbose-state");

(function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem("az-theme");
  } catch (e) {
    /* ignorat */
  }
  document.body.classList.toggle("light-mode", saved === "light");
})();

function setVerboseState(verbose) {
  verboseToggle.checked = verbose;
  verboseState.textContent = verbose ? "ON" : "OFF";
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

verboseToggle.addEventListener("change", async () => {
  const verbose = verboseToggle.checked;
  setVerboseState(verbose);
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verbose }),
    });
  } catch (e) {
    setVerboseState(!verbose);
    window.alert(`Eroare la salvarea setărilor: ${e.message}`);
  }
});

(async function init() {
  try {
    const settings = await api("/api/settings");
    setVerboseState(!!settings.verbose);
  } catch (e) {
    /* indisponibil — rămâne starea implicită a elementelor din HTML */
  }
})();
