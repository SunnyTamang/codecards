// Isolation: dim the canvas down to one thing worth reading.
//
// Two things can be isolated, and only ever one at a time. Selecting a card
// isolates its neighbourhood, N hops upstream and downstream. Picking a ring
// out of the info panel isolates that ring.
//
// Both live here because both write the same `dimmed` class, and two modules
// taking turns on one class is how a canvas ends up half lit. Everything that
// redraws already calls `active()` and `apply()`, so a second mode costs the
// callers nothing.
//
// The traversal runs on the full graph, not the visible one: a callee hidden
// inside a collapsed module is still reached, and its module stays lit. The
// visible set is only consulted when deciding which cards to dim.

window.CC = window.CC || {};

CC.focus = (function () {
  let current = null;
  let ring = null;
  let hops = 1;

  function adjacency() {
    const out = {};
    const back = {};
    CC.view.state.data.edges.forEach(function (edge) {
      (out[edge.source] = out[edge.source] || []).push(edge.target);
      (back[edge.target] = back[edge.target] || []).push(edge.source);
    });
    return { out: out, back: back };
  }

  // Everything a node contains, at any depth. Containment is not a call, so a
  // module owns no call edges: its members do. Selecting `cli` and walking
  // only its own edges therefore reaches nothing and dims the module's own
  // functions, which is the one thing selecting a module cannot mean.
  function subtree(id) {
    const childIndex = CC.view.state.data.childIndex;
    const out = new Set([id]);
    const stack = [id];
    while (stack.length) {
      (childIndex[stack.pop()] || []).forEach(function (child) {
        if (out.has(child)) return;
        out.add(child);
        stack.push(child);
      });
    }
    return out;
  }

  // Breadth-first to `depth` in both directions, seeded with the whole
  // subtree so "this and what it touches" holds for a plate as well as for a
  // single function. `seen` makes cycles finite.
  function neighbourhood(id, depth) {
    const links = adjacency();
    const seen = subtree(id);
    let frontier = Array.from(seen);
    for (let step = 0; step < depth; step++) {
      const next = [];
      frontier.forEach(function (node) {
        (links.out[node] || []).concat(links.back[node] || []).forEach(function (other) {
          if (seen.has(other)) return;
          seen.add(other);
          next.push(other);
        });
      });
      frontier = next;
      if (!frontier.length) break;
    }
    return seen;
  }

  // Where a node is drawn right now: itself, or the collapsed plate it is
  // folded into. A ring names functions, and the reader may be looking at
  // packages.
  function shownAs(id) {
    const state = CC.view.state;
    return CC.collapse.representative(
      id, state.data.parentIndex, state.visible, state.collapsed);
  }

  // Every plate between a node and the canvas. Lit alongside the node itself:
  // a dimmed frame drawn around a lit region reads as though the region had
  // come loose from its own module.
  // An arrow is three marks - dot, line, head - and they have to fade and lift
  // as one. `decide` is handed each mark's edge key and answers whether it
  // stays lit.
  function markEdges(decide, ringed) {
    document.querySelectorAll('#edges [data-edge], #edges [data-head]')
      .forEach(function (mark) {
        const key = mark.dataset.edge || mark.dataset.head;
        const lit = decide(key);
        mark.classList.toggle('dimmed', !lit);
        mark.classList.toggle('ringed', !!ringed && lit);
      });
  }

  function platesAround(id, lit) {
    let enclosing = CC.view.state.data.parentIndex[id];
    while (enclosing) {
      lit.add(enclosing);
      enclosing = CC.view.state.data.parentIndex[enclosing];
    }
  }

  // One ring lit, everything else dimmed.
  //
  // Line weight alone cannot do this job. A ring is a shape, its members can
  // sit at opposite corners of the graph, and the reader has to trace it -
  // which is the one thing a slightly heavier stroke among two hundred
  // strokes does not help with.
  function applyRing(cards) {
    const drawn = ring.map(shownAs).filter(Boolean);
    const members = new Set(drawn);
    const lit = new Set(drawn);
    ring.forEach(function (id) { platesAround(id, lit); });

    // The calls that close the ring, in the terms the canvas is drawing.
    // Two members folded into one plate have no line between them; the plate
    // still lights, which is as much as a collapsed view can say.
    const closing = new Set();
    drawn.forEach(function (id, i) {
      const next = drawn[(i + 1) % drawn.length];
      if (next !== id) closing.add(id + '->' + next);
    });

    cards.forEach(function (card) {
      const id = card.dataset.id;
      card.classList.toggle('dimmed', !lit.has(id));
      card.classList.toggle('ringed', members.has(id));
    });
    markEdges(function (key) { return closing.has(key); }, true);
  }

  function apply() {
    const cards = document.querySelectorAll('#cards .card');
    if (!current && !ring) {
      cards.forEach(function (card) {
        card.classList.remove('dimmed');
        card.classList.remove('ringed');
      });
      markEdges(function () { return true; }, false);
      return;
    }
    if (ring) return applyRing(cards);
    const reached = neighbourhood(current, hops);
    const lit = new Set();
    reached.forEach(function (id) {
      const visible = shownAs(id);
      if (visible) lit.add(visible);
    });
    platesAround(current, lit);
    cards.forEach(function (card) {
      card.classList.toggle('dimmed', !lit.has(card.dataset.id));
      card.classList.remove('ringed');
    });
    markEdges(function (key) {
      const pair = key.split('->');
      return lit.has(pair[0]) && lit.has(pair[1]);
    }, false);
  }

  // Leaving ring mode puts the edge filters back, which needs a fresh layout,
  // so both exits go through here. Skipped when no ring was showing: a plain
  // deselect must not cost a relayout.
  function leaveRing() {
    if (!ring) return null;
    ring = null;
    return CC.view.insist([]);
  }

  function set(id, radius) {
    const relayout = leaveRing();
    current = id;
    if (radius) hops = radius;
    document.getElementById('focus-control').hidden = false;
    if (relayout) return relayout.then(apply);
    apply();
  }

  function clear() {
    const relayout = leaveRing();
    current = null;
    document.getElementById('focus-control').hidden = true;
    if (relayout) return relayout.then(apply);
    apply();
  }

  function setHops(radius) { hops = radius; apply(); }

  // `path` is the ring in call order, the last member calling back to the
  // first. Opening the plates comes first and is asynchronous: a ring folded
  // inside one collapsed package lights as a single card with no lines,
  // which says less than the panel's own text does.
  function setRing(path) {
    current = null;
    ring = path.slice();
    document.getElementById('focus-control').hidden = true;
    // The ring's own calls, named so the canvas draws them whatever the
    // filters say. A ring can be built entirely out of ambiguous calls, which
    // are hidden by default - lighting the cards and none of the lines is the
    // one outcome this control must not produce.
    const calls = ring.map(function (id, i) {
      return id + '->' + ring[(i + 1) % ring.length];
    });
    return CC.view.reveal(ring, calls).then(apply);
  }

  return {
    neighbourhood: neighbourhood,
    set: set,
    clear: clear,
    setHops: setHops,
    setRing: setRing,
    apply: apply,
    ring: function () { return ring; },
    active: function () { return current || ring; },
  };
})();
