import { action, api, checkbox, encode, field, h, numberInput, textInput } from "../lib.js";

// How a manifest setting is edited. Secrets are write-only; "json" holds any JSON value.
function kindOf(spec) {
  if (spec.secret) return "secret";
  if (["json", "number", "boolean"].includes(spec.type)) return spec.type;
  return "string";
}

// Key-order independent text of a JSON value, so "same value" does not depend on how the server ordered keys.
function canon(value) {
  if (Array.isArray(value)) return `[${value.map(canon).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canon(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}
const same = (a, b) => canon(a) === canon(b);

// `loaded` is the stored value the field was drawn from (undefined when nothing is stored); a field counts as
// changed only when the user moved it away from that.
function makeRow(plugin, key, spec, saved) {
  const kind = kindOf(spec);
  const loaded = saved[key];
  let input;
  if (kind === "secret") input = h("input", { type: "password", autocomplete: "new-password" });
  else if (kind === "json") {
    const text = loaded === undefined ? "" : JSON.stringify(loaded, null, 2);
    input = h("textarea", { rows: 5, spellcheck: "false", value: text });
  } else if (kind === "number") input = numberInput(loaded ?? "", { step: "any" });
  // An unset boolean shows the manifest's default (else off); that display alone is never saved.
  else if (kind === "boolean") input = checkbox(loaded ?? spec.default ?? false);
  else input = textInput(loaded ?? "");
  const row = { key, kind, label: spec.label || key, input, loaded, secretName: `plugin.${plugin.name}.${key}` };
  if (kind === "boolean") row.shown = input.checked;
  return row;
}

// What this row asks to save: {value} when the user changed it, or null to leave the setting alone.
// A text or JSON setting that had a value and is now blank is cleared with an explicit "".
function readRow(row) {
  const { kind, label, input, loaded } = row;
  if (kind === "boolean") return input.checked === row.shown ? null : { value: input.checked };
  const text = input.value.trim();
  const had = loaded !== undefined && loaded !== null && loaded !== "";
  if (text === "") return had && (kind === "string" || kind === "json") ? { value: "" } : null;
  if (kind === "number") {
    const number = Number(text);
    if (!Number.isFinite(number)) throw new Error(`${label} must be a number.`);
    return number === loaded ? null : { value: number };
  }
  if (kind === "json") {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      throw new Error(`${label}: invalid JSON (${e.message}).`);
    }
    return same(parsed, loaded) ? null : { value: parsed };
  }
  return text === loaded ? null : { value: text };
}

function showMasks(rows, masked) {
  for (const row of rows) {
    if (row.kind === "secret") {
      row.input.setAttribute("placeholder", masked[row.secretName] ? `stored: ${masked[row.secretName]}` : "not set");
    }
  }
}

function pluginCard(plugin, saved, masked) {
  const enabled = checkbox(plugin.enabled);
  const rows = Object.entries(plugin.settings_schema || {}).map(([key, spec]) => makeRow(plugin, key, spec, saved));
  showMasks(rows, masked);

  // Load error and tool list come from the plugin host and can change when the plugin is enabled.
  const status = h("div");
  const showStatus = (current) => status.replaceChildren(
    ...[
      current.error ? h("p", { class: "error" }, `Failed to load: ${current.error}`) : null,
      current.tools?.length ? h("p", { class: "muted" }, `Tools: ${current.tools.join(", ")}`) : null,
    ].filter(Boolean));
  showStatus(plugin);

  let loadedEnabled = plugin.enabled;

  async function save() {
    // Read and validate the form first: a bad value must not cause any request.
    // Only what the user changed is sent, so nothing else on the server is overwritten with load-time values.
    const settings = {};
    const changed = [];
    const secrets = [];
    for (const row of rows) {
      const change = readRow(row);
      if (!change) continue;
      if (row.kind === "secret") secrets.push([row, change.value]);
      else { settings[row.key] = change.value; changed.push(row); }
    }
    const enabledChanged = enabled.checked !== loadedEnabled;
    // Decide from the server's current state, not from what was loaded with the page.
    const [list, masks, config] = await Promise.all([api("plugins"), api("secrets"), api("config")]);
    const current = list.find((p) => p.name === plugin.name);
    if (!current) throw new Error(`Plugin ${plugin.name} no longer exists. Reload the page.`);
    const schema = current.settings_schema || {};
    if (rows.some((row) => !schema[row.key] || kindOf(schema[row.key]) !== row.kind)) {
      throw new Error(`${plugin.name} settings changed on the server. Reload the page.`);
    }
    // A value the user is about to overwrite must still be what they saw.
    const stored = (config.plugins || {})[plugin.name] || {};
    for (const row of changed) {
      if (!same(stored[row.key], row.loaded)) throw new Error(`${plugin.name}.${row.key} changed elsewhere — reload.`);
    }
    if (enabledChanged && current.enabled !== loadedEnabled) throw new Error(`${plugin.name}.enabled changed elsewhere — reload.`);
    showMasks(rows, masks);
    try {
      for (const [row, value] of secrets) {
        await api("secrets/" + encode(row.secretName), { method: "PUT", body: { value } });
        row.input.value = "";
      }
      const body = enabledChanged ? { enabled: enabled.checked, settings } : { settings };
      const updated = await api("plugins/" + encode(plugin.name), { method: "PUT", body });
      for (const row of changed) {
        row.loaded = settings[row.key] === "" ? undefined : settings[row.key];
        if (row.kind === "boolean") row.shown = row.loaded;
      }
      loadedEnabled = enabled.checked;
      const result = updated.find((p) => p.name === plugin.name);
      if (result) showStatus(result);
    } finally {
      // Show what the server now stores, even when a later write failed after a secret was saved.
      // This refresh is cosmetic, so its own failure must not hide the real error.
      if (rows.some((row) => row.kind === "secret")) {
        await api("secrets").then((fresh) => showMasks(rows, fresh), () => {});
      }
    }
    return `${plugin.name} saved.`;
  }

  return h("div", { class: "card" },
    h("div", { class: "row" }, h("strong", {}, plugin.name), h("div", { class: "spacer" }),
      h("label", { class: "check" }, enabled, "Enabled")),
    h("p", { class: "muted" }, plugin.description),
    status,
    rows.map((row) => field(row.label, row.input)),
    action("Save", save, "primary"));
}

export default async function plugins(main) {
  const [list, config, masked] = await Promise.all([api("plugins"), api("config"), api("secrets")]);
  const settings = config.plugins || {};
  const cards = list.map((plugin) => pluginCard(plugin, settings[plugin.name] || {}, masked));
  main.replaceChildren(h("h2", {}, "Plugins"), ...(cards.length ? cards : [h("p", { class: "muted" }, "No plugins found.")]));
}
