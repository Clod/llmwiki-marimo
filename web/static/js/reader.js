const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
function toggle(cls, btn, key) {
  const on = document.body.classList.toggle(cls);
  btn.setAttribute("aria-pressed", on);
  if (key) store.set(key, on ? "1" : "0");
}
const indexBtn = document.getElementById("index-btn"), chatBtn = document.getElementById("chat-btn");
if (store.get("llmwiki.index") === "1" && !matchMedia("(max-width: 1100px)").matches) { document.body.classList.add("index-open"); indexBtn.setAttribute("aria-pressed", "true"); }
indexBtn.onclick = () => toggle("index-open", indexBtn, "llmwiki.index");
function setChat(visible) {
  document.body.classList.toggle("chat-hidden", !visible);
  chatBtn.setAttribute("aria-pressed", visible);
  store.set("llmwiki.chat", visible ? "1" : "0");
}
if (store.get("llmwiki.chat") === "0") setChat(false);
chatBtn.onclick = () => setChat(document.body.classList.contains("chat-hidden"));
const narrow = matchMedia("(max-width: 1100px)");
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && narrow.matches && document.body.classList.contains("index-open")) toggle("index-open", indexBtn, "llmwiki.index");
});
function filterIndex(q) {
  q = q.trim().toLowerCase();
  document.querySelectorAll(".index-group").forEach(g => {
    let any = false;
    g.querySelectorAll("a").forEach(a => { const hit = !q || a.textContent.toLowerCase().includes(q); a.hidden = !hit; any ||= hit; });
    g.hidden = !any;
  });
}
function toggleConfirm(open) {
  const panel = document.getElementById("confirm-delete");
  panel.hidden = !open;
  document.querySelector("[aria-controls=confirm-delete]").setAttribute("aria-expanded", open);
  if (open) panel.querySelector(".cancel").focus();
}
function toggleSave(open) {
  const panel = document.getElementById("save-panel");
  panel.hidden = !open;
  document.getElementById("save-btn").setAttribute("aria-expanded", open);
  if (open) { document.getElementById("save-result").innerHTML = ""; panel.querySelector("input[name=title]").focus(); }
}
// The conversation can be saved once it has its first answer: the final
// render of each answer arrives as an out-of-band swap.
document.body.addEventListener("htmx:oobAfterSwap", e => {
  if (e.detail.target?.id?.startsWith("answer-")) setChatButtons(true);
});
function setChatButtons(enabled) {
  document.getElementById("save-btn").disabled = !enabled;
  document.getElementById("clear-btn").disabled = !enabled;
}
// After a clear the thread shows the empty state again; nothing is left to save.
document.body.addEventListener("htmx:afterSettle", e => {
  if (e.detail.requestConfig?.elt?.id === "clear-btn") {
    setChatButtons(false); toggleSave(false);
    document.getElementById("save-result").innerHTML = "";
  }
});
// A successful save closes the panel and clears the title; a failed one
// leaves both, so the user can correct and retry.
document.body.addEventListener("htmx:afterSettle", e => {
  if (e.detail.target?.id === "save-result" && document.querySelector("#save-result .ok")) {
    const panel = document.getElementById("save-panel");
    panel.reset(); panel.hidden = true;
    document.getElementById("save-btn").setAttribute("aria-expanded", false);
  }
});
function ask(text) {
  const form = document.getElementById("composer");
  form.querySelector("textarea").value = text.trim();
  form.requestSubmit();
}

// The conversation survives navigation. The browser keeps one conversation id
// per wiki in sessionStorage; the page loads its messages from the server, which
// keeps them in memory (web/state.py). A new tab starts a new conversation.
(() => {
  const chat = document.getElementById("chat");
  const key = "llmwiki.conversation." + chat.dataset.wiki;
  let id = null;
  try { id = sessionStorage.getItem(key); } catch {}
  const known = Boolean(id);
  if (!id) {
    id = chat.dataset.conversation;
    try { sessionStorage.setItem(key, id); } catch {}
  }
  document.querySelectorAll("#chat input[name=conversation_id]").forEach(input => { input.value = id; });
  if (known) {
    htmx.ajax("GET", `/w/${chat.dataset.wiki}/chat/${id}/thread`, { target: "#messages", swap: "innerHTML" });
  }
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.detail.target?.id !== "messages" || !e.detail.pathInfo?.requestPath.endsWith("/thread")) return;
    const thread = document.getElementById("messages");
    setChatButtons(Boolean(thread.querySelector(".a")));
    thread.scrollTop = thread.scrollHeight;
  });
})();

// The review dialog: the draft of the page opens in a native modal <dialog> that the
// user edits before saving. Focus moves into it (the textarea has `autofocus`) and
// returns to "Guardar" when it closes, by "Cancelar" or by Escape.
document.body.addEventListener("htmx:afterSwap", e => {
  if (e.detail.target?.id !== "save-result") return;
  const dialog = e.detail.target.querySelector("dialog.review");
  if (!dialog) return;
  dialog.addEventListener("close", () => {
    dialog.remove();
    document.getElementById("save-btn").focus();
  });
  dialog.showModal();
});
// A saved page replaces the dialog by the notice: focus goes back to "Guardar".
document.body.addEventListener("htmx:afterSettle", e => {
  if (e.detail.target?.id === "save-result" && document.querySelector("#save-result .ok")) {
    document.getElementById("save-btn").focus();
  }
});
function togglePreview(button) {
  const dialog = button.closest("dialog");
  const editor = dialog.querySelector(".review-editor"), preview = dialog.querySelector(".review-preview");
  const showPreview = preview.hidden;
  const done = () => {
    preview.hidden = !showPreview; editor.hidden = showPreview;
    button.setAttribute("aria-pressed", showPreview);
    button.textContent = showPreview ? button.dataset.labelEdit : button.dataset.labelPreview;
    (showPreview ? preview : editor).focus?.();
  };
  if (!showPreview) { done(); return; }
  htmx.ajax("POST", button.dataset.url, { target: preview, swap: "innerHTML",
                                          values: { markdown: editor.value, page: button.dataset.page } }).then(done);
}

// The thread follows the conversation: a new question, and each chunk of the answer
// as it streams, scroll the thread to its end, so the answer stays in view. A user who
// scrolls up to read an earlier answer stops the following; scrolling back to the end,
// or asking again, resumes it.
(() => {
  const thread = document.getElementById("messages");
  if (!thread) return;
  const SLACK = 48;                                   // px from the end that still count as "at the end"
  let follow = true;
  const atEnd = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight <= SLACK;
  thread.addEventListener("scroll", () => { follow = atEnd(); }, { passive: true });
  new MutationObserver(records => {
    const asked = records.some(r => [...r.addedNodes].some(n => n.classList?.contains("q")));
    if (asked) follow = true;
    if (follow) thread.scrollTop = thread.scrollHeight;
  }).observe(thread, { childList: true, subtree: true, characterData: true });
})();
