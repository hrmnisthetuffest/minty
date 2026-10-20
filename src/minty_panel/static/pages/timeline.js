import { action, api, h } from "../lib.js";

export default async function timeline(main) {
  const box = h("div");

  function item(r) {
    const when = new Date(r.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return h("div", { class: `card tl ${r.kind} ${r.status}` },
      h("div", { class: "row" }, h("strong", {}, r.title), h("span", { class: "spacer" }),
        h("span", { class: "muted" }, `${when} · ${r.kind === "job" ? "job" : "action"} · ${r.status}`)),
      r.detail ? h("p", { class: "muted" }, r.detail) : "");
  }

  async function draw() {
    const rows = await api("timeline");
    const days = new Map();
    for (const r of rows) {
      const day = new Date(r.ts * 1000).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(r);
    }
    box.replaceChildren(...(rows.length
      ? [...days].map(([day, items]) => h("section", {}, h("h3", {}, day), ...items.map(item)))
      : [h("p", { class: "muted" }, "Nothing has happened yet.")]));
  }

  main.replaceChildren(
    h("h2", {}, "Timeline"),
    h("p", { class: "muted" }, "Jobs I ran and actions I took, newest first."),
    h("div", { class: "row" }, action("Refresh", draw, "primary")),
    box);
  await draw();
}
