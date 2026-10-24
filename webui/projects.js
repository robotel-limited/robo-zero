/* Projects page — title, description, prompt injected into every chat linked
to the project; creating chats from a project happens here (each project's
"+ New chat" button), not via text syntax on rename. */

const listEl = document.getElementById("project-list");
const newBtn = document.getElementById("new-project-btn");

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

// expanded project ids — survive a loadProjects() (after save/delete) while
// the page stays loaded, so the accordion doesn't close on every save.
const expandedIds = new Set();

// Model catalog (stage E) — loaded once from the server, so the list isn't
// duplicated here and in webui/settings.js.
let MODEL_OPTIONS = [{ id: "deepseek-flash", label: "deepseek-flash" }];
async function loadModelOptions() {
  try {
    MODEL_OPTIONS = await api("/api/models");
  } catch (e) {
    /* unavailable — keep the minimal fallback above */
  }
}

function renderProject(project) {
  const card = document.createElement("div");
  card.className = "project-card";

  const row = document.createElement("div");
  row.className = "project-card-row";

  const titleToggle = document.createElement("button");
  titleToggle.type = "button";
  titleToggle.className = "project-card-title";
  titleToggle.textContent = project.title || "(untitled)";

  const descPreview = document.createElement("div");
  descPreview.className = "project-card-desc";
  descPreview.textContent = project.description || "";

  row.append(titleToggle, descPreview);
  card.appendChild(row);

  const details = document.createElement("div");
  details.className = "project-card-details";

  const titleInput = document.createElement("input");
  titleInput.className = "project-title-input";
  titleInput.value = project.title;
  titleInput.placeholder = "Project title";

  const descInput = document.createElement("textarea");
  descInput.className = "project-desc-input";
  descInput.placeholder = "Description";
  descInput.rows = 2;
  descInput.value = project.description;

  const modelLabel = document.createElement("div");
  modelLabel.className = "project-field-label";
  modelLabel.textContent = "Chat model (KB/memory extraction always runs on deepseek-flash)";

  const modelSelect = document.createElement("select");
  modelSelect.className = "project-model-select";
  for (const { id, label } of MODEL_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = label;
    modelSelect.appendChild(opt);
  }
  modelSelect.value = project.model || "deepseek-flash";

  const voiceOnlyLabel = document.createElement("label");
  voiceOnlyLabel.className = "project-field-label project-voice-only-label";
  const voiceOnlyCheckbox = document.createElement("input");
  voiceOnlyCheckbox.type = "checkbox";
  voiceOnlyCheckbox.checked = Boolean(project.voice_only);
  voiceOnlyLabel.append(
    voiceOnlyCheckbox,
    document.createTextNode(
      " Voice-only (no UI to approve/deny — skip confirmation for shell/fs/browser actions)"
    )
  );

  const promptLabel = document.createElement("div");
  promptLabel.className = "project-field-label";
  promptLabel.textContent = "Prompt injected into every chat in this project";

  const promptInput = document.createElement("textarea");
  promptInput.className = "project-prompt-input";
  promptInput.rows = 6;
  promptInput.value = project.prompt;

  const actions = document.createElement("div");
  actions.className = "project-card-actions";

  const newChatBtn = document.createElement("button");
  newChatBtn.textContent = "+ New chat";
  newChatBtn.addEventListener("click", () => createChatForProject(project.id));

  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Save";
  saveBtn.addEventListener("click", () =>
    saveProject(project.id, {
      title: titleInput.value.trim(),
      description: descInput.value,
      prompt: promptInput.value,
      model: modelSelect.value,
      voice_only: voiceOnlyCheckbox.checked,
    })
  );

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "danger";
  deleteBtn.textContent = "Delete project";
  deleteBtn.addEventListener("click", () => deleteProject(project.id, project.title));

  actions.append(newChatBtn, saveBtn, deleteBtn);
  details.append(
    titleInput,
    descInput,
    modelLabel,
    modelSelect,
    voiceOnlyLabel,
    promptLabel,
    promptInput,
    actions
  );
  card.appendChild(details);

  const setExpanded = (expanded) => {
    card.classList.toggle("project-card--expanded", expanded);
    details.hidden = !expanded;
  };
  setExpanded(expandedIds.has(project.id));

  titleToggle.addEventListener("click", () => {
    const expand = !expandedIds.has(project.id);
    if (expand) expandedIds.add(project.id);
    else expandedIds.delete(project.id);
    setExpanded(expand);
  });

  loadKbInto(card, project.id);
  loadFoldersInto(card, project.id);
  loadReportsInto(card, project.id);
  loadMemoryInto(card, project.id);
  loadRulesInto(card, project.id);
  loadCostInto(card, project.id);
  return card;
}

function formatCost(usd) {
  if (!usd) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return "$" + usd.toFixed(4);
}

async function loadCostInto(card, projectId) {
  const el = document.createElement("div");
  el.className = "cost-estimate";
  el.title = "Estimated from token usage (cache hit/miss + output, DeepSeek rate at each call's time); ignores the Chinese-holiday exception to peak hours.";
  try {
    const totals = await api("/api/usage/cost");
    el.textContent = `Estimated cost: ${formatCost(totals.by_project[projectId] || 0)}`;
  } catch (e) {
    el.textContent = `Estimated cost: unavailable (${e.message})`;
  }
  card.querySelector(".project-card-actions").before(el);
}

async function loadProjects() {
  const projects = await api("/api/projects");
  listEl.innerHTML = "";
  if (projects.length === 0) {
    const empty = document.createElement("div");
    empty.className = "project-empty";
    empty.textContent = "No projects yet.";
    listEl.appendChild(empty);
    return;
  }
  for (const project of projects) listEl.appendChild(renderProject(project));
}

async function saveProject(id, fields) {
  if (!fields.title) {
    window.alert("Title is empty.");
    return;
  }
  try {
    await api(`/api/projects/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    await loadProjects();
  } catch (e) {
    window.alert(`Error saving: ${e.message}`);
  }
}

async function deleteProject(id, title) {
  if (!window.confirm(`Delete project "${title}" and all its chats?`)) return;
  try {
    await api(`/api/projects/${id}`, { method: "DELETE" });
    await loadProjects();
  } catch (e) {
    window.alert(`Error deleting: ${e.message}`);
  }
}

async function createChatForProject(id) {
  try {
    const chat = await api("/api/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: id }),
    });
    window.location.href = `/?chat=${chat.id}`;
  } catch (e) {
    window.alert(`Error creating chat: ${e.message}`);
  }
}

newBtn.addEventListener("click", async () => {
  const title = window.prompt("Project title:");
  if (!title || !title.trim()) return;
  try {
    await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim(), description: "", prompt: "" }),
    });
    await loadProjects();
  } catch (e) {
    window.alert(`Error creating project: ${e.message}`);
  }
});

loadModelOptions().then(loadProjects);
