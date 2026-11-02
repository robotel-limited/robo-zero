/* modal folder picker for the "Linked folders" box on /projects:
   read-only listing of directories via /api/fs/browse.
   Returns the chosen absolute path, or null on cancel. */

// fetch local: project_folders.js (cu folderApi) e încărcat DUPĂ acest fișier
async function browse(path) {
  const resp = await fetch("/api/fs/browse?path=" + encodeURIComponent(path || ""));
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.json();
}

function openFolderBrowser(startPath) {
  return new Promise((resolve) => {
    // un singur overlay o dată
    const existing = document.getElementById("folder-browser-overlay");
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.id = "folder-browser-overlay";
    overlay.className = "folder-browser-overlay";

    const panel = document.createElement("div");
    panel.className = "folder-browser";

    // head: titlu + închidere
    const head = document.createElement("div");
    head.className = "folder-browser-head";
    const title = document.createElement("span");
    title.textContent = "Pick a folder";
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "folder-browser-close";
    closeBtn.textContent = "✕";
    head.append(title, closeBtn);

    // path bar:calea curentă + urcă un nivel
    const pathBar = document.createElement("div");
    pathBar.className = "folder-browser-pathbar";
    const pathInput = document.createElement("input");
    pathInput.readOnly = true;
    pathInput.className = "folder-browser-path";
    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "folder-browser-up";
    upBtn.textContent = "↑ Up";
    pathBar.append(pathInput, upBtn);

    // rădăcinile permise
    const rootsRow = document.createElement("div");
    rootsRow.className = "folder-browser-roots";

    const list = document.createElement("ul");
    list.className = "folder-browser-list";

    const status = document.createElement("div");
    status.className = "folder-browser-status";

    // foot: selectare + renunțare
    const foot = document.createElement("div");
    foot.className = "folder-browser-foot";
    const selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = "folder-browser-select";
    selectBtn.textContent = "Select this folder";
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "folder-browser-cancel";
    cancelBtn.textContent = "Cancel";
    foot.append(selectBtn, cancelBtn);

    panel.append(head, pathBar, rootsRow, list, status, foot);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    let current = startPath || "";
    let parent = null;

    const onKey = (e) => {
      if (e.key === "Escape") close(null);
    };
    document.addEventListener("keydown", onKey);

    function close(value) {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(value);
    }

    function render(data) {
      current = data.path;
      parent = data.parent;
      pathInput.value = data.path;
      upBtn.disabled = !parent;

      rootsRow.textContent = "";
      for (const root of data.roots) {
        const rb = document.createElement("button");
        rb.type = "button";
        rb.className = "folder-browser-root";
        rb.textContent = root;
        rb.addEventListener("click", () => go(root));
        rootsRow.appendChild(rb);
      }

      list.textContent = "";
      if (data.entries.length === 0) {
        const empty = document.createElement("li");
        empty.className = "folder-browser-empty";
        empty.textContent = "(no subfolders)";
        list.appendChild(empty);
        return;
      }
      for (const entry of data.entries) {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "folder-browser-entry";
        btn.textContent = `📁 ${entry.name}`;
        btn.addEventListener("click", () => go(entry.path));
        li.appendChild(btn);
        list.appendChild(li);
      }
    }

    async function go(target) {
      status.textContent = "Loading…";
      try {
        const data = await browse(target);
        render(data);
        status.textContent = "";
      } catch (e) {
        status.textContent = `Error: ${e.message}`;
      }
    }

    closeBtn.addEventListener("click", () => close(null));
    cancelBtn.addEventListener("click", () => close(null));
    selectBtn.addEventListener("click", () => close(current));
    upBtn.addEventListener("click", () => {
      if (parent) go(parent);
    });

    go(current);
  });
}
