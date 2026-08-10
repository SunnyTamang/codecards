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

  const KIND_ICON = {
    package: '▦', module: '▤', class: '◎',
    function: 'ƒ', method: 'ƒ',
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Colour by the containing package, so siblings share a hue and
  // orientation arrives before any label is read.
  //
  // Hashing the TOP-LEVEL package instead is useless in the common case:
  // pointing at one package makes every id share a first segment, and the
  // whole canvas comes out one colour. Hue comes from a stable hash rather
  // than iteration order, so colours do not move when the graph changes.
  function packageHue(key) {
    const text = String(key || '');
    let hash = 0;
    for (let i = 0; i < text.length; i++) {
      hash = (hash * 31 + text.charCodeAt(i)) | 0;
    }
    return Math.abs(hash) % 360;
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
        if (site.cond) gutter.appendChild(markGlyph('cond', '⑂', 'inside a conditional'));
        if (site.loop) gutter.appendChild(markGlyph('loop', '↻', 'inside a loop'));
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

  function markGlyph(kind, glyph, title) {
    const mark = el('span', 'mark', glyph);
    mark.dataset.kind = kind;
    mark.title = title;
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
    card.style.setProperty('--pkg',
      'hsl(' + packageHue(node.parent || node.id) + ' 62% 58%)');
    if (opts.isContainer) card.classList.add('container');
    if (opts.isOrphan) card.classList.add('orphan');

    const head = el('div', 'card-head');
    head.appendChild(el('span', 'card-icon', KIND_ICON[node.kind] || 'ƒ'));
    head.appendChild(el('span', 'card-name', node.name));

    const badges = el('div', 'card-badges');
    if (opts.fanIn) badges.appendChild(el('span', 'badge-in', '↓' + opts.fanIn));
    if (opts.fanOut) badges.appendChild(el('span', 'badge-out', '↑' + opts.fanOut));
    if (opts.internal) {
      const badge = el('span', 'badge-internal', '↺' + opts.internal);
      badge.title = opts.internal + ' internal calls';
      badges.appendChild(badge);
    }
    head.appendChild(badges);
    card.appendChild(head);

    const pin = el('button', 'card-pin', '◉');
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

  return {
    CARD_W: CARD_W,
    CARD_H: CARD_H,
    build: build,
    renderSource: renderSource,
    packageHue: packageHue,
  };
})();
