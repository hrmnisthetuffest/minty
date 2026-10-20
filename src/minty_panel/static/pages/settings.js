import { SECTIONS } from "../settings-schema.js";
import { action, api, checkbox, field, h, numberInput, pageHead, select, textInput } from "../lib.js";

// One row of the settings page: how to draw it and how to read it back.
function build(cfg, [section, key, label, kind, help]) {
  const value = (section ? cfg[section] : cfg)[key];
  if (kind === "bool") {
    const box = checkbox(value);
    return { input: box, read: () => box.checked, el: h("label", { class: "field check" }, box, h("span", {}, label), help ? h("small", {}, help) : null) };
  }
  if (kind.startsWith("select:")) {
    const input = select(kind.slice(7).split(","), value);
    return { input, read: () => input.value, el: field(label, input, help) };
  }
  if (kind.startsWith("number:")) {
    const input = numberInput(value, { step: kind.slice(7) });
    return {
      input,
      read: () => {
        const raw = String(input.value).trim();
        const number = Number(raw);
        // Number("") is 0: a blank field must not silently become a zero threshold.
        if (raw === "" || !Number.isFinite(number)) throw new Error(`${label} must be a number.`);
        return number;
      },
      el: field(label, input, help),
    };
  }
  const input = textInput(value ?? "");
  return { input, read: () => (kind === "nullable" && !input.value.trim() ? null : input.value.trim()), el: field(label, input, help) };
}

// `focus` is "section.key" (from the search bar): that row is scrolled into view and flashes.
export default async function settings(main, events, focus = "") {
  const cfg = await api("config");
  const rows = [];
  const status = h("span", { class: "state" }, "No changes");
  const markDirty = () => { status.textContent = "Unsaved changes"; status.className = "state dirty"; };

  const panels = SECTIONS.map((sec, n) => {
    const items = sec.rows.map((f) => {
      const row = { f, sec, ...build(cfg, f) };
      row.text = `${f[2]} ${f[4]} ${f[1]} ${sec.title}`.toLowerCase();
      row.input.addEventListener("change", markDirty);
      row.input.addEventListener("input", markDirty);
      rows.push(row);
      return row.el;
    });
    return {
      sec,
      el: h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("span", { class: "num" }, String(n + 1).padStart(2, "0")), h("h3", {}, sec.title), h("p", {}, sec.blurb)),
        h("div", { class: "setting-list" }, items)),
    };
  });

  const search = h("input", { type: "search", class: "settings-search", placeholder: "Filter settings", autocomplete: "off" });
  search.addEventListener("input", () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const row of rows) row.el.hidden = !words.every((w) => row.text.includes(w));
    for (const p of panels) p.el.hidden = rows.filter((r) => r.sec === p.sec).every((r) => r.el.hidden);
  });

  const index = h("div", { class: "settings-index" },
    SECTIONS.map((sec, n) => h("button", { type: "button", class: "link", onclick: () => {
      const target = panels[n].el;
      if (target.scrollIntoView) target.scrollIntoView({ behavior: "smooth", block: "start" });
    } }, sec.title)));

  const save = action("Save", async () => {
    const values = rows.map((r) => r.read());
    // Merge into the live config so settings changed elsewhere since this page loaded are kept.
    const fresh = await api("config");
    rows.forEach(({ f }, i) => { (f[0] ? fresh[f[0]] : fresh)[f[1]] = values[i]; });
    await api("config", { method: "PUT", body: fresh });
    status.textContent = "Saved";
    status.className = "state";
    return "Saved. Wake word and microphone changes reload the audio pipeline.";
  }, "primary");

  main.replaceChildren(
    pageHead("Control panel", "Settings", "Everything that can be set, in one place. Press Ctrl+K (or Cmd+K) to jump to any setting."),
    h("div", { class: "settings-layout" },
      index,
      h("div", {},
        search,
        panels.map((p) => p.el),
        h("p", { class: "muted" }, "Plugin settings (the browser, weather, Spotify and others) are on the ", h("a", { href: "#plugins" }, "Plugins"),
          " page; on/off switches for the rest of the core are on ", h("a", { href: "#flags" }, "Switches"), "."),
        h("div", { class: "savebar" }, save,
          action("Test voice", async () => { await api("say", { method: "POST", body: { text: "Minty online." } }); }),
          h("span", { class: "spacer" }), status))),
  );

  const target = rows.find((r) => `${r.f[0]}.${r.f[1]}` === focus);
  if (target) {
    if (target.el.scrollIntoView) target.el.scrollIntoView({ block: "center" });
    if (target.el.classList) target.el.classList.add("flash");
  }
}
