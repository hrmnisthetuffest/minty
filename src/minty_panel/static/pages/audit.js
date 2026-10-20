import { action, api, encode, h, textInput } from "../lib.js";

export default async function audit(main) {
  const list = h("div");
  const q = textInput("", { placeholder: "Search what I know…" });

  async function draw() {
    const { entries } = await api(`audit?query=${encode(q.value)}`);
    list.replaceChildren(...(entries.length
      ? entries.map((e) => {
          const text = textInput(e.text);
          return h("div", { class: "card" },
            h("div", { class: "row" }, text,
              action("Correct", async () => {
                await api(`audit/${encode(e.id)}`, { method: "PUT", body: { text: text.value } });
                await draw();
                return "Corrected.";
              }, "primary"),
              action("Forget", async () => {
                if (!confirm(`Forget this?\n\n${e.text}`)) return;
                await api(`audit/${encode(e.id)}`, { method: "DELETE" });
                await draw();
                return "Forgotten.";
              }, "danger")),
            h("p", { class: "muted" }, `${e.source}${e.date ? " · " + e.date : ""} · ${e.confidence} · ${e.store}`));
        })
      : [h("p", { class: "muted" }, "Nothing matches.")]));
  }

  main.replaceChildren(
    h("h2", {}, "What I know about you"),
    h("p", { class: "muted" }, "Every remembered fact with where it came from. Correct it or make me forget it."),
    h("div", { class: "row" }, q, action("Search", draw, "primary")),
    list);
  await draw();
}
