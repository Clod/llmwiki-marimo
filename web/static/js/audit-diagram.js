// The audit page: the diagrams of the right column follow the turn the reader is on.
// Mermaid draws the <pre class="mermaid"> elements; the server (`routes/audit.py`) puts
// them in the column, and htmx replaces them when a turn is selected.
(() => {
  const column = document.getElementById("audit-diagrams");
  if (!column) return;
  let shown = document.querySelector(".audit-columns")?.dataset.selected;

  const draw = () => {
    if (!window.mermaid) return;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", flowchart: { useMaxWidth: false } });
    mermaid.run({ nodes: column.querySelectorAll("pre.mermaid:not([data-processed])") });
  };

  // Selecting a turn asks the server for its diagrams (the button of the turn holds the address).
  const select = (n) => {
    if (!n || n === shown) return;
    const button = document.querySelector(`.audit-select[data-turn="${n}"]`);
    if (!button) return;
    shown = n;
    htmx.trigger(button, "click");
  };
  const turnOf = (element) => element?.closest?.(".audit-turn")?.querySelector(".audit-select")?.dataset.turn;
  const fromHash = () => turnOf(document.getElementById(location.hash.slice(1)));

  document.body.addEventListener("htmx:afterSwap", (e) => { if (e.target === column) draw(); });
  document.getElementById("audit-left")?.addEventListener("click", (e) => select(turnOf(e.target)));
  window.addEventListener("hashchange", () => select(fromHash()));
  // A hash that points to another turn skips the first drawing: the server, which never
  // receives the hash, sent the diagrams of the last turn, and drawing them would show them
  // until the diagrams of the turn in the hash arrive.
  window.addEventListener("DOMContentLoaded", () => {
    const n = fromHash();
    if (n && n !== shown) select(n);
    else draw();
  });
})();
