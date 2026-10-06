// The relations screen: the wiki graph on a canvas, with force-graph (vasturiano, MIT).
(() => {
  const root = document.getElementById("relations");
  const stage = document.getElementById("stage");
  const wiki = root.dataset.wiki;
  const localPage = root.dataset.page;
  const depth = Number(root.dataset.depth) || 1;
  const searchBox = document.getElementById("search");
  const showSummary = document.getElementById("show-summary");
  const showSource = document.getElementById("show-source");
  const counts = document.getElementById("counts");
  const css = getComputedStyle(document.documentElement);
  const token = name => css.getPropertyValue(name).trim();
  const COLORS = { concept: token("--color-node-concept"), summary: token("--color-node-summary"),
                   source: token("--color-node-source") };
  const EDGE = token("--color-edge"), EDGE_LIT = token("--color-edge-highlight"), DIM = token("--color-node-dim");
  const OUTLINE = token("--color-node-outline"), TEXT = token("--color-text"), BG = token("--color-bg");
  const FONT = token("--font-sans");
  const radius = n => Math.min(22, 3 + 2.5 * Math.sqrt(n.degree));
  const LABEL_PX = 9;   // a node shows its label unprompted once it is this many pixels in radius

  // Local graph: its default shows summaries and sources, there are few nodes.
  if (localPage) { showSource.checked = true; }

  let all = { nodes: [], edges: [] };
  const byId = new Map();
  let links = [];
  let neighbors = new Map();      // id → Set of ids, over the whole graph
  const taken = [];               // label boxes drawn in this frame, in graph units
  let touched = false;            // the user zoomed or panned: stop refitting
  for (const ev of ["wheel", "pointerdown"]) stage.addEventListener(ev, () => { touched = true; }, { passive: true });
  let hover = null, pinned = null; // hovered node, node found by the search box
  let focusSet = null, focusLinks = null;

  const fg = ForceGraph()(stage)
    .backgroundColor(BG)
    .nodeId("id")
    .nodeLabel(() => "")
    .nodeRelSize(1)
    .linkColor(l => (focusLinks && focusLinks.has(l) ? EDGE_LIT : EDGE))
    .linkWidth(l => (focusLinks && focusLinks.has(l) ? 2 : 1))
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((n, color, ctx) => {
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 2, 0, 2 * Math.PI); ctx.fill();
    })
    .onNodeHover(n => { hover = n || null; refocus(); stage.style.cursor = n ? "pointer" : ""; })
    .onNodeClick(openNode)
    .onRenderFramePre(() => { taken.length = 0; })
    .cooldownTicks(300)
    .warmupTicks(20)
    .onEngineStop(() => { root.dataset.settled = "1"; });
  // Gravity toward the centre keeps unconnected groups of pages in one view.
  let pulled = [];
  const gravity = alpha => { for (const n of pulled) { n.vx -= n.x * 0.06 * alpha; n.vy -= n.y * 0.06 * alpha; } };
  gravity.initialize = nodes => { pulled = nodes; };
  fg.d3Force("gravity", gravity);
  fg.d3Force("charge").strength(-60).distanceMax(400);
  fg.d3Force("link").distance(40);

  function size() { fg.width(stage.clientWidth).height(stage.clientHeight); }
  new ResizeObserver(size).observe(stage);
  size();

  function refocus() {
    const n = hover || pinned;
    if (!n) { focusSet = focusLinks = null; return; }
    focusSet = new Set([n.id, ...(neighbors.get(n.id) || [])]);
    focusLinks = new Set(links.filter(l => idOf(l.source) === n.id || idOf(l.target) === n.id));
  }
  const idOf = e => (typeof e === "object" ? e.id : e);

  // Concepts and summaries are circles, sources rounded squares, as in the legend.
  function shape(ctx, n, r) {
    ctx.beginPath();
    if (n.kind === "source") ctx.roundRect(n.x - r, n.y - r, 2 * r, 2 * r, r * 0.3);
    else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
  }

  function drawNode(n, ctx, k) {
    const r = radius(n), dim = focusSet && !focusSet.has(n.id);
    const lit = n === hover || n === pinned;
    // The highlighted node wears a yellow ring; what is out of focus fades to the dimmed blue.
    shape(ctx, n, r);
    ctx.fillStyle = dim ? DIM : COLORS[n.kind];
    ctx.fill();
    if (!dim) { ctx.lineWidth = 1 / k; ctx.strokeStyle = OUTLINE; ctx.stroke(); }
    if (lit) {
      shape(ctx, n, r + 2 / k);
      ctx.lineWidth = 4 / k; ctx.strokeStyle = COLORS.source; ctx.stroke();
    }
    const want = lit || (!dim && (r * k >= LABEL_PX || (focusSet && focusSet.has(n.id) && k > 1.5)));
    if (want) {
      const fs = 12 / k;
      ctx.font = `${lit ? 600 : 400} ${fs}px ${FONT}`;
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      const w = ctx.measureText(n.title).width;
      const box = [n.x - w / 2, n.y + r, n.x + w / 2, n.y + r + fs + 2 / k];
      // Labels do not overlap: the hovered node always shows its own, the others yield.
      const clash = taken.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      if (lit || !clash) { taken.push(box); label(n, ctx, k, r, w, fs); }
    }
  }

  function label(n, ctx, k, r, w, fs) {
    ctx.globalAlpha = 0.8; ctx.fillStyle = BG;
    ctx.fillRect(n.x - w / 2 - 2 / k, n.y + r + 1 / k, w + 4 / k, fs + 2 / k);
    ctx.globalAlpha = 1; ctx.fillStyle = TEXT;
    ctx.fillText(n.title, n.x, n.y + r + 2 / k);
  }

  function openNode(n) {
    if (n.kind === "source") {
      window.open(`/w/${wiki}/view/${encodeURIComponent(n.title)}`, "_blank", "noopener");
    } else {
      location.href = `/w/${wiki}/pages/${n.id}`;
    }
  }

  // Pages within `depth` links of `localPage`, over every edge, either direction.
  function localIds() {
    const seen = new Set([localPage]);
    let frontier = [localPage];
    for (let d = 0; d < depth; d++) {
      const next = [];
      for (const id of frontier) for (const m of neighbors.get(id) || []) if (!seen.has(m)) { seen.add(m); next.push(m); }
      frontier = next;
    }
    return seen;
  }

  function visible(n, scope) {
    if (scope && !scope.has(n.id)) return false;
    return n.kind === "summary" ? showSummary.checked : n.kind === "source" ? showSource.checked : true;
  }

  function update() {
    const scope = localPage ? localIds() : null;
    // Larger nodes first: they draw their labels first, so the smaller ones yield.
    const nodes = all.nodes.filter(n => visible(n, scope)).sort((a, b) => b.degree - a.degree);
    const ids = new Set(nodes.map(n => n.id));
    const edges = links.filter(l => ids.has(idOf(l.source)) && ids.has(idOf(l.target)));
    delete root.dataset.settled;
    fg.graphData({ nodes, links: edges });
    counts.textContent = root.dataset.countsLabel.replace("{nodes}", nodes.length).replace("{edges}", edges.length);
    document.getElementById("empty").hidden = all.nodes.length > 0;
    if (pinned && !ids.has(pinned.id)) pinned = null;
    refocus();
    root.dataset.nodes = nodes.length; root.dataset.edges = edges.length;
  }

  function fold(s) { return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }

  function find(text) {
    const q = fold(text.trim());
    if (!q) return null;
    const pages = all.nodes.filter(n => n.kind !== "source");
    return pages.find(n => fold(n.title) === q) || pages.find(n => fold(n.title).includes(q)) || null;
  }

  function goTo(n) {
    if (n.kind === "summary") showSummary.checked = true;
    pinned = n; update();
    pinned = n; refocus();
    fg.centerAt(n.x, n.y, 400); fg.zoom(4, 400);
  }

  searchBox.addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const n = find(searchBox.value);
    if (n) goTo(n); else { pinned = null; refocus(); }
  });
  searchBox.addEventListener("input", () => { if (!searchBox.value) { pinned = null; refocus(); } });
  // The zoom buttons use the library's own zoom; once the user zooms the view stays put.
  document.getElementById("zoom-in").addEventListener("click", () => { touched = true; fg.zoom(fg.zoom() * 1.5, 250); });
  document.getElementById("zoom-out").addEventListener("click", () => { touched = true; fg.zoom(fg.zoom() / 1.5, 250); });
  document.getElementById("zoom-fit").addEventListener("click", () => { touched = false; fg.zoomToFit(300, 80, () => true); });
  showSummary.addEventListener("change", update);
  showSource.addEventListener("change", update);

  fetch(root.dataset.url).then(r => r.json()).then(data => {
    all = data;
    for (const n of all.nodes) byId.set(n.id, n);
    links = all.edges.map(e => ({ source: e.source, target: e.target, type: e.type }));
    neighbors = new Map(all.nodes.map(n => [n.id, new Set()]));
    for (const e of all.edges) { neighbors.get(e.source).add(e.target); neighbors.get(e.target).add(e.source); }
    document.getElementById("titles").replaceChildren(
      ...all.nodes.filter(n => n.kind !== "source").map(n => Object.assign(document.createElement("option"), { value: n.title })));
    update();
    // Fit the graph on every settle until the user takes the view into their hands.
    fg.onEngineStop(() => {
      root.dataset.settled = "1";
      if (touched) return;
      fg.zoomToFit(300, 80, () => true);
      setTimeout(() => { if (!touched && fg.zoom() > 2.5) fg.zoom(2.5, 200); }, 350);
    });
    root.dataset.ready = "1";
  });

  // For tests and for the console: the graph and the screen position of a node.
  window.relationsGraph = { fg, find, screenOf: id => { const n = byId.get(id); return fg.graph2ScreenCoords(n.x, n.y); } };
})();
