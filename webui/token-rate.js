/* Session token counter (color-coded by how full the context window is)
   and the DeepSeek off-peak rate indicator dot. */

const tokenCountTextEl = document.getElementById("token-count-text");
const rateDotEl = document.getElementById("rate-dot");

function formatTokenCount(count) {
  if (count < 1000) return `${count}`;
  const thousands = count / 1000;
  const rounded = thousands >= 10 ? Math.round(thousands) : Math.round(thousands * 10) / 10;
  return `${rounded}k`;
}

function setTokenCount(count, contextTokens) {
  tokenCountTextEl.textContent = `${formatTokenCount(count)} tokens`;
  const fraction = contextTokens ? count / contextTokens : 0;
  let level = "low";
  if (fraction >= 0.9) level = "critical";
  else if (fraction >= 0.7) level = "high";
  else if (fraction >= 0.5) level = "medium";
  tokenCountTextEl.classList.remove(
    "token-count--low",
    "token-count--medium",
    "token-count--high",
    "token-count--critical"
  );
  tokenCountTextEl.classList.add(`token-count--${level}`);
}

async function refreshTokenCount(chatId) {
  try {
    const chat = await api(`/api/chats/${chatId}`);
    if (chatId === currentChatId) setTokenCount(chat.token_count, chat.context_tokens);
  } catch (e) {
    /* ignored — counter stays at the last known value */
  }
}

// Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri (DeepSeek prices
// doubled vs off-peak) — see cost.py for the same rule on the server,
// used for cost calculation; here we only show the current state.
function isPeakNow() {
  const now = new Date();
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return false;
  const hour = now.getUTCHours();
  return (hour >= 1 && hour < 4) || (hour >= 6 && hour < 10);
}

function refreshRateDot() {
  const peak = isPeakNow();
  rateDotEl.classList.toggle("rate-dot--peak", peak);
  rateDotEl.classList.toggle("rate-dot--offpeak", !peak);
  rateDotEl.title = peak ? "no-discount (DeepSeek peak hour)" : "discount (reduced DeepSeek rate, ~half)";
}

refreshRateDot();
setInterval(refreshRateDot, 60_000);
