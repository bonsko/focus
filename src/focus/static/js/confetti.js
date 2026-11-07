/* small confetti burst, ~1s, no dependency. */
(() => {
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  window.burst = function burst(origin) {
    if (reduced()) return;
    const ox = origin && Number.isFinite(origin.x) ? origin.x : innerWidth / 2;
    const oy = origin && Number.isFinite(origin.y) ? origin.y : innerHeight * 0.42;
    const css = getComputedStyle(document.documentElement);
    const colors = ['--love', '--gold', '--rose', '--pine', '--foam', '--iris']
      .map((v) => css.getPropertyValue(v).trim())
      .filter(Boolean);
    const dpr = window.devicePixelRatio || 1;
    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:100';
    canvas.width = innerWidth * dpr;
    canvas.height = innerHeight * dpr;
    document.body.append(canvas);

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const parts = Array.from({ length: 46 }, () => ({
      x: ox + (Math.random() - 0.5) * 46,
      y: oy,
      vx: (Math.random() - 0.5) * 9,
      vy: -3 - Math.random() * 7,
      s: 3 + Math.random() * 4,
      r: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.28,
      c: colors[(Math.random() * colors.length) | 0],
    }));

    const start = performance.now();
    function frame(now) {
      const t = now - start;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      ctx.globalAlpha = Math.max(0, 1 - t / 900);
      for (const p of parts) {
        p.vy += 0.22;
        p.vx *= 0.99;
        p.x += p.vx;
        p.y += p.vy;
        p.r += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.r);
        ctx.fillStyle = p.c;
        ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.6);
        ctx.restore();
      }
      if (t < 1000) requestAnimationFrame(frame);
      else canvas.remove();
    }
    requestAnimationFrame(frame);
  };
})();
