// Mirror of graph/walkthrough.py. The Python version is the specification,
// and the golden test asserts this reproduces the shipped goldenTrace exactly.
//
// One step per call site, ordered by line. The ordering is lexical, not
// executional: a call inside an `if` still produces a step, and the player
// labels it so the sequence is not mistaken for a real trace.

window.CC = window.CC || {};

CC.trace = (function () {
  const DRAWN = ['resolved', 'inferred', 'ambiguous'];

  function build(edges, entryId, maxDepth, tiers) {
    const allowed = new Set(tiers || DRAWN);
    const bySource = {};
    edges.forEach(function (edge) {
      if (!allowed.has(edge.confidence)) return;
      (bySource[edge.source] = bySource[edge.source] || []).push(edge);
    });

    const steps = [];
    const onStack = new Set();

    function walk(callerId, depth, stack) {
      if (depth >= maxDepth) return;
      const outgoing = [];
      (bySource[callerId] || []).forEach(function (edge) {
        (edge.sites && edge.sites.length ? edge.sites : [{ line: 0 }]).forEach(
          function (site) { outgoing.push({ edge: edge, site: site }); });
      });
      outgoing.sort(function (a, b) { return (a.site.line || 0) - (b.site.line || 0); });

      outgoing.forEach(function (call) {
        const calleeId = call.edge.target;
        const recursive = onStack.has(calleeId);
        steps.push({
          index: steps.length,
          callerId: callerId,
          calleeId: calleeId,
          line: call.site.line || 0,
          depth: depth,
          stack: stack.slice(),
          confidence: call.edge.confidence,
          cond: !!call.site.cond,
          loop: !!call.site.loop,
          recursive: recursive,
        });
        if (recursive) return;  // marked, not re-entered
        onStack.add(calleeId);
        walk(calleeId, depth + 1, stack.concat([calleeId]));
        onStack.delete(calleeId);
      });
    }

    onStack.add(entryId);
    walk(entryId, 0, [entryId]);
    return steps;
  }

  return { build: build, DRAWN: DRAWN };
})();
