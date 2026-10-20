import { api, checkbox, h, toast } from "../lib.js";

// Every on/off setting of the core, grouped, each a switch that saves at once.
export default async function flags(main) {
  const body = h("div");
  main.replaceChildren(
    h("h2", {}, "Switches"),
    h("p", { class: "muted" }, "Every on/off setting in one place. A change is saved immediately."),
    body,
  );
  const list = await api("flags");
  const groups = new Map();
  for (const f of list) {
    if (!groups.has(f.group)) groups.set(f.group, []);
    groups.get(f.group).push(f);
  }
  for (const [group, items] of groups) {
    body.append(h("h3", {}, group.replace(/_/g, " ")));
    for (const f of items) {
      const box = checkbox(f.value);
      box.addEventListener("change", async () => {
        try {
          const out = await api("flags", { method: "POST", body: { path: f.path, value: box.checked } });
          toast(`${f.label}: ${out.value ? "on" : "off"}`);
        } catch (e) {
          box.checked = !box.checked;
          toast(String(e.message || e), true);
        }
      });
      body.append(h("label", { class: "card row" }, box, h("span", {}, f.label)));
    }
  }
}
