/* Fișa proiectului pe cardul din /projects — read-only: faptele active pe
categorii, cu sursă și dată, plus istoricul înlocuirilor și rezumatele
discuțiilor (pliabile). Corecțiile se fac din chat, nu de aici. */

function memoryDay(ts) {
  return new Date(ts * 1000).toLocaleDateString("sv-SE"); // YYYY-MM-DD, ora locală
}

function memoryFactItem(fact) {
  const li = document.createElement("li");
  li.textContent = fact.text;
  const meta = document.createElement("span");
  meta.className = "memory-meta";
  const parts = [];
  if (fact.event_date) parts.push(`eveniment: ${fact.event_date}`);
  parts.push(`sursa: „${fact.source_chat_title || "discuție ștearsă"}”, ${memoryDay(fact.created_at)}`);
  meta.textContent = ` — ${parts.join("; ")}`;
  li.appendChild(meta);
  return li;
}

function memoryCollapsible(label, children) {
  const details = document.createElement("details");
  details.className = "memory-collapsible";
  const summary = document.createElement("summary");
  summary.textContent = label;
  details.append(summary, ...children);
  return details;
}

function renderMemory(memory) {
  const box = document.createElement("div");
  box.className = "memory-box";

  const heading = document.createElement("div");
  heading.className = "project-field-label";
  heading.textContent = "Fișa proiectului (extrasă automat din discuții — corecțiile se fac din chat)";
  box.appendChild(heading);

  if (memory.facts.length === 0) {
    const empty = document.createElement("div");
    empty.className = "memory-meta";
    empty.textContent = "Încă goală.";
    box.appendChild(empty);
  }
  const byCategory = new Map();
  for (const fact of memory.facts) {
    const cat = fact.category || "Altele";
    if (!byCategory.has(cat)) byCategory.set(cat, []);
    byCategory.get(cat).push(fact);
  }
  for (const [cat, facts] of byCategory) {
    const title = document.createElement("div");
    title.className = "memory-category";
    title.textContent = cat;
    const ul = document.createElement("ul");
    ul.className = "memory-facts";
    facts.forEach((f) => ul.appendChild(memoryFactItem(f)));
    box.append(title, ul);
  }

  if (memory.superseded.length > 0) {
    const ul = document.createElement("ul");
    ul.className = "memory-facts memory-superseded";
    memory.superseded.forEach((f) => ul.appendChild(memoryFactItem(f)));
    box.appendChild(memoryCollapsible(`Istoric înlocuiri (${memory.superseded.length})`, [ul]));
  }

  if (memory.chat_summaries.length > 0) {
    const items = memory.chat_summaries.map((d) => {
      const div = document.createElement("div");
      div.className = "memory-summary";
      const title = document.createElement("strong");
      title.textContent = `${d.chat_title} (${memoryDay(d.updated_at)})`;
      const text = document.createElement("div");
      text.textContent = d.summary;
      div.append(title, text);
      return div;
    });
    box.appendChild(memoryCollapsible(`Rezumatele discuțiilor (${items.length})`, items));
  }
  return box;
}

async function loadMemoryInto(card, projectId) {
  try {
    const resp = await fetch(`/api/projects/${projectId}/memory`);
    if (!resp.ok) throw new Error(`${resp.status}`);
    card.querySelector(".project-card-actions").before(renderMemory(await resp.json()));
  } catch (e) {
    const err = document.createElement("div");
    err.className = "memory-meta";
    err.textContent = `Fișa nu s-a putut încărca: ${e.message}`;
    card.querySelector(".project-card-actions").before(err);
  }
}

/* baza de cunoștințe a proiectului — upload fără multipart (body brut,
numele în query string), vezi upload_routes.py */

function kbFormatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function kbStatusLabel(file) {
  if (file.status === "processing") return "se procesează…";
  if (file.status === "error") return `eroare: ${file.error}`;
  return `${kbFormatSize(file.size)} · ${file.chars} caractere extrase`;
}

function kbFileItem(projectId, file, onChange) {
  const li = document.createElement("li");
  li.className = "kb-file-item";

  const link = document.createElement("a");
  link.className = "kb-file-name";
  link.href = `/api/projects/${projectId}/kb/${file.id}/raw`;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = file.filename;
  li.appendChild(link);

  const status = document.createElement("span");
  status.className = "kb-file-status" + (file.status === "error" ? " error" : "");
  status.textContent = kbStatusLabel(file);
  li.appendChild(status);

  const delBtn = document.createElement("button");
  delBtn.className = "kb-file-delete";
  delBtn.textContent = "🗑";
  delBtn.title = "Șterge fișierul";
  delBtn.addEventListener("click", async () => {
    if (!window.confirm(`Ștergi „${file.filename}” din baza de cunoștințe?`)) return;
    await fetch(`/api/projects/${projectId}/kb/${file.id}`, { method: "DELETE" });
    onChange();
  });
  li.appendChild(delBtn);

  return li;
}

async function kbUpload(projectId, file, onChange) {
  const resp = await fetch(
    `/api/projects/${projectId}/kb?filename=${encodeURIComponent(file.name)}`,
    { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file }
  );
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    window.alert(`Eroare la încărcare: ${body.detail || resp.status}`);
    return;
  }
  onChange();
}

function renderKb(projectId, files, onChange) {
  const box = document.createElement("div");
  box.className = "kb-box";

  const heading = document.createElement("div");
  heading.className = "project-field-label";
  heading.textContent = "Baza de cunoștințe (fișiere text, PDF, DOCX, imagini)";
  box.appendChild(heading);

  const uploadRow = document.createElement("div");
  uploadRow.className = "kb-upload-row";
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.addEventListener("change", async () => {
    for (const file of input.files) await kbUpload(projectId, file, onChange);
    input.value = "";
  });
  uploadRow.appendChild(input);
  box.appendChild(uploadRow);

  if (files.length === 0) {
    const empty = document.createElement("div");
    empty.className = "kb-empty";
    empty.textContent = "Niciun fișier încă.";
    box.appendChild(empty);
  } else {
    const ul = document.createElement("ul");
    ul.className = "kb-file-list";
    files.forEach((f) => ul.appendChild(kbFileItem(projectId, f, onChange)));
    box.appendChild(ul);
  }
  return box;
}

async function loadKbInto(card, projectId) {
  let current = document.createElement("div");
  card.querySelector(".project-card-actions").before(current);
  const refresh = async () => {
    try {
      const resp = await fetch(`/api/projects/${projectId}/kb`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const files = await resp.json();
      const box = renderKb(projectId, files, refresh);
      current.replaceWith(box);
      current = box;
    } catch (e) {
      const err = document.createElement("div");
      err.className = "kb-empty";
      err.textContent = `Baza de cunoștințe nu s-a putut încărca: ${e.message}`;
      current.replaceWith(err);
      current = err;
    }
  };
  await refresh();
}
