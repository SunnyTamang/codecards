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
  let field = null;
  //: Whether the gesture that just finished was a drag rather than a click.
  //: A drag ends with a click event too, and the background click handler
  //: must not treat "the user finished panning" as "the user clicked away".
  let dragged = false;
  let onViewChange = null;
  let view = { x: 0, y: 0, scale: 1 };

  function clampScale(scale) {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
  }

  // The coordinate ground is re-ruled rather than transformed. Scaling the
  // grid with #world would thicken every hairline at 3x and dissolve it at
  // 0.05; re-ruling keeps a hairline one pixel wide at every zoom, which is
  // the whole point of drawing on a chart.
  //
  // The spacing steps by powers of two so that zooming subdivides the field
  // instead of sliding it: the lines you were reading stay where they were.
  const GRID_BASE = 120;
  const MAJOR_EVERY = 5;
  const TICK_POOL = 28;

  function ruleField() {
    if (!field) return;
    // Minor ruling subdivides; every fifth line is a major one carrying a
    // coordinate value in the margin. A single uniform weight with nothing
    // readable off it is wallpaper, not a measurement surface.
    let step = GRID_BASE * view.scale;
    let world = GRID_BASE;
    while (step < 56) { step *= 2; world *= 2; }
    while (step > 224) { step /= 2; world /= 2; }
    const major = step * MAJOR_EVERY;
    field.style.setProperty('--minor', step + 'px');
    field.style.setProperty('--major', major + 'px');
    field.style.backgroundPosition =
      [(view.x % step) + 'px ' + (view.y % step) + 'px',
       (view.x % step) + 'px ' + (view.y % step) + 'px',
       (view.x % major) + 'px ' + (view.y % major) + 'px',
       (view.x % major) + 'px ' + (view.y % major) + 'px'].join(', ');
    rule(major, world * MAJOR_EVERY);
  }

  // Coordinate values along the top and left margins. The spans are made once
  // and rewritten in place: rebuilding them on every pan frame would allocate
  // through the whole gesture.
  let ticksTop = null;
  let ticksLeft = null;

  function tickPool(host) {
    if (host.childElementCount) return host.children;
    for (let i = 0; i < TICK_POOL; i++) host.appendChild(document.createElement('span'));
    return host.children;
  }

  function rule(majorPx, majorWorld) {
    if (!ticksTop || !ticksLeft) return;
    const across = tickPool(ticksTop);
    const down = tickPool(ticksLeft);
    const w = viewport.clientWidth;
    const h = viewport.clientHeight;

    for (let i = 0; i < TICK_POOL; i++) {
      const x = (view.x % majorPx) + i * majorPx;
      const span = across[i];
      if (x < 18 || x > w) { span.style.display = 'none'; continue; }
      span.style.display = '';
      span.style.left = x + 'px';
      span.textContent = Math.round((x - view.x) / view.scale / majorWorld) * majorWorld;
    }
    for (let i = 0; i < TICK_POOL; i++) {
      const y = (view.y % majorPx) + i * majorPx;
      const span = down[i];
      if (y < 2 || y > h - 12) { span.style.display = 'none'; continue; }
      span.style.display = '';
      span.style.top = y + 'px';
      span.textContent = Math.round((y - view.y) / view.scale / majorWorld) * majorWorld;
    }
  }

  function apply(notify) {
    world.style.transform =
      `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
    ruleField();
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

  // Fitting is only worth doing down to the point where the result can still
  // be read, and the floor has to sit just BELOW the block/card boundary
  // rather than on it. Landing exactly on 0.6 is the worst of both tiers: it
  // renders card tier, whose smallest line is 10px, at 60%. A hair under it
  // renders block tier instead, one name at display size, which is legible
  // here and still shows more of the graph. Past this floor the camera lands
  // centred and overflowing: less of the graph, all of it readable.
  const FIT_FLOOR = 0.55;

  function fit(bounds, padding, options) {
    const pad = padding || 0;
    const w = viewport.clientWidth - pad * 2;
    const h = viewport.clientHeight - pad * 2;
    // An empty graph has zero extent. Guard the division rather than
    // producing NaN coordinates that poison every later calculation.
    const natural = Math.min(
      bounds.w > 0 ? w / bounds.w : 1,
      bounds.h > 0 ? h / bounds.h : 1
    );
    const scale = clampScale(Math.max(FIT_FLOOR, natural));

    // Centring the extent is only correct while the extent still fits. Once
    // the floor clamps, the content is wider than the viewport by definition
    // and centring the whole span pushes both ends off screen, clipping the
    // objects at either edge and cutting the plate label in half. Anchor on
    // the brightest object instead, so the thing worth reading first is the
    // thing that lands on screen.
    const anchor = options && options.anchor;
    if (natural < FIT_FLOOR && anchor) {
      view = {
        x: viewport.clientWidth / 2 - anchor.x * scale,
        y: viewport.clientHeight / 2 - anchor.y * scale,
        scale: scale,
      };
    } else {
      view = {
        x: pad + (w - bounds.w * scale) / 2 - bounds.x * scale,
        y: pad + (h - bounds.h * scale) / 2 - bounds.y * scale,
        scale: scale,
      };
    }
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

  // How far the pointer must travel before a press becomes a drag.
  const DRAG_THRESHOLD = 4;

  function bindPointer() {
    let armed = false;     // pressed, but not yet moved enough to be a drag
    let panning = false;   // actually dragging, pointer captured
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastY = 0;

    viewport.addEventListener('pointerdown', function (event) {
      // Selecting code inside a card must not pan the view, or reading
      // becomes impossible.
      if (event.target.closest('.card-body')) return;
      if (event.target.closest('button, a, input, select, textarea')) return;
      if (event.button !== 0) return;
      armed = true;
      dragged = false;
      startX = lastX = event.clientX;
      startY = lastY = event.clientY;
    });

    viewport.addEventListener('pointermove', function (event) {
      if (!armed) return;
      if (!panning) {
        // Capture is deferred until the press is unambiguously a drag.
        // Capturing on pointerdown retargets the following pointerup and
        // click to the viewport, so no click ever reached a card: expanding,
        // selecting and pinning were all dead on arrival, while scripted
        // element.click() kept working and hid it.
        const dx = event.clientX - startX;
        const dy = event.clientY - startY;
        if (dx * dx + dy * dy < DRAG_THRESHOLD * DRAG_THRESHOLD) return;
        panning = true;
        dragged = true;
        viewport.classList.add('panning');
        viewport.setPointerCapture(event.pointerId);
      }
      view.x += event.clientX - lastX;
      view.y += event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      apply(true);
    });

    function stop(event) {
      armed = false;
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
    field = options.field || document.getElementById('field');
    ticksTop = document.getElementById('ticks-top');
    ticksLeft = document.getElementById('ticks-left');
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
    didDrag: function () { return dragged; },
  };
})();
