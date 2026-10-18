/* regulile învățate ale unui proiect (pagina /projects): propuneri extrase
automat din corecțiile lui Sorin, care intră în contextul agentului doar
după aprobare de aici. Respinsele rămân arhivate, pliabile, ca extragerea
să nu le repropună. */

async function rulesApi(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.json();
}

function rulesJson(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function pendingRuleItem(projectId, rule, onChange) {
  const base = `/api/projects/${projectId}/rules/${rule.id}`;
  const li = document.createElement("li");
  li.className = "kb-file-item";

  const text = document.createElement("input");
  text.className = "folder-note";
  text.value = rule.text;
  text.addEventListener("change", async () => {
    try {
      await rulesApi(base, rulesJson("PATCH", { text: text.value }));
    } catch (e) {
      window.alert(`Eroare: ${e.message}`);
    }
    onChange();
  });

  const approveBtn = document.createElement("button");
  approveBtn.className = "kb-file-delete";
  approveBtn.title = "Aprobă regula (intră în contextul agentului)";
  approveBtn.textContent = "✓";
  approveBtn.addEventListener("click", async () => {
    try {
      await rulesApi(`${base}/approve`, { method: "POST" });
    } catch (e) {
      window.alert(`Eroare: ${e.message}`);
    }
    onChange();
  });

  const rejectBtn = document.createElement("button");
  rejectBtn.className = "kb-file-delete";
  rejectBtn.title = "Respinge regula";
  rejectBtn.textContent = "✕";
  rejectBtn.addEventListener("click", async () => {
    try {
      await rulesApi(`${base}/reject`, { method: "POST" });
    } catch (e) {
      window.alert(`Eroare: ${e.message}`);
    }
    onChange();
  });

  li.append(text, approveBtn, rejectBtn);
  return li;
}

function readonlyRuleItem(rule, struck) {
  const li = document.createElement("li");
  li.textContent = rule.text;
  if (struck) li.classList.add("memory-superseded");
  const meta = document.createElement("span");
  meta.className = "memory-meta";
  meta.textContent = ` — (${rule.category || "Altele"})`;
  li.appendChild(meta);
  return li;
}

function renderRules(projectId, list, onChange) {
  const box = document.createElement("div");
  box.className = "kb-box";

  const heading = document.createElement("div");
  heading.className = "project-field-label";
  heading.textContent = "Reguli învățate (propuneri extrase din corecțiile tale — aprobă-le ca să intre în context)";
  box.appendChild(heading);

  const pending = list.filter((r) => r.status === "pending");
  const approved = list.filter((r) => r.status === "approved");
  const rejected = list.filter((r) => r.status === "rejected" || r.status === "superseded");

  if (pending.length === 0 && approved.length === 0) {
    const empty = document.createElement("div");
    empty.className = "kb-empty";
    empty.textContent = "Nicio regulă propusă încă.";
    box.appendChild(empty);
  }

  if (pending.length > 0) {
    const label = document.createElement("div");
    label.className = "memory-meta";
    label.textContent = "În așteptare:";
    const ul = document.createElement("ul");
    ul.className = "kb-file-list";
    pending.forEach((r) => ul.appendChild(pendingRuleItem(projectId, r, onChange)));
    box.append(label, ul);
  }

  if (approved.length > 0) {
    const ul = document.createElement("ul");
    ul.className = "memory-facts";
    approved.forEach((r) => ul.appendChild(readonlyRuleItem(r, false)));
    box.appendChild(ul);
  }

  if (rejected.length > 0) {
    const ul = document.createElement("ul");
    ul.className = "memory-facts memory-superseded";
    rejected.forEach((r) => ul.appendChild(readonlyRuleItem(r, true)));
    box.appendChild(memoryCollapsible(`Respinse/înlocuite (${rejected.length})`, [ul]));
  }

  return box;
}

async function loadRulesInto(card, projectId) {
  let current = document.createElement("div");
  card.querySelector(".project-card-actions").before(current);
  const refresh = async () => {
    try {
      const list = await rulesApi(`/api/projects/${projectId}/rules`);
      const box = renderRules(projectId, list, refresh);
      current.replaceWith(box);
      current = box;
    } catch (e) {
      const err = document.createElement("div");
      err.className = "kb-empty";
      err.textContent = `Regulile nu s-au putut încărca: ${e.message}`;
      current.replaceWith(err);
      current = err;
    }
  };
  await refresh();
}
