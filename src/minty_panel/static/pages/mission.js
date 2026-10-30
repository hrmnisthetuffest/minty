import { api, h, report, toast } from "../lib.js";

// Mission Control: what needs you first, then the missions with what would prove each one done (an outcome contract), what else is running, the workflows
// and how their last runs went, recent outcomes and decisions. Every line is read from a job, a run, the watchdog or a store; there are no made-up percentages.
const STATE = { verified: "✓", "accepted unchecked": "~", "to do": "○" };

function ago(seconds) {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export default async function mission(main, events) {
  const box = h("div", { class: "mc" });
  main.replaceChildren(h("h2", {}, "Mission Control"), box);

  async function stop(id) {
    if (!confirm("Stop this? Whatever it already did stays done.")) return;
    try { await api(`jobs/${id}/cancel`, { method: "POST" }); toast("Stopping…"); } catch (e) { report(e); }
    load();
  }

  function needs(list) {
    return h("section", { class: "mc-card mc-needs" }, h("h3", {}, list.length ? `Needs you (${list.length})` : "Nothing needs you"),
      ...list.map((n) => h("p", { class: "mc-need" }, h("b", {}, n.kind), " ", n.text, n.verified === false ? h("span", { class: "muted" }, " (a hypothesis)") : null,
        n.flow ? h("a", { href: `#flows:${n.flow}` }, " open the workflow") : null)));
  }

  function missionCard(m) {
    const progress = m.total ? `${m.verified} of ${m.total} verified${m.unchecked ? `, ${m.unchecked} accepted without a check` : ""}.` : "It has not planned yet.";
    return h("div", { class: `mc-mission ${m.active ? "on" : ""} ${m.complete ? "done" : ""}` },
      h("div", { class: "mc-head" }, h("b", {}, m.label), h("span", { class: `mc-pill ${m.health}` }, m.active ? m.health : m.status), m.origin === "telegram" ? h("span", { class: "muted" }, " from Telegram") : null,
        m.active ? h("button", { type: "button", onclick: () => stop(m.job) }, "Stop") : null),
      h("p", {}, m.objective),
      h("p", { class: "muted" }, `Done when: ${m.completion}. ${progress}`),
      ...m.criteria.map((c) => h("div", { class: `mc-crit ${c.state.replace(/\s+/g, "-")}`, title: c.evidence || "no evidence yet" }, h("span", {}, STATE[c.state] || "○"), " ", c.step,
        c.attempts > 1 ? h("span", { class: "muted" }, ` (attempt ${c.attempts})`) : null)),
      m.blocker ? h("p", { class: "mc-block" }, `Blocked: ${m.blocker}`) : null,
      m.active && m.doing ? h("p", { class: "muted" }, `Now: ${m.doing}`) : null,
      m.next && m.active ? h("p", { class: "muted" }, `Next: ${m.next}`) : null,
      m.result ? h("p", { class: "muted" }, m.result) : null);
  }

  function render(o) {
    const flowRows = o.flows.map((f) => h("tr", {}, h("td", {}, h("a", { href: `#flows:${f.id}` }, f.name)), h("td", {}, f.enabled ? "on" : "off"),
      h("td", { class: "muted" }, `v${f.version} · ${f.creator === "user" ? "you" : f.creator}`),
      h("td", { class: f.last && ["failed", "blocked"].includes(f.last.status) ? "error" : "" }, f.last ? `${f.last.mode} run ${f.last.status}${f.last.verified ? " ✓" : ""}` : "never run")));
    const running = o.running.length ? h("section", { class: "mc-card" }, h("h3", {}, "Running now"), ...o.running.map((r) => h("div", { class: "mc-run" }, h("b", {}, r.label), ` ${r.goal} `,
      h("span", { class: `mc-pill ${r.health}` }, r.status === "running" ? r.health : r.status.replace("_", " ")), h("span", { class: "muted" }, ` ${ago(r.seconds)}${r.doing ? ` · ${r.doing}` : ""} `),
      h("button", { type: "button", onclick: () => stop(r.id) }, "Stop")))) : null;
    const decisions = o.decisions.length ? h("section", { class: "mc-card" }, h("h3", {}, "Latest decisions"),
      ...o.decisions.map((d) => h("p", {}, h("b", {}, `${d.date} ${d.kind}:`), ` ${d.title}`, d.rationale ? h("span", { class: "muted" }, ` — ${d.rationale}`) : null))) : null;
    box.replaceChildren(...[
      needs(o.needs_you),
      h("section", { class: "mc-card" }, h("h3", {}, "Missions"), ...(o.missions.length ? o.missions.map(missionCard) : [h("p", { class: "muted" }, "No missions yet.")])),
      running,
      h("section", { class: "mc-card" }, h("h3", {}, "Workflows"), o.flows.length ? h("table", {}, h("tbody", {}, ...flowRows)) : h("p", { class: "muted" }, "No workflows yet. Draw one on the Automations page, or ask Minty.")),
      h("section", { class: "mc-card" }, h("h3", {}, "Recent outcomes"), ...(o.recent.length ? o.recent.map((r) => h("p", {}, h("b", {}, r.label), ` ${r.status}: ${r.result || r.goal}`)) : [h("p", { class: "muted" }, "Nothing has finished yet.")])),
      decisions,
    ].filter(Boolean));
  }

  async function load() {
    try { render(await api("control/overview")); } catch (e) { box.replaceChildren(h("p", { class: "error" }, e.message)); }
  }

  await load();
  const timer = setInterval(() => { if (document.body.contains(box)) load(); else { clearInterval(timer); off(); } }, 10000);
  const off = events.on((e) => {
    if (document.body.contains(box) && ["job", "flow", "notify"].includes(e.type)) { clearTimeout(box._t); box._t = setTimeout(load, 700); }
  });
}
