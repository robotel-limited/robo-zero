/* on-disk folders linked to a project (the /projects page): path,
ro/rw mode, note, search indexing, the compact map the agent sees.
While a folder is indexing, the list reloads itself. */

const FOLDER_POLL_MS = 3000;

async function folderApi(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.json();
}

function folderJson(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function folderStatusLabel(folder) {
  if (!folder.indexable) return "not indexed";
  if (folder.index_status === "indexing") return "indexing…";
  if (folder.index_status === "error") return `error: ${folder.index_error}`;
  if (folder.index_status === "ready") {
    const note = folder.index_error ? ` (${folder.index_error})` : "";
    return `${folder.indexed_files} files indexed${note}`;
  }
  return "pending";
}

function folderItem(projectId, folder, onChange) {
  const base = `/api/projects/${projectId}/folders/${folder.id}`;
  const patch = async (fields) => {
    try {
      await folderApi(base, folderJson("PATCH", fields));
    } catch (e) {
      window.alert(`Error: ${e.message}`);
    }
    onChange();
  };

  const li = document.createElement("li");
  li.className = "folder-item";

  const top = document.createElement("div");
  top.className = "kb-file-item";

  const path = document.createElement("code");
  path.className = "folder-path";
  path.textContent = folder.path;

  const mode = document.createElement("select");
  for (const m of ["ro", "rw"]) mode.add(new Option(m === "ro" ? "ro (read)" : "rw (write)", m));
  mode.value = folder.mode;
  mode.addEventListener("change", () => patch({ mode: mode.value }));

  const indexLabel = document.createElement("label");
  indexLabel.className = "folder-check";
  const indexBox = document.createElement("input");
  indexBox.type = "checkbox";
  indexBox.checked = folder.indexable;
  indexBox.addEventListener("change", () => patch({ indexable: indexBox.checked }));
  indexLabel.append(indexBox, " indexable");

  const status = document.createElement("span");
  status.className = "kb-file-status" + (folder.index_status === "error" ? " error" : "");
  status.textContent = folderStatusLabel(folder);

  top.append(path, mode, indexLabel, status);

  if (folder.indexable) {
    const reindexBtn = document.createElement("button");
    reindexBtn.className = "kb-file-delete";
    reindexBtn.title = "Reindex";
    reindexBtn.textContent = "⟳";
    reindexBtn.addEventListener("click", async () => {
      try {
        await folderApi(`${base}/reindex`, { method: "POST" });
      } catch (e) {
        window.alert(`Error: ${e.message}`);
      }
      onChange();
    });
    top.appendChild(reindexBtn);
  }

  const delBtn = document.createElement("button");
  delBtn.className = "kb-file-delete";
  delBtn.title = "Unlink the folder (files on disk stay untouched)";
  delBtn.textContent = "✕";
  delBtn.addEventListener("click", async () => {
    if (!window.confirm(`Unlink ${folder.path} from the project? Files on disk stay untouched.`)) return;
    await fetch(base, { method: "DELETE" });
    onChange();
  });
  top.appendChild(delBtn);

  const note = document.createElement("input");
  note.className = "folder-note";
  note.placeholder = "Note for the agent (what's here, what it's for)";
  note.value = folder.note;
  note.addEventListener("change", () => patch({ note: note.value }));

  const map = document.createElement("details");
  map.className = "memory-collapsible";
  const summary = document.createElement("summary");
  summary.textContent = "Map seen by the agent";
  const pre = document.createElement("pre");
  pre.className = "folder-map";
  pre.textContent = folder.map_text || "(not generated yet — appears on the project's first chat)";
  map.append(summary, pre);

  li.append(top, note, map);
  return li;
}

function folderAddRow(projectId, onChange) {
  const row = document.createElement("div");
  row.className = "kb-upload-row folder-add-row";

  const path = document.createElement("input");
  path.className = "folder-add-path";
  path.placeholder = "/absolute/path/to/folder";

  const mode = document.createElement("select");
  mode.add(new Option("ro (read)", "ro"));
  mode.add(new Option("rw (write)", "rw"));

  const indexLabel = document.createElement("label");
  indexLabel.className = "folder-check";
  const indexBox = document.createElement("input");
  indexBox.type = "checkbox";
  indexLabel.append(indexBox, " indexable");

  const addBtn = document.createElement("button");
  addBtn.textContent = "Link";
  addBtn.addEventListener("click", async () => {
    if (!path.value.trim()) return;
    try {
      await folderApi(
        `/api/projects/${projectId}/folders`,
        folderJson("POST", { path: path.value.trim(), mode: mode.value, indexable: indexBox.checked })
      );
      path.value = "";
      indexBox.checked = false;
    } catch (e) {
      window.alert(`Error: ${e.message}`);
    }
    onChange();
  });

  row.append(path, mode, indexLabel, addBtn);
  return row;
}

function renderFolders(projectId, list, onChange) {
  const box = document.createElement("div");
  box.className = "kb-box";

  const heading = document.createElement("div");
  heading.className = "project-field-label";
  heading.textContent = "Linked folders (direct access for the agent; indexable = searchable with project_search)";
  box.append(heading, folderAddRow(projectId, onChange));

  if (list.length === 0) {
    const empty = document.createElement("div");
    empty.className = "kb-empty";
    empty.textContent = "No linked folders.";
    box.appendChild(empty);
  } else {
    const ul = document.createElement("ul");
    ul.className = "kb-file-list";
    list.forEach((f) => ul.appendChild(folderItem(projectId, f, onChange)));
    box.appendChild(ul);
  }
  return box;
}

async function loadFoldersInto(card, projectId) {
  let current = document.createElement("div");
  card.querySelector(".project-card-actions").before(current);
  let timer = null;
  const refresh = async () => {
    clearTimeout(timer);
    try {
      const list = await folderApi(`/api/projects/${projectId}/folders`);
      const box = renderFolders(projectId, list, refresh);
      current.replaceWith(box);
      current = box;
      if (list.some((f) => f.index_status === "indexing")) timer = setTimeout(refresh, FOLDER_POLL_MS);
    } catch (e) {
      const err = document.createElement("div");
      err.className = "kb-empty";
      err.textContent = `Folders could not be loaded: ${e.message}`;
      current.replaceWith(err);
      current = err;
    }
  };
  await refresh();
}
