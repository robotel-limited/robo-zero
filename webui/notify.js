/* notificare "task terminat" când tab-ul nu e activ: beep scurt (Web Audio,
   fără fișier audio) + cerculeț galben pe favicon. Nu facem nimic dacă tabul
   e vizibil. Folosit din app.js la fiecare eveniment terminal (final/error/
   cancelled) — pot fi mai multe task-uri într-un chat, notificăm la fiecare. */

const faviconLink = document.querySelector('link[rel="icon"]');
const baseFaviconHref = faviconLink ? faviconLink.href : null;
let dotFaviconHref = null;
let audioCtx = null;

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

async function buildDotFaviconHref() {
  if (dotFaviconHref || !baseFaviconHref) return dotFaviconHref;
  const svgText = await (await fetch(baseFaviconHref)).text();
  const withDot = svgText.replace(
    "</svg>",
    '<circle cx="860" cy="100" r="110" fill="#ffd400" stroke="#01041a" stroke-width="16"/></svg>'
  );
  dotFaviconHref = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(withDot)));
  return dotFaviconHref;
}

function setFaviconDot(on) {
  if (!faviconLink) return;
  if (on) {
    buildDotFaviconHref().then((href) => {
      if (href) faviconLink.href = href;
    });
  } else {
    faviconLink.href = baseFaviconHref;
  }
}

function notifyIfHidden() {
  if (document.hidden) {
    playBeep();
    setFaviconDot(true);
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) setFaviconDot(false);
});
