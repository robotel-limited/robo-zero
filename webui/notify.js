/* notificare "task terminat" când tab-ul nu e activ: beep scurt (Web Audio,
   fără fișier audio) + favicon care pâlpâie (alternează cu un pătrat galben
   solid, vizibil și la dimensiunea mică a unui tab). Nu facem nimic dacă
   tabul e vizibil. Folosit din app.js la fiecare eveniment terminal
   (final/error/cancelled) — pot fi mai multe task-uri într-un chat,
   notificăm la fiecare. */

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
  // AudioContext poate porni doar după un gest al utilizatorului — "unlock"
  // la trimiterea mesajului, mult înainte ca task-ul să se termine.
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
    /* audio indisponibil — ignorăm */
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
