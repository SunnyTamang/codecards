// Mirror of graph/collapse.py. The Python version is the specification.
// a separate change asserts this reproduces the shipped `initialView` exactly.

window.CC = window.CC || {};

CC.collapse = (function () {
  const ORDER = ['resolved', 'inferred', 'ambiguous', 'external', 'unresolved'];

  // Walk up the containment tree to the outermost collapsed ancestor, or to
  // the node itself when nothing above it is collapsed.
  function representative(id, parentIndex, visible, collapsed) {
    let best = visible.has(id) ? id : null;
    let cursor = parentIndex[id];
    while (cursor !== null && cursor !== undefined) {
      if (collapsed.has(cursor) && visible.has(cursor)) best = cursor;
      cursor = parentIndex[cursor];
    }
    return best;
  }

  function aggregate(edges, parentIndex, visible, collapsed) {
    const merged = new Map();
    edges.forEach(function (edge) {
      const source = representative(edge.source, parentIndex, visible, collapsed);
      const target = representative(edge.target, parentIndex, visible, collapsed);
      if (!source || !target || source === target) return;  // internal
      const key = source + ' ' + target;
      let entry = merged.get(key);
      if (!entry) {
        entry = { source: source, target: target, weight: 0, tiers: {} };
        merged.set(key, entry);
      }
      const weight = (edge.sites && edge.sites.length) || 1;
      entry.weight += weight;
      entry.tiers[edge.confidence] = (entry.tiers[edge.confidence] || 0) + weight;
    });
    // Display at the highest confidence present: at least one call is certain.
    return Array.from(merged.values()).map(function (entry) {
      entry.confidence = ORDER.find(function (tier) { return entry.tiers[tier]; });
      return entry;
    });
  }

  function internalCounts(edges, parentIndex, visible, collapsed) {
    const counts = {};
    edges.forEach(function (edge) {
      const source = representative(edge.source, parentIndex, visible, collapsed);
      const target = representative(edge.target, parentIndex, visible, collapsed);
      if (source && source === target) {
        counts[source] = (counts[source] || 0) + ((edge.sites && edge.sites.length) || 1);
      }
    });
    return counts;
  }

  return { representative: representative, aggregate: aggregate,
           internalCounts: internalCounts, ORDER: ORDER };
})();
