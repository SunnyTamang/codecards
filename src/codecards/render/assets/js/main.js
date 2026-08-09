// Boot. a separate change grows this into full wiring.
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
  CC.view.init(window.CODECARDS_DATA);
}

document.addEventListener('DOMContentLoaded', __cc_boot);
