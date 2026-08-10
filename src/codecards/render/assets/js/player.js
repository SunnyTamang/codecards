// Transport and animation for the walkthrough.
//
// A step is only an explanation if you can see the code. Each one expands
// whatever container hides the active card, pins it to source tier, and
// highlights the line making the call.

window.CC = window.CC || {};

CC.player = (function () {
  const state = { steps: [], index: -1, playing: false, entryId: null };
  let timer = null;
  const autoPinned = new Set();

  function speed() {
    return Number(document.getElementById('speed').value) || 900;
  }

  function shortName(id) {
    const parts = String(id).split('.');
    return parts.slice(-2).join('.');
  }

  function ancestorsOf(id) {
    const chain = [];
    let cursor = CC.view.state.data.parentIndex[id];
    while (cursor !== null && cursor !== undefined) {
      chain.push(cursor);
      cursor = CC.view.state.data.parentIndex[cursor];
    }
    return chain;
  }

  // A card hidden inside a collapsed container cannot be highlighted, so open
  // whatever hides it before drawing the step.
  function reveal(ids) {
    const next = new Set(CC.view.state.collapsed);
    let changed = false;
    ids.forEach(function (id) {
      ancestorsOf(id).forEach(function (ancestor) {
        if (next.delete(ancestor)) changed = true;
      });
    });
    return changed ? CC.view.layout(next) : Promise.resolve();
  }

  function clearMarks() {
    document.querySelectorAll('.card.active').forEach(function (card) {
      card.classList.remove('active');
    });
    document.querySelectorAll('.src-line.step-active').forEach(function (line) {
      line.classList.remove('step-active');
    });
    document.querySelectorAll('#edges path.active').forEach(function (path) {
      path.classList.remove('active');
    });
  }

  function draw() {
    const step = state.steps[state.index];
    if (!step) return Promise.resolve();
    return reveal([step.callerId, step.calleeId]).then(function () {
      clearMarks();
      autoPinned.forEach(function (id) { CC.zoom.unpin(id); });
      autoPinned.clear();

      const caller = document.querySelector('.card[data-id="' + step.callerId + '"]');
      const callee = document.querySelector('.card[data-id="' + step.calleeId + '"]');
      if (caller) caller.classList.add('active');
      if (callee) callee.classList.add('active');

      CC.zoom.pin(step.callerId);
      autoPinned.add(step.callerId);

      const line = document.querySelector(
        '.card[data-id="' + step.callerId + '"] .src-line[data-line="' + step.line + '"]');
      if (line) line.classList.add('step-active');

      const path = document.querySelector(
        '#edges path[data-edge="' + step.callerId + '->' + step.calleeId + '"]');
      if (path) path.classList.add('active');

      const box = CC.view.boxes()[step.callerId];
      if (box) CC.canvas.panTo(box.x + box.w / 2, box.y + box.h / 2, { animate: true });

      renderBreadcrumb(step);
      renderCaption(step);
    });
  }

  function renderBreadcrumb(step) {
    const host = document.getElementById('breadcrumb');
    host.replaceChildren();
    const chain = step.stack.concat([step.calleeId]);
    chain.forEach(function (id, position) {
      if (position) {
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = '->';
        host.appendChild(sep);
      }
      const item = document.createElement('span');
      item.className = position === chain.length - 1 ? 'current' : '';
      item.textContent = shortName(id);
      host.appendChild(item);
    });
    host.hidden = false;
  }

  function renderCaption(step) {
    const host = document.getElementById('caption');
    host.replaceChildren();
    const node = CC.view.state.data.nodeIndex[step.callerId] || {};
    const where = (node.file || '?') + ':' + step.line;
    host.appendChild(document.createTextNode(
      (state.index + 1) + '/' + state.steps.length + '  ' +
      where + ' -> ' + shortName(step.calleeId)));
    [
      step.cond && 'in a conditional',
      step.loop && 'in a loop',
      step.recursive && 'recursive, not re-entered',
      step.confidence !== 'resolved' && step.confidence,
    ].forEach(function (label) {
      if (!label) return;
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = label;
      host.appendChild(tag);
    });
    host.hidden = false;
  }

  function goTo(index) {
    if (!state.steps.length) return Promise.resolve();
    state.index = Math.max(0, Math.min(state.steps.length - 1, index));
    return draw();
  }

  function next() {
    if (state.index >= state.steps.length - 1) { pause(); return Promise.resolve(); }
    return goTo(state.index + 1);
  }

  function prev() { return goTo(state.index - 1); }

  function play() {
    if (!state.steps.length) return;
    state.playing = true;
    document.getElementById('play').textContent = 'Pause';
    timer = setInterval(function () {
      if (state.index >= state.steps.length - 1) { pause(); return; }
      next();
    }, speed());
  }

  function pause() {
    state.playing = false;
    document.getElementById('play').textContent = 'Play';
    if (timer) { clearInterval(timer); timer = null; }
  }

  function start(entryId) {
    stop();
    state.entryId = entryId;
    state.steps = CC.trace.build(
      CC.view.state.data.edges, entryId, CC.view.state.data.meta.maxDepth);
    document.getElementById('transport').hidden = false;
    if (!state.steps.length) {
      const host = document.getElementById('caption');
      host.replaceChildren(document.createTextNode(
        shortName(entryId) + ' makes no calls we could resolve.'));
      host.hidden = false;
      return Promise.resolve();
    }
    return goTo(0);
  }

  function stop() {
    pause();
    clearMarks();
    autoPinned.forEach(function (id) { CC.zoom.unpin(id); });
    autoPinned.clear();
    state.steps = [];
    state.index = -1;
    state.entryId = null;
    document.getElementById('transport').hidden = true;
    document.getElementById('breadcrumb').hidden = true;
    document.getElementById('caption').hidden = true;
  }

  return {
    state: state,
    start: start, stop: stop,
    play: play, pause: pause,
    next: next, prev: prev, goTo: goTo,
  };
})();
