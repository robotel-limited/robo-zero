/* Pending image attachments for the compose box: upload-on-pick/paste,
   the thumbnail strip, and the urls handed to the send handler. */

const attachImageBtn = document.getElementById("attach-image-btn");
const attachImageInput = document.getElementById("attach-image-input");
const composeAttachmentsEl = document.getElementById("compose-attachments");
const composeInputEl = document.getElementById("compose-input");

let pendingAttachments = []; // [{url}] — uploaded images, not sent yet

function renderAttachments() {
  composeAttachmentsEl.innerHTML = "";
  pendingAttachments.forEach((att, i) => {
    const box = document.createElement("div");
    box.className = "compose-attachment";
    const img = document.createElement("img");
    img.src = att.url;
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachments();
    });
    box.append(img, removeBtn);
    composeAttachmentsEl.appendChild(box);
  });
}

async function uploadAttachment(file) {
  try {
    const { url } = await api("/api/attachments", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    pendingAttachments.push({ url });
    renderAttachments();
  } catch (e) {
    appendMessage("message-error", `Error attaching image: ${e.message}`);
  }
}

function getPendingAttachmentUrls() {
  return pendingAttachments.map((a) => a.url);
}

function clearPendingAttachments() {
  pendingAttachments = [];
  renderAttachments();
}

attachImageBtn.addEventListener("click", () => attachImageInput.click());

attachImageInput.addEventListener("change", async () => {
  for (const file of attachImageInput.files) await uploadAttachment(file);
  attachImageInput.value = "";
});

composeInputEl.addEventListener("paste", async (ev) => {
  const files = [...(ev.clipboardData?.items || [])]
    .filter((item) => item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (files.length === 0) return;
  ev.preventDefault();
  for (const file of files) await uploadAttachment(file);
});
