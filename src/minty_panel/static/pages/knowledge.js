import { api, encode, h, report, toast } from "../lib.js";

// The Second Brain: everything Minty knows as one living map. Every node is a real record (an item, an original, a person, a decision, a note, a mission), every line a typed link.
// Size = confirmed connections, glow = how recently it changed, ring = who wrote it (solid you, dashed Minty, dotted from outside), dot = how far it is checked.
// Nothing here is decoration: the data comes from /api/knowledge/graph, and each action goes through the same store Minty's own tools use.
const COLORS = {
  idea: "#ffd166", insight: "#ff9f43", concept: "#4cc9f0", finding: "#6ee7b0", observation: "#2ec4b6", lesson: "#ff7b54", question: "#ff5d9e", hypothesis: "#b388ff", opportunity: "#c7f464",
  note: "#cfd3ff", reference: "#8da2c0", article: "#7aa7ff", document: "#7aa7ff", dump: "#9aa0b4", source: "#8f94b8", person: "#ff8fab", organization: "#5aa9ff", company: "#5aa9ff",
  project: "#ffd6a5", product: "#9bf6ff", place: "#a0e7a0", tool: "#c0c0ff", event: "#ffc6ff", team: "#ff8fab", decision: "#ffffff", mission: "#f0c55a", flow: "#f0c55a", playbook: "#f0c55a", other: "#b0b4d0",
};
const REL_RGB = { contradicts: "255,107,112", supports: "110,231,176", answers: "76,201,240", "derived from": "130,140,255", "led to": "255,209,102", about: "190,190,230" };
const WHO = { you: "You", minty: "Minty", outside: "From outside" };
const RELS = ["supports", "contradicts", "answers", "part of", "derived from", "about", "relates to", "uses", "led to", "inspired by"];
const reduceMotion = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const color = (n) => COLORS[n.kind] || COLORS.other;
const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
const ago = (ts) => {
  if (!ts) return "";
  const d = (Date.now() / 1000 - ts) / 86400;
  return d < 1 ? "today" : d < 2 ? "yesterday" : d < 60 ? `${Math.round(d)} days ago` : new Date(ts * 1000).toLocaleDateString();
};
const hue = (s) => { let x = 0; for (const c of String(s)) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };

const sprites = new Map();
function glowSprite(col) {
  let c = sprites.get(col);
  if (c) return c;
  c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, col + "cc"); grad.addColorStop(0.35, col + "44"); grad.addColorStop(1, col + "00");
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  sprites.set(col, c);
  return c;
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------
// The universe: layout, drawing, pointer handling
// ---------------------------------------------------------------------------------------------------------------------------------------------------
function createUniverse(canvas, hooks) {
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  let nodes = [], edges = [], clusters = [], byId = new Map(), adj = new Map(), anchors = new Map();
  let view = { x: 0, y: 0, k: 1 }, tween = null;
  let busyUntil = 0, autoFit = false, alpha = 0, sel = null, hover = null, drag = null, hits = null, timeCut = null, dirty = true, frame = 0, alive = true;
  const filters = { hiddenKinds: new Set(), suggestions: true, sources: true };
  const pointers = new Map();
  let pinch = null;
  const stars = Array.from({ length: 150 }, (_, i) => ({ x: ((i * 7919) % 1000) / 1000, y: ((i * 104729) % 997) / 997, d: 0.15 + (((i * 31) % 70) / 100), s: 0.4 + (((i * 13) % 10) / 10) }));
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("kb-pos") || "{}"); } catch { saved = {}; }

  const visible = (n) => !filters.hiddenKinds.has(n.kind) && (filters.sources || n.kind !== "source") && (timeCut == null || !n.created || n.created <= timeCut);
  const radius = (n) => (n.kind === "source" ? 3.2 + 2.2 * n.size : 4.2 + 3.4 * n.size);
  const toWorld = (px, py) => ({ x: (px - view.x) / view.k, y: (py - view.y) / view.k });

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(200, r.width); H = Math.max(200, r.height);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    dirty = true;
  }

  // ---- data in: keep what was already placed, seed what is new near the things it connects to ----------------------------------------------------
  function setData(g) {
    const old = byId;
    const firstLoad = old.size === 0;
    nodes = g.nodes.map((n) => {
      const prev = old.get(n.id);
      return Object.assign(prev || { x: 0, y: 0, vx: 0, vy: 0, _pop: 0 }, n, { vx: prev ? prev.vx : 0, vy: prev ? prev.vy : 0 });
    });
    byId = new Map(nodes.map((n) => [n.id, n]));
    edges = g.edges.map((e) => ({ ...e, ai: byId.get(e.a), bi: byId.get(e.b) })).filter((e) => e.ai && e.bi);
    clusters = g.clusters;
    adj = new Map(nodes.map((n) => [n.id, []]));
    for (const e of edges) { adj.get(e.a).push(e); adj.get(e.b).push(e); }
    // cluster anchors: a golden-angle spiral, so clusters start apart; saved positions override it
    const ordered = clusters.filter((c) => c.id !== "_loose");
    const centre = new Map();
    ordered.forEach((c, i) => { const a = i * 2.39996, r = 230 * Math.sqrt(i + 0.7) + 30 * Math.sqrt(c.size); centre.set(c.id, { x: Math.cos(a) * r, y: Math.sin(a) * r }); });
    const looseR = 260 + 110 * Math.sqrt(ordered.length + 1);
    let looseI = 0; const looseN = Math.max(1, nodes.filter((n) => n.cluster === "_loose").length);
    let placedSaved = 0;
    for (const n of nodes) {
      if (n.x !== 0 || n.y !== 0) { continue; }
      const sp = saved[n.id];
      if (sp) { n.x = sp[0]; n.y = sp[1]; placedSaved++; continue; }
      const nb = adj.get(n.id).map((e) => (e.a === n.id ? e.bi : e.ai)).filter((m) => m.x !== 0 || m.y !== 0);
      if (nb.length) { n.x = nb.reduce((s, m) => s + m.x, 0) / nb.length + (Math.random() - 0.5) * 40; n.y = nb.reduce((s, m) => s + m.y, 0) / nb.length + (Math.random() - 0.5) * 40; }
      else if (n.cluster === "_loose") { const a = (looseI++ / looseN) * Math.PI * 2; n.x = Math.cos(a) * looseR + (Math.random() - 0.5) * 60; n.y = Math.sin(a) * looseR + (Math.random() - 0.5) * 60; }
      else { const c = centre.get(n.cluster) || { x: 0, y: 0 }; n.x = c.x + (Math.random() - 0.5) * 90; n.y = c.y + (Math.random() - 0.5) * 90; }
      if (!firstLoad) n._pop = performance.now();
    }
    anchors = new Map();
    for (const c of clusters) {
      const ms = c.members.map((id) => byId.get(id)).filter(Boolean);
      if (ms.length) anchors.set(c.id, { x: ms.reduce((s, m) => s + m.x, 0) / ms.length, y: ms.reduce((s, m) => s + m.y, 0) / ms.length });
    }
    alpha = firstLoad ? (placedSaved > nodes.length / 2 ? 0.25 : 1) : Math.max(alpha, 0.35);
    if (firstLoad) { fit(false); autoFit = true; }
    dirty = true;
  }

  // ---- layout: grid repulsion, springs along links, a gentle pull to the cluster, damping --------------------------------------------------------
  function tick() {
    const N = nodes.length;
    if (!N) return;
    const cell = 150, grid = new Map();
    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      const key = (Math.floor(n.x / cell) + 4000) * 10000 + (Math.floor(n.y / cell) + 4000);
      (grid.get(key) || grid.set(key, []).get(key)).push(i);
    }
    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      const cx = Math.floor(n.x / cell) + 4000, cy = Math.floor(n.y / cell) + 4000;
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        const list = grid.get((cx + dx) * 10000 + (cy + dy));
        if (!list) continue;
        for (const j of list) {
          if (j <= i) continue;
          const m = nodes[j];
          let ddx = n.x - m.x, ddy = n.y - m.y, d2 = ddx * ddx + ddy * ddy;
          if (d2 > 230 * 230) continue;
          if (d2 < 1) { ddx = Math.random() - 0.5; ddy = Math.random() - 0.5; d2 = 1; }
          const f = ((radius(n) + radius(m)) * 22 * alpha) / (d2 + 60);
          n.vx += ddx * f; n.vy += ddy * f; m.vx -= ddx * f; m.vy -= ddy * f;
        }
      }
    }
    for (const e of edges) {
      const a = e.ai, b = e.bi;
      const conf = e.status === "confirmed", across = a.cluster !== b.cluster;
      const L = ((conf ? 56 : 100) + (radius(a) + radius(b)) * 1.1) * (across ? 2.4 : 1);
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const share = 1 / Math.sqrt(Math.max(1, Math.min(adj.get(a.id).length, adj.get(b.id).length)));
      const f = ((d - L) / d) * (conf ? 0.32 : 0.06) * share * (across ? 0.2 : 1) * alpha;
      dx *= f; dy *= f;
      a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
    }
    for (const n of nodes) {
      const an = anchors.get(n.cluster);
      if (an) { n.vx += (an.x - n.x) * 0.02 * alpha; n.vy += (an.y - n.y) * 0.02 * alpha; }
      n.vx -= n.x * 0.0004 * alpha; n.vy -= n.y * 0.0004 * alpha;
      if (drag && drag.node === n) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.8; n.vy *= 0.8;
      n.x += Math.max(-30, Math.min(30, n.vx)); n.y += Math.max(-30, Math.min(30, n.vy));
    }
    alpha *= 0.985;
    if (alpha < 0.02) { alpha = 0; persist(); if (autoFit) { autoFit = false; fit(true); } }
  }
  let persistTimer = 0;
  function persist() {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(() => {
      const keep = {};
      for (const [id, p] of Object.entries(saved)) if (!byId.has(id)) keep[id] = p;
      for (const n of nodes) keep[n.id] = [Math.round(n.x), Math.round(n.y)];
      saved = keep;
      try { localStorage.setItem("kb-pos", JSON.stringify(Object.fromEntries(Object.entries(keep).slice(-3000)))); } catch { /* private window: the layout just settles again next time */ }
    }, 400);
  }

  // ---- view ------------------------------------------------------------------------------------------------------------------------------------------
  function fit(animate = true, subset = null) {
    const list = (subset || nodes).filter(visible);
    if (!list.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of list) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const bw = Math.max(80, x1 - x0), bh = Math.max(80, y1 - y0);
    const k = Math.max(0.08, Math.min(subset ? 2.2 : 1.4, Math.min((W - 40) / bw, (H - 150) / bh)));
    flyTo({ x: W / 2 - ((x0 + x1) / 2) * k, y: H / 2 + 22 - ((y0 + y1) / 2) * k, k }, animate ? 700 : 0);
  }
  function flyTo(target, ms = 600) {
    if (!ms || reduceMotion) { view = target; tween = null; dirty = true; return; }
    tween = { from: { ...view }, to: target, t0: performance.now(), ms };
  }
  function centreOn(n, k = Math.max(view.k, 1.1)) { flyTo({ x: W / 2 - n.x * k, y: H / 2 - n.y * k, k }, 650); }
  function zoomAt(px, py, factor) {
    busyUntil = performance.now() + 220;
    const k = Math.max(0.05, Math.min(5, view.k * factor));
    const w = toWorld(px, py);
    view = { k, x: px - w.x * k, y: py - w.y * k };
    tween = null; dirty = true;
  }

  // ---- drawing ---------------------------------------------------------------------------------------------------------------------------------------
  function shape(n, x, y, r) {
    ctx.beginPath();
    if (n.kind === "source") { ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); }
    else if (n.kind === "decision") { for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + (i * Math.PI) / 5, rr = i % 2 ? r * 0.5 : r * 1.15; ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr); } ctx.closePath(); }
    else if (n.system === "vault") { ctx.rect(x - r * 0.85, y - r * 0.85, r * 1.7, r * 1.7); }
    else if (n.system === "work") { for (let i = 0; i < 6; i++) { const a = (i * Math.PI) / 3; ctx.lineTo(x + Math.cos(a) * r * 1.1, y + Math.sin(a) * r * 1.1); } ctx.closePath(); }
    else ctx.arc(x, y, r, 0, Math.PI * 2);
  }
  function neighbourhood(n) {
    const set = new Set([n.id]);
    for (const e of adj.get(n.id) || []) { set.add(e.a); set.add(e.b); }
    return set;
  }
  function draw(now) {
    const big = nodes.length > 400;  // a big map stays still when nothing is happening: no pulsing, no flowing dashes
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    for (const s of stars) {
      const tw = reduceMotion ? 0.6 : 0.55 + 0.45 * Math.sin(now / 900 + s.x * 40);
      const sx = (((s.x * W + view.x * s.d * 0.12) % W) + W) % W, sy = (((s.y * H + view.y * s.d * 0.12) % H) + H) % H;
      ctx.fillStyle = `rgba(200,210,255,${0.12 + 0.3 * s.d * tw})`;
      ctx.fillRect(sx, sy, s.s * 1.4, s.s * 1.4);
    }
    const focusSet = hover ? neighbourhood(hover) : sel ? neighbourhood(sel) : null;
    const dimmed = (n) => (hits && !hits.has(n.id) && !(sel && n.id === sel.id)) || (!hits && focusSet && !focusSet.has(n.id));
    ctx.save();
    ctx.translate(view.x, view.y); ctx.scale(view.k, view.k);
    // cluster halos
    for (const c of clusters) {
      if (c.id === "_loose") continue;
      const ms = c.members.map((id) => byId.get(id)).filter((m) => m && visible(m));
      if (ms.length < 2) continue;
      let cx = 0, cy = 0;
      for (const m of ms) { cx += m.x; cy += m.y; }
      cx /= ms.length; cy /= ms.length;
      let r = 0;
      for (const m of ms) r = Math.max(r, Math.hypot(m.x - cx, m.y - cy));
      r = r * 1.15 + 46;
      const hu = hue(c.id), grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      grad.addColorStop(0, `hsla(${hu},70%,60%,0.10)`); grad.addColorStop(0.7, `hsla(${hu},70%,55%,0.05)`); grad.addColorStop(1, `hsla(${hu},70%,50%,0)`);
      ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      c._cx = cx; c._cy = cy; c._r = r;
    }
    // links, one stroke per style (hundreds of separate strokes per frame is what makes a big map slow)
    const batches = new Map();
    for (const e of edges) {
      const a = e.ai, b = e.bi;
      if (!visible(a) || !visible(b)) continue;
      if (e.status === "suggested" && !filters.suggestions) continue;
      const lit = (sel && (e.a === sel.id || e.b === sel.id)) || (hover && (e.a === hover.id || e.b === hover.id));
      const state = lit ? "L" : dimmed(a) && dimmed(b) ? "F" : e.status === "suggested" ? "S" : e.rel === "contradicts" ? "C" : "N";
      const rgb = REL_RGB[e.rel] || "170,175,215", key = state + rgb;
      let batch = batches.get(key);
      if (!batch) { batch = { state, rgb, list: [] }; batches.set(key, batch); }
      batch.list.push(e);
    }
    const sq = Math.sqrt(view.k);
    for (const { state, rgb, list } of batches.values()) {
      ctx.beginPath();
      for (const e of list) {
        const a = e.ai, b = e.bi, dx = b.x - a.x, dy = b.y - a.y, bend = ((e.a.length + e.b.length) % 2 ? 1 : -1) * 0.1;
        ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo((a.x + b.x) / 2 - dy * bend, (a.y + b.y) / 2 + dx * bend, b.x, b.y);
      }
      ctx.strokeStyle = `rgba(${rgb},${state === "L" ? 0.9 : state === "F" ? 0.04 : state === "S" ? 0.2 : state === "C" ? 0.5 : 0.26})`;
      ctx.lineWidth = (state === "L" ? 1.8 : state === "C" ? 1.4 : 0.9) / sq;
      ctx.setLineDash(state === "S" ? [5 / view.k, 5 / view.k] : state === "C" && !reduceMotion && !big ? [7 / view.k, 4 / view.k] : []);
      ctx.lineDashOffset = state === "C" && !reduceMotion && !big ? -(now / 60) / view.k : 0;
      ctx.stroke();
    }
    ctx.lineDashOffset = 0;
    ctx.setLineDash([]);
    // nodes
    const labelled = [];
    for (const n of nodes) {
      if (!visible(n)) continue;
      const col = color(n);
      let pop = 1;
      if (n._pop) { pop = Math.min(1, (now - n._pop) / 700); if (pop >= 1) n._pop = 0; pop = 1 - Math.pow(1 - pop, 3); }
      const r = radius(n) * pop;
      const faded = dimmed(n);
      const pulse = reduceMotion || big ? 1 : 1 + 0.12 * Math.sin(now / 700 + n.x * 0.01) * n.glow;
      const halo = r * (2.6 + n.glow * 2.2) * pulse;
      ctx.globalAlpha = Math.min(1, (faded ? 0.1 : 0.35 + 0.65 * Math.min(1, 0.4 + n.glow)) * (n._pop ? 1.4 : 1));
      if (!(big && now < busyUntil)) ctx.drawImage(glowSprite(col), n.x - halo, n.y - halo, halo * 2, halo * 2);  // halos are the dear part: left out while a big map is being moved
      ctx.globalAlpha = faded ? 0.18 : 1;
      shape(n, n.x, n.y, r);
      ctx.fillStyle = n.kind === "source" ? "rgba(15,15,35,.9)" : col;
      ctx.fill();
      ctx.lineWidth = (n.kind === "source" ? 1.4 : 1) / Math.sqrt(view.k);
      ctx.strokeStyle = n.kind === "source" ? col : "rgba(10,10,30,.55)";
      ctx.stroke();
      if (n.authored) {
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 2.6, 0, Math.PI * 2);
        ctx.setLineDash(n.authored === "minty" ? [3 / view.k, 3 / view.k] : n.authored === "outside" ? [1 / view.k, 3 / view.k] : []);
        ctx.strokeStyle = n.authored === "outside" ? "rgba(240,197,90,.9)" : n.authored === "minty" ? "rgba(170,190,255,.8)" : "rgba(255,255,255,.55)";
        ctx.lineWidth = 1.1 / Math.sqrt(view.k); ctx.stroke(); ctx.setLineDash([]);
      }
      if (n.status === "verified" || n.status === "disputed" || n.status === "outdated") {
        ctx.beginPath(); ctx.arc(n.x + r * 0.85, n.y - r * 0.85, 2.4 / Math.sqrt(view.k) + 1, 0, Math.PI * 2);
        ctx.fillStyle = n.status === "verified" ? "#6ee7b0" : n.status === "disputed" ? "#ff6b70" : "#8d8fa8"; ctx.fill();
      }
      if (sel && sel.id === n.id) { ctx.beginPath(); ctx.arc(n.x, n.y, r + 7 / view.k * 1.2, 0, Math.PI * 2); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.6 / Math.sqrt(view.k); ctx.stroke(); }
      if (hover && hover.id === n.id) { ctx.beginPath(); ctx.arc(n.x, n.y, r + 5 / view.k * 1.2, 0, Math.PI * 2); ctx.strokeStyle = "rgba(255,255,255,.6)"; ctx.lineWidth = 1.2 / Math.sqrt(view.k); ctx.stroke(); }
      ctx.globalAlpha = 1;
      const pri = (sel && sel.id === n.id ? 1e6 : 0) + (hover && hover.id === n.id ? 9e5 : 0) + (hits && hits.has(n.id) ? 5e5 : 0) + (focusSet && focusSet.has(n.id) ? 2e5 : 0) + n.degree * 10 + (n.glow || 0);
      const want = pri >= 2e5 || view.k >= 2.2 || (view.k >= 1.3 && n.degree >= 2) || (view.k >= 0.7 && n.degree >= 6) || n.degree >= 10;
      if (want && !faded) labelled.push([pri, n, r]);
    }
    ctx.restore();
    // labels in screen space so the text stays crisp and the same size at every zoom
    ctx.font = "12px Inter, system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
    const placed = [];
    labelled.sort((a, b) => b[0] - a[0]);
    for (const [pri, n, r] of labelled.slice(0, view.k < 1 ? 30 : 100)) {
      const text = clip(n.label, 26), tw = ctx.measureText(text).width;
      const sx = n.x * view.k + view.x, sy = n.y * view.k + view.y + r * view.k + 6;
      if (sx < -80 || sx > W + 80 || sy < -20 || sy > H + 20) continue;
      const box = [sx - tw / 2 - 3, sy - 1, sx + tw / 2 + 3, sy + 15];
      if (pri < 5e5 && placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
      placed.push(box);
      ctx.fillStyle = "rgba(10,10,28,.62)"; ctx.fillRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
      ctx.fillStyle = pri >= 2e5 ? "#fff" : "rgba(235,238,255,.86)";
      ctx.fillText(text, sx, sy);
    }
    if (view.k < 1.2) {
      ctx.font = "600 11px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle";
      for (const c of clusters) {
        if (c.id === "_loose" || c._cx == null) continue;
        const sx = c._cx * view.k + view.x, sy = (c._cy - c._r * 0.78) * view.k + view.y;
        if (sx < 0 || sx > W || sy < 0 || sy > H) continue;
        ctx.fillStyle = `hsla(${hue(c.id)},85%,78%,${0.55 * Math.min(1, view.k * 2)})`;
        ctx.fillText(c.label.toUpperCase().split("").join(" "), sx, sy);
      }
    }
  }

  // ---- loop ------------------------------------------------------------------------------------------------------------------------------------------
  let last = 0;
  function loop(now) {
    if (!alive) return;
    requestAnimationFrame(loop);
    if (busyUntil && now >= busyUntil) { busyUntil = 0; dirty = true; }  // movement stopped: draw once more with the halos
    if (tween) {
      const t = Math.min(1, (now - tween.t0) / tween.ms), e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      view = { x: tween.from.x + (tween.to.x - tween.from.x) * e, y: tween.from.y + (tween.to.y - tween.from.y) * e, k: tween.from.k + (tween.to.k - tween.from.k) * e };
      if (t >= 1) tween = null;
      dirty = true;
    }
    if (alpha > 0) { const t0 = performance.now(); let n = 0; do { tick(); n++; } while (alpha > 0 && n < 4 && performance.now() - t0 < 10); dirty = true; }  // as many steps as fit in 10 ms
    const animated = !reduceMotion && (nodes.length <= 400 ? nodes.some((n) => n.glow > 0.05 || n._pop) : nodes.some((n) => n._pop)) && now - last > 33;
    if (dirty || animated) { last = now; dirty = false; draw(now); }
  }

  // ---- pointer ---------------------------------------------------------------------------------------------------------------------------------------
  function pick(px, py) {
    const p = toWorld(px, py);
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      const d = Math.hypot(n.x - p.x, n.y - p.y), rr = radius(n) + 5 / view.k;
      if (d < rr && d < bd) { best = n; bd = d; }
    }
    return best;
  }
  const pos = (ev) => { const r = canvas.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
  canvas.addEventListener("pointerdown", (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    autoFit = false;
    const [px, py] = pos(ev);
    pointers.set(ev.pointerId, [px, py]);
    if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), k: view.k }; drag = null; return; }
    const n = pick(px, py);
    drag = { node: n, x: px, y: py, vx: view.x, vy: view.y, moved: false };
    tween = null;
  });
  canvas.addEventListener("pointermove", (ev) => {
    const [px, py] = pos(ev);
    if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, [px, py]);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d) * pinch.k / view.k);
      return;
    }
    if (drag) {
      if (Math.hypot(px - drag.x, py - drag.y) > 4) drag.moved = true;
      if (drag.moved) {
        busyUntil = performance.now() + 220;
        if (drag.node) { const w = toWorld(px, py); drag.node.x = w.x; drag.node.y = w.y; alpha = Math.max(alpha, 0.25); }
        else { view = { ...view, x: drag.vx + px - drag.x, y: drag.vy + py - drag.y }; }
        dirty = true;
      }
      return;
    }
    const n = pick(px, py);
    if (n !== hover) { hover = n; canvas.style.cursor = n ? "pointer" : "grab"; dirty = true; hooks.hover(n, px, py); }
    else if (n) hooks.hover(n, px, py);
  });
  const up = (ev) => {
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinch = null;
    if (!drag) return;
    const d = drag; drag = null;
    if (!d.moved) { sel = d.node || null; dirty = true; hooks.select(sel); }
    else if (d.node) persist();
  };
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("pointerleave", () => { if (hover && !drag) { hover = null; dirty = true; hooks.hover(null); } });
  canvas.addEventListener("dblclick", (ev) => { const n = pick(...pos(ev)); if (n) hooks.focus(n); });
  canvas.addEventListener("wheel", (ev) => { ev.preventDefault(); autoFit = false; const [px, py] = pos(ev); zoomAt(px, py, Math.exp(-ev.deltaY * 0.0015)); }, { passive: false });
  const ro = new ResizeObserver(() => resize());
  ro.observe(canvas);
  resize();
  requestAnimationFrame(loop);

  return {
    setData, fit, resize,
    get nodes() { return nodes; },
    node: (id) => byId.get(id),
    select(n) { sel = n || null; dirty = true; },
    centre: centreOn,
    zoom: (f) => zoomAt(W / 2, H / 2, f),
    setHits(ids) { hits = ids && ids.size ? ids : null; dirty = true; },
    setFilters(f) { Object.assign(filters, f); dirty = true; },
    toggleKind(k) { filters.hiddenKinds.has(k) ? filters.hiddenKinds.delete(k) : filters.hiddenKinds.add(k); dirty = true; return !filters.hiddenKinds.has(k); },
    isShown: (k) => !filters.hiddenKinds.has(k),
    setTime(t) { timeCut = t; dirty = true; },
    fitCluster(id) { const c = clusters.find((x) => x.id === id); if (c) fit(true, c.members.map((m) => byId.get(m)).filter(Boolean)); },
    fitNodes(list) { fit(true, list); },
    wake() { alpha = Math.max(alpha, 0.4); },
    stop() { alive = false; ro.disconnect(); },
  };
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------------------------------------------------------------------------------
export default async function knowledge(main, events) {
  let data = null, showPrivate = false, tab = "inspector", selected = null, timer = 0, playing = 0;
  const canvas = h("canvas", { class: "kb-canvas", role: "img", "aria-label": "Your knowledge as a map: each dot is something Minty knows, each line a connection. Use the search box or the lists beside it for a text version." });
  const tip = h("div", { class: "kb-tip", hidden: true });
  const legend = h("div", { class: "kb-legend" });
  const status = h("span", { class: "kb-status" });
  const empty = h("div", { class: "kb-empty", hidden: true },
    h("h3", {}, "Your second brain is empty"),
    h("p", {}, "Drop a thought, a link or a file into the Inbox, or tell Minty: “add this to my second brain”. Everything you add shows up here as something you can zoom into, connect and question."),
    h("button", { type: "button", onclick: () => setTab("inbox") }, "Open the Inbox"));
  const time = h("input", { type: "range", min: 0, max: 1000, value: 1000, class: "kb-time", "aria-label": "Show the map as it was at an earlier time" });
  const timeLabel = h("span", { class: "kb-time-label" }, "now");
  const playBtn = h("button", { type: "button", class: "kb-ico", title: "Watch it grow", "aria-label": "Watch it grow" }, "▶");
  const stage = h("div", { class: "kb-stage" }, canvas, tip, empty, legend,
    h("div", { class: "kb-zoom" }, h("button", { type: "button", title: "Zoom in", onclick: () => uni.zoom(1.4) }, "+"), h("button", { type: "button", title: "Zoom out", onclick: () => uni.zoom(1 / 1.4) }, "−"),
      h("button", { type: "button", title: "Fit everything (F)", onclick: () => uni.fit() }, "⤢")),
    h("div", { class: "kb-timebar" }, playBtn, time, timeLabel), status);
  const search = h("input", { type: "search", class: "kb-search", placeholder: "Search your knowledge  ( / )", "aria-label": "Search your knowledge", autocomplete: "off" });
  const results = h("div", { class: "kb-results", hidden: true });
  const priv = h("input", { type: "checkbox", id: "kb-priv" });
  const sugg = h("input", { type: "checkbox", id: "kb-sug", checked: true });
  const srcs = h("input", { type: "checkbox", id: "kb-src", checked: true });
  const tabs = { inspector: h("button", { type: "button" }, "Inspector"), inbox: h("button", { type: "button" }, "Inbox"), attention: h("button", { type: "button" }, "Needs attention") };
  const body = h("div", { class: "kb-body" });
  const side = h("aside", { class: "kb-side" }, h("div", { class: "kb-tabs" }, ...Object.values(tabs)), body);
  const bar = h("div", { class: "kb-bar" }, h("div", { class: "kb-searchbox" }, search, results),
    h("label", { class: "kb-chk", title: "Dotted lines: connections Minty or the system suggested but nobody confirmed" }, sugg, " suggestions"),
    h("label", { class: "kb-chk" }, srcs, " originals"),
    h("label", { class: "kb-chk", title: "Private knowledge is hidden until you ask" }, priv, " private"));
  const root = h("div", { class: "kb" }, bar, h("div", { class: "kb-main" }, stage, side));
  main.replaceChildren(h("h2", {}, "Second Brain"), root);

  const uni = createUniverse(canvas, {
    hover(n, px, py) {
      if (!n) { tip.hidden = true; return; }
      tip.hidden = false;
      tip.replaceChildren(h("b", {}, clip(n.label, 60)), h("span", {}, ` ${n.kind}${n.authored ? " · " + WHO[n.authored] : ""}${n.status && n.status !== "unverified" ? " · " + n.status : ""}`), n.snippet ? h("em", {}, clip(n.snippet, 110)) : null);
      tip.style.left = Math.min(px + 16, stage.clientWidth - 260) + "px"; tip.style.top = Math.min(py + 16, stage.clientHeight - 90) + "px";
    },
    select(n) { if (n) openNode(n.id, false); else { selected = null; setTab("inspector"); renderInspector(null); } },
    focus(n) { uni.fitNodes([n, ...neighbours(n)]); },
  });

  const neighbours = (n) => data.edges.filter((e) => e.a === n.id || e.b === n.id).map((e) => uni.node(e.a === n.id ? e.b : e.a)).filter(Boolean);

  // ---- loading -----------------------------------------------------------------------------------------------------------------------------------
  const q = () => (showPrivate ? "&private=1" : "");
  async function load(initial = false) {
    try { data = await api(`knowledge/graph?limit=900${q()}`); } catch (e) { status.textContent = e.message; return; }
    uni.setData(data);
    empty.hidden = !data.empty;
    renderLegend();
    const s = data.stats;
    status.textContent = data.empty ? "" : `${data.nodes.length} things · ${data.edges.filter((e) => e.status === "confirmed").length} connections · ${data.edges.filter((e) => e.status === "suggested").length} suggested${data.truncated ? ` · ${data.truncated} more not shown` : ""}`;
    const born = data.nodes.map((n) => n.created).filter(Boolean);
    time.dataset.min = born.length ? Math.min(...born) : 0;
    time.dataset.max = Date.now() / 1000;
    if (initial) { await refreshAttention(); }
  }
  let attention = null;
  async function refreshAttention() {
    try { attention = await api(`knowledge/attention?x=1${q()}`); } catch { return; }
    const n = (attention.gaps?.length || 0) + (attention.conflicts?.length || 0);
    tabs.attention.textContent = `Needs attention${n ? ` (${n})` : ""}`;
    const waiting = attention.captures?.length || 0;
    tabs.inbox.textContent = `Inbox${waiting ? ` (${waiting})` : ""}`;
    if (tab === "attention") renderAttention();
  }
  function renderLegend() {
    const kinds = Object.entries(data.legend).sort((a, b) => b[1] - a[1]);
    legend.replaceChildren(...kinds.map(([kind, n]) => {
      const b = h("button", { type: "button", class: `kb-chip ${uni.isShown(kind) ? "" : "off"}`, title: `Show or hide ${kind}` }, h("i", { style: `background:${COLORS[kind] || COLORS.other}` }), `${kind} ${n}`);
      b.addEventListener("click", () => { b.classList.toggle("off", !uni.toggleKind(kind)); });
      return b;
    }));
  }

  // ---- search ------------------------------------------------------------------------------------------------------------------------------------
  let searchTimer = 0, found = [];
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const text = search.value.trim();
    if (!text) { uni.setHits(null); results.hidden = true; return; }
    searchTimer = setTimeout(async () => {
      try { found = (await api(`knowledge/search?q=${encode(text)}${q()}`)).hits; } catch (e) { report(e); return; }
      uni.setHits(new Set(found.map((f) => f.ref)));
      results.replaceChildren(...(found.length ? found.slice(0, 10).map((f) => h("button", { type: "button", class: "kb-result", onclick: () => { go(f.ref); results.hidden = true; } },
        h("i", { style: `background:${COLORS[f.kind] || COLORS.other}` }), h("b", {}, clip(f.label, 50)), h("span", {}, f.kind), f.why ? h("em", {}, clip(f.why, 90)) : null)) : [h("p", { class: "muted" }, "Nothing matches that.")]));
      results.hidden = false;
    }, 220);
  });
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && found[0]) { go(found[0].ref); results.hidden = true; }
    if (ev.key === "Escape") { search.value = ""; uni.setHits(null); results.hidden = true; search.blur(); }
  });
  function go(ref) { const n = uni.node(ref); if (n) { uni.select(n); uni.centre(n, 1.4); } openNode(ref, !n); }

  // ---- tabs --------------------------------------------------------------------------------------------------------------------------------------
  function setTab(name) {
    tab = name;
    for (const [k, b] of Object.entries(tabs)) b.classList.toggle("on", k === name);
    if (name === "inspector") renderInspector(selected);
    else if (name === "inbox") renderInbox();
    else renderAttention();
  }
  for (const [k, b] of Object.entries(tabs)) b.addEventListener("click", () => setTab(k));

  // ---- the inspector: the thing, where it came from, how it connects ---------------------------------------------------------------------------------
  async function openNode(ref, fly = true) {
    let got;
    try { got = await api(`knowledge/node?ref=${encode(ref)}${q()}`); } catch (e) { report(e); return; }
    selected = got;
    const n = uni.node(ref);
    if (n) { uni.select(n); if (fly) uni.centre(n); }
    setTab("inspector");
  }
  const act = async (fn, msg) => { try { await fn(); if (msg) toast(msg); await Promise.all([load(), refreshAttention()]); if (selected) await openNode(selected.ref, false); } catch (e) { report(e); } };
  const badge = (text, cls = "") => h("span", { class: `kb-badge ${cls}` }, text);
  const jump = (ref, label) => h("a", { href: "#", class: "kb-jump", onclick: (e) => { e.preventDefault(); go(ref); } }, label);

  function renderInspector(g) {
    if (!g) {
      body.replaceChildren(h("div", { class: "kb-hint" }, h("h3", {}, "Pick something"),
        h("p", {}, "Click any dot to read it, see where it came from and how it connects. Double-click to zoom into its neighbourhood. Press / to search."),
        h("ul", {}, h("li", {}, "Size: how many confirmed connections it has."), h("li", {}, "Glow: changed recently."), h("li", {}, "Solid ring: you wrote it. Dashed: Minty. Dotted: it came from outside."),
          h("li", {}, "Green dot: checked. Red: disputed."), h("li", {}, "Dotted lines: suggested, not yet confirmed."))));
      return;
    }
    const parts = [];
    const own = g.system === "knowledge" && g.kind !== "source";
    parts.push(h("div", { class: "kb-ihead" }, h("i", { style: `background:${COLORS[g.kind] || COLORS.other}` }), h("div", {}, h("p", { class: "kicker" }, `${g.kind}${g.system && g.system !== "knowledge" ? " · " + g.system : ""}`), h("h3", {}, g.title)),));
    if (own) {
      parts.push(h("div", { class: "kb-badges" }, badge(WHO[g.authored] || g.authored, `who-${g.authored}`), badge(g.status, `st-${g.status}`), g.archived ? badge("archived") : null, g.project ? badge(g.project) : null, ...(g.tags || []).map((t) => badge("#" + t)),
        h("span", { class: "muted" }, ` added ${ago(g.created)}${g.reviewed ? ` · reviewed ${ago(g.reviewed)}` : ""}`)));
      if (g.note) parts.push(h("p", { class: "kb-warn" }, g.note));
      const show = h("div", { class: "kb-text" });
      const full = g.body || g.title;
      const setView = (summary) => { show.textContent = summary ? g.summary : full; sv.classList.toggle("on", summary); ov.classList.toggle("on", !summary); };
      const ov = h("button", { type: "button", class: "on", onclick: () => setView(false) }, "Original");
      const sv = h("button", { type: "button", title: "Written by Minty from the original; never replaces it", onclick: () => setView(true) }, "Summary (by Minty)");
      parts.push(g.summary ? h("div", { class: "kb-toggle" }, ov, sv) : null, show);
      show.textContent = full;
      parts.push(h("div", { class: "kb-actions" },
        h("button", { type: "button", onclick: () => act(() => api(`knowledge/items/${g.id}`, { method: "PATCH", body: { status: "verified" } }), "Marked verified") }, "✓ Verify"),
        h("button", { type: "button", onclick: () => act(() => api(`knowledge/items/${g.id}`, { method: "PATCH", body: { status: "disputed" } }), "Marked disputed") }, "Dispute"),
        h("button", { type: "button", onclick: () => act(() => api(`knowledge/items/${g.id}`, { method: "PATCH", body: { reviewed: true } }), "Reviewed") }, "Reviewed today"),
        h("button", { type: "button", onclick: () => editForm(g) }, "Edit"),
        h("button", { type: "button", class: "danger", title: "Hides it everywhere; nothing is deleted", onclick: () => act(() => api(`knowledge/items/${g.id}`, { method: "PATCH", body: { archive: !g.archived } }), g.archived ? "Restored" : "Archived") }, g.archived ? "Restore" : "Archive")));
    } else if (g.kind === "source") {
      parts.push(h("div", { class: "kb-badges" }, badge(g.sub), badge(WHO[g.origin] || g.origin), h("span", { class: "muted" }, ` kept ${ago(g.captured)}`)));
      if (g.note) parts.push(h("p", { class: "kb-warn" }, g.note));
      if (g.uri) parts.push(h("p", {}, h("a", { href: g.uri, target: "_blank", rel: "noopener noreferrer" }, g.uri)));
      parts.push(h("div", { class: "kb-text" }, g.text || "(no text was read from this original)"));
      if (g.used_by?.length) parts.push(h("h4", {}, "Knowledge resting on it"), h("ul", { class: "kb-list" }, ...g.used_by.map((u) => h("li", {}, jump(u.ref, u.label)))));
    } else if (g.system === "world") {
      parts.push(h("p", { class: "muted" }, g.note || ""));
      if (g.facts?.length) parts.push(h("h4", {}, "What is known"), h("ul", { class: "kb-list" }, ...g.facts.map((f) => h("li", {}, h("b", {}, f.attr + ": "), f.value, h("span", { class: "muted" }, ` (${f.provenance}${f.status !== "current" ? ", " + f.status : ""})`)))));
      if (g.rationale) parts.push(h("p", {}, g.rationale));
      if (g.decisions?.length) parts.push(h("h4", {}, "Decisions"), h("ul", { class: "kb-list" }, ...g.decisions.map((d) => h("li", {}, `${d.kind}: ${d.title}`))));
    } else if (g.contract) {
      parts.push(h("p", {}, `${g.contract.status}: ${g.contract.verified} of ${g.contract.total} criteria verified.`), h("p", {}, h("a", { href: "#mission" }, "Open Mission Control")));
    } else if (g.path) parts.push(h("p", { class: "muted" }, g.path));
    if (g.evidence?.length) parts.push(h("h4", {}, "Where it came from"), h("ul", { class: "kb-list" }, ...g.evidence.map((e) => h("li", {}, jump(`source:${e.source}`, e.title || e.uri || e.kind), h("span", { class: "muted" }, ` ${e.kind} · ${WHO[e.origin] || e.origin}`), e.quote ? h("em", {}, ` “${e.quote}”`) : null))));
    else if (own) parts.push(h("p", { class: "kb-warn" }, "No source is attached. It is only as good as whoever wrote it."));
    const links = g.links || [];
    const confirmed = links.filter((l) => l.status === "confirmed"), suggested = links.filter((l) => l.status === "suggested");
    if (links.length || own) {
      parts.push(h("h4", {}, `Connections (${confirmed.length})`), h("ul", { class: "kb-list" }, ...confirmed.map((l) => h("li", {}, h("span", { class: "kb-rel", style: `color:rgb(${REL_RGB[l.rel] || "190,195,230"})` }, l.direction === "out" ? l.rel : `is ${l.rel} of`), " ", jump(l.ref, l.label), l.why ? h("em", {}, ` — ${l.why}`) : null))));
      if (suggested.length) parts.push(h("h4", {}, "Suggested, not confirmed"), h("ul", { class: "kb-list" }, ...suggested.map((l) => h("li", {}, h("span", { class: "kb-rel" }, l.rel), " ", jump(l.ref, l.label), l.why ? h("em", {}, ` — ${l.why}`) : null,
        h("span", { class: "kb-yn" }, h("button", { type: "button", title: "Confirm", onclick: () => act(() => api(`knowledge/links/${l.id}/confirm`, { method: "POST" }), "Connected") }, "✓"),
          h("button", { type: "button", title: "Not related", onclick: () => act(() => api(`knowledge/links/${l.id}/reject`, { method: "POST" }), "Dismissed") }, "✗"))))));
    }
    if (g.related?.length) parts.push(h("h4", {}, "Might be related"), h("ul", { class: "kb-list" }, ...g.related.map((r) => h("li", {}, jump(r.ref, r.title), h("em", {}, ` — ${r.why}`),
      h("span", { class: "kb-yn" }, h("button", { type: "button", title: "Connect", onclick: () => act(() => api("knowledge/links", { method: "POST", body: { src: g.ref, rel: "relates to", dst: r.ref, why: r.why } }), "Connected") }, "＋"))))));
    if (own || g.system === "world") parts.push(connectForm(g));
    if (g.history?.length) parts.push(h("h4", {}, "History"), h("ul", { class: "kb-list kb-hist" }, ...g.history.map((x) => h("li", {}, h("span", { class: "muted" }, `${ago(x.ts)} · ${x.actor}: `), x.action))));
    body.replaceChildren(...parts.filter(Boolean));
  }
  function editForm(g) {
    const title = h("input", { type: "text", value: g.title }), text = h("textarea", { rows: 8 }, g.body);
    const form = h("div", { class: "kb-edit" }, title, text, h("div", { class: "kb-actions" },
      h("button", { type: "button", onclick: () => act(() => api(`knowledge/items/${g.id}`, { method: "PATCH", body: { title: title.value, body: text.value } }), "Saved") }, "Save"),
      h("button", { type: "button", onclick: () => renderInspector(g) }, "Cancel")), h("p", { class: "muted" }, "Changing the words of something verified makes it unchecked again; the old words stay in its history."));
    body.replaceChildren(form);
  }
  function connectForm(g) {
    const rel = h("select", {}, ...RELS.map((r) => h("option", { value: r }, r)));
    const target = h("input", { type: "search", placeholder: "connect to… (type to search)", autocomplete: "off" });
    const pick = h("div", { class: "kb-pick" });
    let timer2 = 0;
    target.addEventListener("input", () => {
      clearTimeout(timer2);
      timer2 = setTimeout(async () => {
        if (!target.value.trim()) { pick.replaceChildren(); return; }
        try { const hits = (await api(`knowledge/search?q=${encode(target.value)}${q()}`)).hits.filter((x) => x.ref !== g.ref).slice(0, 6);
          pick.replaceChildren(...hits.map((x) => h("button", { type: "button", onclick: () => act(() => api("knowledge/links", { method: "POST", body: { src: g.ref, rel: rel.value, dst: x.ref } }), "Connected") }, `${rel.value} → ${clip(x.label, 40)}`))); } catch (e) { report(e); }
      }, 220);
    });
    return h("div", { class: "kb-connect" }, h("h4", {}, "Connect it"), h("div", { class: "kb-row" }, rel, target), pick);
  }

  // ---- the inbox: anything in, nothing lost, everything inspectable --------------------------------------------------------------------------------
  async function renderInbox() {
    const text = h("textarea", { rows: 5, placeholder: "Brain dump, a thought, a link, anything…  Several thoughts at once are split up for you." });
    const pr = h("input", { type: "checkbox" });
    const file = h("input", { type: "file", hidden: true });
    const send = async (payload, path = "knowledge/captures") => { try { const got = await api(path, { method: "POST", body: { ...payload, private: pr.checked } }); toast(got.status === "filed" ? `Filed ${got.result?.atoms?.length || 0} piece(s)` : got.status === "review" ? "Saved, waiting for your approval" : got.status === "failed" ? `Could not process it: ${got.error}` : "Saved");
      await Promise.all([load(), refreshAttention()]); renderInbox(); } catch (e) { report(e); } };
    const sendText = () => { const t = text.value.trim(); if (!t) return; const link = /^https?:\/\/\S+$/.test(t); send(link ? { url: t } : { text: t }).then(() => { text.value = ""; }); };
    text.addEventListener("keydown", (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") sendText(); });
    file.addEventListener("change", () => upload(file.files[0], pr.checked));
    body.replaceChildren(h("div", { class: "kb-capture" }, text, h("div", { class: "kb-row" }, h("button", { type: "button", onclick: sendText }, "Capture"), h("button", { type: "button", onclick: () => file.click() }, "Add a file…"), file,
      h("label", { class: "kb-chk" }, pr, " private"), h("span", { class: "muted" }, "Ctrl+Enter to capture. Or drop a file on the map."))), h("p", { class: "muted" }, "Loading…"));
    let caps = [];
    try { caps = await api(`knowledge/captures?x=1${q()}`); } catch (e) { report(e); }
    if (tab !== "inbox" || !body.lastChild || !body.lastChild.matches("p.muted")) return;  // the owner moved on while it loaded
    const list = caps.length ? caps.map(captureCard) : [h("p", { class: "muted" }, "The inbox is empty. Everything you capture is kept here with its original until you archive it.")];
    body.lastChild.replaceWith(h("div", { class: "kb-caps" }, ...list));
  }
  async function upload(f, isPrivate) {
    if (!f) return;
    if (f.size > 8 * 1024 * 1024) { toast("That file is over 8 MB.", true); return; }
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    try { const got = await api("knowledge/captures/upload", { method: "POST", body: { name: f.name, data: btoa(bin), private: isPrivate } });
      toast(got.status === "filed" ? "File filed" : got.status === "failed" ? `Kept, but no text could be read: ${got.error}` : "Saved"); await Promise.all([load(), refreshAttention()]); if (tab === "inbox") renderInbox(); } catch (e) { report(e); }
  }
  const STATUS_HELP = { pending: "waiting to be processed", processing: "processing…", review: "waiting for your approval", filed: "filed", failed: "failed", archived: "archived" };
  function captureCard(c) {
    const atoms = c.result?.atoms || [];
    const call = (path, method = "POST", body) => act(() => api(`knowledge/captures/${c.id}${path}`, { method, body }));
    const rawBox = h("textarea", { rows: 4, hidden: true }, c.raw);
    return h("div", { class: `kb-cap st-${c.status}` },
      h("div", { class: "kb-cap-head" }, badge(STATUS_HELP[c.status] || c.status, `st-${c.status}`), h("span", { class: "muted" }, `${c.channel || "capture"} · ${ago(c.created)}${c.origin !== "you" ? " · from " + (c.origin === "minty" ? "Minty" : "outside") : ""}${c.attempts > 1 ? ` · tried ${c.attempts}×` : ""}`)),
      h("p", { class: "kb-raw" }, clip(c.raw, 220)), rawBox,
      c.error ? h("p", { class: "kb-warn" }, c.error) : null,
      ...(c.result?.flags || []).map((f) => h("p", { class: "kb-warn" }, f)),
      atoms.length ? h("ul", { class: "kb-list" }, ...atoms.map((a) => h("li", {}, badge(a.kind), " ", a.item ? jump(`item:${a.item}`, a.title) : a.title, a.duplicate_of ? h("em", {}, " — already known: added as more evidence") : null,
        a.entities?.length ? h("em", {}, ` — about ${a.entities.join(", ")}`) : null, a.similar?.length ? h("em", {}, ` — maybe related: ${a.similar.map((s) => s.why).join("; ")}`) : null))) : null,
      c.result?.entities?.proposed?.length ? h("p", { class: "muted" }, `Names I don't know yet: ${c.result.entities.proposed.join(", ")}`) : null,
      ...(c.result?.notes || []).map((n) => h("p", { class: "muted" }, n)),
      h("div", { class: "kb-actions" },
        c.status === "review" ? h("button", { type: "button", onclick: () => call("/approve") }, "Approve and file") : null,
        c.status !== "archived" ? h("button", { type: "button", onclick: () => call("/process") }, c.status === "pending" || c.status === "failed" ? "Process" : "Reprocess") : null,
        h("button", { type: "button", onclick: () => { if (rawBox.hidden) { rawBox.hidden = false; return; } act(() => api(`knowledge/captures/${c.id}`, { method: "PATCH", body: { text: rawBox.value } }), "Updated; process it again"); } }, "Edit"),
        c.status !== "archived" ? h("button", { type: "button", onclick: () => call("/archive") }, "Archive") : null,
        h("button", { type: "button", class: "danger", onclick: () => { if (confirm("Remove this from the inbox for good? Anything already filed from it stays.")) call("", "DELETE"); } }, "Remove")));
  }

  // ---- what needs attention -------------------------------------------------------------------------------------------------------------------------
  function renderAttention() {
    const a = attention;
    if (!a) { body.replaceChildren(h("p", { class: "muted" }, "Looking…")); return; }
    const meter = (label, pct) => h("div", { class: "kb-meter" }, h("span", {}, label), h("div", {}, h("i", { style: `width:${pct}%` })), h("b", {}, `${pct}%`));
    const parts = [h("div", { class: "kb-health" }, meter("rests on a source", a.health.sourced_pct), meter("connected", a.health.linked_pct),
      h("p", { class: "muted" }, `${a.health.items} items · ${a.health.verified} verified · ${a.health.from_outside} from outside`))];
    if (a.conflicts.length) parts.push(h("h4", {}, `Where it disagrees (${a.conflicts.length})`), h("ul", { class: "kb-list" }, ...a.conflicts.map((c) => h("li", {},
      badge(c.verified ? "verified" : "a guess", c.verified ? "st-verified" : "st-unverified"), " ", ...(c.a.startsWith("world:") ? [h("b", {}, c.titles.join(" · "))] : [jump(`item:${c.a}`, c.titles[0]), " ↔ ", jump(`item:${c.b}`, c.titles[1])]),
      h("em", {}, ` — ${c.why}`),
      !c.verified && !c.a.startsWith("world:") ? h("span", { class: "kb-yn" }, h("button", { type: "button", title: "Mark as contradicting", onclick: () => act(async () => { await api("knowledge/links", { method: "POST", body: { src: `item:${c.a}`, rel: "contradicts", dst: `item:${c.b}`, why: c.why } }); }, "Recorded as a contradiction") }, "✓"),
        h("button", { type: "button", title: "Not a conflict", onclick: () => act(async () => { const l = await api("knowledge/links", { method: "POST", body: { src: `item:${c.a}`, rel: "contradicts", dst: `item:${c.b}` } }); await api(`knowledge/links/${l.id}/reject`, { method: "POST" }); }, "Dismissed") }, "✗")) : null))));
    if (a.captures.length) parts.push(h("h4", {}, `Waiting in the inbox (${a.captures.length})`), h("ul", { class: "kb-list" }, ...a.captures.map((c) => h("li", {}, badge(c.status, `st-${c.status}`), ` ${clip(c.text, 80)}`))), h("button", { type: "button", onclick: () => setTab("inbox") }, "Open the Inbox"));
    const groups = {};
    for (const g of a.gaps) (groups[g.kind] ||= []).push(g);
    for (const [kind, list] of Object.entries(groups)) {
      if (kind === "captures waiting") continue;
      parts.push(h("h4", {}, `${kind[0].toUpperCase() + kind.slice(1)} (${list.length})`), h("ul", { class: "kb-list" }, ...list.slice(0, 8).map((g) => h("li", {}, jump(g.ref, g.title), h("em", {}, ` — ${g.why}`)))));
    }
    if (parts.length === 1 && !a.gaps.length) parts.push(h("p", { class: "muted" }, "Nothing is missing or in conflict right now."));
    body.replaceChildren(...parts);
  }

  // ---- time: watch the universe grow -------------------------------------------------------------------------------------------------------------------
  function cut() {
    const min = Number(time.dataset.min), max = Number(time.dataset.max);
    if (+time.value >= 1000 || !max) { uni.setTime(null); timeLabel.textContent = "now"; return; }
    const t = min + ((max - min) * +time.value) / 1000;
    uni.setTime(t); timeLabel.textContent = new Date(t * 1000).toLocaleDateString();
  }
  time.addEventListener("input", () => { stopPlay(); cut(); });
  function stopPlay() { clearInterval(playing); playing = 0; playBtn.textContent = "▶"; }
  playBtn.addEventListener("click", () => {
    if (playing) { stopPlay(); return; }
    playBtn.textContent = "❚❚"; time.value = 0; cut();
    playing = setInterval(() => { time.value = Math.min(1000, +time.value + 12); cut(); if (+time.value >= 1000) stopPlay(); }, 60);
  });

  // ---- wiring ------------------------------------------------------------------------------------------------------------------------------------
  priv.addEventListener("change", () => { showPrivate = priv.checked; selected = null; Promise.all([load(), refreshAttention()]).then(() => setTab(tab)); });
  sugg.addEventListener("change", () => uni.setFilters({ suggestions: sugg.checked }));
  srcs.addEventListener("change", () => uni.setFilters({ sources: srcs.checked }));
  stage.addEventListener("dragover", (ev) => { ev.preventDefault(); stage.classList.add("drop"); });
  stage.addEventListener("dragleave", () => stage.classList.remove("drop"));
  stage.addEventListener("drop", (ev) => { ev.preventDefault(); stage.classList.remove("drop"); const f = ev.dataTransfer.files[0]; if (f) { setTab("inbox"); upload(f, priv.checked); } });
  const keys = (ev) => {
    if (!document.body.contains(root)) { document.removeEventListener("keydown", keys); return; }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (ev.key === "/" && !typing) { ev.preventDefault(); search.focus(); }
    else if (!typing && ev.key === "f") uni.fit();
    else if (!typing && (ev.key === "+" || ev.key === "=")) uni.zoom(1.3);
    else if (!typing && ev.key === "-") uni.zoom(1 / 1.3);
    else if (ev.key === "Escape" && !typing) { uni.select(null); uni.setHits(null); selected = null; renderInspector(null); }
  };
  document.addEventListener("keydown", keys);

  await load(true);
  setTab("inspector");
  timer = setInterval(() => {
    if (!document.body.contains(root)) { clearInterval(timer); stopPlay(); uni.stop(); off(); return; }
    if (document.hidden || playing) return;
    load().then(refreshAttention);
  }, 30000);
  const off = events.on((e) => { if (document.body.contains(root) && ["job", "notify"].includes(e.type)) { clearTimeout(root._t); root._t = setTimeout(() => refreshAttention(), 1500); } });
}
