import { action, api, h, pageHead, textInput, toast } from "../lib.js";

const LIST_LIMIT = 150;
const STATES = [["all", "All"], ["answered", "Answered"], ["unanswered", "Not answered"], ["skipped", "Skipped"]];

// 500 fixed questions about you. One at a time (Interview) or all together (All questions). Answers stay on this computer.
export default async function profile(main) {
  const data = await api("profile");
  const questions = data.questions;
  const byId = new Map(questions.map((q) => [q.id, q]));
  const titleOf = new Map(data.categories.map((c) => [c.id, c.title]));
  const state = { mode: "interview", category: "all", current: null, search: "", status: "all", draft: "" };
  let progress = data.progress;

  const pool = () => questions.filter((q) => state.category === "all" || q.category === state.category);
  // The next question still to answer after `after` (wrapping round); ones you skipped come last.
  function nextOpen(after) {
    const ids = pool().map((q) => q.id);
    const order = [...ids.filter((i) => after == null || i > after), ...ids.filter((i) => after != null && i <= after)];
    for (const skipped of [false, true]) {
      const found = order.find((i) => !byId.get(i).answer && byId.get(i).skipped === skipped);
      if (found != null) return found;
    }
    return null;
  }
  state.current = nextOpen(null);

  const body = h("div");

  function countOf(id) {
    const group = questions.filter((q) => id === "all" || q.category === id);
    return `${group.filter((q) => q.answer).length}/${group.length}`;
  }

  function header() {
    const picker = h("select", {}, [["all", `All categories (${countOf("all")})`],
      ...data.categories.map((c) => [c.id, `${c.title} (${countOf(c.id)})`])].map(([value, label]) => h("option", { value }, label)));
    picker.value = state.category;
    picker.addEventListener("change", () => {
      state.category = picker.value;
      state.current = nextOpen(null);
      state.draft = "";
      draw();
    });
    const mode = (key, label) => h("button", { type: "button", class: state.mode === key ? "primary" : "", onclick: () => { state.mode = key; draw(); } }, label);
    const fill = h("div", { class: "bar-fill" });
    fill.setAttribute("style", `width:${(progress.answered / progress.total) * 100}%`);
    return h("div", {},
      h("div", { class: "card profile-count" },
        h("div", { class: "row" }, h("span", { class: "big-number" }, `${progress.answered} / ${progress.total}`),
          h("span", { class: "muted" }, progress.skipped ? `answered, ${progress.skipped} skipped` : "answered")),
        h("div", { class: "bar-track" }, fill)),
      h("div", { class: "row" }, mode("interview", "Interview"), mode("list", "All questions"), picker));
  }

  function interview() {
    const q = state.current == null ? null : byId.get(state.current);
    if (!q) {
      return h("div", { class: "card" }, h("p", { class: "lead" }, "Nothing left to answer here. Pick another category, or open All questions to change an answer."));
    }
    const box = h("textarea", { rows: 4, placeholder: "Your answer. Short is fine. Leave it if you would rather not say.", maxlength: 600 });
    box.value = state.draft || q.answer || "";
    box.addEventListener("input", () => { state.draft = box.value; });

    const go = (id) => { state.current = id; state.draft = ""; draw(); };
    const save = action("Save & next", async () => {
      const text = box.value.trim();
      if (!text) throw new Error("Write an answer first, or press Skip.");
      const out = await api(`profile/${q.id}`, { method: "PUT", body: { text } });
      q.answer = out.answer;
      q.skipped = false;
      progress = out.progress;
      go(nextOpen(q.id));
    }, "primary");
    box.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) save.click(); });

    const skip = action("Skip", async () => {
      if (!q.answer) {
        const out = await api(`profile/${q.id}/skip`, { method: "POST" });
        q.skipped = true;
        progress = out.progress;
      }
      go(nextOpen(q.id));
    });
    const back = action("Back", async () => {
      const ids = pool().map((x) => x.id);
      const before = ids.filter((i) => i < q.id);
      go(before.length ? before[before.length - 1] : ids[ids.length - 1]);
    });
    const clear = q.answer ? action("Clear answer", async () => {
      await api(`profile/${q.id}`, { method: "DELETE" });
      q.answer = "";
      progress = { ...progress, answered: progress.answered - 1 };
      state.draft = "";
      draw();
    }, "danger") : null;

    return h("div", { class: "card question-card" },
      h("p", { class: "kicker" }, `Question ${q.id} of ${progress.total} · ${titleOf.get(q.category)}`),
      h("h3", { class: "question" }, q.text),
      box,
      h("div", { class: "row gap-top" }, save, skip, back, h("span", { class: "spacer" }), clear),
      h("p", { class: "muted" }, "Ctrl+Enter saves. Nothing here is sent anywhere unless a request is related to it."));
  }

  function list() {
    const search = h("input", { type: "search", placeholder: "Filter questions and answers", autocomplete: "off" });
    search.value = state.search;
    const status = h("select", {}, STATES.map(([value, label]) => h("option", { value }, label)));
    status.value = state.status;
    const rows = h("div");
    function fill() {
      const words = state.search.toLowerCase().split(/\s+/).filter(Boolean);
      const shown = pool().filter((q) => {
        if (state.status === "answered" && !q.answer) return false;
        if (state.status === "unanswered" && (q.answer || q.skipped)) return false;
        if (state.status === "skipped" && !q.skipped) return false;
        const text = `${q.text} ${q.answer}`.toLowerCase();
        return words.every((w) => text.includes(w));
      });
      const items = [
        ...shown.slice(0, LIST_LIMIT).map((q) => h("div", { class: "qa" },
          h("div", { class: "qa-text" },
            h("span", { class: "muted" }, `${q.id}. `), q.text,
            h("div", { class: q.answer ? "qa-answer" : "muted" }, q.answer || (q.skipped ? "Skipped" : "—"))),
          h("button", { type: "button", onclick: () => { state.mode = "interview"; state.current = q.id; state.draft = ""; draw(); } }, "Edit"))),
        shown.length > LIST_LIMIT ? h("p", { class: "muted" }, `Showing the first ${LIST_LIMIT} of ${shown.length}. Use the filter to narrow it.`) : null,
        shown.length ? null : h("p", { class: "muted" }, "Nothing matches."),
      ];
      rows.replaceChildren(...items.filter(Boolean));
    }
    search.addEventListener("input", () => { state.search = search.value; fill(); });
    status.addEventListener("change", () => { state.status = status.value; fill(); });
    fill();
    return h("div", {}, h("div", { class: "row" }, search, status), h("div", { class: "card" }, rows));
  }

  function draw() {
    body.replaceChildren(header(), state.mode === "interview" ? interview() : list());
  }
  draw();

  main.replaceChildren(
    pageHead("Personalisation", "Profile",
      "Five hundred fixed questions about you. Answer as many as you like, in any order; every one can be skipped. Related answers are added to what Minty is given with a request, so it knows you."),
    body,
    h("p", { class: "muted" }, "Your answers are kept in one private file on this computer. ",
      h("a", { href: "/api/profile/export", download: "minty-profile.md" }, "Download them"),
      ". Turn the sharing with the model off with ", h("a", { href: "#settings:profile.enabled" }, "Settings"), "."),
  );
}
