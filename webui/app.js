/* webui for robo-zero — no framework, no vendor libraries.
   Orchestrator: page-level state (current chat, SSE connection, settings),
   panel toggles, and the compose/send/cancel/approve controls. Message
   rendering, the chat list, token/rate display and attachments each live
   in their own file, loaded before this one (see index.html). */

const appEl = document.getElementById("app");
const composeForm = document.getElementById("compose-form");
const composeInput = document.getElementById("compose-input");
const newChatBtn = document.getElementById("new-chat-btn");

let currentChatId = null;
let eventSource = null;
let settings = { verbose: true };

function setupPanelToggle(btnId, cssClass, storageKey) {
  const btn = document.getElementById(btnId);
  if (!btn || !appEl) return;
  if (localStorage.getItem(storageKey) === "1") {
    appEl.classList.add(cssClass);
  }
  btn.addEventListener("click", () => {
    appEl.classList.toggle(cssClass);
    localStorage.setItem(storageKey, appEl.classList.contains(cssClass) ? "1" : "0");
  });
}

setupPanelToggle("toggle-main-btn", "hide-main", "robo-zero-hide-main");
setupPanelToggle("toggle-sidebar-btn", "hide-sidebar", "robo-zero-hide-sidebar");

async function selectChat(chatId) {
  currentChatId = chatId;
  if (appEl && appEl.classList.contains("hide-main")) {
    appEl.classList.remove("hide-main");
    localStorage.setItem("robo-zero-hide-main", "0");
  }
  messagesEl.innerHTML = "";
  hideApproval();
  clearPendingAttachments();
  const chat = await api(`/api/chats/${chatId}`);
  for (const entry of chat.log) renderLogEntry(entry);
  setRunning(chat.is_running);
  setTokenCount(chat.token_count, chat.context_tokens);
  await loadChatList();
  connectEvents(chatId);
}

function connectEvents(chatId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/chats/${chatId}/events`);
  eventSource.onmessage = (ev) => {
    const entry = JSON.parse(ev.data);
    renderLogEntry(entry);
    if (entry.type === "user") loadChatList();
    refreshTokenCount(chatId);
  };
}

async function createChat() {
  const chat = await api("/api/chats", { method: "POST" });
  await selectChat(chat.id);
}

newChatBtn.addEventListener("click", () => createChat());

composeForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = composeInput.value.trim();
  const images = getPendingAttachmentUrls();
  if ((!text && images.length === 0) || !currentChatId) return;
  unlockAudio();
  composeInput.value = "";
  clearPendingAttachments();
  setRunning(true);
  try {
    await api(`/api/chats/${currentChatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, images }),
    });
  } catch (e) {
    appendMessage("message-error", `Error sending: ${e.message}`);
    setRunning(false);
  }
});

cancelBtn.addEventListener("click", async () => {
  if (!currentChatId) return;
  await api(`/api/chats/${currentChatId}/cancel`, { method: "POST" }).catch(() => {});
});

approvalBar.querySelector("#approve-yes").addEventListener("click", async () => {
  await api(`/api/chats/${currentChatId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: true }),
  }).catch(() => {});
});

approvalBar.querySelector("#approve-no").addEventListener("click", async () => {
  await api(`/api/chats/${currentChatId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: false }),
  }).catch(() => {});
});

(async function init() {
  try {
    settings = await api("/api/settings");
  } catch (e) {
    /* unavailable — keep default values */
  }

  const requestedChatId = new URLSearchParams(window.location.search).get("chat");
  const chats = await api("/api/chats");
  if (requestedChatId && chats.some((c) => c.id === requestedChatId)) {
    await selectChat(requestedChatId);
  } else if (chats.length > 0) {
    await selectChat(chats[0].id);
  } else {
    await createChat();
  }
})();
