import { action, api, checkbox, encode, field, h, select, textInput, toast } from "../lib.js";

const NAME = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const FLAGS = [["enabled", "Enabled"], ["supports_tools", "Supports tools"], ["requires_key", "Needs key"],
  ["trains_on_data", "Trains on my prompts"], ["allow_sensitive", "Allow private requests"]];
const TIERS = ["fast", "deep"];
const FREE_TIERS = [
  ["Groq", "https://console.groq.com"], ["Cerebras", "https://cloud.cerebras.ai"],
  ["Google AI Studio (Gemini)", "https://aistudio.google.com"], ["OpenRouter", "https://openrouter.ai"],
  ["Mistral", "https://console.mistral.ai"], ["NVIDIA NIM", "https://build.nvidia.com"],
];

// The Anthropic API has a built-in address, so only OpenAI-compatible providers need a base URL.
function validate(p) {
  if (!p.model) throw new Error(`Provider ${p.name}: model must not be blank.`);
  if (p.kind === "openai_compat" && !p.base_url) throw new Error(`Provider ${p.name}: base URL must not be blank.`);
  if (p.key_name && !NAME.test(p.key_name)) {
    throw new Error(`Provider ${p.name}: shared key name must use lowercase letters, digits, - or _ (max 40), or be blank.`);
  }
}

export default async function brain(main) {
  const [cfg, keys] = await Promise.all([api("config"), api("secrets")]);
  const list = h("div");
  const providers = cfg.providers;
  const syncers = [];
  const syncAll = () => syncers.forEach((sync) => sync());
  const maskers = []; // one per card: repaint its key placeholder after any key changed
  const showMasks = () => maskers.forEach((show) => show());
  // Show the core's masked values, never what we sent.
  const refreshKeys = async () => {
    const latest = await api("secrets");
    for (const name of Object.keys(keys)) delete keys[name];
    Object.assign(keys, latest);
  };

  function card(p, index) {
    const flags = {};
    for (const [key] of FLAGS) flags[key] = checkbox(p[key]);
    const kind = select(["openai_compat", "anthropic"], p.kind);
    const baseUrl = textInput(p.base_url);
    const model = textInput(p.model);
    const tier = select(TIERS, p.tier ?? "fast");
    const keyName = textInput(p.key_name ?? "");
    // The secret this card reads and writes: another provider's key when key_name is set, else its own.
    const secret = () => keyName.value.trim() || p.name;
    const mask = () => (keys[secret()] ? `stored: ${keys[secret()]}` : "paste API key");
    const keyInput = h("input", { type: "password", autocomplete: "new-password", placeholder: mask() });
    const shared = h("small", {});
    const showKey = () => {
      keyInput.placeholder = mask();
      shared.replaceChildren(keyName.value.trim() ? `Uses the ${keyName.value.trim()} key` : "");
    };
    showKey();
    maskers.push(showKey);
    keyName.addEventListener("input", showKey);
    const checkedSecret = () => {
      const name = secret();
      if (!NAME.test(name)) throw new Error(`Provider ${p.name}: shared key name must use lowercase letters, digits, - or _ (max 40), or be blank.`);
      return name;
    };
    const sync = () => {
      for (const key in flags) p[key] = flags[key].checked;
      p.kind = kind.value; p.base_url = baseUrl.value.trim(); p.model = model.value.trim();
      p.tier = tier.value; p.key_name = keyName.value.trim();
    };
    syncers.push(sync);
    const move = (delta) => () => {
      syncAll();
      const j = index + delta;
      if (j < 0 || j >= providers.length) return;
      [providers[index], providers[j]] = [providers[j], providers[index]];
      draw();
    };
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("strong", {}, `${index + 1}. ${p.name}`), h("div", { class: "spacer" }),
        h("button", { type: "button", "aria-label": `Move ${p.name} up`, disabled: index === 0, onclick: move(-1) }, "↑"),
        h("button", { type: "button", "aria-label": `Move ${p.name} down`, disabled: index === providers.length - 1, onclick: move(1) }, "↓"),
        h("button", { type: "button", class: "danger", onclick: () => { if (confirm(`Remove provider ${p.name}?`)) { syncAll(); providers.splice(index, 1); draw(); } } }, "Remove")),
      h("div", { class: "row" }, FLAGS.map(([key, label]) => h("label", { class: "check" }, flags[key], label))),
      field("API style", kind), field("Tier", tier), field("Base URL", baseUrl), field("Model", model),
      field("Share key of provider", keyName, "Leave blank to use this provider's own key."),
      h("div", { class: "field" }, h("span", { class: "label" }, "Extra request fields (read-only)"),
        h("pre", {}, JSON.stringify(p.extra ?? {}, null, 2))),
      field("API key (write-only)", keyInput),
      shared,
      h("div", { class: "row" },
        action("Save key", async () => {
          const name = checkedSecret();
          if (!keyInput.value.trim()) throw new Error("Paste a key first.");
          await api("secrets/" + encode(name), { method: "PUT", body: { value: keyInput.value.trim() } });
          keyInput.value = "";
          try { await refreshKeys(); } catch { keys[name] = "saved"; }
          showMasks();
          return `Key saved for ${p.name}.`;
        }),
        action("Remove key", async () => {
          const name = checkedSecret();
          await api("secrets/" + encode(name), { method: "DELETE" });
          try { await refreshKeys(); } catch { delete keys[name]; }
          showMasks();
          return "Key removed.";
        })),
    );
  }

  function draw() {
    syncers.length = 0;
    maskers.length = 0;
    list.replaceChildren(...providers.map(card));
  }

  const newName = textInput("", { placeholder: "name, e.g. mistral" });
  const newUrl = textInput("", { placeholder: "https://api.example.com/v1" });
  const newModel = textInput("", { placeholder: "model id" });

  main.replaceChildren(
    h("h2", {}, "Brain"),
    h("p", { class: "muted" }, "Minty tries providers top to bottom. When one runs out or fails, it cools down and the next one takes over."),
    list,
    h("div", { class: "row" }, action("Save providers", async () => {
      syncAll();
      providers.forEach(validate);
      // Only the provider list is ours to write; everything else comes from the live config.
      const fresh = await api("config");
      fresh.providers = providers;
      await api("config", { method: "PUT", body: fresh });
      return "Saved. Takes effect immediately.";
    }, "primary")),
    h("h3", {}, "Add a provider"),
    h("div", { class: "card" }, field("Name", newName), field("Base URL (OpenAI-compatible)", newUrl), field("Model", newModel),
      action("Add", async () => {
        const name = newName.value.trim();
        if (!NAME.test(name)) throw new Error("Name: use lowercase letters, digits, - or _ (max 40).");
        if (providers.some((p) => p.name === name)) throw new Error(`A provider named "${name}" already exists.`);
        const added = { name, kind: "openai_compat", base_url: newUrl.value.trim(), model: newModel.value.trim(),
          enabled: true, supports_tools: true, requires_key: true, trains_on_data: false, allow_sensitive: true,
          tier: "fast", key_name: "", extra: {} };
        validate(added);
        syncAll();
        providers.push(added);
        newName.value = newUrl.value = newModel.value = "";
        draw();
        toast("Added. Press “Save providers” to keep it.");
      })),
    h("h3", {}, "Free API keys"),
    h("ul", {}, FREE_TIERS.map(([name, url]) => h("li", {}, h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, name)))),
  );
  draw();
}
