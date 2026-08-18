// SVG edges. Drawn in world coordinates inside #world so pan and zoom move
// edges and cards together.
//
// Every edge is three marks: a connector dot on the object it leaves, a line,
// and a chevron head that stops short of the object it points at. All three
// are drawn geometry rather than SVG markers, because a marker is sized from
// stroke-width and therefore grows with the canvas transform, while the lines
// carry vector-effect: non-scaling-stroke and do not. That mismatch is what
// made a hairline arrive under a heavy triangle at high zoom. Sizing every
// mark against the current scale keeps the whole assembly one weight.

window.CC = window.CC || {};

CC.edges = (function () {
  const NS = 'http://www.w3.org/2000/svg';

  // All in screen pixels; divided by the scale at draw time.
  const GAP = 8;        // how far the head stops short of the target
  const HEAD = 7.5;     // chevron arm length
  const SPREAD = 0.42;  // chevron half-angle, radians
  const DOT = 4.5;      // connector dot radius
  const BOW = 0.055;    // how far the line bows off dead straight

  // The sides that face each other. With cards free to be dragged anywhere, a
  // fixed bottom-to-top anchor is wrong the moment anything sits level with or
  // above the thing it calls.
  function port(from, to) {
    const fc = { x: from.x + from.w / 2, y: from.y + from.h / 2 };
    const tc = { x: to.x + to.w / 2, y: to.y + to.h / 2 };
    const dx = tc.x - fc.x;
    const dy = tc.y - fc.y;
    if (Math.abs(dx) * from.h > Math.abs(dy) * from.w) {
      return { x: fc.x + (dx > 0 ? from.w / 2 : -from.w / 2), y: fc.y };
    }
    return { x: fc.x, y: fc.y + (dy > 0 ? from.h / 2 : -from.h / 2) };
  }

  function el(tag, cls, layer) {
    const node = document.createElementNS(NS, tag);
    if (cls) node.setAttribute('class', cls);
    layer.appendChild(node);
    return node;
  }

  function render(svg, viewEdges, boxes, options) {
    const opts = options || {};
    Array.from(svg.querySelectorAll('g.edge-layer')).forEach(function (g) { g.remove(); });

    const scale = (CC.canvas && CC.canvas.getView().scale) || 1;
    const gap = GAP / scale;
    const head = HEAD / scale;
    const dot = DOT / scale;

    const layer = document.createElementNS(NS, 'g');
    layer.setAttribute('class', 'edge-layer');

    viewEdges.forEach(function (edge) {
      const source = boxes[edge.source];
      const target = boxes[edge.target];
      if (!source || !target) return;  // an endpoint is culled or hidden

      // A source-tier card anchors the line to the line of code making the
      // call. Everything else leaves from the side facing the target.
      const at = opts.anchorFor ? opts.anchorFor(edge) : null;
      const from = at ? { x: at.x, y: at.y } : port(source, target);
      const to = port(target, source);

      const vx = to.x - from.x;
      const vy = to.y - from.y;
      const length = Math.hypot(vx, vy) || 1;
      const ux = vx / length;
      const uy = vy / length;
      const tip = { x: to.x - ux * gap, y: to.y - uy * gap };

      // Bowed just enough that two lines between the same pair of objects do
      // not lie on top of each other.
      const cx = (from.x + tip.x) / 2 - uy * length * BOW;
      const cy = (from.y + tip.y) / 2 + ux * length * BOW;

      // The tier class and the edge id live on this path alone. Anything else
      // carrying them would inflate every count of drawn edges.
      const line = el('path',
        edge.confidence + (edge.circular ? ' circular' : ''), layer);
      line.setAttribute('d',
        'M' + from.x + ',' + from.y + ' Q' + cx + ',' + cy + ' ' + tip.x + ',' + tip.y);
      line.dataset.edge = edge.source + '->' + edge.target;

      // A line drawn at its weakest tier says "something here is a guess"
      // and cannot say how much. The count can, and hovering is where a
      // reader asks. Only when the aggregate stands for more than one call.
      if (edge.tiers && edge.weight > 1) {
        const parts = Object.keys(edge.tiers).map(function (tier) {
          return edge.tiers[tier] + ' ' + tier;
        });
        const title = document.createElementNS(NS, 'title');
        title.textContent = edge.weight + ' calls: ' + parts.join(', ');
        line.appendChild(title);
      }

      // Where the call leaves from. Pushed out along the line by its own
      // radius so it sits against the card rather than half behind it: the
      // edge layer paints under the cards, so a dot centred on the boundary
      // shows only its outer half.
      const origin = el('circle', 'edge-dot edge-dot-' + edge.confidence, layer);
      origin.setAttribute('cx', String(from.x + ux * dot));
      origin.setAttribute('cy', String(from.y + uy * dot));
      origin.setAttribute('r', String(dot));

      // Drawn from the curve's real incoming tangent, so the head never points
      // somewhere the line did not arrive from.
      const tx = tip.x - cx;
      const ty = tip.y - cy;
      const tl = Math.hypot(tx, ty) || 1;
      const ax = tx / tl;
      const ay = ty / tl;
      const cos = Math.cos(SPREAD);
      const sin = Math.sin(SPREAD);
      const left = {
        x: tip.x - head * (ax * cos - ay * sin),
        y: tip.y - head * (ay * cos + ax * sin),
      };
      const right = {
        x: tip.x - head * (ax * cos + ay * sin),
        y: tip.y - head * (ay * cos - ax * sin),
      };
      const chevron = el('path', 'edge-head edge-head-' + edge.confidence, layer);
      chevron.setAttribute('d',
        'M' + left.x + ',' + left.y + ' L' + tip.x + ',' + tip.y +
        ' L' + right.x + ',' + right.y);

      if (edge.weight > 1) {
        const label = el('text', 'weight', layer);
        label.setAttribute('x', String((from.x + tip.x) / 2));
        label.setAttribute('y', String((from.y + tip.y) / 2));
        label.textContent = String(edge.weight);
      }
    });

    svg.appendChild(layer);
  }

  return { render: render, port: port };
})();
