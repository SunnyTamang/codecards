// Boot: canvas first, then the graph, then the toolbar.
function __cc_boot() {
  let repaint = null;
  CC.canvas.init({
    viewport: document.getElementById('viewport'),
    world: document.getElementById('world'),
    onViewChange: function (v) {
      document.getElementById('zoom-readout').textContent =
        Math.round(v.scale * 100) + '%';
      // Culling and tier changes are cheap but not free; coalesce to one
      // pass per frame rather than one per wheel tick.
      if (repaint) cancelAnimationFrame(repaint);
      repaint = requestAnimationFrame(function () {
        repaint = null;
        CC.view.paint();
      });
    },
  });
  // Clicking the background clears the selection. Without this the panel and
  // the focus dimming have no obvious way out: the only escape was a keyboard
  // shortcut nobody is told about.
  document.getElementById('viewport').addEventListener('click', function (event) {
    if (event.target.closest('.card')) return;
    if (CC.canvas.didDrag()) return;   // finishing a pan is not a click away
    CC.view.deselect();
  });

  CC.view.init(window.CODECARDS_DATA).then(function () {
    CC.controls.init();
  });
}

document.addEventListener('DOMContentLoaded', __cc_boot);
