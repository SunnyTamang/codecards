// SVG edges. One <path> per visible edge, drawn in world coordinates inside
// #world so pan and zoom move edges and cards together.

window.CC = window.CC || {};

CC.edges = (function () {
  const NS = 'http://www.w3.org/2000/svg';

  function ensureMarkers(svg) {
    if (svg.querySelector('defs')) return;
    const defs = document.createElementNS(NS, 'defs');
    ['resolved', 'inferred', 'ambiguous', 'active'].forEach(function (tier) {
      const marker = document.createElementNS(NS, 'marker');
      marker.setAttribute('id', 'arrow-' + tier);
      marker.setAttribute('viewBox', '0 0 8 8');
      marker.setAttribute('refX', '7');
      marker.setAttribute('refY', '4');
      marker.setAttribute('markerWidth', '6');
      marker.setAttribute('markerHeight', '6');
      marker.setAttribute('orient', 'auto-start-reverse');
      const head = document.createElementNS(NS, 'path');
      head.setAttribute('d', 'M0,0 L8,4 L0,8 z');
      head.setAttribute('class', 'head ' + tier);
      marker.appendChild(head);
      defs.appendChild(marker);
    });
    svg.appendChild(defs);
  }

  // Leave from the calling line when the source card is showing its code,
  // otherwise from the box's bottom edge. Anchoring to the line is what turns
  // "these two functions are connected" into "this line calls it".
  //
  // `at` arrives already in world coordinates and is trusted as given. It
  // cannot be validated against `box`: a source-tier card deliberately grows
  // past the card-sized box layout reserved for it, in both axes, so any
  // check against those bounds rejects every line but the first and silently
  // disables the feature for every real card.
  function anchor(box, at) {
    if (at) {
      return { x: at.x, y: at.y, side: 'right' };
    }
    return { x: box.x + box.w / 2, y: box.y + box.h, side: 'bottom' };
  }

  function path(from, to) {
    if (from.side === 'right') {
      const mid = from.x + Math.max(30, (to.x - from.x) / 2);
      return 'M' + from.x + ',' + from.y +
             ' C' + mid + ',' + from.y + ' ' + mid + ',' + to.y +
             ' ' + to.x + ',' + to.y;
    }
    const mid = from.y + Math.max(24, (to.y - from.y) / 2);
    return 'M' + from.x + ',' + from.y +
           ' C' + from.x + ',' + mid + ' ' + to.x + ',' + mid +
           ' ' + to.x + ',' + to.y;
  }

  function render(svg, viewEdges, boxes, options) {
    const opts = options || {};
    ensureMarkers(svg);
    Array.from(svg.querySelectorAll('g.edge-layer')).forEach(function (g) { g.remove(); });

    const layer = document.createElementNS(NS, 'g');
    layer.setAttribute('class', 'edge-layer');

    viewEdges.forEach(function (edge) {
      const source = boxes[edge.source];
      const target = boxes[edge.target];
      if (!source || !target) return;  // an endpoint is culled or hidden

      const at = opts.anchorFor ? opts.anchorFor(edge) : null;
      const from = anchor(source, at);
      const to = { x: target.x + target.w / 2, y: target.y };

      const shape = document.createElementNS(NS, 'path');
      shape.setAttribute('d', path(from, to));
      shape.setAttribute('class', edge.confidence);
      shape.setAttribute('marker-end', 'url(#arrow-' + edge.confidence + ')');
      shape.dataset.edge = edge.source + '->' + edge.target;
      layer.appendChild(shape);

      if (edge.weight > 1) {
        const label = document.createElementNS(NS, 'text');
        label.setAttribute('class', 'weight');
        label.setAttribute('x', String((from.x + to.x) / 2));
        label.setAttribute('y', String((from.y + to.y) / 2));
        label.textContent = String(edge.weight);
        layer.appendChild(label);
      }
    });

    svg.appendChild(layer);
  }

  return { render: render };
})();
