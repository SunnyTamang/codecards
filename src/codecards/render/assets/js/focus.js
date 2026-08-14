// Neighbourhood isolation. Selecting a card dims everything outside N hops
// of it, upstream and downstream.
//
// The traversal runs on the full graph, not the visible one: a callee hidden
// inside a collapsed module is still reached, and its module stays lit. The
// visible set is only consulted when deciding which cards to dim.

window.CC = window.CC || {};

CC.focus = (function () {
  let current = null;
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

  function apply() {
    const cards = document.querySelectorAll('#cards .card');
    if (!current) {
      cards.forEach(function (card) { card.classList.remove('dimmed'); });
      document.querySelectorAll('#edges path').forEach(function (p) {
        p.classList.remove('dimmed');
      });
      return;
    }
    const reached = neighbourhood(current, hops);
    const state = CC.view.state;
    const lit = new Set();
    reached.forEach(function (id) {
      const visible = CC.collapse.representative(
        id, state.data.parentIndex, state.visible, state.collapsed);
      if (visible) lit.add(visible);
    });
    // The plates you are inside stay lit. A dimmed frame drawn around a lit
    // region reads as though the region had come loose from its own module.
    let enclosing = state.data.parentIndex[current];
    while (enclosing) {
      lit.add(enclosing);
      enclosing = state.data.parentIndex[enclosing];
    }
    cards.forEach(function (card) {
      card.classList.toggle('dimmed', !lit.has(card.dataset.id));
    });
    document.querySelectorAll('#edges path').forEach(function (path) {
      const pair = (path.dataset.edge || '').split('->');
      path.classList.toggle('dimmed', !(lit.has(pair[0]) && lit.has(pair[1])));
    });
  }

  function set(id, radius) {
    current = id;
    if (radius) hops = radius;
    document.getElementById('focus-control').hidden = false;
    apply();
  }

  function clear() {
    current = null;
    document.getElementById('focus-control').hidden = true;
    apply();
  }

  function setHops(radius) { hops = radius; apply(); }

  return {
    neighbourhood: neighbourhood,
    set: set,
    clear: clear,
    setHops: setHops,
    apply: apply,
    active: function () { return current; },
  };
})();
