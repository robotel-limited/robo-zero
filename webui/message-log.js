/* Rendering of chat log entries into the message list: user/agent/tool
   messages, the typing indicator, tool-approval prompts, and per-message
   copy/delete actions. */

const messagesEl = document.getElementById("messages");
const approvalBar = document.getElementById("approval-bar");
const approvalText = document.getElementById("approval-text");
const sendBtn = document.getElementById("send-btn");
const cancelBtn = document.getElementById("cancel-btn");

// tool_result from image_generate returns the message "Image generated: /files/images/<id>.jpg"
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
  if (!confirm("Delete this exchange? Your message and this response will also be removed from the conversation context.")) {
    return;
  }
  try {
    const res = await fetch(`/api/chats/${currentChatId}/turns/${turnId}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || "Could not delete (it may have already been summarized into the context).");
      return;
    }
  } catch {
    alert("Could not reach the server.");
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
    copyBtn.title = "Copy response";
    copyBtn.innerHTML = COPY_ICON;
    copyBtn.addEventListener("click", () => copyMessageText(text, copyBtn));
    div.appendChild(copyBtn);

    if (turnId) {
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "message-delete-btn";
      deleteBtn.title = "Delete exchange (and from context)";
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
      if (document.getElementById("typing-indicator")) showTypingIndicator();
      break;
    case "tool_result":
      // actual produced content (e.g. a generated image) stays visible even with
      // verbose=off — only the reasoning (text calls/results) is hidden
      if (settings.verbose || IMAGE_PATH_RE.test(entry.message)) {
        appendMessage("message-tool-result", `${entry.name}: ${entry.message}`, null, entry.turn_id);
      }
      if (document.getElementById("typing-indicator")) showTypingIndicator();
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
      appendMessage("message-error", `Error: ${entry.text}`);
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
        "Project fact sheet updated:\n" +
          [...entry.added.map((t) => `+ ${t}`), ...entry.replaced.map((r) => `~ ${r.old} → ${r.new}`)].join("\n")
      );
      break;
    case "project_context_refreshed":
      appendMessage("message-memory", "New context received from other project chats:\n" + entry.lines.join("\n"));
      break;
    case "project_rules_proposed":
      appendMessage(
        "message-memory",
        "New rules proposed (awaiting approval on the project card in /projects):\n" +
          entry.added.map((t) => `+ ${t}`).join("\n")
      );
      break;
    default:
      break;
  }
}

function showApproval(toolName, args) {
  approvalText.textContent = `Approve execution? ${toolName}(${args})`;
  approvalBar.classList.remove("hidden");
}

function hideApproval() {
  approvalBar.classList.add("hidden");
}

function setRunning(running) {
  cancelBtn.classList.toggle("hidden", !running);
  sendBtn.disabled = running;
  if (!running) hideApproval();
  if (running) showTypingIndicator();
  else hideTypingIndicator();
}

function showTypingIndicator() {
  let div = document.getElementById("typing-indicator");
  if (!div) {
    div = document.createElement("div");
    div.id = "typing-indicator";
    div.className = "message message-typing";
    div.innerHTML = "<span></span><span></span><span></span>";
  }
  messagesEl.appendChild(div); // appendChild on an already-attached node moves it to the end
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideTypingIndicator() {
  document.getElementById("typing-indicator")?.remove();
}
