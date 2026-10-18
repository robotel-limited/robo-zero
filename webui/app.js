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
const tokenCountEl = document.getElementById("token-count");
const tokenCountTextEl = document.getElementById("token-count-text");
const rateDotEl = document.getElementById("rate-dot");

let currentChatId = null;
let eventSource = null;
let settings = { verbose: true };
let projectsCache = [];
let pendingAttachments = []; // [{url, previewUrl}] — imagini urcate, nu încă trimise

const attachImageBtn = document.getElementById("attach-image-btn");
const attachImageInput = document.getElementById("attach-image-input");
const composeAttachmentsEl = document.getElementById("compose-attachments");

const appEl = document.getElementById("app");

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

setupPanelToggle("toggle-main-btn", "hide-main", "a0-hide-main");
setupPanelToggle("toggle-sidebar-btn", "hide-sidebar", "a0-hide-sidebar");

function formatTokenCount(count) {
  if (count < 1000) return `${count}`;
  const thousands = count / 1000;
  const rounded = thousands >= 10 ? Math.round(thousands) : Math.round(thousands * 10) / 10;
  return `${rounded}k`;
}

function setTokenCount(count, contextTokens) {
  tokenCountTextEl.textContent = `${formatTokenCount(count)} tokeni`;
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

// Peak = 01:00-04:00 și 06:00-10:00 UTC, luni-vineri (prețuri DeepSeek
// dublate față de off-peak) — vezi cost.py pentru aceeași regulă pe server,
// folosită la calculul costului; aici doar indicăm starea curentă.
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
  rateDotEl.title = peak ? "no-discount (oră de vârf DeepSeek)" : "discount (tarif redus DeepSeek, ~jumătate)";
}

refreshRateDot();
setInterval(refreshRateDot, 60_000);

async function refreshTokenCount(chatId) {
  try {
    const chat = await api(`/api/chats/${chatId}`);
    if (chatId === currentChatId) setTokenCount(chat.token_count, chat.context_tokens);
  } catch (e) {
    /* ignorat — contorul rămâne la ultima valoare cunoscută */
  }
}

// tool_result de la image_generate întoarce mesajul "Imagine generată: /files/images/<id>.jpg"
const IMAGE_PATH_RE = /\/files\/images\/[\w.-]+\.(?:jpg|jpeg|png)\b/;

const COPY_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const COPIED_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg>';

function copyMessageText(text, btn) {
  const done = () => {
    btn.classList.add("copied");
    btn.innerHTML = COPIED_ICON;
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.innerHTML = COPY_ICON;
    }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(done);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {
      /* ignorat — fallback best-effort */
    }
    document.body.removeChild(ta);
    done();
  }
}

const DELETE_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';

async function deleteTurn(turnId) {
  if (!turnId) return;
  if (!confirm("Ștergi acest schimb? Mesajul tău și acest răspuns vor fi eliminate și din contextul conversației.")) {
    return;
  }
  try {
    const res = await fetch(`/api/chats/${currentChatId}/turns/${turnId}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || "Nu s-a putut șterge (poate a fost deja rezumat în context).");
      return;
    }
  } catch {
    alert("Nu s-a putut contacta serverul.");
    return;
  }
  removeTurnFromDom(turnId);
}

function removeTurnFromDom(turnId) {
  messagesEl.querySelectorAll(`[data-turn-id="${turnId}"]`).forEach((el) => el.remove());
}

function appendMessage(cls, text, images, turnId) {
  const div = document.createElement("div");
  div.className = `message ${cls}`;
  if (turnId) div.dataset.turnId = turnId;
  const imageMatch = text.match(IMAGE_PATH_RE);
  if (imageMatch) {
    div.appendChild(document.createTextNode(text.slice(0, imageMatch.index)));
    const img = document.createElement("img");
    img.src = imageMatch[0];
    img.className = "message-image";
    div.appendChild(img);
  } else if (text) {
    div.textContent = text;
  }
  for (const url of images || []) {
    const img = document.createElement("img");
    img.src = url;
    img.className = "message-image";
    div.appendChild(img);
  }
  if (cls === "message-agent-response" && text) {
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "message-copy-btn";
    copyBtn.title = "Copiază răspunsul";
    copyBtn.innerHTML = COPY_ICON;
    copyBtn.addEventListener("click", () => copyMessageText(text, copyBtn));
    div.appendChild(copyBtn);

    if (turnId) {
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "message-delete-btn";
      deleteBtn.title = "Șterge schimbul (și din context)";
      deleteBtn.innerHTML = DELETE_ICON;
      deleteBtn.addEventListener("click", () => deleteTurn(turnId));
      div.appendChild(deleteBtn);
    }
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderLogEntry(entry) {
  switch (entry.type) {
    case "user":
      appendMessage("message-user", entry.text, entry.images, entry.turn_id);
      break;
    case "tool_call":
      if (settings.verbose)
        appendMessage("message-tool-call", `→ ${entry.name}(${entry.arguments})`, null, entry.turn_id);
      break;
    case "tool_result":
      // conținut produs efectiv (ex. imagine generată) rămâne vizibil chiar la
      // verbose=off — doar raționamentul (apeluri/rezultate text) se ascunde
      if (settings.verbose || IMAGE_PATH_RE.test(entry.message)) {
        appendMessage("message-tool-result", `${entry.name}: ${entry.message}`, null, entry.turn_id);
      }
      break;
    case "turn_deleted":
      removeTurnFromDom(entry.turn_id);
      break;
    case "final":
      appendMessage("message-agent-response", entry.text, null, entry.turn_id);
      setRunning(false);
      notifyIfHidden();
      break;
    case "error":
      appendMessage("message-error", `Eroare: ${entry.text}`);
      setRunning(false);
      notifyIfHidden();
      break;
    case "cancelled":
      appendMessage("message-cancelled", entry.text);
      setRunning(false);
      notifyIfHidden();
      break;
    case "approval_required":
      showApproval(entry.tool_name, entry.arguments);
      break;
    case "approval_resolved":
      hideApproval();
      break;
    case "project_memory_updated":
      appendMessage(
        "message-memory",
        "Fișa proiectului actualizată:\n" +
          [...entry.added.map((t) => `+ ${t}`), ...entry.replaced.map((r) => `~ ${r.old} → ${r.new}`)].join("\n")
      );
      break;
    case "project_context_refreshed":
      appendMessage("message-memory", "Context nou primit din alte discuții ale proiectului:\n" + entry.lines.join("\n"));
      break;
    case "project_rules_proposed":
      appendMessage(
        "message-memory",
        "Reguli noi propuse (așteaptă aprobare pe cardul proiectului din /projects):\n" +
          entry.added.map((t) => `+ ${t}`).join("\n")
      );
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

function formatCost(usd) {
  if (!usd) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return "$" + usd.toFixed(4);
}

let costCache = { by_chat: {}, by_project: {} };

function renderChatItem(chat) {
  const item = document.createElement("div");
  item.className = "chat-list-item" + (chat.id === currentChatId ? " active" : "");

  const titleEl = document.createElement("span");
  titleEl.className = "chat-list-title";
  titleEl.textContent = chat.title;
  titleEl.addEventListener("click", () => selectChat(chat.id));
  item.appendChild(titleEl);

  const costEl = document.createElement("span");
  costEl.className = "chat-list-cost";
  costEl.title = "Cost estimat al conversației (cache hit/miss + output, tarif DeepSeek la ora apelului)";
  costEl.textContent = formatCost(costCache.by_chat[chat.id] || 0);
  item.appendChild(costEl);

  const projectSelect = document.createElement("select");
  projectSelect.className = "chat-list-project-select";
  projectSelect.title = "Proiect";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "Fără proiect";
  projectSelect.appendChild(noneOpt);
  for (const project of projectsCache) {
    const opt = document.createElement("option");
    opt.value = project.id;
    opt.textContent = project.title;
    projectSelect.appendChild(opt);
  }
  projectSelect.value = chat.project_id || "";
  projectSelect.addEventListener("click", (ev) => ev.stopPropagation());
  projectSelect.addEventListener("change", async (ev) => {
    ev.stopPropagation();
    await setChatProject(chat.id, projectSelect.value || null);
  });
  item.appendChild(projectSelect);

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

  return item;
}

// chei de grup expandate — persistă peste loadChatList() (apelat după orice
// acțiune: trimitere mesaj, redenumire, ștergere) cât timp pagina rămâne
// încărcată, ca accordionul să nu se închidă la fiecare refresh.
const expandedGroups = new Set();

function renderChatGroup(key, label, chats) {
  const group = document.createElement("div");
  group.className = "chat-group";

  const header = document.createElement("button");
  header.type = "button";
  header.className = "chat-group-header";

  const body = document.createElement("div");
  body.className = "chat-group-body";
  for (const chat of chats) body.appendChild(renderChatItem(chat));

  const setExpanded = (expanded) => {
    group.classList.toggle("chat-group--expanded", expanded);
    body.hidden = !expanded;
    header.textContent = `${expanded ? "▾" : "▸"} ${label} (${chats.length})`;
  };
  // grupul care conține chat-ul activ e mereu vizibil, altfel ar rămâne
  // ascuns în spatele propriului header colapsat
  const containsActive = chats.some((c) => c.id === currentChatId);
  setExpanded(expandedGroups.has(key) || containsActive);

  header.addEventListener("click", () => {
    const expand = !expandedGroups.has(key);
    if (expand) expandedGroups.add(key);
    else expandedGroups.delete(key);
    setExpanded(expand);
  });

  group.append(header, body);
  return group;
}

async function setChatProject(chatId, projectId) {
  try {
    await api(`/api/chats/${chatId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
  } catch (e) {
    appendMessage("message-error", `Eroare la atribuirea proiectului: ${e.message}`);
  }
  await loadChatList();
}

async function loadChatList() {
  const [chats, projects, costTotals] = await Promise.all([
    api("/api/chats"),
    api("/api/projects"),
    api("/api/usage/cost"),
  ]);
  projectsCache = projects;
  costCache = costTotals;
  chatListEl.innerHTML = "";
  if (chats.length === 0) return;

  const byProject = new Map();
  const unassigned = [];
  for (const chat of chats) {
    if (chat.project_id) {
      if (!byProject.has(chat.project_id)) byProject.set(chat.project_id, []);
      byProject.get(chat.project_id).push(chat);
    } else {
      unassigned.push(chat);
    }
  }

  for (const project of projects) {
    const chatsInProject = byProject.get(project.id);
    if (!chatsInProject || chatsInProject.length === 0) continue;
    chatListEl.appendChild(renderChatGroup(project.id, project.title, chatsInProject));
  }

  if (unassigned.length > 0) {
    chatListEl.appendChild(renderChatGroup("__unassigned__", "Neafiliat", unassigned));
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
  if (appEl && appEl.classList.contains("hide-main")) {
    appEl.classList.remove("hide-main");
    localStorage.setItem("a0-hide-main", "0");
  }
  messagesEl.innerHTML = "";
  hideApproval();
  pendingAttachments = [];
  renderAttachments();
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

function renderAttachments() {
  composeAttachmentsEl.innerHTML = "";
  pendingAttachments.forEach((att, i) => {
    const box = document.createElement("div");
    box.className = "compose-attachment";
    const img = document.createElement("img");
    img.src = att.url;
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachments();
    });
    box.append(img, removeBtn);
    composeAttachmentsEl.appendChild(box);
  });
}

async function uploadAttachment(file) {
  try {
    const { url } = await api("/api/attachments", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    pendingAttachments.push({ url });
    renderAttachments();
  } catch (e) {
    appendMessage("message-error", `Eroare la atașarea imaginii: ${e.message}`);
  }
}

attachImageBtn.addEventListener("click", () => attachImageInput.click());

attachImageInput.addEventListener("change", async () => {
  for (const file of attachImageInput.files) await uploadAttachment(file);
  attachImageInput.value = "";
});

composeInput.addEventListener("paste", async (ev) => {
  const files = [...(ev.clipboardData?.items || [])]
    .filter((item) => item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (files.length === 0) return;
  ev.preventDefault();
  for (const file of files) await uploadAttachment(file);
});

composeForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = composeInput.value.trim();
  const images = pendingAttachments.map((a) => a.url);
  if ((!text && images.length === 0) || !currentChatId) return;
  unlockAudio();
  composeInput.value = "";
  pendingAttachments = [];
  renderAttachments();
  setRunning(true);
  try {
    await api(`/api/chats/${currentChatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, images }),
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
  try {
    settings = await api("/api/settings");
  } catch (e) {
    /* indisponibil — rămân valorile implicite */
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
