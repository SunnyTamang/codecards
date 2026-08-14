// Layout and painting. ELK computes coordinates; this owns the DOM.

window.CC = window.CC || {};

CC.view = (function () {
  const CULL_MARGIN = 400;
  const PAD_TOP = 30;
  const PAD = 14;

  const elk = new ELK();
  const state = {
    data: null,
    collapsed: new Set(),
    visible: new Set(),
    edges: [],
    boxes: {},
    internalCounts: {},
    orphans: new Set(),
    entries: new Set(),
  };
  let svg = null;
  let cardLayer = null;
  let mounted = new Map();
  let selectedId = null;
  let tierFilter = new Set(['resolved', 'inferred']);

  function setTierFilter(tiers) {
    tierFilter = new Set(tiers);
    return layout(state.collapsed);
  }

  function drawnEdges() {
    const hidden = hiddenIds();
    const out = [];
    state.data.edges.forEach(function (edge) {
      if (!tierFilter.has(edge.confidence)) return;
      const source = foldHidden(edge.source, hidden);
      const target = foldHidden(edge.target, hidden);
      if (!source || !target) return;
      if (source === edge.source && target === edge.target) {
        out.push(edge);
        return;
      }
      out.push(Object.assign({}, edge, { source: source, target: target }));
    });
    return out;
  }

  function toggle(id) {
    const next = new Set(state.collapsed);
    if (next.has(id)) next.delete(id); else next.add(id);
    // Expanding means "show me inside this", so the camera goes to the thing
    // that was opened. Refitting the whole graph instead answers a question
    // nobody asked and, on a wide graph, answers it at a scale where nothing
    // is readable: opening one package in this project's own graph dropped
    // the view to 39%, which is small enough to render every card's detail
    // illegible while still being too large for the block tier to help.
    return layout(next, { focusOn: next.has(id) ? null : id });
  }

  // The panel is a mode the reader opts into, via a card's info control.
  // Once it is open it follows the selection, so walking the graph with it
  // open keeps working; while it is closed, selecting stays a canvas action
  // and never covers the thing being read.
  function select(id, options) {
    selectedId = id;
    mounted.forEach(function (card, cardId) {
      card.classList.toggle('selected', cardId === id);
    });
    if ((options && options.panel) || CC.panel.isOpen()) CC.panel.show(id);
    CC.focus.set(id);
    // Selecting opens the card, which moves where its edges leave from.
    paint();
  }

  function selected() { return selectedId; }

  // Clearing has to undo all three things selecting did, or the canvas keeps
  // a card outlined, the panel open, or two thirds of the graph dimmed with
  // no obvious way back.
  function deselect() {
    selectedId = null;
    mounted.forEach(function (card) { card.classList.remove('selected'); });
    CC.panel.hide();
    CC.focus.clear();
    paint();
  }

  function nodeById(id) { return state.data.nodeIndex[id]; }

  function childrenOf(id) { return state.data.childIndex[id] || []; }

  // Special methods are hidden by default: __eq__ and __hash__ are language
  // machinery, not flow anyone reads a codebase to understand.
  let showDunders = false;

  function hiddenIds() {
    const out = new Set();
    if (showDunders) return out;
    state.data.nodes.forEach(function (node) {
      if (node.dunder) out.add(node.id);
    });
    return out;
  }

  // An edge touching a hidden node is re-pointed at its nearest visible
  // ancestor rather than dropped. __init__ has real callers, since
  // constructor calls retarget onto it, so dropping would delete the flow
  // "something builds a Mailer" from the graph entirely.
  function foldHidden(id, hidden) {
    let cursor = id;
    while (cursor && hidden.has(cursor)) cursor = state.data.parentIndex[cursor];
    return cursor || null;
  }

  function setShowDunders(show) {
    showDunders = !!show;
    return layout(state.collapsed);
  }

  // A node is visible when nothing above it is collapsed. Descent stops at a
  // collapsed container: the container itself is visible, its children are not.
  function computeVisible(collapsed) {
    const hidden = hiddenIds();
    const visible = new Set();
    function walk(id) {
      if (hidden.has(id)) return;
      visible.add(id);
      if (collapsed.has(id)) return;
      childrenOf(id).forEach(walk);
    }
    state.data.roots.forEach(walk);
    return visible;
  }

  function buildElkTree(collapsed, visible) {
    // state.edges is already aggregated for this view by the time layout runs,
    // so fan-in is known before ELK is asked for coordinates and magnitude can
    // decide the box rather than only what is drawn inside it.
    const counts = fanCounts();

    function toElk(id) {
      const kids = collapsed.has(id) ? [] : childrenOf(id).filter(function (c) {
        return visible.has(c);
      });
      const node = { id: id };
      if (kids.length) {
        node.children = kids.map(toElk);
        node.layoutOptions = {
          'elk.padding': '[top=' + PAD_TOP + ',left=' + PAD +
                         ',bottom=' + PAD + ',right=' + PAD + ']',
        };
      } else {
        const box = CC.cards.boxFor(counts.inCount[id] || 0);
        node.width = box.w;
        node.height = box.h;
      }
      return node;
    }
    return {
      id: 'root',
      layoutOptions: {
        'elk.algorithm': 'layered',
        'elk.direction': 'DOWN',
        // Without this, edges that cross a container boundary are routed as
        // if the hierarchy were flat and the layered ordering falls apart.
        'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
        'elk.layered.spacing.nodeNodeBetweenLayers': '70',
        'elk.spacing.nodeNode': '34',
        'elk.spacing.edgeNode': '20',
      },
      children: state.data.roots.filter(function (r) { return visible.has(r); }).map(toElk),
      edges: state.edges.map(function (edge, index) {
        return { id: 'e' + index, sources: [edge.source], targets: [edge.target] };
      }),
    };
  }

  // ELK returns child coordinates relative to the parent. Accumulate down the
  // tree; skipping this puts every nested card in the wrong place while the
  // top-level ones look correct.
  function flatten(elkNode, offsetX, offsetY, out) {
    (elkNode.children || []).forEach(function (child) {
      const x = offsetX + (child.x || 0);
      const y = offsetY + (child.y || 0);
      out[child.id] = { x: x, y: y, w: child.width || 0, h: child.height || 0 };
      flatten(child, x, y, out);
    });
    return out;
  }

  // Aggregated view edges carry weight and tiers but not call sites, so the
  // raw edge list is the only place the line numbers survive.
  function firstCallSite(source, target) {
    for (const edge of state.data.edges) {
      if (edge.source === source && edge.target === target) {
        const sites = edge.sites || [];
        if (sites.length) return sites[0];
      }
    }
    return null;
  }

  function callLinesFor(id) {
    const map = new Map();
    state.data.edges.forEach(function (edge) {
      if (edge.source !== id) return;
      (edge.sites || []).forEach(function (site) {
        map.set(site.line, site);
      });
    });
    return map;
  }

  function fanCounts() {
    const inCount = {};
    const outCount = {};
    state.edges.forEach(function (edge) {
      outCount[edge.source] = (outCount[edge.source] || 0) + (edge.weight || 1);
      inCount[edge.target] = (inCount[edge.target] || 0) + (edge.weight || 1);
    });
    return { inCount: inCount, outCount: outCount };
  }

  // ---- moving things ----
  // ELK gives the opening arrangement. From then on state.boxes is the truth,
  // and a drag edits it directly: no second coordinate system to keep in sync,
  // and edges keep reading the same boxes they always did.

  function depthOf(id) {
    let depth = 0;
    let cursor = state.data.parentIndex[id];
    while (cursor) { depth++; cursor = state.data.parentIndex[cursor]; }
    return depth;
  }

  // Every leaf under a node, which is what actually carries a position. A
  // container's box is derived, so moving one means moving its members.
  function movableUnder(id) {
    const out = [];
    (function walk(node) {
      const kids = childrenOf(node).filter(function (c) { return state.visible.has(c); });
      if (!kids.length || state.collapsed.has(node)) { out.push(node); return; }
      kids.forEach(walk);
    })(id);
    return out;
  }

  function isDrawnContainer(id) {
    return !state.collapsed.has(id) &&
      childrenOf(id).some(function (c) { return state.visible.has(c); });
  }

  function moveBy(ids, dx, dy) {
    ids.forEach(function (id) {
      const box = state.boxes[id];
      if (box) { box.x += dx; box.y += dy; }
    });
  }

  // Deepest first, so a module re-fits around a class that has already
  // re-fitted around its methods. Any other order leaves the outer plate one
  // frame behind whenever something nested moves.
  function refitContainers() {
    const containers = Object.keys(state.boxes)
      .filter(isDrawnContainer)
      .sort(function (a, b) { return depthOf(b) - depthOf(a); });

    containers.forEach(function (id) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      childrenOf(id).forEach(function (child) {
        const box = state.visible.has(child) && state.boxes[child];
        if (!box) return;
        x0 = Math.min(x0, box.x);
        y0 = Math.min(y0, box.y);
        x1 = Math.max(x1, box.x + box.w);
        y1 = Math.max(y1, box.y + box.h);
      });
      if (x0 === Infinity) return;
      const box = state.boxes[id];
      box.x = x0 - PAD;
      box.y = y0 - PAD_TOP;
      box.w = (x1 - x0) + PAD * 2;
      box.h = (y1 - y0) + PAD_TOP + PAD;
    });
  }

  // Siblings are the only plates that can meaningfully collide: a parent
  // always contains its own child. Marking the one that was run into is what
  // makes an overlap read as a consequence rather than as breakage.
  function collidingWith(id) {
    const box = state.boxes[id];
    const parent = state.data.parentIndex[id];
    if (!box) return [];
    return Object.keys(state.boxes).filter(function (other) {
      if (other === id) return false;
      if (state.data.parentIndex[other] !== parent) return false;
      if (!isDrawnContainer(other)) return false;
      const b = state.boxes[other];
      return box.x < b.x + b.w && b.x < box.x + box.w &&
             box.y < b.y + b.h && b.y < box.y + box.h;
    });
  }

  // Push state.boxes back onto the DOM without rebuilding anything. Called on
  // every pointermove, so it touches only the two properties that changed.
  function reposition() {
    mounted.forEach(function (card, id) {
      const box = state.boxes[id];
      if (!box) return;
      card.style.left = box.x + 'px';
      card.style.top = box.y + 'px';
      card.style.width = box.w + 'px';
      if (!card.classList.contains('container')) return;
      card.style.height = box.h + 'px';
    });
  }

  // Centre of the most-called visible object: where a reader should land when
  // the whole graph cannot fit at a readable scale.
  function brightestPoint() {
    const counts = fanCounts();
    let best = null;
    let bestCount = -1;
    Object.keys(state.boxes).forEach(function (id) {
      const count = counts.inCount[id] || 0;
      if (count > bestCount) {
        bestCount = count;
        best = state.boxes[id];
      }
    });
    if (!best) return null;
    return { x: best.x + best.w / 2, y: best.y + best.h / 2 };
  }

  // Just the edge layer. Cheap enough to run on hover, where the cards are
  // unchanged and only the point a line leaves from has moved.
  // The boxes as actually drawn. An open card grows past the box layout
  // reserved for it, so ports taken from the layout box would start a line
  // somewhere inside the card the reader can see.
  function drawnBoxes() {
    const out = Object.assign({}, state.boxes);
    const scale = CC.canvas.getView().scale;
    mounted.forEach(function (card, id) {
      if (card.classList.contains('container')) return;
      const box = state.boxes[id];
      if (!box) return;
      const rect = card.getBoundingClientRect();
      const w = rect.width / scale;
      const h = rect.height / scale;
      if (w > box.w + 1 || h > box.h + 1) {
        out[id] = { x: box.x, y: box.y, w: w, h: h };
      }
    });
    return out;
  }

  function redrawEdges() {
    if (!svg || !state.data) return;
    CC.edges.render(svg, state.edges, drawnBoxes(), {
      // World-space point an edge should leave from: the right edge of the
      // source card, at the vertical centre of the line making the call.
      // Measured from the DOM, because a source-tier card grows past both
      // the width and the height that layout reserved for it.
      anchorFor: function (edge) {
        const card = mounted.get(edge.source);
        if (!card || !card.classList.contains('tier-source')) return null;
        const site = firstCallSite(edge.source, edge.target);
        if (!site) return null;
        const row = card.querySelector('.src-line[data-line="' + site.line + '"]');
        if (!row) return null;
        // The body is only laid out while the card is open. A hidden row
        // measures zero and would anchor the edge to the card's top corner.
        if (!row.getClientRects().length) return null;
        // The body scrolls past a few hundred pixels of source, and a row
        // below that fold still measures where it would have been, hundreds
        // of pixels below the card. Anchoring to the calling line is a promise
        // that the line is on screen; when it is not, leave from the card edge
        // like any other edge rather than pointing at empty canvas.
        const body = row.parentElement;
        const bodyRect = body.getBoundingClientRect();
        const centre = row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2;
        if (centre < bodyRect.top || centre > bodyRect.bottom) return null;
        const box = state.boxes[edge.source];
        const cardRect = card.getBoundingClientRect();
        const rowRect = row.getBoundingClientRect();
        const scale = CC.canvas.getView().scale;
        return {
          x: box.x + cardRect.width / scale,
          y: box.y + (rowRect.top + rowRect.height / 2 - cardRect.top) / scale,
        };
      },
    });
    if (CC.focus && CC.focus.active()) CC.focus.apply();
  }

  function paint() {
    if (!state.data) return;
    const rect = CC.canvas.visibleWorldRect(CULL_MARGIN);
    const counts = fanCounts();
    const wanted = new Set();

    Object.keys(state.boxes).forEach(function (id) {
      const box = state.boxes[id];
      const outside = box.x > rect.x + rect.w || box.x + box.w < rect.x ||
                      box.y > rect.y + rect.h || box.y + box.h < rect.y;
      if (outside) return;
      wanted.add(id);
      if (mounted.has(id)) return;

      const node = nodeById(id);
      const isContainer = !state.collapsed.has(id) && childrenOf(id).length > 0;
      const card = CC.cards.build(node, {
        isContainer: isContainer,
        isOrphan: state.orphans.has(id),
        isEntry: state.entries.has(id),
        fanIn: counts.inCount[id] || 0,
        fanOut: counts.outCount[id] || 0,
        internal: state.internalCounts[id] || 0,
        callLines: callLinesFor(id),
      });
      card.style.left = box.x + 'px';
      card.style.top = box.y + 'px';
      card.style.width = box.w + 'px';
      // Containers take their full box too, so a module reads as the region
      // its members sit inside rather than as a label bar floating above
      // them. Children are separately positioned siblings painted after the
      // container, so they sit on top of its transparent ground.
      card.style.height = box.h + 'px';
      cardLayer.appendChild(card);
      mounted.set(id, card);

      CC.drag.attach(card, id);

      // Pointing at a card opens its source, which is pure CSS, and opening it
      // moves the point its edges leave from. Nothing else redraws on hover,
      // so without this the lines stay anchored to the geometry the card had
      // before it opened, and are left pointing into empty space after it
      // closes again.
      card.addEventListener('pointerenter', redrawEdges);
      card.addEventListener('pointerleave', redrawEdges);

      const head = card.querySelector('.card-head');
      head.addEventListener('click', function () {
        // A drag ends with a click on the thing that was dragged. Treating
        // that as a click would expand a module every time one was moved.
        if (CC.drag.didMove()) return;
        if (childrenOf(id).length && state.collapsed.has(id)) {
          toggle(id);
        } else {
          select(id);
        }
      });
      head.addEventListener('dblclick', function () {
        if (childrenOf(id).length && !state.collapsed.has(id)) toggle(id);
      });
    });

    mounted.forEach(function (card, id) {
      if (!wanted.has(id)) {
        card.remove();
        mounted.delete(id);
      }
    });

    // Tiers first. Edge anchoring reads the tier class and the resulting
    // rendered geometry, so rendering edges before this would anchor every
    // edge against the previous frame's tier.
    if (CC.zoom) CC.zoom.apply();

    redrawEdges();

    if (selectedId && mounted.has(selectedId)) {
      mounted.get(selectedId).classList.add('selected');
    }
    if (CC.focus && CC.focus.active()) CC.focus.apply();
  }

  // `options.refit === false` keeps the current camera. Refitting is right
  // when the user expands something and wants to see the result, and wrong
  // during a walkthrough, where it yanks the zoom out to the whole graph and
  // makes the very line the step is pointing at unreadable.
  function layout(collapsed, options) {
    state.collapsed = collapsed;
    state.visible = computeVisible(collapsed);
    state.edges = CC.collapse.aggregate(drawnEdges(), state.data.parentIndex,
                                        state.visible, collapsed);
    state.internalCounts = CC.collapse.internalCounts(
      drawnEdges(), state.data.parentIndex, state.visible, collapsed);

    CC.view.ready = false;
    return elk.layout(buildElkTree(collapsed, state.visible)).then(function (result) {
      state.boxes = flatten(result, 0, 0, {});
      mounted.forEach(function (card) { card.remove(); });
      mounted = new Map();
      const extent = Object.keys(state.boxes).reduce(function (acc, id) {
        const box = state.boxes[id];
        return {
          x: Math.min(acc.x, box.x), y: Math.min(acc.y, box.y),
          right: Math.max(acc.right, box.x + box.w),
          bottom: Math.max(acc.bottom, box.y + box.h),
        };
      }, { x: Infinity, y: Infinity, right: -Infinity, bottom: -Infinity });
      const refit = !(options && options.refit === false);
      if (refit && extent.x !== Infinity) {
        const focus = options && options.focusOn && state.boxes[options.focusOn];
        CC.canvas.fit(focus
          ? { x: focus.x, y: focus.y, w: focus.w, h: focus.h }
          : { x: extent.x, y: extent.y,
              w: extent.right - extent.x, h: extent.bottom - extent.y },
          40, { anchor: brightestPoint() });
      }
      paint();
      CC.view.ready = true;
    });
  }

  function index(data) {
    const nodeIndex = {};
    const childIndex = {};
    const parentIndex = {};
    const roots = [];
    data.nodes.forEach(function (node) {
      nodeIndex[node.id] = node;
      parentIndex[node.id] = node.parent;
      if (node.parent) {
        (childIndex[node.parent] = childIndex[node.parent] || []).push(node.id);
      } else {
        roots.push(node.id);
      }
    });
    return { nodeIndex: nodeIndex, childIndex: childIndex,
             parentIndex: parentIndex, roots: roots };
  }

  function init(data) {
    svg = document.getElementById('edges');
    cardLayer = document.getElementById('cards');
    state.data = Object.assign({}, data, index(data));
    state.orphans = new Set(data.orphans || []);
    // "nothing calls it" is a structural fallback that matches thousands of
    // functions in a large library, so marking those would mark half the
    // canvas. Only a reason that says something about intent counts as a door.
    state.entries = new Set(
      (data.entryPoints || [])
        .filter(function (e) {
          // "nothing calls it" is structural, and a test is a way into the
          // test suite rather than into the program. Marking either would
          // mark half the canvas.
          return e.reasons.some(function (r) {
            return r !== 'no_callers' && r !== 'test';
          });
        })
        .map(function (e) { return e.id; })
    );
    return layout(new Set(data.initialView.collapsed));
  }

  return {
    ready: false,
    init: init,
    layout: layout,
    paint: paint,
    state: state,
    boxes: function () { return state.boxes; },
    toggle: toggle,
    select: select,
    selected: selected,
    deselect: deselect,
    setTierFilter: setTierFilter,
    setShowDunders: setShowDunders,
    movableUnder: movableUnder,
    isDrawnContainer: isDrawnContainer,
    moveBy: moveBy,
    refitContainers: refitContainers,
    collidingWith: collidingWith,
    reposition: reposition,
    // Back to the computed arrangement. ELK is re-run rather than a snapshot
    // restored, so the result is the same layout a fresh open would give.
    resetLayout: function () { return layout(state.collapsed); },
  };
})();
