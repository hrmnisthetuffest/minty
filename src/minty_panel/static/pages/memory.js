import { action, api, h } from "../lib.js";

export default async function memory(main) {
  const body = h("div");

  function noteRow(note, index) {
    return h("div", { class: "card row" },
      h("span", {}, note), h("div", { class: "spacer" }),
      action("Forget", async () => {
        if (!confirm(`Forget this note?\n\n${note}`)) return;
        // Notes are deleted by position; if the list changed since it was drawn, a stale
        // index would remove the wrong note. Check, and redraw instead when it moved.
        const { notes } = await api("memory");
        if (notes[index] !== note) {
          await draw();
          throw new Error("The memory list changed in the meantime. It was refreshed; try again.");
        }
        await api(`memory/${index}`, { method: "DELETE" });
        await draw();
        return "Forgotten.";
      }, "danger"));
  }

  async function draw() {
    const { notes } = await api("memory");
    body.replaceChildren(...(notes.length
      ? notes.map(noteRow)
      : [h("p", { class: "muted" }, "Nothing remembered yet. Say “Minty, remember that …”.")]));
  }

  main.replaceChildren(h("h2", {}, "Memory"), h("p", { class: "muted" }, "Notes Minty keeps about you and adds to every conversation."), body);
  await draw();
}
