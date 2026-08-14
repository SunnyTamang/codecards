// Moving things on the canvas.
//
// A card carries its own position; a plate's position is derived from what it
// holds, so dragging a plate moves its members and lets the re-fit put the
// plate back around them. That way containment is never asserted, it is always
// recomputed, and "inside cli" cannot quietly stop being true.
//
// Nothing here starts until the pointer has travelled past a threshold. A card
// click expands a module and a plate click selects it, and both of those must
// survive a hand that moves four pixels while pressing.

window.CC = window.CC || {};

CC.drag = (function () {
  const THRESHOLD = 4;
  let moved = false;

  function begin(handle, id, event) {
    // Cards live inside #viewport, whose pointerdown pans the canvas. Without
    // this the background would pan underneath the thing being dragged.
    event.stopPropagation();

    const startX = event.clientX;
    const startY = event.clientY;
    const ids = CC.view.movableUnder(id);
    const container = CC.view.isDrawnContainer(id);
    let live = false;
    moved = false;

    function move(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (!live) {
        if (Math.hypot(dx, dy) < THRESHOLD) return;
        live = true;
        moved = true;
        handle.setPointerCapture(ev.pointerId);
        handle.classList.add('dragging');
        document.body.classList.add('is-dragging');
      }
      // The pointer moves in screen pixels; the boxes are in world units.
      const scale = CC.canvas.getView().scale || 1;
      const last = move.last || { x: 0, y: 0 };
      CC.view.moveBy(ids, (dx - last.x) / scale, (dy - last.y) / scale);
      move.last = { x: dx, y: dy };

      CC.view.refitContainers();
      CC.view.reposition();
      mark(container ? id : CC.view.state.data.parentIndex[id]);
      CC.view.paint();
    }

    function mark(plateId) {
      document.querySelectorAll('#cards .card.shadowed')
        .forEach(function (c) { c.classList.remove('shadowed'); });
      if (!plateId) return;
      CC.view.collidingWith(plateId).forEach(function (other) {
        const card = document.querySelector('#cards .card[data-id="' + CSS.escape(other) + '"]');
        if (card) card.classList.add('shadowed');
      });
    }

    function end(ev) {
      if (live) handle.releasePointerCapture(ev.pointerId);
      handle.classList.remove('dragging');
      document.body.classList.remove('is-dragging');
      document.querySelectorAll('#cards .card.shadowed')
        .forEach(function (c) { c.classList.remove('shadowed'); });
      move.last = null;
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
    }

    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  function attach(card, id) {
    card.addEventListener('pointerdown', function (event) {
      // Only the primary button, and never from a control that has its own job.
      if (event.button !== 0) return;
      if (event.target.closest('button, a, input, select')) return;
      // A card sits on top of its plate, so a press inside a child belongs to
      // the child. Let it through rather than dragging the whole module.
      if (event.target.closest('.card') !== card) return;
      begin(card, id, event);
    });
  }

  // Whether the gesture that just finished actually moved something, so a
  // click handler can tell a drag from a click.
  function didMove() { return moved; }

  return { attach: attach, didMove: didMove };
})();
