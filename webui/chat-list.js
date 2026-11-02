/* Sidebar chat list: grouped by project (accordion), per-chat cost
   display, and per-chat actions (project assignment, delete).
   Chat titles are renamed inline (double-click on the title). */

const chatListEl = document.getElementById("chat-list");

let projectsCache = [];
let costCache = { by_chat: {}, by_project: {} };

function formatCost(usd) {
  if (!usd) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return "$" + usd.toFixed(4);
}

function renderChatItem(chat) {
  const item = document.createElement("div");
  item.className = "chat-list-item" + (chat.id === currentChatId ? " active" : "");
  item.dataset.chatId = chat.id;

  const titleEl = document.createElement("span");
  titleEl.className = "chat-list-title";
  titleEl.textContent = chat.title;
  titleEl.addEventListener("click", () => selectChat(chat.id));
  titleEl.addEventListener("dblclick", () => startInlineRename(chat.id, titleEl));
  item.appendChild(titleEl);

  const costEl = document.createElement("span");
  costEl.className = "chat-list-cost";
  costEl.title = "Estimated conversation cost (cache hit/miss + output, DeepSeek rate at call time)";
  costEl.textContent = formatCost(costCache.by_chat[chat.id] || 0);
  item.appendChild(costEl);

  const projectSelect = document.createElement("select");
  projectSelect.className = "chat-list-project-select";
  projectSelect.title = "Project";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "No project";
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

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "chat-list-action";
  deleteBtn.textContent = "🗑";
  deleteBtn.title = "Delete";
  deleteBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    deleteChat(chat.id);
  });
  item.appendChild(deleteBtn);

  return item;
}

let inlineRename = null; // { chatId, input, done } — un singur edit activ

function startInlineRename(chatId, titleEl) {
  if (inlineRename || !titleEl) return;
  const current = titleEl.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "chat-list-title chat-list-title-input";
  input.value = current === "Conversație nouă" ? "" : current;
  input.placeholder = "Chat name";
  titleEl.replaceWith(input);
  const state = { chatId, input, done: false };
  inlineRename = state;
  input.focus();
  input.select();
  const finish = async (save) => {
    if (state.done) return;
    state.done = true;
    inlineRename = null;
    const value = input.value.trim();
    if (save && value && value !== current) {
      try {
        await api(`/api/chats/${chatId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: value }),
        });
      } catch (e) {
        appendMessage("message-error", `Error renaming: ${e.message}`);
      }
    }
    await loadChatList();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", (ev) => ev.stopPropagation());
}

function startInlineRenameById(chatId) {
  const item = document.querySelector(`.chat-list-item[data-chat-id="${chatId}"]`);
  const titleEl = item && item.querySelector(".chat-list-title");
  if (!titleEl) return false;
  startInlineRename(chatId, titleEl);
  return true;
}

// expanded group keys — persist across loadChatList() (called after any
// action: sending a message, renaming, deleting) while the page stays
// loaded, so the accordion doesn't close on every refresh.
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
  // the group containing the active chat is always visible, otherwise it
  // would stay hidden behind its own collapsed header
  const containsActive = chats.some((c) => c.id === currentChatId);
  setExpanded(expandedGroups.has(key) || containsActive);

  header.addEventListener("click", () => {
    const expand = !expandedGroups.has(key);
    if (expand) expandedGroups.add(key);
    else expandedGroups.delete(key);
    setExpanded(expand);
  });

  // header + project actions share a row; the buttons stay outside the
  // header <button> (nested buttons are invalid and the click would bubble
  // into the expand/collapse toggle)
  const row = document.createElement("div");
  row.className = "chat-group-row";
  row.appendChild(header);

  if (key !== "__unassigned__") {
    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "chat-list-action";
    renameBtn.textContent = "✎";
    renameBtn.title = "Rename project";
    renameBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      renameProject(key, label);
    });
    row.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "chat-list-action";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Delete project";
    deleteBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteProjectGroup(key, label, chats);
    });
    row.appendChild(deleteBtn);
  }

  group.append(row, body);
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
    appendMessage("message-error", `Error assigning project: ${e.message}`);
  }
  await loadChatList();
}

async function renameProject(projectId, currentTitle) {
  const title = window.prompt("New project name:", currentTitle);
  if (!title || !title.trim()) return;
  try {
    await api(`/api/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim() }),
    });
  } catch (e) {
    appendMessage("message-error", `Error renaming project: ${e.message}`);
    return;
  }
  await loadChatList();
}

async function deleteProjectGroup(projectId, title, chats) {
  if (!window.confirm(`Delete project "${title}" and its ${chats.length} chat(s)? This cannot be undone.`)) return;
  try {
    await api(`/api/projects/${projectId}`, { method: "DELETE" });
  } catch (e) {
    appendMessage("message-error", `Error deleting project: ${e.message}`);
    return;
  }
  if (chats.some((c) => c.id === currentChatId)) {
    currentChatId = null;
    if (eventSource) eventSource.close();
    messagesEl.innerHTML = "";
    const remaining = await api("/api/chats");
    if (remaining.length > 0) await selectChat(remaining[0].id);
    else await createChat();
    return;
  }
  await loadChatList();
}

async function loadChatList() {
  // Nu reconstrui lista cât timp un titlu se editează inline: re-render-ul
  // ar șterge input-ul din DOM (selectChat() reîncarcă lista la fiecare click).
  // Editarea se reîmprospătează oricum la finalizare, când inlineRename e null.
  if (inlineRename) {
    if (inlineRename.input.isConnected) return;
    inlineRename.done = true;
    inlineRename = null;
  }
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
    chatListEl.appendChild(renderChatGroup("__unassigned__", "Unassigned", unassigned));
  }
}

async function deleteChat(chatId) {
  if (!window.confirm("Permanently delete this conversation?")) return;
  try {
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
  } catch (e) {
    appendMessage("message-error", `Error deleting: ${e.message}`);
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
