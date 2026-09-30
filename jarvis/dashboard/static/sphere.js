"use strict";

// La présence de l'assistant : une sphère de points lumineux (spirale de Fibonacci), projetée en
// perspective sur un canvas 2D. Aucune dépendance : fonctionne hors ligne.
// Interface : Orb.state = "idle" | "listening" | "thinking" | "speaking" ; Orb.levels(micro, voix).

const Orb = (() => {
  const canvas = document.getElementById("orb");
  const ctx = canvas.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const css = getComputedStyle(document.documentElement);
  const hex = (name) => {
    const v = css.getPropertyValue(name).trim().replace("#", "");
    return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
  };
  const colors = { idle: hex("--accent"), listening: hex("--listen"), thinking: hex("--think"), speaking: hex("--speak") };

  const N = 2200;
  const TAU = Math.PI * 2;
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  const px = new Float32Array(N), py = new Float32Array(N), pz = new Float32Array(N), seed = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const y = 1 - (2 * (i + 0.5)) / N;
    const r = Math.sqrt(1 - y * y);
    const a = i * GOLDEN;
    px[i] = Math.cos(a) * r; py[i] = y; pz[i] = Math.sin(a) * r;
    seed[i] = Math.random() * TAU;
  }

  const BINS = 6;                                   // points regroupés par profondeur : peu de changements de style
  const binX = Array.from({ length: BINS }, () => new Float32Array(N));
  const binY = Array.from({ length: BINS }, () => new Float32Array(N));
  const binN = new Int32Array(BINS);

  let state = "idle";
  let color = colors.idle.slice();
  let mic = 0, out = 0, level = 0, amp = 0.02, swirl = 0, speed = 0.12, yaw = 0, t = 0, last = 0;
  let W = 0, H = 0, dpr = 1;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = canvas.clientWidth || 300;
    H = canvas.clientHeight || 300;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }

  function draw(now) {
    requestAnimationFrame(draw);
    if (document.hidden) return;
    // 30 i/s au repos, 50 quand ça réagit : Whisper tourne sur le même PC.
    const frame = 1000 / (state === "idle" || reduced ? 30 : 50);
    if (now - last < frame) return;
    const step = Math.min((now - last) / 1000, 0.1);
    last = now;
    const slow = reduced ? 0.25 : 1;
    t += step * slow;

    level += ((state === "speaking" ? out : state === "listening" ? mic : 0) - level) * 0.3;
    const lv = Math.min(1, level * 1.6);
    const ampGoal = state === "speaking" ? 0.05 + lv * 0.42
      : state === "listening" ? 0.03 + lv * 0.3
      : state === "thinking" ? 0.06 : 0.018;
    amp += (ampGoal - amp) * 0.2;
    swirl += ((state === "thinking" ? 1.6 : 0) - swirl) * 0.04;
    speed += ((state === "thinking" ? 1.1 : state === "speaking" ? 0.3 : 0.12) - speed) * 0.05;
    yaw += speed * step * slow;
    const goal = colors[state] || colors.idle;
    color = color.map((v, i) => v + (goal[i] - v) * 0.06);

    const breath = 1 + (state === "idle" ? 0.022 * Math.sin(t * 1.1) : 0);
    const R = Math.min(W, H) * 0.3 * breath;
    const cx = W / 2, cy = H / 2;
    const focal = 3.2;
    const tilt = 0.32 + 0.05 * Math.sin(t * 0.25);
    const cosT = Math.cos(tilt), sinT = Math.sin(tilt);
    const pulse = 1 + (state === "speaking" ? lv * 0.14 : state === "listening" ? lv * 0.06 : 0);
    const shake = state === "listening" ? lv * 0.5 : 0;

    binN.fill(0);
    for (let i = 0; i < N; i++) {
      let x = px[i], y = py[i], z = pz[i];
      // déformation : ondes qui traversent la surface, plus fortes avec le niveau sonore
      const wave = Math.sin(x * 3.1 + t * 2.3) * Math.sin(y * 2.7 - t * 1.9) * Math.sin(z * 3.3 + t * 1.3 + seed[i] * 0.15);
      const jitter = shake * Math.sin(seed[i] + t * 14) * 0.25;
      const k = pulse * (1 + amp * wave + jitter);
      // tourbillon : la rotation dépend de la hauteur du point
      const a = yaw + swirl * y * 1.4;
      const ca = Math.cos(a), sa = Math.sin(a);
      const rx = (x * ca + z * sa) * k;
      const rz = (-x * sa + z * ca) * k;
      const ry = y * k;
      const y2 = ry * cosT - rz * sinT;
      const z2 = ry * sinT + rz * cosT;
      const s = focal / (focal - z2);               // perspective : le proche grossit
      const depth = (z2 + 1.4) / 2.8;               // 0 (fond) … 1 (devant)
      let b = Math.floor(depth * BINS);
      b = b < 0 ? 0 : b >= BINS ? BINS - 1 : b;
      const n = binN[b]++;
      binX[b][n] = cx + rx * R * s;
      binY[b][n] = cy + y2 * R * s;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // halo
    const rgb = (a) => `rgba(${Math.round(color[0])},${Math.round(color[1])},${Math.round(color[2])},${a})`;
    const halo = ctx.createRadialGradient(cx, cy, R * 0.3, cx, cy, R * 2.1);
    halo.addColorStop(0, rgb(0.1 + amp * 0.5));
    halo.addColorStop(0.5, rgb(0.035));
    halo.addColorStop(1, rgb(0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    ctx.globalCompositeOperation = "lighter";
    for (let b = 0; b < BINS; b++) {
      const d = (b + 0.5) / BINS;
      const size = (0.7 + d * 1.5) * Math.max(0.8, Math.min(W, H) / 560 + 0.4);
      ctx.fillStyle = rgb(0.1 + d * d * 0.8);
      const xs = binX[b], ys = binY[b];
      for (let i = 0, m = binN[b]; i < m; i++) ctx.fillRect(xs[i] - size / 2, ys[i] - size / 2, size, size);
    }
    ctx.globalCompositeOperation = "source-over";
  }

  addEventListener("resize", resize);
  new ResizeObserver(resize).observe(canvas);
  resize();
  requestAnimationFrame(draw);
  return {
    set state(s) { state = s; },
    levels(m, o) { mic = m; out = o; },
  };
})();
