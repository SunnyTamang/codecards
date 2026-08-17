// The toolbar: entry points, confidence filtering, search, focus radius,
// theme, the info panel, and keyboard shortcuts.

window.CC = window.CC || {};

CC.controls = (function () {
  //: Most a single entry-point group lists before it stops being a menu.
  const MAX_ENTRIES_PER_GROUP = 50;

  //: Enough stale files to recognise what changed, not so many that the
  //: instruction under them scrolls out of sight.
  const STALE_SHOWN = 10;

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

  //: Everything the canvas says without words. None of it was written down
  //: anywhere in the interface before.
  const LEGEND = [
    ['edge resolved', 'solid line', 'the target is certain'],
    ['edge inferred', 'dashed line', 'one definition has that name, so a guess'],
    ['edge ambiguous', 'faint dashes', 'several candidates, none trusted, hidden by default'],
    ['weight', '7', 'how many calls this one line stands for'],
    ['chip entry', 'entry', 'a way into the program'],
    ['chip unused', 'unused', 'nothing calls it, and the language does not either'],
    ['badge', '\u2193 \u2191 \u21ba', 'calls in, calls out, calls that stay inside'],
    ['glyph', '\u2442 \u21bb', 'this call sits in a conditional, or in a loop'],
    ['pin', '\u25c9', 'hold this card open at full source while you zoom out'],
    ['colour', 'card edge', 'grouped by the package a card belongs to'],
  ];

  function buildLegend() {
    const table = document.createElement('table');
    table.className = 'legend';
    LEGEND.forEach(function (row) {
      const tr = document.createElement('tr');
      const sample = document.createElement('td');
      sample.className = 'sample ' + row[0].split(' ')[0];
      sample.textContent = row[1];
      const what = document.createElement('td');
      what.textContent = row[2];
      tr.appendChild(sample);
      tr.appendChild(what);
      table.appendChild(tr);
    });
    return table;
  }

  function status(text) {
    document.getElementById('statusbar').textContent = text;
  }

  function defaultStatus() {
    const stats = CC.view.state.data.stats;
    const skipped = stats.skipped.length;
    const stale = (stats.stale || []).length;
    const withheld = CC.view.state.edgesWithheld || 0;
    status(
      stats.callableCount + ' callables, ' + stats.edgeCount + ' edges, ' +
      Math.round(stats.resolutionRate * 100) + '% resolved' +
      // Whatever the traffic filter is holding back is said here rather than
      // left to be inferred. An empty stretch of canvas has to mean "no
      // relationship", and it stops meaning that the moment something is
      // withheld without saying so.
      (withheld ? ' - ' + withheld + ' quiet edge' + (withheld === 1 ? '' : 's') +
        ' hidden, showing ' + CC.view.state.edgeFloor + '+ calls' : '') +
      (skipped ? ', ' + skipped + ' file' + (skipped === 1 ? '' : 's') + ' skipped' : '') +
      // Sits in the status bar rather than the panel alone: it qualifies
      // everything on the canvas, so it should be readable without opening
      // anything.
      (stale ? ' - ' + stale + ' file' + (stale === 1 ? '' : 's') +
        ' newer than the index' : ''));
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
      const all = grouped[reason];
      // "nothing calls it" is a structural fallback, so on a large library it
      // matches thousands of functions: scikit-learn produced 6,014 of them
      // and a menu nobody could use. The count stays honest in the label.
      const shown = all.slice(0, MAX_ENTRIES_PER_GROUP);
      group.label = (REASON_LABEL[reason] || reason) +
        (all.length > shown.length
          ? ' (' + shown.length + ' of ' + all.length + ')'
          : '');
      shown.forEach(function (entry) {
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

    // First, above the numbers: this qualifies every one of them. A reader
    // who stops after the resolution rate should already have seen it.
    if ((stats.stale || []).length) {
      const staleHeading = document.createElement('h3');
      staleHeading.textContent = 'This graph may be out of date';
      host.appendChild(staleHeading);

      const why = document.createElement('p');
      why.className = 'stale';
      why.textContent =
        'It was resolved from an index built before ' + stats.stale.length +
        ' of these files were last edited, so anything defined or called in ' +
        'them may be drawn as it used to be:';
      host.appendChild(why);

      stats.stale.slice(0, STALE_SHOWN).forEach(function (name) {
        const line = document.createElement('div');
        line.className = 'skipped';
        line.textContent = name;
        host.appendChild(line);
      });
      if (stats.stale.length > STALE_SHOWN) {
        const more = document.createElement('div');
        more.className = 'skipped';
        more.textContent = '... and ' + (stats.stale.length - STALE_SHOWN) + ' more';
        host.appendChild(more);
      }
      if (stats.reindexCommand) {
        const how = document.createElement('p');
        how.className = 'stale';
        how.textContent =
          'Rebuild it with, from the environment the project runs in - an ' +
          'indexer run outside it resolves far less and still reports success:';
        host.appendChild(how);
        const cmd = document.createElement('div');
        cmd.className = 'skipped';
        cmd.textContent = stats.reindexCommand;
        host.appendChild(cmd);
      }
    }

    const table = document.createElement('table');
    const rows = [
      ['Calls found', stats.totalCalls],
      ['Resolved', (stats.byConfidence.resolved || 0) + ' (solid edges)'],
      ['Inferred', (stats.byConfidence.inferred || 0) + ' (dashed, a guess)'],
      ['Ambiguous', (stats.byConfidence.ambiguous || 0) + ' (hidden by default)'],
      ['Unresolved', (stats.byConfidence.unresolved || 0) + ' (not drawn)'],
      ['Resolution rate', Math.round(stats.resolutionRate * 100) + '%'],
    ];
    if (CC.view.state.edgesWithheld) {
      rows.push(['Quiet edges hidden',
                 CC.view.state.edgesWithheld + ' carrying fewer than ' +
                 CC.view.state.edgeFloor + ' calls']);
    }
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

    const legend = document.createElement('h3');
    legend.textContent = 'How to read it';
    host.appendChild(legend);
    host.appendChild(buildLegend());

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
        CC.view.deselect();
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
    document.getElementById('show-dunders').addEventListener('change', function (event) {
      CC.view.setShowDunders(event.target.checked);
    });
    document.getElementById('edge-traffic').addEventListener('change', function (event) {
      const raw = event.target.value;
      CC.view.setWeightFloor(raw === 'auto' ? null : Number(raw)).then(defaultStatus);
    });
    document.getElementById('focus-hops').addEventListener('input', function (event) {
      document.getElementById('focus-hops-value').textContent = event.target.value;
      CC.focus.setHops(Number(event.target.value));
    });
    document.getElementById('theme').addEventListener('click', function () {
      const root = document.documentElement;
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    });
    document.getElementById('reset-layout').addEventListener('click', function () {
      CC.view.resetLayout();
      status('Layout reset');
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
