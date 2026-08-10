// The detail side panel: everything about one callable that does not fit on
// a card, plus the links out to a real editor.

window.CC = window.CC || {};

CC.panel = (function () {
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function linkList(ids, role) {
    const list = el('ul');
    list.dataset.role = role;
    ids.forEach(function (id) {
      const item = el('li');
      const link = el('a', null, id);
      link.addEventListener('click', function () { CC.view.select(id); });
      item.appendChild(link);
      list.appendChild(item);
    });
    if (!ids.length) list.appendChild(el('li', 'muted', 'none'));
    return list;
  }

  function show(id) {
    const state = CC.view.state;
    const node = state.data.nodeIndex[id];
    if (!node) return;
    const host = document.getElementById('panel');
    host.replaceChildren();

    host.appendChild(el('h2', null, node.id));
    if (node.signature) host.appendChild(el('div', 'sig', node.name + node.signature));
    if (node.file) {
      host.appendChild(el('div', 'path', node.file + ':' + node.lineStart));
    }
    if (node.summary) host.appendChild(el('p', 'summary', node.summary));
    if (node.decorators && node.decorators.length) {
      host.appendChild(el('h3', null, 'Decorators'));
      host.appendChild(el('div', 'decorators', node.decorators.join(', ')));
    }

    const callers = [];
    const callees = [];
    state.data.edges.forEach(function (edge) {
      if (edge.target === id) callers.push(edge.source);
      if (edge.source === id) callees.push(edge.target);
    });
    host.appendChild(el('h3', null, 'Called by'));
    host.appendChild(linkList(callers, 'callers'));
    host.appendChild(el('h3', null, 'Calls'));
    host.appendChild(linkList(callees, 'callees'));

    const actions = el('div', 'actions');
    if (node.file) {
      const location = node.file + ':' + node.lineStart;
      const editor = el('a', null, 'Open in editor');
      editor.dataset.role = 'editor';
      editor.href = 'vscode://file/' + location;
      actions.appendChild(editor);

      const copy = el('button', null, 'Copy path');
      copy.dataset.role = 'copy-path';
      copy.addEventListener('click', function () {
        if (navigator.clipboard) navigator.clipboard.writeText(location);
      });
      actions.appendChild(copy);
    }
    const trace = el('button', null, 'Trace from here');
    trace.dataset.role = 'trace';
    // Guarded so the panel works even when the player is unavailable.
    trace.addEventListener('click', function () {
      if (CC.player) CC.player.start(id);
    });
    actions.appendChild(trace);
    host.appendChild(actions);

    host.hidden = false;
  }

  function hide() { document.getElementById('panel').hidden = true; }

  return { show: show, hide: hide };
})();
