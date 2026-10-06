// One tooltip element for the whole page. The title attribute moves to
// data-tip on first use, so the browser's own tooltip does not also show.
// Delegated listeners also cover the content HTMX inserts later.
(() => {
  const tip = document.getElementById("tip");
  let timer = null, current = null;
  function show(el) {
    const text = el.dataset.tip;
    if (!text) return;
    tip.textContent = text;
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    let top = r.bottom + 8, left = r.left + r.width / 2 - t.width / 2;
    if (top + t.height > innerHeight - 8) top = r.top - t.height - 8;
    left = Math.max(8, Math.min(left, innerWidth - t.width - 8));
    tip.style.top = top + "px"; tip.style.left = left + "px";
    tip.classList.add("on");
  }
  function enter(e) {
    const el = e.target.closest?.("[title], [data-tip]");
    if (!el || el === current) return;
    if (el.hasAttribute("title")) { el.dataset.tip = el.title; el.removeAttribute("title"); }
    tip.classList.remove("on"); current = el; clearTimeout(timer);
    timer = setTimeout(() => show(el), e.type === "focusin" ? 0 : 450);
  }
  function leave(e) {
    if (!current || (e.relatedTarget && current.contains(e.relatedTarget))) return;
    clearTimeout(timer); tip.classList.remove("on"); current = null;
  }
  document.addEventListener("mouseover", enter);
  document.addEventListener("mouseout", leave);
  document.addEventListener("focusin", enter);
  document.addEventListener("focusout", leave);
  document.addEventListener("keydown", e => { if (e.key === "Escape") { clearTimeout(timer); tip.classList.remove("on"); } });
  document.addEventListener("htmx:beforeRequest", () => { clearTimeout(timer); tip.classList.remove("on"); current = null; });
})();
