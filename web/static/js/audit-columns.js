// The separator of the audit page: it changes --audit-diagrams-w, the width of the column of the
// diagrams. A wide diagram needs a wide column; the turns on the left keep MIN. The width chosen is
// stored per browser, as the reading page stores its widths (panels.js).
(() => {
  const MIN = 320, STEP = 16, BIG_STEP = 64, KEY = "llmwiki.auditDiagramsW";
  const columns = document.querySelector(".audit-columns");
  const sep = document.querySelector(".audit-separator");
  const right = document.getElementById("audit-diagrams");
  if (!columns || !sep || !right) return;
  const narrow = matchMedia("(max-width: 768px)");
  const body = document.body;

  const width = () => right.getBoundingClientRect().width;
  // The widest the diagrams can be: the turns keep MIN, and the separator and the two gaps keep theirs.
  function max() {
    const css = getComputedStyle(columns), gap = parseFloat(css.columnGap) || 0;
    const room = columns.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - 2 * gap - sep.offsetWidth;
    return Math.max(MIN, room - MIN);
  }
  const clamp = v => Math.round(Math.min(max(), Math.max(MIN, v)));
  function sync() {
    sep.setAttribute("aria-valuemax", Math.round(max()));
    sep.setAttribute("aria-valuenow", Math.round(width()));
  }
  function set(value, save = true) {
    const w = clamp(value);
    columns.style.setProperty("--audit-diagrams-w", w + "px");
    if (save) { try { localStorage.setItem(KEY, String(w)); } catch {} }
    sync();
  }
  function reset() {
    columns.style.removeProperty("--audit-diagrams-w");
    try { localStorage.removeItem(KEY); } catch {}
    sync();
  }

  // The diagrams are at the right of the separator, so moving the separator to the left widens them.
  let startX = 0, startWidth = 0;
  sep.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    e.preventDefault();
    startX = e.clientX; startWidth = width();
    sep.setPointerCapture(e.pointerId);
    sep.classList.add("dragging"); body.classList.add("resizing");
  });
  sep.addEventListener("pointermove", e => {
    if (sep.hasPointerCapture(e.pointerId)) set(startWidth - (e.clientX - startX));
  });
  const stop = e => {
    if (sep.hasPointerCapture(e.pointerId)) sep.releasePointerCapture(e.pointerId);
    sep.classList.remove("dragging"); body.classList.remove("resizing");
  };
  sep.addEventListener("pointerup", stop);
  sep.addEventListener("pointercancel", stop);
  sep.addEventListener("dblclick", reset);
  sep.addEventListener("keydown", e => {
    const dir = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (dir === undefined) return;
    e.preventDefault();
    set(width() - dir * (e.shiftKey ? BIG_STEP : STEP));
  });

  // A smaller window can leave the stored width too wide: the column shrinks for this view only.
  function fit() {
    if (narrow.matches) return;
    if (columns.style.getPropertyValue("--audit-diagrams-w") && width() > max()) set(max(), false);
    sync();
  }
  let stored = NaN;
  try { stored = parseInt(localStorage.getItem(KEY), 10); } catch {}
  if (stored > 0) set(stored, false);
  addEventListener("resize", fit);
  fit();
})();
