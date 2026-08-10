// The toolbar: entry points, confidence filtering, search, focus radius,
// theme, the info panel, and keyboard shortcuts.

window.CC = window.CC || {};

CC.controls = (function () {
  const REASON_LABEL = {
    main_block: '__main__ block',
    console_script: 'console script',
    decorated: 'decorated',
    test: 'test',
    no_callers: 'nothing calls it',
  };

  // Which tiers are drawn. `ambiguous` starts off: several candidates and no
  // reason to trust any of them is not information, it is noise.
  const visibleTiers = new Set(['resolved', 'inferred']);

  function status(text) {
    document.getElementById('statusbar').textContent = text;
  }

  function defaultStatus() {
    const stats = CC.view.state.data.stats;
    const skipped = stats.skipped.length;
    status(
      stats.callableCount + ' callables, ' + stats.edgeCount + ' edges, ' +
      Math.round(stats.resolutionRate * 100) + '% resolved' +
      (skipped ? ', ' + skipped + ' file' + (skipped === 1 ? '' : 's') + ' skipped' : ''));
  }

  function setTierVisibility(tier, visible) {
    if (visible) visibleTiers.add(tier); else visibleTiers.delete(tier);
    CC.view.setTierFilter(visibleTiers);
  }

  function buildEntryDropdown() {
    const select = document.getElementById('entry-select');
    select.replaceChildren();
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Walk through...';
    select.appendChild(blank);

    const grouped = {};
    CC.view.state.data.entryPoints.forEach(function (entry) {
      const reason = entry.reasons[0] || 'no_callers';
      (grouped[reason] = grouped[reason] || []).push(entry);
    });
    Object.keys(grouped).forEach(function (reason) {
      const group = document.createElement('optgroup');
      group.label = REASON_LABEL[reason] || reason;
      grouped[reason].forEach(function (entry) {
        const option = document.createElement('option');
        option.value = entry.id;
        option.textContent = entry.id;
        group.appendChild(option);
      });
      select.appendChild(group);
    });

    select.addEventListener('change', function () {
      if (select.value) CC.player.start(select.value);
    });
  }

  function search(term) {
    const needle = term.trim().toLowerCase();
    if (!needle) return;
    const nodes = CC.view.state.data.nodes;
    const hit = nodes.find(function (node) {
      return node.id.toLowerCase() === needle;
    }) || nodes.find(function (node) {
      return node.id.toLowerCase().indexOf(needle) !== -1;
    });
    if (!hit) { status('No match for "' + term + '"'); return; }

    // A hit inside a collapsed container is useless until the container opens.
    const next = new Set(CC.view.state.collapsed);
    let cursor = CC.view.state.data.parentIndex[hit.id];
    while (cursor !== null && cursor !== undefined) {
      next.delete(cursor);
      cursor = CC.view.state.data.parentIndex[cursor];
    }
    CC.view.layout(next).then(function () {
      CC.view.select(hit.id);
      const box = CC.view.boxes()[hit.id];
      if (box) CC.canvas.panTo(box.x + box.w / 2, box.y + box.h / 2, { animate: true });
      defaultStatus();
    });
  }

  function showInfo() {
    const stats = CC.view.state.data.stats;
    const host = document.getElementById('info-panel');
    host.replaceChildren();

    const heading = document.createElement('h2');
    heading.textContent = 'What this graph knows';
    host.appendChild(heading);

    const table = document.createElement('table');
    const rows = [
      ['Calls found', stats.totalCalls],
      ['Resolved', (stats.byConfidence.resolved || 0) + ' (solid edges)'],
      ['Inferred', (stats.byConfidence.inferred || 0) + ' (dashed, a guess)'],
      ['Ambiguous', (stats.byConfidence.ambiguous || 0) + ' (hidden by default)'],
      ['Unresolved', (stats.byConfidence.unresolved || 0) + ' (not drawn)'],
      ['Resolution rate', Math.round(stats.resolutionRate * 100) + '%'],
    ];
    rows.forEach(function (row) {
      const tr = document.createElement('tr');
      const label = document.createElement('td');
      label.textContent = row[0];
      const value = document.createElement('td');
      value.textContent = String(row[1]);
      tr.appendChild(label);
      tr.appendChild(value);
      table.appendChild(tr);
    });
    host.appendChild(table);

    const note = document.createElement('p');
    note.className = 'skipped';
    note.textContent =
      'Static analysis cannot see dispatch through getattr, registries, ' +
      'monkey-patching, or metaclass-generated methods. Those calls are ' +
      'counted as unresolved rather than guessed at.';
    host.appendChild(note);

    if (stats.skipped.length) {
      const skippedHeading = document.createElement('h3');
      skippedHeading.textContent = 'Files skipped';
      host.appendChild(skippedHeading);
      stats.skipped.forEach(function (item) {
        const line = document.createElement('div');
        line.className = 'skipped';
        line.textContent = item.path + ' - ' + item.reason;
        host.appendChild(line);
      });
    }
    host.hidden = false;
  }

  function bindKeys() {
    document.addEventListener('keydown', function (event) {
      const target = event.target;
      // Never steal a keystroke from a field the user is typing in.
      if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT' ||
                     target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      if (event.key === ' ') {
        event.preventDefault();
        if (CC.player.state.playing) CC.player.pause(); else CC.player.play();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        CC.player.next();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        CC.player.prev();
      } else if (event.key === 'Escape') {
        CC.panel.hide();
        CC.focus.clear();
        document.getElementById('info-panel').hidden = true;
      } else if (event.key === '/') {
        event.preventDefault();
        document.getElementById('search').focus();
      }
    });
  }

  function init() {
    buildEntryDropdown();
    defaultStatus();

    document.getElementById('search').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') search(event.target.value);
    });
    document.getElementById('show-inferred').addEventListener('change', function (event) {
      setTierVisibility('inferred', event.target.checked);
    });
    document.getElementById('show-ambiguous').addEventListener('change', function (event) {
      setTierVisibility('ambiguous', event.target.checked);
    });
    document.getElementById('focus-hops').addEventListener('input', function (event) {
      document.getElementById('focus-hops-value').textContent = event.target.value;
      CC.focus.setHops(Number(event.target.value));
    });
    document.getElementById('theme').addEventListener('click', function () {
      const root = document.documentElement;
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    });
    document.getElementById('info').addEventListener('click', function () {
      const host = document.getElementById('info-panel');
      if (host.hidden) showInfo(); else host.hidden = true;
    });

    document.getElementById('play').addEventListener('click', function () {
      if (CC.player.state.playing) CC.player.pause(); else CC.player.play();
    });
    document.getElementById('step-fwd').addEventListener('click', CC.player.next);
    document.getElementById('step-back').addEventListener('click', CC.player.prev);
    document.getElementById('stop').addEventListener('click', CC.player.stop);

    bindKeys();
    setTierVisibility('resolved', true);
  }

  return { init: init, setTierVisibility: setTierVisibility, showInfo: showInfo,
           status: status, defaultStatus: defaultStatus };
})();
