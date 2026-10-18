/* Sidebar chat list: grouped by project (accordion), per-chat cost
   display, and per-chat actions (project assignment, rename, delete). */

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

  const titleEl = document.createElement("span");
  titleEl.className = "chat-list-title";
  titleEl.textContent = chat.title;
  titleEl.addEventListener("click", () => selectChat(chat.id));
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

  const renameBtn = document.createElement("button");
  renameBtn.className = "chat-list-action";
  renameBtn.textContent = "✎";
  renameBtn.title = "Rename";
  renameBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    renameChat(chat.id, chat.title);
  });
  item.appendChild(renameBtn);

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
    appendMessage("message-error", `Error assigning project: ${e.message}`);
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
    chatListEl.appendChild(renderChatGroup("__unassigned__", "Unassigned", unassigned));
  }
}

async function renameChat(chatId, currentTitle) {
  const title = window.prompt("New title:", currentTitle);
  if (!title || !title.trim()) return;
  await api(`/api/chats/${chatId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title.trim() }),
  }).catch((e) => appendMessage("message-error", `Error renaming: ${e.message}`));
  await loadChatList();
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
