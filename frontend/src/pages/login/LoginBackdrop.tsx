/**
 * 로그인 바탕(문제점 89번) — IoT 지역 방송 그림을 캔버스로 그린다.
 *
 * 밤하늘·산 세 겹·마을 집(창에 불)·왼쪽 언덕의 방송탑. 탑의 나팔 스피커에서 소리 고리가 퍼지고,
 * 마을 곳곳의 단말(스피커 기둥)로 빛 줄이 이어져 점이 흘러간다 — 「서버 → 단말 방송」 그대로.
 * 멈춘 그림(하늘·산·집)은 크기가 바뀔 때만 한 번 그리고, 매 프레임은 별·고리·흐르는 점·빛 입자만.
 *
 * 회사 사진을 쓰고 싶으면 frontend/public/brand/login-bg.jpg 를 넣는다 — 있으면 그림 대신 사진을
 * 깔고 별·빛 입자만 얹는다(태양광 관제와 같은 방식). 움직임 줄이기를 켠 사람에겐 멈춘 한 장면만.
 */

import { useEffect, useRef, useState } from 'react';

const PHOTO = '/brand/login-bg.jpg';

/** 늘 같은 마을이 나오게 — 새로고침마다 집 배치가 바뀌면 어수선하다. */
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Pt = { x: number; y: number };

/** 산 능선 — 사인 몇 개를 겹친 높이(0~1 의 x 에 대한 y 픽셀). */
function ridge(W: number, H: number, base: number, amp: number, seed: number) {
  const r = rng(seed);
  const waves = Array.from({ length: 4 }, (_, i) => ({ f: (i + 1) * (1.2 + r()), p: r() * 6.28, a: amp / (i + 1.3) }));
  return (x: number) => H * base - waves.reduce((s, w) => s + Math.sin((x / W) * w.f * 6.28 + w.p) * w.a * H, 0);
}

interface Scene {
  W: number;
  H: number;
  /** 방송탑 나팔 위치 */
  horn: Pt;
  /** 탑 꼭대기 경고등 */
  beacon: Pt;
  /** 마을 단말(스피커 기둥) 꼭대기 */
  nodes: Pt[];
}

/** 멈춘 그림을 off-screen 캔버스에 그리고 움직일 것들의 위치를 돌려준다. */
function paintStatic(g: CanvasRenderingContext2D, W: number, H: number, withScene: boolean): Scene {
  const u = Math.min(W, H * 1.6) / 1000; // 화면 크기에 맞춘 단위
  const r = rng(20261014);

  // 하늘: 위는 짙은 남색, 지평선 쪽은 보랏빛 → 노을
  const sky = g.createLinearGradient(0, 0, 0, H);
  sky.addColorStop(0, '#050b1c');
  sky.addColorStop(0.45, '#0d1a3c');
  sky.addColorStop(0.62, '#2a2f5e');
  sky.addColorStop(0.72, '#6b4a6e');
  sky.addColorStop(1, '#0a1226');
  g.fillStyle = sky;
  g.fillRect(0, 0, W, H);
  if (!withScene) return { W, H, horn: { x: 0, y: 0 }, beacon: { x: 0, y: 0 }, nodes: [] };

  // 노을빛(오른쪽 지평선)
  const glow = g.createRadialGradient(W * 0.74, H * 0.66, 0, W * 0.74, H * 0.66, W * 0.45);
  glow.addColorStop(0, 'rgba(255,170,96,.55)');
  glow.addColorStop(0.35, 'rgba(240,120,110,.18)');
  glow.addColorStop(1, 'rgba(240,120,110,0)');
  g.fillStyle = glow;
  g.fillRect(0, 0, W, H);

  const layer = (f: (x: number) => number, fill: string | CanvasGradient) => {
    g.beginPath();
    g.moveTo(0, H);
    for (let x = 0; x <= W; x += 6) g.lineTo(x, f(x));
    g.lineTo(W, H);
    g.closePath();
    g.fillStyle = fill;
    g.fill();
  };
  const far = ridge(W, H, 0.62, 0.05, 11);
  const mid = ridge(W, H, 0.7, 0.045, 23);
  const near = ridge(W, H, 0.82, 0.03, 37);
  layer(far, '#232b57');
  layer(mid, '#151d40');

  // 방송탑 — 왼쪽 중간 산 위, 철골 탑과 양쪽 나팔 스피커
  const tx = W * 0.17;
  const ty = mid(tx) + 2;
  const th = H * 0.24;
  const top = ty - th;
  g.strokeStyle = '#3c4f86';
  g.lineWidth = Math.max(1, 1.6 * u);
  g.beginPath();
  g.moveTo(tx - 22 * u, ty);
  g.lineTo(tx - 3 * u, top);
  g.moveTo(tx + 22 * u, ty);
  g.lineTo(tx + 3 * u, top);
  for (let i = 0; i < 9; i++) {
    const a = ty - (th * i) / 9;
    const b = ty - (th * (i + 1)) / 9;
    const wa = 22 * u * (1 - i / 9) + 3 * u * (i / 9);
    const wb = 22 * u * (1 - (i + 1) / 9) + 3 * u * ((i + 1) / 9);
    g.moveTo(tx - wa, a);
    g.lineTo(tx + wb, b);
    g.moveTo(tx + wa, a);
    g.lineTo(tx - wb, b);
  }
  g.stroke();
  const hornY = top + th * 0.14;
  g.fillStyle = '#5a6fa8';
  for (const dir of [-1, 1]) {
    g.beginPath();
    g.moveTo(tx + dir * 3 * u, hornY - 4 * u);
    g.lineTo(tx + dir * 20 * u, hornY - 10 * u);
    g.lineTo(tx + dir * 20 * u, hornY + 10 * u);
    g.lineTo(tx + dir * 3 * u, hornY + 4 * u);
    g.closePath();
    g.fill();
  }

  // 가까운 언덕 + 마을
  const nearFill = g.createLinearGradient(0, H * 0.72, 0, H);
  nearFill.addColorStop(0, '#0f1834');
  nearFill.addColorStop(1, '#060b1a');
  layer(near, nearFill);

  // 집 — 기와 지붕(처마가 살짝 올라간 곡선)과 불 켜진 창
  const houses: { x: number; w: number }[] = [];
  for (let x = W * 0.04; x < W * 0.98; ) {
    const w = (26 + r() * 22) * u;
    if (r() < 0.78) houses.push({ x, w });
    x += w + (6 + r() * 30) * u;
  }
  for (const hs of houses) {
    const base = near(hs.x + hs.w / 2) + 3 * u;
    const h = hs.w * (0.55 + r() * 0.2);
    g.fillStyle = '#0a1124';
    g.fillRect(hs.x, base - h, hs.w, h);
    const eave = hs.w * 0.18;
    g.beginPath();
    g.moveTo(hs.x - eave, base - h + 2 * u);
    g.quadraticCurveTo(hs.x + hs.w * 0.12, base - h - 1 * u, hs.x + hs.w * 0.2, base - h - hs.w * 0.32);
    g.lineTo(hs.x + hs.w * 0.8, base - h - hs.w * 0.32);
    g.quadraticCurveTo(hs.x + hs.w * 0.88, base - h - 1 * u, hs.x + hs.w + eave, base - h + 2 * u);
    g.closePath();
    g.fillStyle = '#1a2447';
    g.fill();
    const lit = r();
    if (lit < 0.85) {
      const ww = hs.w * 0.2;
      const wh = h * 0.34;
      const n = hs.w > 38 * u ? 2 : 1;
      g.save();
      g.shadowColor = 'rgba(255,196,110,.9)';
      g.shadowBlur = 10 * u;
      g.fillStyle = lit < 0.35 ? '#ffd58a' : '#f5b766';
      for (let k = 0; k < n; k++) {
        const wx = hs.x + (hs.w / (n + 1)) * (k + 1) - ww / 2;
        g.fillRect(wx, base - h * 0.72, ww, wh);
      }
      g.restore();
    }
  }

  // 마을 단말 — 스피커 기둥 다섯 개(가운데 상자 뒤는 피한다)
  const nodes: Pt[] = [];
  for (const fx of [0.31, 0.45, 0.62, 0.78, 0.91]) {
    const x = W * fx;
    const base = near(x) + 2 * u;
    const ph = H * 0.07 + r() * H * 0.025;
    g.strokeStyle = '#2b3a68';
    g.lineWidth = Math.max(1, 2 * u);
    g.beginPath();
    g.moveTo(x, base);
    g.lineTo(x, base - ph);
    g.stroke();
    g.fillStyle = '#4a5f98';
    g.beginPath();
    g.moveTo(x, base - ph + 3 * u);
    g.lineTo(x + 9 * u, base - ph - 2 * u);
    g.lineTo(x + 9 * u, base - ph + 9 * u);
    g.lineTo(x, base - ph + 6 * u);
    g.closePath();
    g.fill();
    nodes.push({ x, y: base - ph });
  }

  return { W, H, horn: { x: tx, y: hornY }, beacon: { x: tx, y: top - 3 * u }, nodes };
}

export function LoginBackdrop() {
  const cv = useRef<HTMLCanvasElement | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);
  const [photo, setPhoto] = useState(false);

  // 회사 사진이 있으면 그것을 깐다. 없으면 SPA 가 index.html 을 돌려주므로 onerror 로 끝난다.
  useEffect(() => {
    const img = new Image();
    img.onload = () => setPhoto(img.naturalWidth > 0);
    img.src = PHOTO;
  }, []);

  useEffect(() => {
    const c = cv.current;
    const x = c?.getContext('2d');
    if (!c || !x) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const still = document.createElement('canvas');
    type Star = { x: number; y: number; r: number; p: number; s: number };
    type Mote = { x: number; y: number; r: number; vx: number; vy: number; life: number; max: number; hue: string };
    let scene: Scene;
    let stars: Star[] = [];
    let motes: Mote[] = [];
    let raf = 0;
    let W = 0;
    let H = 0;

    const mote = (): Mote => ({
      x: Math.random() * W,
      y: H * 0.6 + Math.random() * H * 0.45,
      r: Math.random() * 1.5 + 0.5,
      vy: -(Math.random() * 0.22 + 0.06),
      vx: (Math.random() - 0.5) * 0.14,
      life: 0,
      max: 400 + Math.random() * 500,
      hue: Math.random() < 0.55 ? '255,200,120' : '120,200,255',
    });

    const size = () => {
      W = innerWidth;
      H = innerHeight;
      for (const k of [c, still]) {
        k.width = W * dpr;
        k.height = H * dpr;
      }
      c.style.width = `${W}px`;
      c.style.height = `${H}px`;
      const sg = still.getContext('2d')!;
      sg.setTransform(dpr, 0, 0, dpr, 0, 0);
      scene = paintStatic(sg, W, H, !photo);
      x.setTransform(dpr, 0, 0, dpr, 0, 0);
      stars = Array.from({ length: Math.round((W * H) / 8000) }, () => ({
        x: Math.random() * W,
        y: Math.random() * H * 0.5,
        r: Math.random() * 1.1 + 0.3,
        p: Math.random() * Math.PI * 2,
        s: Math.random() * 1.5 + 0.5,
      }));
      motes = Array.from({ length: Math.round(W / 50) }, mote);
    };

    const frame = (t: number) => {
      // 창이 막 열리는 순간이나 숨은 창에서는 크기가 0 일 수 있다 — 크기가 생기면 그때 다시 그린다.
      if (innerWidth !== W || innerHeight !== H) size();
      if (!W || !H) {
        if (!reduce) raf = requestAnimationFrame(frame);
        return;
      }
      const sec = t / 1000;
      x.clearRect(0, 0, W, H);
      if (!photo) x.drawImage(still, 0, 0, W, H);

      for (const s of stars) {
        const a = 0.25 + 0.75 * Math.abs(Math.sin(sec * s.s + s.p));
        x.fillStyle = `rgba(255,255,255,${a * 0.8})`;
        x.beginPath();
        x.arc(s.x, s.y, s.r, 0, 7);
        x.fill();
      }

      if (!photo) {
        const u = Math.min(W, H * 1.6) / 1000;
        const { horn, beacon, nodes } = scene;

        // 소리 고리 — 나팔 양쪽으로 2.4초마다 하나씩, 커지며 옅어진다
        x.lineWidth = Math.max(1, 1.6 * u);
        for (let k = 0; k < 3; k++) {
          const ph = (sec / 2.4 + k / 3) % 1;
          const rad = 24 * u + ph * W * 0.22;
          x.strokeStyle = `rgba(120,205,255,${0.55 * (1 - ph)})`;
          for (const [a0, a1] of [
            [-0.45, 0.45],
            [Math.PI - 0.45, Math.PI + 0.45],
          ]) {
            x.beginPath();
            x.arc(horn.x, horn.y, rad, a0, a1);
            x.stroke();
          }
        }

        // 탑 → 단말 빛 줄과 그 위를 흐르는 점
        nodes.forEach((n, i) => {
          const cx = (beacon.x + n.x) / 2;
          const cy = Math.min(beacon.y, n.y) - H * (0.08 + i * 0.015);
          x.strokeStyle = 'rgba(110,190,255,.22)';
          x.lineWidth = Math.max(1, 1.1 * u);
          x.beginPath();
          x.moveTo(beacon.x, beacon.y);
          x.quadraticCurveTo(cx, cy, n.x, n.y);
          x.stroke();
          const p = (sec / (3.2 + i * 0.4) + i * 0.21) % 1;
          const q = 1 - p;
          const px = q * q * beacon.x + 2 * q * p * cx + p * p * n.x;
          const py = q * q * beacon.y + 2 * q * p * cy + p * p * n.y;
          const gr = x.createRadialGradient(px, py, 0, px, py, 7 * u);
          gr.addColorStop(0, 'rgba(170,225,255,.95)');
          gr.addColorStop(1, 'rgba(170,225,255,0)');
          x.fillStyle = gr;
          x.beginPath();
          x.arc(px, py, 7 * u, 0, 7);
          x.fill();
          // 단말 꼭대기 표시등 — 점이 막 닿으면 밝아진다
          const lit = p > 0.92 ? 1 : 0.55;
          x.fillStyle = `rgba(120,215,255,${lit})`;
          x.beginPath();
          x.arc(n.x, n.y - 3 * u, 2.6 * u, 0, 7);
          x.fill();
        });

        // 탑 꼭대기 빨간 경고등(ON AIR)
        const on = 0.35 + 0.65 * Math.max(0, Math.sin(sec * 2.2));
        const bg = x.createRadialGradient(beacon.x, beacon.y, 0, beacon.x, beacon.y, 12 * u);
        bg.addColorStop(0, `rgba(255,70,90,${on})`);
        bg.addColorStop(1, 'rgba(255,70,90,0)');
        x.fillStyle = bg;
        x.beginPath();
        x.arc(beacon.x, beacon.y, 12 * u, 0, 7);
        x.fill();
      }

      for (let i = 0; i < motes.length; i++) {
        const m = motes[i];
        m.x += m.vx;
        m.y += m.vy;
        m.life++;
        const k = Math.sin((Math.PI * m.life) / m.max);
        const gr = x.createRadialGradient(m.x, m.y, 0, m.x, m.y, m.r * 6);
        gr.addColorStop(0, `rgba(${m.hue},${0.6 * k})`);
        gr.addColorStop(1, `rgba(${m.hue},0)`);
        x.fillStyle = gr;
        x.beginPath();
        x.arc(m.x, m.y, m.r * 6, 0, 7);
        x.fill();
        if (m.life >= m.max) motes[i] = mote();
      }
      if (!reduce) raf = requestAnimationFrame(frame);
    };

    const move = (e: PointerEvent) => {
      if (wrap.current)
        wrap.current.style.transform = `translate(${(e.clientX / W - 0.5) * -12}px, ${(e.clientY / H - 0.5) * -8}px) scale(1.02)`;
    };
    const onResize = () => {
      size();
      if (reduce) frame(performance.now());
    };
    size();
    raf = requestAnimationFrame(frame);
    addEventListener('resize', onResize);
    if (!reduce) addEventListener('pointermove', move);
    return () => {
      cancelAnimationFrame(raf);
      removeEventListener('resize', onResize);
      removeEventListener('pointermove', move);
    };
  }, [photo]);

  return (
    <>
      <div className="lg-bgwrap" ref={wrap}>
        {photo && <div className="lg-bg" style={{ backgroundImage: `url(${PHOTO})` }} />}
        <canvas className="lg-fx" ref={cv} aria-hidden="true" />
      </div>
      <div className="lg-shade" />
    </>
  );
}
