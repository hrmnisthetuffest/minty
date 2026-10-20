import { action, api, encode, field, h, select, textInput } from "../lib.js";

const FOLDERS = ["inbox", "notes", "projects", "areas", "people"];

export default async function notes(main) {
  const results = h("div");
  const viewer = h("div");
  const lists = h("div");
  let current = null;

  const tagsOf = (text) => text.split(",").map((t) => t.trim()).filter(Boolean);

  function row(note, extra) {
    return h("div", { class: "card row" },
      h("a", { href: "#notes", onclick: (e) => { e.preventDefault(); open(note.id); } }, note.title || note.id),
      h("span", { class: "muted" }, " " + (note.badge || "") + (note.warning ? "  ⚠ " + note.warning : "")),
      h("div", { class: "spacer" }), extra || null);
  }

  async function open(id) {
    const n = await api(`vault/notes/${encode(id)}`);
    current = n;
    const body = h("textarea", { rows: "14", value: n.body });
    const tags = textInput(n.tags.join(", "));
    viewer.replaceChildren(
      h("h3", {}, n.title),
      h("p", { class: "muted" }, `${n.badge}  ·  ${n.path}`),
      n.warning ? h("p", { class: "error" }, n.warning) : null,
      field("Text", body),
      field("Tags (comma separated)", tags),
      h("div", { class: "row" },
        action("Save", async () => {
          const saved = await api(`vault/notes/${encode(n.id)}`, { method: "PUT", body: { text: body.value, tags: tagsOf(tags.value) } });
          current = saved;
          await draw();
          return "Saved.";
        }, "primary"),
        action("Archive", async () => {
          if (!confirm(`Move “${n.title}” to the archive? It is kept, not deleted.`)) return;
          await api(`vault/notes/${encode(n.id)}/archive`, { method: "POST" });
          viewer.replaceChildren();
          await draw();
          return "Archived.";
        }, "danger")),
      n.backlinks.length
        ? h("div", {}, h("h4", {}, "Linked from"), ...n.backlinks.map((b) =>
            h("div", {}, h("a", { href: "#notes", onclick: (e) => { e.preventDefault(); open(b.id); } }, b.title))))
        : h("p", { class: "muted" }, "Nothing links here yet."));
  }

  async function search(q) {
    const { hits } = await api(`vault/search?q=${encode(q)}`);
    results.replaceChildren(...(hits.length
      ? hits.map((x) => h("div", { class: "card" },
          h("div", {}, x.kind === "note" ? row(x) : h("strong", {}, x.badge)),
          h("p", { class: "muted" }, x.snippet), x.warning ? h("p", { class: "error" }, x.warning) : null))
      : [h("p", { class: "muted" }, "Nothing matches that in your notes or past conversations.")]));
  }

  async function draw() {
    const data = await api("vault");
    const s = data.status;
    lists.replaceChildren(
      h("p", { class: "muted" }, `${s.notes} notes, ${s.inbox} in the inbox · versioning: ${s.versioning}`),
      data.proposals.length ? h("h3", {}, "Filing suggestions") : null,
      ...data.proposals.map((p) => h("div", { class: "card row" },
        h("span", {}, `“${p.title}” → ${p.folder}` + (p.project ? ` (project ${p.project})` : "") + (p.tags.length ? ` [${p.tags.join(", ")}]` : "")),
        h("div", { class: "spacer" }),
        action("Approve", async () => { await api(`vault/inbox/${encode(p.note_id)}/approve`, { method: "POST" }); await draw(); return "Filed."; }, "primary"),
        action("Not now", async () => { await api(`vault/inbox/${encode(p.note_id)}/reject`, { method: "POST" }); await draw(); }))),
      h("h3", {}, "Recent"),
      ...data.recent.map((n) => row(n)));
  }

  const q = textInput("", { placeholder: "Where did I see…" });
  const title = textInput("", { placeholder: "Title" });
  const text = h("textarea", { rows: "4", placeholder: "Text" });
  const folder = select(FOLDERS, "inbox");

  main.replaceChildren(
    h("h2", {}, "Notes"),
    h("p", { class: "muted" }, "Your notes folder. Plain Markdown files; Minty never deletes one."),
    h("div", { class: "row" }, q, action("Search", () => search(q.value), "primary")),
    results,
    h("h3", {}, "New note"),
    field("Title", title), field("Text", text), field("Folder", folder),
    action("Save note", async () => {
      const made = await api("vault/notes", { method: "POST", body: { title: title.value, text: text.value, folder: folder.value } });
      title.value = ""; text.value = "";
      await draw();
      await open(made.id);
      return "Saved.";
    }, "primary"),
    viewer, lists);
  await draw();
}
