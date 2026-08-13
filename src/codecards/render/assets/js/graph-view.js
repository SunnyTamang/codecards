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
    return layout(next);
  }

  function select(id) {
    selectedId = id;
    mounted.forEach(function (card, cardId) {
      card.classList.toggle('selected', cardId === id);
    });
    CC.panel.show(id);
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
        node.width = CC.cards.CARD_W;
        node.height = CC.cards.CARD_H;
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
      if (!isContainer) card.style.height = box.h + 'px';
      cardLayer.appendChild(card);
      mounted.set(id, card);

      const head = card.querySelector('.card-head');
      head.addEventListener('click', function () {
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

    CC.edges.render(svg, state.edges, state.boxes, {
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
        CC.canvas.fit({ x: extent.x, y: extent.y,
                        w: extent.right - extent.x, h: extent.bottom - extent.y }, 40);
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
  };
})();
