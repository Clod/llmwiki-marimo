// The search field of the roster shows only the names that contain what is typed,
// without case nor accents ("dolar" also finds the accented spelling). It works on
// the list the page holds; a change of the vocabulary replaces the list, and the
// field starts empty.
const fold = text => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
document.addEventListener("input", e => {
  if (e.target.id !== "roster-search") return;
  const query = fold(e.target.value);
  let shown = 0;
  document.querySelectorAll("#roster-list li[data-name]").forEach(li => {
    const hit = !query || fold(li.dataset.name).includes(query);
    li.hidden = !hit;
    if (hit) shown += 1;
  });
  document.getElementById("roster-none").hidden = shown > 0;
});

// A click on a dataset name of the roster loads the dataset into a native modal
// <dialog>; the marked row (the key clicked) is scrolled into view. Closing it by
// ×, Close or Escape returns the focus to the name that opened it.
let datasetOpener = null;
document.body.addEventListener("htmx:beforeRequest", e => {
  if (e.detail.elt?.classList?.contains("roster-data")) datasetOpener = e.detail.elt;
});
document.body.addEventListener("htmx:afterSwap", e => {
  if (e.detail.target?.id !== "dataset-slot") return;
  const dialog = e.detail.target.querySelector("dialog.dataset");
  if (!dialog) return;
  dialog.addEventListener("close", () => { dialog.remove(); datasetOpener?.focus(); });
  dialog.showModal();
  dialog.querySelector("tr.marked")?.scrollIntoView({ block: "center" });
});
