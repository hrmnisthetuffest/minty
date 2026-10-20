import { action, api, encode, field, h, report, select } from "../lib.js";

const NAME = /^[a-z0-9][a-z0-9_-]{0,39}$/;

export default function personality(main) {
  return show(main);
}

// `chosen` pre-selects a personality in the file picker only (used right after creating one);
// the Active select always shows the saved active personality.
async function show(main, chosen) {
  const [cfg, names] = await Promise.all([api("config"), api("personalities")]);

  const create = action("New…", async () => {
    const name = prompt("Name for the new personality (a-z, 0-9, - or _):");
    if (!name) return;
    if (!NAME.test(name)) throw new Error("Use lowercase letters, digits, - or _ (max 40).");
    if (names.includes(name)) throw new Error(`A personality named "${name}" already exists.`);
    await api("personalities/" + encode(name), { method: "PUT", body: { text: "You are Minty, a helpful voice assistant." } });
    await show(main, name);
  });

  if (!names.length) {
    main.replaceChildren(h("h2", {}, "Personality"),
      h("div", { class: "card" }, h("p", { class: "muted" }, "No personalities yet. Create one to get started."), create));
    return;
  }

  // The saved active personality may have been deleted behind our back: fall back to the first one.
  let savedActive = cfg.personality.active;
  const activeStart = names.includes(savedActive) ? savedActive : names[0];
  const active = select(names, activeStart);
  const sliders = {};
  for (const key of ["formality", "humor", "verbosity"]) {
    sliders[key] = h("input", { type: "range", min: 0, max: 10, value: cfg.personality[key] });
  }
  const picker = select(names, names.includes(chosen) ? chosen : activeStart);
  const text = h("textarea", { rows: 12 });

  // loadedName is the personality whose text is in the textarea; null while loading or after a failed load.
  let loadedName = null;
  let requests = 0;
  const saveText = action("Save text", async () => {
    if (!loadedName) throw new Error("The personality text is not loaded. Pick it again.");
    await api("personalities/" + encode(loadedName), { method: "PUT", body: { text: text.value } });
    return "Saved";
  }, "primary");
  const load = async () => {
    const mine = ++requests;
    const name = picker.value;
    loadedName = null;
    saveText.disabled = true;
    text.value = "";
    try {
      const data = await api("personalities/" + encode(name));
      if (mine !== requests) return;
      text.value = data.text;
      loadedName = name;
      saveText.disabled = false;
    } catch (e) {
      if (mine === requests) report(e);
    }
  };
  picker.addEventListener("change", load);

  main.replaceChildren(
    h("h2", {}, "Personality"),
    h("div", { class: "card" },
      field("Active personality", active),
      field("Formality (casual → formal)", sliders.formality),
      field("Humor (none → witty)", sliders.humor),
      field("Verbosity (terse → detailed)", sliders.verbosity),
      action("Save", async () => {
        const fresh = await api("config");
        fresh.personality.active = active.value;
        for (const key in sliders) fresh.personality[key] = Number(sliders[key].value);
        await api("config", { method: "PUT", body: fresh });
        savedActive = active.value;
        return "Saved. Applies to the next thing Minty says.";
      }, "primary")),
    h("h3", {}, "Edit personality text"),
    h("div", { class: "card" }, field("Personality file", picker), text,
      h("div", { class: "row", style: "margin-top:10px" },
        saveText,
        create,
        action("Delete", async () => {
          const name = picker.value;
          if (name === savedActive || name === active.value) {
            throw new Error("Switch the active personality first, then delete this one.");
          }
          if (!confirm(`Delete the "${name}" personality?`)) return;
          await api("personalities/" + encode(name), { method: "DELETE" });
          await show(main);
        }, "danger"))),
  );
  await load();
}
