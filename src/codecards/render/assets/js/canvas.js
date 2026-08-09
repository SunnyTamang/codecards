// Pan, zoom, and the shared coordinate system.
//
// Cards and the SVG edge layer both live inside #world, so one transform
// moves them together and an edge endpoint can never drift from its card.
// Everything else in the renderer works in world coordinates and lets this
// module worry about the screen.

window.CC = window.CC || {};

CC.canvas = (function () {
  const MIN_SCALE = 0.05;
  const MAX_SCALE = 3;

  let viewport = null;
  let world = null;
  let onViewChange = null;
  let view = { x: 0, y: 0, scale: 1 };

  function clampScale(scale) {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
  }

  function apply(notify) {
    world.style.transform =
      `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
    if (notify && onViewChange) onViewChange(getView());
  }

  function getView() {
    return { x: view.x, y: view.y, scale: view.scale };
  }

  function setView(next) {
    view = {
      x: next.x,
      y: next.y,
      scale: clampScale(next.scale),
    };
    apply(true);
  }

  // Screen point -> world point. The inverse of the CSS transform.
  function screenToWorld(clientX, clientY) {
    const box = viewport.getBoundingClientRect();
    return {
      x: (clientX - box.left - view.x) / view.scale,
      y: (clientY - box.top - view.y) / view.scale,
    };
  }

  // Zoom about a screen point, holding the world point under it fixed.
  // Solve for the translation that keeps screenToWorld(anchor) unchanged.
  function zoomBy(factor, anchorClientX, anchorClientY) {
    const box = viewport.getBoundingClientRect();
    const scale = clampScale(view.scale * factor);
    if (scale === view.scale) return;
    const ax = anchorClientX - box.left;
    const ay = anchorClientY - box.top;
    view = {
      x: ax - (ax - view.x) * (scale / view.scale),
      y: ay - (ay - view.y) * (scale / view.scale),
      scale: scale,
    };
    apply(true);
  }

  function fit(bounds, padding) {
    const pad = padding || 0;
    const w = viewport.clientWidth - pad * 2;
    const h = viewport.clientHeight - pad * 2;
    // An empty graph has zero extent. Guard the division rather than
    // producing NaN coordinates that poison every later calculation.
    const scale = clampScale(Math.min(
      bounds.w > 0 ? w / bounds.w : 1,
      bounds.h > 0 ? h / bounds.h : 1
    ));
    view = {
      x: pad + (w - bounds.w * scale) / 2 - bounds.x * scale,
      y: pad + (h - bounds.h * scale) / 2 - bounds.y * scale,
      scale: scale,
    };
    apply(true);
  }

  function panTo(worldX, worldY, options) {
    const animate = options && options.animate;
    const target = {
      x: viewport.clientWidth / 2 - worldX * view.scale,
      y: viewport.clientHeight / 2 - worldY * view.scale,
      scale: view.scale,
    };
    if (!animate) {
      setView(target);
      return Promise.resolve();
    }
    const from = getView();
    const start = performance.now();
    const duration = 320;
    return new Promise(function (resolve) {
      function frame(now) {
        const t = Math.min(1, (now - start) / duration);
        const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        setView({
          x: from.x + (target.x - from.x) * e,
          y: from.y + (target.y - from.y) * e,
          scale: view.scale,
        });
        if (t < 1) requestAnimationFrame(frame); else resolve();
      }
      requestAnimationFrame(frame);
    });
  }

  // The world rectangle currently on screen, grown by `margin` screen pixels.
  // Culling asks this: anything outside it need not exist in the DOM.
  function visibleWorldRect(margin) {
    const m = margin || 0;
    const topLeft = screenToWorld(
      viewport.getBoundingClientRect().left - m,
      viewport.getBoundingClientRect().top - m
    );
    return {
      x: topLeft.x,
      y: topLeft.y,
      w: (viewport.clientWidth + m * 2) / view.scale,
      h: (viewport.clientHeight + m * 2) / view.scale,
    };
  }

  function bindPointer() {
    let panning = false;
    let lastX = 0;
    let lastY = 0;

    viewport.addEventListener('pointerdown', function (event) {
      // Only drag from empty canvas or a card's chrome. Selecting code inside
      // a card must not pan the view, or reading becomes impossible.
      if (event.target.closest('.card-body')) return;
      if (event.button !== 0) return;
      panning = true;
      lastX = event.clientX;
      lastY = event.clientY;
      viewport.classList.add('panning');
      viewport.setPointerCapture(event.pointerId);
    });

    viewport.addEventListener('pointermove', function (event) {
      if (!panning) return;
      view.x += event.clientX - lastX;
      view.y += event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      apply(true);
    });

    function stop(event) {
      if (!panning) return;
      panning = false;
      viewport.classList.remove('panning');
      if (viewport.hasPointerCapture(event.pointerId)) {
        viewport.releasePointerCapture(event.pointerId);
      }
    }
    viewport.addEventListener('pointerup', stop);
    viewport.addEventListener('pointercancel', stop);

    viewport.addEventListener('wheel', function (event) {
      event.preventDefault();
      // Trackpad pinch arrives as a wheel event with ctrlKey set; treat both
      // as zoom, scaled so a pinch feels proportional rather than stepped.
      const intensity = event.ctrlKey ? 0.01 : 0.0018;
      zoomBy(Math.exp(-event.deltaY * intensity), event.clientX, event.clientY);
    }, { passive: false });
  }

  function init(options) {
    viewport = options.viewport;
    world = options.world;
    onViewChange = options.onViewChange || null;
    if (!viewport.dataset.ccBound) {
      bindPointer();
      viewport.dataset.ccBound = '1';
    }
    apply(false);
  }

  return {
    MIN_SCALE: MIN_SCALE,
    MAX_SCALE: MAX_SCALE,
    init: init,
    getView: getView,
    setView: setView,
    zoomBy: zoomBy,
    fit: fit,
    panTo: panTo,
    screenToWorld: screenToWorld,
    visibleWorldRect: visibleWorldRect,
  };
})();
