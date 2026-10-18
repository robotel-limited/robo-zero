/* "task finished" notification when the tab isn't active: short beep (Web
   Audio, no audio file) + flashing favicon (alternates with a solid yellow
   square, visible even at a tab's small size). Does nothing if the tab is
   visible. Used from app.js on every terminal event (final/error/cancelled)
   — a chat can have several tasks, we notify on each. */

const ALERT_FAVICON_HREF =
  "data:image/svg+xml;base64," +
  btoa(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 960">' +
      '<rect width="960" height="960" rx="160" fill="#ffd400"/>' +
      "</svg>"
  );
const FLASH_INTERVAL_MS = 500;

const faviconLink = document.querySelector('link[rel="icon"]');
const baseFaviconHref = faviconLink ? faviconLink.href : null;
let audioCtx = null;
let flashTimer = null;
let flashOn = false;

function unlockAudio() {
  // AudioContext can only start after a user gesture — "unlock" it on
  // sending a message, well before the task finishes.
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  }
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
}

function playBeep() {
  if (!audioCtx) return;
  try {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.15;
    osc.connect(gain).connect(audioCtx.destination);
    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.3);
    osc.stop(audioCtx.currentTime + 0.3);
  } catch (e) {
    /* audio unavailable — ignore */
  }
}

function startFlashing() {
  if (!faviconLink || flashTimer) return;
  flashTimer = setInterval(() => {
    flashOn = !flashOn;
    faviconLink.href = flashOn ? ALERT_FAVICON_HREF : baseFaviconHref;
  }, FLASH_INTERVAL_MS);
}

function stopFlashing() {
  if (flashTimer) {
    clearInterval(flashTimer);
    flashTimer = null;
  }
  flashOn = false;
  if (faviconLink) faviconLink.href = baseFaviconHref;
}

function notifyIfHidden() {
  if (document.hidden) {
    playBeep();
    startFlashing();
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) stopFlashing();
});
