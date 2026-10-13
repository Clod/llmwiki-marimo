// Separators of the reading page: they change --index-w and --chat-w. The widths chosen are stored per
// browser; the inline script in the head of page.html applies them before the first paint.
(() => {
  const MIN = { index: 160, chat: 280 }, PAGE_MIN = 360, STEP = 16, BIG_STEP = 64;
  const reader = document.querySelector(".reader");
  const narrow = matchMedia("(max-width: 1100px)");
  const body = document.body;
  const key = col => "llmwiki." + col + "W";
  const overrides = { index: null, chat: null };
  for (const col of ["index", "chat"]) { const v = parseInt(store.get(key(col)), 10); if (v > 0) overrides[col] = v; }

  let style = document.getElementById("panel-widths");
  if (!style) { style = document.createElement("style"); style.id = "panel-widths"; document.head.appendChild(style); }
  function render() {
    const rules = ["index", "chat"].filter(c => overrides[c] !== null).map(c => `--${c}-w: ${overrides[c]}px;`);
    style.textContent = rules.length ? `@media (min-width: 1101px) { :root { ${rules.join(" ")} } }` : "";
  }
  const width = col => parseFloat(getComputedStyle(document.documentElement).getPropertyValue(`--${col}-w`));
  const visible = col => col === "index" ? body.classList.contains("index-open") : !body.classList.contains("chat-hidden");
  const other = col => col === "index" ? "chat" : "index";

  // The widest a column can be: the page keeps PAGE_MIN and the other visible column keeps its width.
  function max(col) {
    const css = getComputedStyle(reader), gap = parseFloat(css.columnGap) || 0;
    const columns = 1 + 1 + (visible(other(col)) ? 1 : 0);
    const room = reader.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - gap * (columns - 1);
    return Math.max(MIN[col], room - PAGE_MIN - (visible(other(col)) ? width(other(col)) : 0));
  }
  const clamp = (col, v) => Math.round(Math.min(max(col), Math.max(MIN[col], v)));

  const separators = { index: document.querySelector(".separator-index"), chat: document.querySelector(".separator-chat") };
  function sync(col) {
    const sep = separators[col];
    sep.setAttribute("aria-valuemin", MIN[col]);
    sep.setAttribute("aria-valuemax", Math.round(max(col)));
    sep.setAttribute("aria-valuenow", Math.round(width(col)));
  }
  const syncAll = () => { if (!narrow.matches) { sync("index"); sync("chat"); } };

  function set(col, value, save = true) {
    overrides[col] = clamp(col, value);
    render();
    if (save) store.set(key(col), String(overrides[col]));
    syncAll();
  }
  function reset(col) {
    overrides[col] = null;
    render();
    try { localStorage.removeItem(key(col)); } catch {}
    syncAll();
  }

  for (const col of ["index", "chat"]) {
    const sep = separators[col];
    // The chat is at the right of its separator, so moving the separator to the left widens it.
    const sign = col === "chat" ? -1 : 1;
    let startX = 0, startWidth = 0;
    sep.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      e.preventDefault();
      startX = e.clientX; startWidth = width(col);
      sep.setPointerCapture(e.pointerId);
      sep.classList.add("dragging"); body.classList.add("resizing");
    });
    sep.addEventListener("pointermove", e => {
      if (!sep.hasPointerCapture(e.pointerId)) return;
      set(col, startWidth + sign * (e.clientX - startX));
    });
    const stop = e => {
      if (sep.hasPointerCapture(e.pointerId)) sep.releasePointerCapture(e.pointerId);
      sep.classList.remove("dragging"); body.classList.remove("resizing");
    };
    sep.addEventListener("pointerup", stop);
    sep.addEventListener("pointercancel", stop);
    sep.addEventListener("dblclick", () => reset(col));
    sep.addEventListener("keydown", e => {
      const dir = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (dir === undefined) return;
      e.preventDefault();
      set(col, width(col) + sign * dir * (e.shiftKey ? BIG_STEP : STEP));
    });
  }

  // A smaller window or a column that appears can leave the stored widths too wide: the columns shrink for
  // this view only, and the stored widths stay.
  function fit() {
    if (narrow.matches) return;
    for (const col of ["index", "chat"]) if (visible(col) && width(col) > max(col)) set(col, max(col), false);
    syncAll();
  }
  addEventListener("resize", fit);
  document.getElementById("index-btn").addEventListener("click", fit);
  document.getElementById("chat-btn").addEventListener("click", fit);
  render(); fit();
})();
