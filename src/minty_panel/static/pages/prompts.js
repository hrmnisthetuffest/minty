import { action, api, encode, h } from "../lib.js";

export default async function prompts(main) {
  const instructions = h("textarea", { rows: "7", placeholder: "e.g. Keep answers short. Call me Boss. Never mention the weather unless I ask." });
  const learned = h("div");
  const commands = h("div");

  async function draw() {
    const data = await api("prompts");
    instructions.value = data.instructions;
    learned.replaceChildren(...(data.learned.length
      ? data.learned.map((r) => h("div", { class: "card row" },
        h("span", {}, r.text), h("div", { class: "spacer" }),
        action("Forget", async () => {
          await api(`prompts/learned/${encode(r.id)}`, { method: "DELETE" });
          await draw();
          return "Forgotten.";
        }, "danger")))
      : [h("p", { class: "muted" }, "Nothing learned yet. Minty adds things here as it gets to know you.")]));
    commands.replaceChildren(...(data.commands.length
      ? data.commands.map((c) => h("div", { class: "card row" },
        h("span", {}, `“${c.phrase}” → ${c.action}`), h("div", { class: "spacer" }),
        action("Delete", async () => {
          await api(`prompts/commands?phrase=${encode(c.phrase)}`, { method: "DELETE" });
          await draw();
          return "Deleted.";
        }, "danger")))
      : [h("p", { class: "muted" }, "No custom commands. Say “Minty, when I say good morning, read me my day”.")]));
  }

  main.replaceChildren(
    h("h2", {}, "Prompts"),
    h("p", { class: "muted" }, "Three layers shape how Minty behaves: the fixed personality (Personality page), your own standing instructions, and what Minty learns about you."),
    h("h3", {}, "Your standing instructions"),
    h("div", { class: "card" }, instructions,
      h("div", { class: "row gap-top" }, action("Save", async () => {
        await api("prompts/instructions", { method: "PUT", body: { text: instructions.value } });
        await draw();
        return "Saved.";
      }))),
    h("h3", {}, "Learned about you"),
    learned,
    h("h3", {}, "Custom commands"),
    commands,
  );
  await draw();
}
