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
