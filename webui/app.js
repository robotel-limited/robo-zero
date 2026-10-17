/* webui pentru agent-zero-light — fără framework, fără librării vendor. */

const messagesEl = document.getElementById("messages");
const chatListEl = document.getElementById("chat-list");
const composeForm = document.getElementById("compose-form");
const composeInput = document.getElementById("compose-input");
const sendBtn = document.getElementById("send-btn");
const cancelBtn = document.getElementById("cancel-btn");
const approvalBar = document.getElementById("approval-bar");
const approvalText = document.getElementById("approval-text");
const newChatBtn = document.getElementById("new-chat-btn");
const themeToggleBtn = document.getElementById("theme-toggle-btn");

let currentChatId = null;
let eventSource = null;

function applyTheme(mode) {
  document.body.classList.toggle("light-mode", mode === "light");
  try {
    localStorage.setItem("az-theme", mode);
  } catch (e) {
    /* storaj indisponibil (fereastră privată) — ignorăm, temă implicită rămâne */
  }
}

(function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem("az-theme");
  } catch (e) {
    /* ignorat */
  }
  applyTheme(saved === "light" ? "light" : "dark");
})();

themeToggleBtn.addEventListener("click", () => {
  const next = document.body.classList.contains("light-mode") ? "dark" : "light";
  applyTheme(next);
});

// tool_result de la image_generate întoarce mesajul "Imagine generată: /files/images/<id>.jpg"
const IMAGE_PATH_RE = /\/files\/images\/[\w.-]+\.(?:jpg|jpeg|png)\b/;

function appendMessage(cls, text) {
  const div = document.createElement("div");
  div.className = `message ${cls}`;
  const imageMatch = text.match(IMAGE_PATH_RE);
  if (imageMatch) {
    div.appendChild(document.createTextNode(text.slice(0, imageMatch.index)));
    const img = document.createElement("img");
    img.src = imageMatch[0];
    img.className = "message-image";
    div.appendChild(img);
  } else {
    div.textContent = text;
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderLogEntry(entry) {
  switch (entry.type) {
    case "user":
      appendMessage("message-user", entry.text);
      break;
    case "tool_call":
      appendMessage("message-tool-call", `→ ${entry.name}(${entry.arguments})`);
      break;
    case "tool_result":
      appendMessage("message-tool-result", `${entry.name}: ${entry.message}`);
      break;
    case "final":
      appendMessage("message-agent-response", entry.text);
      setRunning(false);
      break;
    case "error":
      appendMessage("message-error", `Eroare: ${entry.text}`);
      setRunning(false);
      break;
    case "cancelled":
      appendMessage("message-cancelled", entry.text);
      setRunning(false);
      break;
    case "approval_required":
      showApproval(entry.tool_name, entry.arguments);
      break;
    case "approval_resolved":
      hideApproval();
      break;
    default:
      break;
  }
}

function showApproval(toolName, args) {
  approvalText.textContent = `Aprobi execuția? ${toolName}(${args})`;
  approvalBar.classList.remove("hidden");
}

function hideApproval() {
  approvalBar.classList.add("hidden");
}

function setRunning(running) {
  cancelBtn.classList.toggle("hidden", !running);
  sendBtn.disabled = running;
  if (!running) hideApproval();
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

async function loadChatList() {
  const chats = await api("/api/chats");
  chatListEl.innerHTML = "";
  for (const chat of chats) {
    const item = document.createElement("div");
    item.className = "chat-list-item" + (chat.id === currentChatId ? " active" : "");

    const titleEl = document.createElement("span");
    titleEl.className = "chat-list-title";
    titleEl.textContent = chat.title;
    titleEl.addEventListener("click", () => selectChat(chat.id));
    item.appendChild(titleEl);

    const renameBtn = document.createElement("button");
    renameBtn.className = "chat-list-action";
    renameBtn.textContent = "✎";
    renameBtn.title = "Redenumește";
    renameBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      renameChat(chat.id, chat.title);
    });
    item.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "chat-list-action";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Șterge";
    deleteBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteChat(chat.id);
    });
    item.appendChild(deleteBtn);

    chatListEl.appendChild(item);
  }
}

async function renameChat(chatId, currentTitle) {
  const title = window.prompt("Titlu nou:", currentTitle);
  if (!title || !title.trim()) return;
  await api(`/api/chats/${chatId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title.trim() }),
  }).catch((e) => appendMessage("message-error", `Eroare la redenumire: ${e.message}`));
  await loadChatList();
}

async function deleteChat(chatId) {
  if (!window.confirm("Ștergi definitiv această conversație?")) return;
  try {
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
  } catch (e) {
    appendMessage("message-error", `Eroare la ștergere: ${e.message}`);
    return;
  }
  if (chatId === currentChatId) {
    currentChatId = null;
    if (eventSource) eventSource.close();
    messagesEl.innerHTML = "";
    const chats = await api("/api/chats");
    if (chats.length > 0) await selectChat(chats[0].id);
    else await createChat();
  } else {
    await loadChatList();
  }
}

async function selectChat(chatId) {
  currentChatId = chatId;
  messagesEl.innerHTML = "";
  hideApproval();
  const chat = await api(`/api/chats/${chatId}`);
  for (const entry of chat.log) renderLogEntry(entry);
  setRunning(chat.is_running);
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
  if (!text || !currentChatId) return;
  composeInput.value = "";
  setRunning(true);
  try {
    await api(`/api/chats/${currentChatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    appendMessage("message-error", `Eroare la trimitere: ${e.message}`);
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
  const chats = await api("/api/chats");
  if (chats.length > 0) {
    await selectChat(chats[0].id);
  } else {
    await createChat();
  }
})();
