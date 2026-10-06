// The folder browser of the picker opens in a native modal <dialog>. Focus moves into
// it and returns to "Open another folder…" when it closes, by "Cancel", × or Escape.
document.body.addEventListener("htmx:afterSwap", e => {
  if (e.detail.target?.id !== "browse-slot") return;
  const dialog = e.detail.target.querySelector("dialog.browse");
  if (!dialog) return;
  const opener = document.querySelector(".open-other .btn");
  dialog.addEventListener("close", () => {
    dialog.remove();
    opener?.focus();
  });
  dialog.showModal();
});

// Opening a folder navigates away with the dialog still open. The browser keeps the page
// in its back-forward cache, so the Back button would show the picker with the dialog
// open. The dialog is removed when the page is left, and again if a cached page returns.
const removeBrowser = () => { document.getElementById("browse-slot").replaceChildren(); };
window.addEventListener("pagehide", removeBrowser);
window.addEventListener("pageshow", e => { if (e.persisted) removeBrowser(); });

// "Create a wiki here…" shows the confirmation of the folder browser; only its own
// button creates the wiki. Showing another folder replaces the panel and drops it.
function toggleCreate(open) {
  const confirm = document.getElementById("browse-create");
  confirm.hidden = !open;
  document.querySelector("[aria-controls=browse-create]").setAttribute("aria-expanded", open);
  if (open) confirm.querySelector(".btn-primary").focus();
}
