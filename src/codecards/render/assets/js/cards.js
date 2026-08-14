// Card DOM. One element per visible node, built once per layout and reused.
//
// Source is painted from precomputed token runs: the analyser sends the raw
// line plus a list of [column, length, class], and this walks the gaps. All
// text goes in through textContent, never innerHTML, so a string literal
// containing markup stays a string literal.

window.CC = window.CC || {};

CC.cards = (function () {
  const CARD_W = 260;
  const CARD_H = 64;

  // Magnitude has to be geometry, not ink. Scaling only the glyph and the
  // name inside an identical box means a function with six callers and one
  // with a single caller draw the same size, and the chart stops encoding
  // the thing it claims to. Area carries it, so the ramp reads at any zoom
  // and from across a room.
  //
  // The box is a pure function of fan-in, so it is stable for a given graph
  // and zoom still never triggers a relayout.
  // Height hugs the two lines a card actually holds; magnitude is spent on
  // width, where it buys more of the signature and path before they ellipse,
  // and on type scale. Growing the box in both axes just claims area: the
  // brightest object became the emptiest surface, which is the opposite of
  // what a magnitude ramp says.
  const BOX = [
    { w: 344, h: 72 },
    { w: 306, h: 68 },
    { w: 268, h: 64 },
    { w: 236, h: 58 },
    { w: 210, h: 54 },
  ];

  const KIND_ICON = {
    package: 'i-package', module: 'i-module', class: 'i-class',
    function: 'i-function', method: 'i-function',
  };

  // Fan-in as a magnitude, brightest first. Five steps, because a chart that
  // ranks more finely than the eye resolves is just noise: the point is that
  // the load-bearing objects read before any label does.
  const MAGNITUDE_BREAKS = [12, 6, 3, 1];

  function magnitudeFor(fanIn) {
    const count = fanIn || 0;
    for (let i = 0; i < MAGNITUDE_BREAKS.length; i++) {
      if (count >= MAGNITUDE_BREAKS[i]) return i;
    }
    return 4;
  }

  function boxFor(fanIn) { return BOX[magnitudeFor(fanIn)]; }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Icons are drawn once in the document's symbol sheet and referenced here.
  // createElementNS is required: an <svg> built with createElement lands in
  // the HTML namespace and renders as nothing at all.
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const XLINK = 'http://www.w3.org/1999/xlink';

  function icon(symbolId, cls) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', '#' + symbolId);
    // Safari below 14 reads only the xlink form; harmless everywhere else.
    use.setAttributeNS(XLINK, 'xlink:href', '#' + symbolId);
    svg.appendChild(use);
    return svg;
  }

  function renderSource(node, callLines) {
    const body = el('pre', 'card-body');
    const lines = (node.source || '').split('\n');
    const tokens = node.tokens || [];
    const first = node.lineStart || 1;

    lines.forEach(function (text, index) {
      const fileLine = first + index;
      const row = el('div', 'src-line');
      row.dataset.line = String(fileLine);

      const gutter = el('div', 'src-gutter');
      const site = callLines && callLines.get ? callLines.get(fileLine) : null;
      if (site) {
        row.classList.add('call-site');
        if (site.cond) gutter.appendChild(markGlyph('cond', 'i-branch', 'inside a conditional'));
        if (site.loop) gutter.appendChild(markGlyph('loop', 'i-loop', 'inside a loop'));
      }
      gutter.appendChild(el('span', 'num', String(fileLine)));
      row.appendChild(gutter);

      row.appendChild(paintLine(text, tokens[index] || []));
      body.appendChild(row);
    });

    if (node.truncated) {
      body.appendChild(el('div', 'card-truncated',
        'source truncated, see ' + (node.file || '') + ':' + first));
    }
    return body;
  }

  function markGlyph(kind, symbolId, title) {
    const mark = el('span', 'mark');
    mark.dataset.kind = kind;
    mark.title = title;
    mark.appendChild(icon(symbolId));
    return mark;
  }

  // Walk the runs in order, emitting unstyled text for the gaps between them.
  // Runs never overlap, which is what makes a single pass correct.
  function paintLine(text, runs) {
    const holder = el('div', 'src-text');
    let cursor = 0;
    for (let i = 0; i < runs.length; i++) {
      const col = runs[i][0];
      const length = runs[i][1];
      const cls = runs[i][2];
      if (col > cursor) holder.appendChild(document.createTextNode(text.slice(cursor, col)));
      holder.appendChild(el('span', cls, text.slice(col, col + length)));
      cursor = col + length;
    }
    if (cursor < text.length) holder.appendChild(document.createTextNode(text.slice(cursor)));
    return holder;
  }

  function build(node, options) {
    const opts = options || {};
    const card = el('div', 'card');
    card.dataset.id = node.id;
    card.dataset.kind = node.kind;
    card.dataset.mag = String(magnitudeFor(opts.fanIn));
    if (opts.isContainer) card.classList.add('container');
    if (opts.isOrphan) card.classList.add('orphan');
    if (opts.isEntry) card.classList.add('entry');

    const head = el('div', 'card-head');
    head.appendChild(icon(KIND_ICON[node.kind] || 'i-function', 'card-icon'));
    head.appendChild(el('span', 'card-name', node.name));
    // Annotations on the object itself. A mark with no word next to it is a
    // decoration until someone looks it up, and nobody looks it up.
    if (opts.isEntry) {
      const flag = el('span', 'card-flag entry');
      flag.title = 'A way into the program';
      flag.appendChild(icon('i-entry'));
      flag.appendChild(el('span', null, 'entry'));
      head.appendChild(flag);
    }
    if (opts.isOrphan) {
      const flag = el('span', 'card-flag unused', 'unused');
      flag.title = 'Nothing in this codebase calls it';
      head.appendChild(flag);
    }

    // Label over figure, so a count reads as a measured quantity rather than
    // as decoration. The label is what makes the number mean anything.
    const badges = el('div', 'card-badges');
    if (opts.fanIn) {
      badges.appendChild(readout('badge-in', 'in', opts.fanIn, opts.fanIn + ' callers'));
    }
    if (opts.fanOut) {
      badges.appendChild(readout('badge-out', 'out', opts.fanOut, opts.fanOut + ' calls out'));
    }
    if (opts.internal) {
      badges.appendChild(readout('badge-internal', 'int', opts.internal,
                                 opts.internal + ' internal calls'));
    }
    head.appendChild(badges);
    card.appendChild(head);

    // Details are asked for, not pushed. Selecting a card says "show me
    // around this"; it should not also throw a panel over a third of the
    // canvas you were reading.
    const info = el('button', 'card-info');
    info.appendChild(icon('i-info'));
    info.setAttribute('aria-label', 'Show details for ' + node.name);
    info.title = 'Callers, calls and actions for this object';
    info.addEventListener('click', function (event) {
      event.stopPropagation();
      CC.view.select(node.id, { panel: true });
    });
    card.appendChild(info);

    const pin = el('button', 'card-pin');
    pin.appendChild(icon('i-pin'));
    pin.setAttribute('aria-label', 'Pin this card open');
    pin.title = 'Keep this card showing its source at any zoom';
    pin.addEventListener('click', function (event) {
      event.stopPropagation();
      CC.zoom.toggle(node.id);
    });
    card.appendChild(pin);

    if (node.signature) card.appendChild(el('div', 'card-sig', node.name + node.signature));
    if (node.summary) card.appendChild(el('div', 'card-summary', node.summary));
    if (node.file) {
      card.appendChild(el('div', 'card-path', node.file + ':' + node.lineStart));
    }
    if (node.source) card.appendChild(renderSource(node, opts.callLines));
    return card;
  }

  function readout(kindClass, label, value, title) {
    const holder = el('span', 'badge ' + kindClass);
    holder.title = title;
    holder.appendChild(el('i', null, label));
    holder.appendChild(el('b', null, String(value)));
    return holder;
  }

  return {
    CARD_W: CARD_W,
    CARD_H: CARD_H,
    build: build,
    renderSource: renderSource,
    icon: icon,
    magnitudeFor: magnitudeFor,
    boxFor: boxFor,
  };
})();
