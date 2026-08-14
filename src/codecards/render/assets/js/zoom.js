// Semantic zoom. A card's tier is a class; CSS decides what that shows.
//
// This never triggers a relayout. Layout was computed once at card
// dimensions and the boxes do not change: block tier draws less inside the
// same box, source tier grows the card in place over its neighbours.

window.CC = window.CC || {};

CC.zoom = (function () {
  // Card tier costs four lines of type: name, signature, docstring, path. The
  // path is 10px, so below roughly 0.6 every one of them is under 7px and the
  // card reads as a smudge. Block tier answers the same zoom with one name at
  // display size, which is legible far further out, so the boundary sits where
  // detail stops being readable rather than where it stops fitting.
  const THRESHOLDS = { block: 0.6, source: 0.8 };
  const pinned = new Set();

  function tierFor(scale) {
    if (scale < THRESHOLDS.block) return 'block';
    if (scale < THRESHOLDS.source) return 'card';
    return 'source';
  }

  function hasSource() {
    return !!(window.CODECARDS_DATA && window.CODECARDS_DATA.meta &&
              window.CODECARDS_DATA.meta.hasSource);
  }

  function apply() {
    const base = tierFor(CC.canvas.getView().scale);
    const ceiling = hasSource() ? 'source' : 'card';
    document.querySelectorAll('#cards .card').forEach(function (card) {
      const id = card.dataset.id;
      let tier = pinned.has(id) ? 'source' : base;
      if (tier === 'source' && ceiling === 'card') tier = 'card';
      card.classList.toggle('tier-block', tier === 'block');
      card.classList.toggle('tier-card', tier === 'card');
      card.classList.toggle('tier-source', tier === 'source');
      card.classList.toggle('pinned', pinned.has(id));
    });
  }

  // Which cards are open decides where edges leave from, so the SVG has to
  // be redrawn when that changes. paint() calls apply() itself, and apply()
  // never calls paint(), so there is no loop.
  function refresh() {
    apply();
    if (CC.view && CC.view.paint) CC.view.paint();
  }

  function pin(id) { pinned.add(id); refresh(); }
  function unpin(id) { pinned.delete(id); refresh(); }
  function isPinned(id) { return pinned.has(id); }
  function toggle(id) { return pinned.has(id) ? (unpin(id), false) : (pin(id), true); }

  return {
    THRESHOLDS: THRESHOLDS,
    tierFor: tierFor,
    apply: apply,
    pin: pin,
    unpin: unpin,
    toggle: toggle,
    isPinned: isPinned,
  };
})();
