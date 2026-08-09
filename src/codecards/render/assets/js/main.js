// Boot. a separate change grows this into full wiring.
function __cc_boot() {
  CC.canvas.init({
    viewport: document.getElementById('viewport'),
    world: document.getElementById('world'),
    onViewChange: function (v) {
      document.getElementById('zoom-readout').textContent =
        Math.round(v.scale * 100) + '%';
    },
  });
}

document.addEventListener('DOMContentLoaded', __cc_boot);
