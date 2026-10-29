import { api, h, report, toast } from "../lib.js";

// Automations: a visual workflow editor that is a window on the very same workflow Minty edits through its own tools.
// The canvas reads and writes only /api/flows (one definition per workflow, every save is a new version), so what Minty changes
// shows up here after a reload of the workflow, and what is saved here is what Minty reads next. Nothing is drawn that cannot run.
const SVG = "http://www.w3.org/2000/svg";
const NODE_W = 188, NODE_H = 58, PORT_R = 6;

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

const portY = (spec, port) => {
  const list = spec.ports.filter((p) => p !== "error");
  if (port === "error") return NODE_H - 6;
  const i = Math.max(0, list.indexOf(port));
  return list.length === 1 ? NODE_H / 2 : 16 + (i * (NODE_H - 36)) / Math.max(1, list.length - 1) + 2;
};

function tidy(doc) {
  const depth = {};
  const preds = {};
  for (const e of doc.edges) (preds[e.to] ||= []).push(e.from);
  const d = (id, seen = new Set()) => {
    if (depth[id] != null) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    return (depth[id] = (preds[id] || []).reduce((m, p) => Math.max(m, d(p, seen) + 1), 0));
  };
  const rows = {};
  for (const n of doc.nodes) {
    const col = d(n.id);
    rows[col] = (rows[col] ?? -1) + 1;
    n.pos = [40 + col * (NODE_W + 56), 40 + rows[col] * (NODE_H + 40)];
  }
}

export default async function flows(main, events) {
  let catalogue = null;
  let list = [];
  let flow = null; // {id, name, doc, version, issues, versions, stats}; id is null for a new one not saved yet
  let dirty = false;
  let sel = null; // {kind: "node"|"edge", id}
  let view = { x: 30, y: 30, k: 1 };
  let live = { run: null, status: {} };
  let tab = "props";
  let connecting = null;
  let runs = [];
  let runDetail = null;
  const spec = (type) => catalogue.nodes.find((n) => n.type === type);
  let fitted = false; // fit the view once the canvas has a real size (it has none while the page is still being laid out)

  const svg = s("svg", { class: "fl-svg", tabindex: "0" });
  const world = s("g");
  const edgesG = s("g");
  const nodesG = s("g");
  const ghost = s("path", { class: "fl-ghost", d: "" });
  const bg = s("rect", { class: "fl-bg", x: -5000, y: -5000, width: 10000, height: 10000 });
  world.append(bg, edgesG, ghost, nodesG);
  svg.append(world);
  const mini = s("svg", { class: "fl-mini", viewBox: "0 0 200 120" });
  const side = h("div", { class: "fl-side" });
  const listBox = h("div", { class: "fl-list" });
  const paletteBox = h("div", { class: "fl-palette" });
  const search = h("input", { type: "text", placeholder: "Search nodes…", class: "fl-search", oninput: () => drawPalette() });
  const bar = h("div", { class: "fl-bar" });
  const status = h("span", { class: "muted fl-status" }, "");
  const stage = h("div", { class: "fl-stage" }, svg, mini);
  const panel = h("div", { class: "fl-panel" });
  const inputBox = h("textarea", { class: "fl-input", rows: "2", placeholder: 'Input for a manual start, e.g. {"n": 5}' });
  const parseInput = () => { try { return JSON.parse(inputBox.value || "{}"); } catch { return {}; } };

  // ---- data ------------------------------------------------------------------------------------------------
  async function refreshList() { list = await api("flows"); drawList(); }
  async function open(id) {
    if (dirty && !confirm("You have unsaved changes in this workflow. Leave without saving?")) return;
    flow = await api(`flows/${id}`);
    dirty = false; sel = null; live = { run: null, status: {} }; runDetail = null; fitted = false;
    if (flow.doc.nodes.length && flow.doc.nodes.every((n) => n.pos[0] === 0 && n.pos[1] === 0)) tidy(flow.doc);
    await loadRuns();
    fit(); drawAll();
    history.replaceState(null, "", `#flows:${id}`);
  }
  function blank() {
    const doc = { name: "new workflow", description: "", nodes: [{ id: "start", type: "trigger.manual", params: {}, pos: [40, 40] }], edges: [] };
    flow = { id: null, name: doc.name, doc, version: 0, issues: [], versions: [], stats: {} };
    dirty = true; sel = null; runs = []; runDetail = null; fit(); drawAll();
  }
  async function loadRuns() {
    if (!flow?.id) { runs = []; return; }
    const r = await api(`flows/${flow.id}/runs`);
    runs = r.runs; flow.stats = r.stats;
  }
  async function check() {
    const r = await api("flows/check", { method: "POST", body: { doc: flow.doc } });
    flow.issues = r.issues;
    return r;
  }
  async function save() {
    flow.doc.name = flow.doc.name.trim();
    const r = flow.id ? await api(`flows/${flow.id}`, { method: "PUT", body: { doc: flow.doc, note: "edited in the editor" } }) : await api("flows", { method: "POST", body: { doc: flow.doc } });
    flow.issues = r.issues || [];
    if (!r.saved) { tab = "problems"; drawAll(); toast("Not saved: it has errors (see Problems).", true); return false; }
    dirty = false;
    const id = flow.id ?? r.id;
    await refreshList();
    flow = await api(`flows/${id}`);
    toast(`Saved as version ${r.version}.`);
    await loadRuns(); drawAll();
    return true;
  }
  async function run(test) {
    if (dirty && !(await save())) return;
    const input = flow.doc.nodes.some((n) => n.type === "trigger.manual") ? parseInput() : {};
    const r = await api(`flows/${flow.id}/run`, { method: "POST", body: { test, input } });
    tab = "runs";
    if (r.status === "blocked") { toast(r.error, true); await loadRuns(); drawAll(); return; }
    live = { run: r.run, status: {} };
    await loadRuns();
    await showRun(r.run); // statuses that arrived before this answer did are read back from the run itself
    toast(test ? "Test run started: nothing that changes something is done." : "Run started.");
  }

  // ---- drawing ---------------------------------------------------------------------------------------------
  function apply() { world.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.k})`); drawMini(); }
  function fit() {
    const ns = flow?.doc.nodes || [];
    if (!ns.length) { view = { x: 30, y: 30, k: 1 }; return apply(); }
    const xs = ns.map((n) => n.pos[0]), ys = ns.map((n) => n.pos[1]);
    const w = Math.max(...xs) + NODE_W - Math.min(...xs) + 60, hgt = Math.max(...ys) + NODE_H - Math.min(...ys) + 60;
    const box = svg.getBoundingClientRect();
    const k = Math.min(1.2, (box.width || 800) / w, (box.height || 500) / hgt);
    view = { k, x: 30 - Math.min(...xs) * k, y: 30 - Math.min(...ys) * k };
    apply();
  }
  const issuesFor = (id) => (flow.issues || []).filter((i) => i.node === id);
  function summary(n) {
    const p = n.params;
    return p.tool || p.flow || p.time || (typeof p.prompt === "string" && p.prompt.slice(0, 20)) || (typeof p.text === "string" && p.text.slice(0, 20)) || spec(n.type)?.title || n.type;
  }
  function drawNodes() {
    nodesG.replaceChildren();
    for (const n of flow.doc.nodes) {
      const sp = spec(n.type);
      if (!sp) continue;
      const st = live.status[n.id] || "";
      const probs = issuesFor(n.id);
      const bad = probs.some((i) => i.level === "error");
      const isSel = sel?.kind === "node" && sel.id === n.id;
      const g = s("g", { class: `fl-node g-${sp.group.toLowerCase()} ${st} ${isSel ? "sel" : ""} ${bad ? "bad" : ""}`, transform: `translate(${n.pos[0]} ${n.pos[1]})`, "data-id": n.id },
        s("rect", { width: NODE_W, height: NODE_H, rx: 6, class: "fl-box" }),
        s("text", { x: 14, y: 22, class: "fl-title" }, n.params.label || sp.title),
        s("text", { x: 14, y: 40, class: "fl-sub" }, `${n.id} · ${summary(n)}`));
      if (st) g.append(s("circle", { cx: NODE_W - 12, cy: 12, r: 5, class: "fl-dot" }));
      if (probs.length) g.append(s("title", {}, probs.map((i) => i.message).join("\n")));
      if (!sp.trigger) g.append(s("circle", { cx: 0, cy: NODE_H / 2, r: PORT_R, class: "fl-port in", "data-in": n.id }));
      const multi = sp.ports.filter((x) => x !== "error").length > 1;
      for (const p of sp.ports) {
        const err = p === "error";
        if (err && !isSel && !flow.doc.edges.some((e) => e.from === n.id && e.port === "error") && connecting?.from !== n.id) continue;
        const y = portY(sp, p);
        g.append(s("circle", { cx: NODE_W, cy: y, r: err ? 4 : PORT_R, class: `fl-port out ${err ? "err" : ""}`, "data-out": n.id, "data-port": p }));
        if (multi && !err) g.append(s("text", { x: NODE_W - 12, y: y + 3, class: "fl-plabel", "text-anchor": "end" }, p));
      }
      g.addEventListener("mousedown", (ev) => nodeDown(ev, n));
      nodesG.append(g);
    }
  }
  const edgeKey = (e) => `${e.from}.${e.port}>${e.to}`;
  function drawEdges() {
    edgesG.replaceChildren();
    for (const e of flow.doc.edges) {
      const a = flow.doc.nodes.find((n) => n.id === e.from), b = flow.doc.nodes.find((n) => n.id === e.to);
      if (!a || !b || !spec(a.type)) continue;
      const x1 = a.pos[0] + NODE_W, y1 = a.pos[1] + portY(spec(a.type), e.port), x2 = b.pos[0], y2 = b.pos[1] + NODE_H / 2;
      const dx = Math.max(40, Math.abs(x2 - x1) / 2);
      const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
      const on = live.status[e.from] && live.status[e.to] && live.status[e.to] !== "skipped";
      const picked = sel?.kind === "edge" && sel.id === edgeKey(e);
      edgesG.append(s("path", { d, class: `fl-edge ${e.port === "error" ? "err" : ""} ${on ? "on" : ""} ${picked ? "sel" : ""}` }),
        s("path", { d, class: "fl-hit", onmousedown: (ev) => { ev.stopPropagation(); sel = { kind: "edge", id: edgeKey(e) }; tab = "props"; drawAll(); } }));
    }
  }
  function drawMini() {
    mini.replaceChildren();
    const ns = flow?.doc.nodes || [];
    if (!ns.length) return;
    const minX = Math.min(...ns.map((n) => n.pos[0])), minY = Math.min(...ns.map((n) => n.pos[1]));
    const maxX = Math.max(...ns.map((n) => n.pos[0] + NODE_W)), maxY = Math.max(...ns.map((n) => n.pos[1] + NODE_H));
    const k = Math.min(190 / Math.max(1, maxX - minX), 110 / Math.max(1, maxY - minY));
    for (const n of ns) mini.append(s("rect", { x: 5 + (n.pos[0] - minX) * k, y: 5 + (n.pos[1] - minY) * k, width: NODE_W * k, height: NODE_H * k, class: `fl-mrect ${live.status[n.id] || ""}` }));
    mini.onclick = (ev) => {
      const r = mini.getBoundingClientRect();
      const wx = minX + (((ev.clientX - r.left) / r.width) * 200 - 5) / k, wy = minY + (((ev.clientY - r.top) / r.height) * 120 - 5) / k;
      const box = svg.getBoundingClientRect();
      view.x = box.width / 2 - wx * view.k; view.y = box.height / 2 - wy * view.k; apply();
    };
  }
  function drawAll() { if (!flow) return drawEmpty(); drawBar(); drawNodes(); drawEdges(); apply(); drawPanel(); drawList(); }
  function drawEmpty() { bar.replaceChildren(h("span", { class: "muted" }, "Pick a workflow on the left, or make a new one.")); nodesG.replaceChildren(); edgesG.replaceChildren(); panel.replaceChildren(); mini.replaceChildren(); }

  let checkTimer = 0;
  function schedCheck() { // problems show up while you edit, not only when you save
    clearTimeout(checkTimer);
    checkTimer = setTimeout(() => { if (flow) check().then(() => { drawNodes(); drawBar(); if (tab === "problems") drawPanel(); }).catch(() => {}); }, 700);
  }
  function markDirty() { dirty = true; drawBar(); schedCheck(); }
  function drawBar() {
    const f = flow;
    const name = h("input", { type: "text", value: f.doc.name, class: "fl-name", oninput: (e) => { f.doc.name = e.target.value; markDirty(); } });
    const errs = (f.issues || []).filter((i) => i.level === "error").length;
    const entry = list.find((x) => x.id === f.id);
    bar.replaceChildren(...[
      name,
      h("button", { type: "button", class: "primary", onclick: () => save().catch(report) }, dirty ? "Save *" : "Save"),
      h("button", { type: "button", onclick: () => run(true).catch(report), title: "Runs what only reads; assesses what would change something without doing it" }, "Test"),
      h("button", { type: "button", onclick: () => run(false).catch(report) }, "Run"),
      f.id ? h("label", { class: "fl-toggle", title: "Runs by itself on its triggers (a schedule, a job ending)" }, h("input", { type: "checkbox", checked: !!entry?.enabled, onchange: (e) => toggle(e.target.checked) }), " On") : null,
      h("button", { type: "button", onclick: () => { tidy(f.doc); markDirty(); fit(); drawAll(); } }, "Tidy"),
      h("button", { type: "button", onclick: fit }, "Fit"),
      f.id ? h("button", { type: "button", onclick: () => duplicate().catch(report) }, "Duplicate") : null,
      f.id ? h("button", { type: "button", onclick: () => archive().catch(report), title: "Nothing is deleted: it can be brought back" }, "Archive") : null,
      status, errs ? h("span", { class: "fl-errs" }, `${errs} problem${errs === 1 ? "" : "s"}`) : null].filter(Boolean));
  }
  async function toggle(on) {
    try { await api(`flows/${flow.id}/enable`, { method: "POST", body: { enabled: on } }); } catch (e) { report(e); }
    await refreshList(); drawBar();
  }
  async function duplicate() {
    const doc = JSON.parse(JSON.stringify(flow.doc));
    doc.name = `${doc.name} copy`;
    const r = await api("flows", { method: "POST", body: { doc } });
    await refreshList();
    if (r.saved) { dirty = false; await open(r.id); } else toast("The copy has errors, so it was not saved.", true);
  }
  async function archive() {
    if (!confirm("Archive this workflow? It stops running and leaves the list, but nothing is deleted.")) return;
    await api(`flows/${flow.id}/archive`, { method: "POST", body: { archived: true } });
    flow = null; dirty = false; await refreshList(); drawEmpty(); history.replaceState(null, "", "#flows");
  }

  function drawList() {
    listBox.replaceChildren(h("button", { type: "button", class: "primary fl-new", onclick: blank }, "+ New workflow"),
      ...list.map((f) => h("button", { type: "button", class: `fl-item ${flow?.id === f.id ? "on" : ""}`, onclick: () => open(f.id).catch(report) },
        h("span", { class: `fl-led ${f.last_run?.status || ""}` }), h("span", { class: "fl-iname" }, f.name), f.enabled ? h("span", { class: "fl-badge" }, "on") : null,
        h("span", { class: "muted fl-imeta" }, `${f.creator === "user" ? "you" : f.creator} · v${f.version}`))));
  }
  function drawPalette() {
    const q = search.value.trim().toLowerCase();
    const groups = {};
    for (const n of catalogue.nodes) if (!q || `${n.title} ${n.type} ${n.doc}`.toLowerCase().includes(q)) (groups[n.group] ||= []).push(n);
    paletteBox.replaceChildren(...Object.entries(groups).map(([g, items]) => h("div", {}, h("div", { class: "fl-group" }, g),
      ...items.map((n) => h("button", { type: "button", class: "fl-pal", title: n.doc, onclick: () => addNode(n.type) }, n.title)))));
  }
  function addNode(type) {
    if (!flow) blank();
    const base = type.split(".")[1].replace(/[^a-z]/g, "");
    let i = 1;
    while (flow.doc.nodes.some((n) => n.id === `${base}${i}`)) i++;
    const box = svg.getBoundingClientRect();
    const pos = [Math.round((box.width / 2 - view.x) / view.k - NODE_W / 2 + (i % 4) * 14), Math.round((box.height / 2 - view.y) / view.k - NODE_H / 2 + (i % 4) * 14)];
    const params = {};
    for (const [k, v] of Object.entries(spec(type).params)) if (v.required) params[k] = v.kind === "obj" || v.kind === "cond" ? {} : v.kind === "list" ? [] : "";
    flow.doc.nodes.push({ id: `${base}${i}`, type, params, pos });
    sel = { kind: "node", id: `${base}${i}` }; tab = "props"; markDirty(); drawAll();
  }

  // ---- the panel on the right --------------------------------------------------------------------------------
  function drawPanel() {
    const labels = [["props", "Settings"], ["problems", `Problems${(flow.issues || []).length ? ` (${flow.issues.length})` : ""}`], ["runs", "Runs"], ["versions", "Versions"], ["assist", "Ask Minty"]];
    const tabs = h("div", { class: "fl-tabs" }, ...labels.map(([k, label]) => h("button", { type: "button", class: tab === k ? "on" : "", onclick: () => { tab = k; drawPanel(); } }, label)));
    const body = { props: propsView, problems: problemsView, runs: runsView, versions: versionsView, assist: assistView }[tab]();
    panel.replaceChildren(tabs, body);
  }
  function propsView() {
    if (sel?.kind === "edge") return h("div", { class: "fl-body" }, h("p", {}, `Connection ${sel.id}`), h("button", { type: "button", onclick: removeSelected }, "Remove connection"));
    const n = sel?.kind === "node" ? flow.doc.nodes.find((x) => x.id === sel.id) : null;
    if (!n) {
      return h("div", { class: "fl-body" }, h("label", { class: "fl-field" }, h("span", {}, "What this workflow is for"), h("textarea", { rows: "3", value: flow.doc.description || "", oninput: (e) => { flow.doc.description = e.target.value; dirty = true; drawBar(); } })),
        h("p", { class: "muted" }, "Click a node to change its settings. Add nodes from the list on the left. Drag from a node's right dot to another node's left dot to connect them; select a connection and press Delete to remove it."),
        h("label", { class: "fl-field" }, h("span", {}, "Input for the next test or run"), inputBox));
    }
    const sp = spec(n.type);
    return h("div", { class: "fl-body" }, h("h3", {}, sp.title), h("p", { class: "muted" }, sp.doc), h("p", { class: "muted" }, `id ${n.id} · ${n.type}${sp.effect !== "none" ? ` · touches: ${sp.effect}` : ""}`),
      ...issuesFor(n.id).map((i) => h("p", { class: i.level === "error" ? "error" : "muted" }, i.message)),
      ...Object.entries(sp.params).map(([name, meta]) => field(n, name, meta)), h("button", { type: "button", onclick: removeSelected }, "Delete this node"));
  }
  function field(n, name, meta) {
    const v = n.params[name];
    const set = (val) => { if (val === "" || val == null) delete n.params[name]; else n.params[name] = val; markDirty(); drawNodes(); };
    let input;
    if (meta.kind === "str") input = h("input", { type: "text", value: v ?? "", oninput: (e) => set(e.target.value) });
    else if (meta.kind === "num") input = h("input", { type: "number", value: v ?? "", oninput: (e) => set(e.target.value === "" ? "" : Number(e.target.value)) });
    else {
      input = h("textarea", { rows: meta.kind === "any" ? "2" : "4", value: v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2), class: "fl-json" });
      input.addEventListener("input", () => {
        const t = input.value.trim();
        if (!t) { input.classList.remove("bad"); return set(""); }
        try { set(JSON.parse(t)); input.classList.remove("bad"); } catch {
          if (meta.kind === "any") { set(t); input.classList.remove("bad"); } else input.classList.add("bad"); // JSON is expected here: keep the old value until it parses
        }
      });
    }
    return h("label", { class: "fl-field" }, h("span", {}, name, meta.required ? " *" : "", h("span", { class: "muted" }, ` ${meta.kind}`)), input);
  }
  function removeSelected() {
    if (!sel) return;
    if (sel.kind === "node") { flow.doc.nodes = flow.doc.nodes.filter((n) => n.id !== sel.id); flow.doc.edges = flow.doc.edges.filter((e) => e.from !== sel.id && e.to !== sel.id); }
    else flow.doc.edges = flow.doc.edges.filter((e) => edgeKey(e) !== sel.id);
    sel = null; markDirty(); drawAll();
  }
  function problemsView() {
    const ps = flow.issues || [];
    return h("div", { class: "fl-body" }, h("button", { type: "button", onclick: () => check().then(drawAll).catch(report) }, "Check again"),
      ...(ps.length ? ps.map((i) => h("p", { class: `fl-issue ${i.level}`, onclick: () => { if (i.node) { sel = { kind: "node", id: i.node }; tab = "props"; drawAll(); } } }, h("b", {}, i.level), i.node ? ` ${i.node}: ` : ": ", i.message)) : [h("p", { class: "muted" }, "No problems found.")]));
  }
  function runsView() {
    const st = flow.stats?.runs ? h("p", { class: "muted" }, `Real runs: ${flow.stats.runs}, ${flow.stats.failed || 0} failed, about ${flow.stats.avg_s}s each, ${flow.stats.tool_calls} tool calls, ${flow.stats.model_calls} model calls.`) : null;
    return h("div", { class: "fl-body" }, st, h("button", { type: "button", onclick: () => loadRuns().then(drawPanel).catch(report) }, "Refresh"),
      ...runs.map((r) => h("button", { type: "button", class: `fl-run ${runDetail?.id === r.id ? "on" : ""}`, onclick: () => showRun(r.id).catch(report) },
        h("span", { class: `fl-led ${r.status}` }), `#${r.id} ${r.mode} v${r.version} · ${r.status}${r.verified ? " ✓" : ""}`, h("span", { class: "muted" }, ` ${r.trigger}`))),
      runDetail ? runDetailView() : null);
  }
  function runDetailView() {
    const d = runDetail;
    return h("div", { class: "fl-detail" }, h("p", {}, d.summary),
      d.diagnosis && d.diagnosis.cause !== "none" ? h("p", { class: "fl-diag" }, h("b", {}, d.diagnosis.cause), ": ", d.diagnosis.advice || d.diagnosis.summary) : null,
      h("table", {}, h("tbody", {}, ...d.steps.map((x) => h("tr", { class: x.status, onclick: () => { sel = { kind: "node", id: x.node }; tab = "props"; drawAll(); } },
        h("td", {}, x.node), h("td", {}, x.status), h("td", { class: "muted" }, x.ms != null ? `${x.ms} ms` : ""), h("td", { class: "error" }, x.error))))),
      d.status === "running" ? h("button", { type: "button", onclick: () => api(`flows/runs/${d.id}/cancel`, { method: "POST" }).then(() => toast("Stopping…")).catch(report) }, "Stop") : null,
      ["interrupted", "failed", "blocked"].includes(d.status) ? h("button", { type: "button", onclick: () => api(`flows/runs/${d.id}/resume`, { method: "POST" }).then(() => { live = { run: d.id, status: {} }; toast("Resuming from the last finished step."); }).catch(report) }, "Resume") : null);
  }
  async function showRun(id) {
    runDetail = await api(`flows/runs/${id}`);
    live = { run: id, status: Object.fromEntries(runDetail.steps.map((x) => [x.node, x.status])) };
    drawAll();
  }
  function versionsView() {
    const vs = flow.versions || [];
    return h("div", { class: "fl-body" }, ...vs.slice().reverse().map((v) => h("div", { class: `fl-ver ${v.status}` },
      h("b", {}, `v${v.version}`), ` ${v.status} · ${v.by || "?"} · ${new Date(v.created * 1000).toLocaleString()}`, h("div", { class: "muted" }, v.note),
      v.version !== flow.version ? h("span", {}, h("button", { type: "button", onclick: () => compare(v.version).catch(report) }, "What changed"), " ",
        h("button", { type: "button", onclick: () => restore(v.version).catch(report) }, "Restore")) : null)), h("div", { id: "fl-diff", class: "muted" }));
  }
  async function compare(v) {
    const r = await api(`flows/${flow.id}/versions?a=${v}`);
    document.getElementById("fl-diff").textContent = `Version ${v} compared with the current one: ${r.summary}`;
  }
  async function restore(v) {
    if (dirty && !confirm("Restoring replaces what you are editing. Continue?")) return;
    await api(`flows/${flow.id}/restore`, { method: "POST", body: { version: v } });
    dirty = false; await open(flow.id); toast(`Version ${v} is back as the newest version.`);
  }
  function assistView() {
    const box = h("textarea", { rows: "4", placeholder: 'e.g. "Make this faster without changing its result", "Find out why it keeps failing and fix it", "Add a notification when the count is over 10"' });
    const out = h("div", { class: "fl-answer muted" });
    const ask = async () => {
      if (!box.value.trim()) return;
      try {
        if (dirty && !(await save())) return;
        if (!flow?.id) return;
        out.textContent = "Working…";
        const r = await api("ask", { method: "POST", body: { text: `The workflow "${flow.doc.name}" (id ${flow.id}) is open in the panel's editor. ${box.value.trim()}` } });
        out.textContent = r.text || r.reply || JSON.stringify(r);
        const keep = tab;
        await open(flow.id); tab = keep; drawAll();
      } catch (e) { out.textContent = ""; report(e); }
    };
    return h("div", { class: "fl-body" }, h("p", { class: "muted" }, "Minty reads and edits this same workflow with its own tools, tests it, and tells you what it changed. Your unsaved changes are saved first."), box,
      h("button", { type: "button", class: "primary", onclick: ask }, "Ask"), out);
  }

  // ---- interaction ---------------------------------------------------------------------------------------------
  const toWorld = (ev) => { const r = svg.getBoundingClientRect(); return [(ev.clientX - r.left - view.x) / view.k, (ev.clientY - r.top - view.y) / view.k]; };
  function nodeDown(ev, n) {
    ev.stopPropagation();
    const t = ev.target;
    if (t.dataset?.out) { connecting = { from: t.dataset.out, port: t.dataset.port }; return; }
    sel = { kind: "node", id: n.id }; tab = "props";
    const [sx, sy] = toWorld(ev), [ox, oy] = n.pos;
    const move = (e) => {
      const [x, y] = toWorld(e);
      n.pos = [Math.round(ox + x - sx), Math.round(oy + y - sy)];
      dirty = true; drawEdges();
      nodesG.querySelector(`[data-id="${n.id}"]`)?.setAttribute("transform", `translate(${n.pos[0]} ${n.pos[1]})`);
      drawMini();
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); drawAll(); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    drawAll();
  }
  svg.addEventListener("mousedown", (ev) => {
    if (ev.target !== svg && ev.target !== bg) return;
    sel = null; drawAll();
    const sx = ev.clientX, sy = ev.clientY, ox = view.x, oy = view.y;
    const move = (e) => { view.x = ox + e.clientX - sx; view.y = oy + e.clientY - sy; apply(); };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
  const dragGhost = (ev) => {
    if (!connecting || !flow) return;
    const a = flow.doc.nodes.find((n) => n.id === connecting.from);
    const [x, y] = toWorld(ev);
    const x1 = a.pos[0] + NODE_W, y1 = a.pos[1] + portY(spec(a.type), connecting.port);
    ghost.setAttribute("d", `M${x1},${y1} C${x1 + 60},${y1} ${x - 60},${y} ${x},${y}`);
  };
  const dropGhost = (ev) => {
    if (!connecting || !flow) return;
    const target = document.elementFromPoint(ev.clientX, ev.clientY);
    const to = target?.dataset?.in || target?.closest?.("[data-id]")?.dataset.id;
    const spTo = to && spec(flow.doc.nodes.find((n) => n.id === to)?.type);
    if (to && to !== connecting.from && spTo && !spTo.trigger && !flow.doc.edges.some((e) => e.from === connecting.from && e.port === connecting.port && e.to === to)) {
      flow.doc.edges.push({ from: connecting.from, port: connecting.port, to });
      markDirty();
    }
    connecting = null; ghost.setAttribute("d", ""); drawAll();
  };
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const k = Math.min(2.5, Math.max(0.25, view.k * (ev.deltaY < 0 ? 1.1 : 0.9)));
    view.x = mx - ((mx - view.x) / view.k) * k; view.y = my - ((my - view.y) / view.k) * k; view.k = k; apply();
  }, { passive: false });
  const keys = (ev) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s" && flow) { ev.preventDefault(); save().catch(report); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && sel && !typing) { ev.preventDefault(); removeSelected(); }
    else if (ev.key === "Escape" && flow) { sel = null; drawAll(); }
  };
  window.addEventListener("mousemove", dragGhost);
  window.addEventListener("mouseup", dropGhost);
  window.addEventListener("keydown", keys);

  // live status from the events socket: the run you are looking at lights up node by node
  const off = events.on((e) => {
    if (e.type !== "flow" || !flow || e.flow !== flow.id) return;
    if (e.run === live.run || live.run == null) {
      live.run = e.run;
      if (e.node) live.status[e.node] = e.status;
      status.textContent = e.node ? `${e.node}: ${e.status}` : `run ${e.status}${e.verified ? " and verified" : ""}`;
      drawNodes(); drawEdges(); drawMini();
    }
    if (!e.node) {
      loadRuns().then(async () => { if (live.run === e.run) runDetail = await api(`flows/runs/${e.run}`); if (tab === "runs") drawPanel(); refreshList(); }).catch(() => {});
    }
  });
  const resize = new ResizeObserver(() => { if (!fitted && flow && svg.getBoundingClientRect().width > 120) { fitted = true; fit(); } });
  resize.observe(stage);
  const observer = new MutationObserver(() => {
    if (document.body.contains(svg)) return;
    off(); observer.disconnect(); resize.disconnect();
    window.removeEventListener("mousemove", dragGhost); window.removeEventListener("mouseup", dropGhost); window.removeEventListener("keydown", keys);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // ---- start ---------------------------------------------------------------------------------------------------
  [catalogue, list] = await Promise.all([api("flows/catalogue"), api("flows")]);
  side.append(h("h3", {}, "Workflows"), listBox, h("h3", {}, "Add a node"), search, paletteBox);
  main.replaceChildren(h("div", { class: "fl-wrap" }, side, h("div", { class: "fl-center" }, bar, stage), panel));
  drawList(); drawPalette();
  const wanted = (location.hash.split(":")[1] || "").trim();
  if (wanted) await open(wanted).catch(report);
  else if (list.length) await open(list[0].id).catch(report);
  else drawEmpty();
}
