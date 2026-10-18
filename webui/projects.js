/* pagina Proiecte — titlu, descriere, prompt injectat la fiecare chat legat
de proiect; crearea chat-urilor dintr-un proiect se face de aici (butonul
"+ Chat nou" al fiecărui proiect), nu din sintaxă de text la redenumire. */

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

// id-urile proiectelor expandate — supraviețuiesc unui loadProjects() (după
// salvare/ștergere) cât timp pagina rămâne încărcată, ca accordionul să nu
// se închidă la fiecare salvare.
const expandedIds = new Set();

// Catalogul de modele (etapa E) — încărcat o singură dată, de la server, ca
// lista să nu fie duplicată aici și în webui/settings.js.
let MODEL_OPTIONS = [{ id: "deepseek-flash", label: "deepseek-flash" }];
async function loadModelOptions() {
  try {
    MODEL_OPTIONS = await api("/api/models");
  } catch (e) {
    /* indisponibil — rămâne fallback-ul minimal de mai sus */
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
  titleToggle.textContent = project.title || "(fără titlu)";

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
  titleInput.placeholder = "Titlu proiect";

  const descInput = document.createElement("textarea");
  descInput.className = "project-desc-input";
  descInput.placeholder = "Descriere";
  descInput.rows = 2;
  descInput.value = project.description;

  const modelLabel = document.createElement("div");
  modelLabel.className = "project-field-label";
  modelLabel.textContent = "Model chat (extracția KB/memorie rulează mereu pe deepseek-flash)";

  const modelSelect = document.createElement("select");
  modelSelect.className = "project-model-select";
  for (const { id, label } of MODEL_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = label;
    modelSelect.appendChild(opt);
  }
  modelSelect.value = project.model || "deepseek-flash";

  const promptLabel = document.createElement("div");
  promptLabel.className = "project-field-label";
  promptLabel.textContent = "Prompt injectat la fiecare chat din acest proiect";

  const promptInput = document.createElement("textarea");
  promptInput.className = "project-prompt-input";
  promptInput.rows = 6;
  promptInput.value = project.prompt;

  const actions = document.createElement("div");
  actions.className = "project-card-actions";

  const newChatBtn = document.createElement("button");
  newChatBtn.textContent = "+ Chat nou";
  newChatBtn.addEventListener("click", () => createChatForProject(project.id));

  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Salvează";
  saveBtn.addEventListener("click", () =>
    saveProject(project.id, {
      title: titleInput.value.trim(),
      description: descInput.value,
      prompt: promptInput.value,
      model: modelSelect.value,
    })
  );

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "danger";
  deleteBtn.textContent = "Șterge proiect";
  deleteBtn.addEventListener("click", () => deleteProject(project.id, project.title));

  actions.append(newChatBtn, saveBtn, deleteBtn);
  details.append(titleInput, descInput, modelLabel, modelSelect, promptLabel, promptInput, actions);
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
  el.title = "Estimat din consumul de tokeni (cache hit/miss + output, tarif DeepSeek la ora fiecărui apel); ignoră excepția sărbătorilor chinezești din orele de vârf.";
  try {
    const totals = await api("/api/usage/cost");
    el.textContent = `Cost estimat: ${formatCost(totals.by_project[projectId] || 0)}`;
  } catch (e) {
    el.textContent = `Cost estimat: indisponibil (${e.message})`;
  }
  card.querySelector(".project-card-actions").before(el);
}

async function loadProjects() {
  const projects = await api("/api/projects");
  listEl.innerHTML = "";
  if (projects.length === 0) {
    const empty = document.createElement("div");
    empty.className = "project-empty";
    empty.textContent = "Niciun proiect încă.";
    listEl.appendChild(empty);
    return;
  }
  for (const project of projects) listEl.appendChild(renderProject(project));
}

async function saveProject(id, fields) {
  if (!fields.title) {
    window.alert("Titlul e vid.");
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
    window.alert(`Eroare la salvare: ${e.message}`);
  }
}

async function deleteProject(id, title) {
  if (!window.confirm(`Ștergi proiectul "${title}" și toate chat-urile lui?`)) return;
  try {
    await api(`/api/projects/${id}`, { method: "DELETE" });
    await loadProjects();
  } catch (e) {
    window.alert(`Eroare la ștergere: ${e.message}`);
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
    window.alert(`Eroare la creare chat: ${e.message}`);
  }
}

newBtn.addEventListener("click", async () => {
  const title = window.prompt("Titlu proiect:");
  if (!title || !title.trim()) return;
  try {
    await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim(), description: "", prompt: "" }),
    });
    await loadProjects();
  } catch (e) {
    window.alert(`Eroare la creare proiect: ${e.message}`);
  }
});

loadModelOptions().then(loadProjects);
