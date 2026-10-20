// Behaviour checks for the panel's DOM-free modules. Run by tests/test_panel_static.py:
//   PANEL_STATIC=<copy of static/ with a module package.json> node --test tests/panel_js_checks.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const load = (name) => import(pathToFileURL(`${process.env.PANEL_STATIC}/${name}`).href);

class FakeSocket {
  static all = [];
  constructor(url) {
    this.url = url;
    this.closed = false;
    FakeSocket.all.push(this);
  }
  close() { this.closed = true; }
  get live() { return FakeSocket.all.filter((s) => !s.closed); }
}

function installEnvironment() {
  FakeSocket.all = [];
  const timers = new Map();
  let next = 1;
  globalThis.WebSocket = FakeSocket;
  globalThis.location = { protocol: "http:", host: "127.0.0.1:7778" };
  globalThis.setTimeout = (fn) => { timers.set(next, fn); return next++; };
  globalThis.clearTimeout = (id) => { timers.delete(id); };
  const probes = [];
  globalThis.fetch = async (url) => { probes.push(url); return { status: 200, ok: true, text: async () => "{}" }; };
  return {
    probes,
    pending: () => timers.size,
    fire() { const fns = [...timers.values()]; timers.clear(); fns.forEach((fn) => fn()); },
  };
}
function serverClose(socket, code) {
  socket.closed = true;
  socket.onclose?.(code === undefined ? undefined : { code });
}
const liveSockets = () => FakeSocket.all.filter((s) => !s.closed);

test("start is idempotent and stop closes the socket", async () => {
  const clock = installEnvironment();
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  events.start();
  events.start();
  assert.equal(FakeSocket.all.length, 1);
  assert.equal(FakeSocket.all[0].url, "ws://127.0.0.1:7778/api/events");
  events.stop();
  assert.equal(liveSockets().length, 0);
  assert.equal(clock.pending(), 0);
});

test("stop cancels a pending reconnect timer", async () => {
  const clock = installEnvironment();
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  events.start();
  serverClose(FakeSocket.all[0]);
  assert.equal(clock.pending(), 1);
  events.stop();
  assert.equal(clock.pending(), 0);
  clock.fire();
  assert.equal(FakeSocket.all.length, 1);
});

test("unexpected close reconnects once", async () => {
  const clock = installEnvironment();
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  events.start();
  serverClose(FakeSocket.all[0]);
  clock.fire();
  assert.equal(FakeSocket.all.length, 2);
  assert.equal(liveSockets().length, 1);
});

test("a stale close after stop/start cannot open a second socket", async () => {
  const clock = installEnvironment();
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  const seen = [];
  events.on((e) => seen.push(e));
  events.start();
  const first = FakeSocket.all[0];
  events.stop();
  assert.equal(first.onclose, null);
  assert.equal(first.onmessage, null);
  events.start();
  clock.fire();
  assert.equal(liveSockets().length, 1);
  FakeSocket.all[1].onmessage({ data: JSON.stringify({ type: "state" }) });
  assert.equal(seen.length, 1);
});

test("a throwing handler is reported and does not block others", async () => {
  installEnvironment();
  const { makeEvents } = await load("events.js");
  const errors = [];
  const events = makeEvents({ onError: (e) => errors.push(e.message) });
  const seen = [];
  events.on(() => { throw new Error("boom"); });
  events.on((e) => seen.push(e.type));
  events.start();
  FakeSocket.all[0].onmessage({ data: "not json" });
  FakeSocket.all[0].onmessage({ data: JSON.stringify({ type: "state" }) });
  assert.deepEqual(errors, ["boom"]);
  assert.deepEqual(seen, ["state"]);
});

test("runner cleans up a superseded page immediately", async () => {
  const { createRunner } = await load("runner.js");
  const runner = createRunner();
  const log = [];
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const first = runner.run(async () => { await slow; return () => log.push("slow cleanup"); });
  const second = await runner.run(async () => () => log.push("fast cleanup"));
  assert.equal(second, true);
  release();
  assert.equal(await first, false);
  assert.deepEqual(log, ["slow cleanup"]);
  runner.stop();
  assert.deepEqual(log, ["slow cleanup", "fast cleanup"]);
});

test("runner stop supersedes a page still loading", async () => {
  const { createRunner } = await load("runner.js");
  const runner = createRunner();
  const log = [];
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const pending = runner.run(async () => { await slow; return () => log.push("cleaned"); });
  runner.stop();
  release();
  assert.equal(await pending, false);
  assert.deepEqual(log, ["cleaned"]);
});

test("runner drops errors of superseded runs but raises current ones", async () => {
  const { createRunner } = await load("runner.js");
  const runner = createRunner();
  let fail;
  const stale = runner.run(() => new Promise((_, reject) => { fail = reject; }));
  await runner.run(async () => undefined);
  fail(new Error("late"));
  assert.equal(await stale, false);
  await assert.rejects(runner.run(async () => { throw new Error("now"); }), /now/);
});

test("api dispatches minty:unauthorized on 401 and throws Unauthorized", async () => {
  const { api, Unauthorized } = await load("lib.js");
  const dispatched = [];
  globalThis.window = { dispatchEvent: (e) => dispatched.push(e.type) };
  globalThis.fetch = async () => ({ status: 401, ok: false, text: async () => "" });
  await assert.rejects(api("status"), Unauthorized);
  assert.deepEqual(dispatched, ["minty:unauthorized"]);
  globalThis.fetch = async (url, options) => {
    assert.equal(options.headers["X-Requested-With"], "minty");
    return { status: 200, ok: true, text: async () => '{"ok":true}' };
  };
  assert.deepEqual(await api("status"), { ok: true });
  assert.equal(dispatched.length, 1);
});

// ---------------------------------------------------------------------------------------------
// Page behaviour: a tiny fake DOM plus a stubbed fetch, enough to drive the config pages.

class FakeText {
  constructor(data) { this.nodeType = 3; this.data = data; }
  get textContent() { return this.data; }
}

class FakeElement {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = new Map();
    this.listeners = {};
    this.className = "";
    this.checked = false;
    this.rawValue = undefined;
  }
  setAttribute(key, value) { this.attrs.set(key, String(value)); }
  getAttribute(key) { return this.attrs.has(key) ? this.attrs.get(key) : null; }
  get disabled() { return this.attrs.has("disabled"); }
  set disabled(on) { if (on) this.attrs.set("disabled", ""); else this.attrs.delete("disabled"); }
  get placeholder() { return this.getAttribute("placeholder"); }
  set placeholder(v) { this.setAttribute("placeholder", v); }
  append(...nodes) { this.children.push(...nodes.map((n) => (typeof n === "string" ? new FakeText(n) : n))); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() {}
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async dispatch(type) { for (const fn of this.listeners[type] ?? []) await fn({ type, target: this }); }
  async click() { if (!this.disabled) await this.dispatch("click"); }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  get options() { return this.children.filter((c) => c.tagName === "OPTION"); }
  get value() {
    if (this.tagName === "OPTION") return this.rawValue ?? this.textContent;
    if (this.tagName === "SELECT") {
      const values = this.options.map((o) => o.value);
      if (this.rawValue === undefined) return values[0] ?? "";
      return values.includes(this.rawValue) ? this.rawValue : "";
    }
    return this.rawValue ?? "";
  }
  set value(v) { this.rawValue = String(v); }
}

function walk(root, visit) {
  visit(root);
  for (const child of root.children ?? []) walk(child, visit);
}
const findAll = (root, test) => { const out = []; walk(root, (e) => { if (e.nodeType === 1 && test(e)) out.push(e); }); return out; };
const button = (root, label) => findAll(root, (e) => e.tagName === "BUTTON" && e.textContent === label)[0];
const control = (root, label) => {
  const wrapper = findAll(root, (e) => e.tagName === "LABEL" && e.textContent.includes(label))[0];
  assert.ok(wrapper, `no field labelled ${label}`);
  return findAll(wrapper, (e) => ["INPUT", "SELECT", "TEXTAREA"].includes(e.tagName))[0];
};

function pageEnvironment(handler) {
  const toastBox = new FakeElement("div");
  const calls = [];
  const env = { calls, reloads: 0, answers: { prompt: null, confirm: true } };
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => new FakeText(text),
    getElementById: () => toastBox,
  };
  globalThis.window = { dispatchEvent() {} };
  globalThis.location = { reload() { env.reloads += 1; } };
  globalThis.setTimeout = () => 0;
  globalThis.prompt = () => env.answers.prompt;
  globalThis.confirm = () => env.answers.confirm;
  globalThis.fetch = async (url, options = {}) => {
    const path = url.replace(/^\/api\//, "");
    const body = options.body ? JSON.parse(options.body) : undefined;
    const call = { method: options.method, path, body };
    calls.push(call);
    const result = await handler(call);
    const status = result?.status ?? (result === undefined ? 204 : 200);
    const data = result?.status ? result.data : result;
    return { status, ok: status < 400, text: async () => (data === undefined ? "" : JSON.stringify(data)) };
  };
  env.toasts = () => toastBox.children.map((t) => ({ text: t.textContent, error: t.className.includes("error") }));
  env.puts = (path) => calls.filter((c) => c.method === "PUT" && c.path === path);
  return env;
}

const rawConfig = () => ({
  providers: [
    { name: "groq", kind: "openai_compat", base_url: "https://g/v1", model: "m1", enabled: true, supports_tools: true, requires_key: true, trains_on_data: false, allow_sensitive: true },
    { name: "cerebras", kind: "openai_compat", base_url: "https://c/v1", model: "m2", enabled: true, supports_tools: true, requires_key: true, trains_on_data: false, allow_sensitive: true },
  ],
  personality: { active: "minty", formality: 5, humor: 3, verbosity: 3 },
  stt: { provider: "groq", groq_model: "w", local_model: "base.en", local_only: false },
  tts: { engine: "kokoro", kokoro_voice: "am_michael", kokoro_model: "", kokoro_voices: "", speed: 1, voice_model: "", minty_filter: false },
  audio: { input_device: null, output_device: null, wake_word: "hey_jarvis", wake_threshold: 0.5, speech_rms_threshold: 400, auto_sensitivity: true, mic_sensitivity: 1, listen_duck: true, listen_duck_level: 0.1, silence_ms: 900, max_wait_ms: 4000, max_record_s: 15, barge_in: true },
  safety: { confirm_risky: true },
  plugins: { weather: { units: "metric" } },
  update: { repo: "" },
  terminal_mute_key: "m",
  terminal_listen_key: "space",
  api_port: 7777,
});

// The settings page reads every row of the schema: fill what the hand-written fixture leaves out with a harmless value.
const { SECTIONS } = await load("settings-schema.js");
function baseConfig() {
  const cfg = rawConfig();
  for (const sec of SECTIONS) {
    for (const [section, key, , kind] of sec.rows) {
      const node = section ? (cfg[section] ??= {}) : cfg;
      if (node[key] !== undefined) continue;
      node[key] = kind === "bool" ? false : kind.startsWith("number") ? 1 : kind.startsWith("select:") ? kind.slice(7).split(",")[0] : "";
    }
  }
  return cfg;
}

// A config the second GET returns: someone else added a top-level key and changed a plugin setting.
function driftingConfigs() {
  let gets = 0;
  return () => {
    gets += 1;
    const cfg = baseConfig();
    if (gets > 1) { cfg.extra_top = { keep: true }; cfg.plugins.weather.units = "imperial"; }
    return cfg;
  };
}

async function mount(name) {
  const { default: page } = await load(`pages/${name}.js`);
  const main = new FakeElement("main");
  await page(main);
  return main;
}

function personalityServer({ names = ["minty", "dry"], cfg = baseConfig(), texts = {} } = {}) {
  return (call) => {
    if (call.path === "config") return call.method === "PUT" ? undefined : (typeof cfg === "function" ? cfg() : cfg);
    if (call.path === "personalities") return [...names];
    if (call.path.startsWith("personalities/")) {
      const name = decodeURIComponent(call.path.slice("personalities/".length));
      if (call.method === "GET") return { name, text: texts[name] ?? `text of ${name}` };
      if (call.method === "PUT") { if (!names.includes(name)) names.push(name); return undefined; }
      if (call.method === "DELETE") { names.splice(names.indexOf(name), 1); return undefined; }
    }
    throw new Error(`unexpected ${call.method} ${call.path}`);
  };
}

test("describe drops only the body prefix and numbers providers from 1", async () => {
  const { api } = await load("lib.js");
  pageEnvironment(() => ({
    status: 422,
    data: { detail: [
      { loc: ["audio", "wake_threshold"], msg: "Input should be less than or equal to 1" },
      { loc: ["body", "api_port"], msg: "too small" },
      { loc: ["providers", 2, "model"], msg: "Field required" },
      { loc: [], msg: "bare" },
    ] },
  }));
  await assert.rejects(api("config", { method: "PUT", body: {} }), (e) => {
    assert.equal(e.message,
      "audio.wake_threshold: Input should be less than or equal to 1; api_port: too small; providers #3 model: Field required; bare");
    return true;
  });
});

test("select keeps a value that is not in its option list", async () => {
  const { select } = await load("lib.js");
  pageEnvironment(() => undefined);
  assert.equal(select(["a", "b"], "legacy").value, "legacy");
  assert.equal(select(["a", "b"], "b").value, "b");
});

test("personality: the active personality cannot be deleted", async () => {
  const env = pageEnvironment(personalityServer());
  const main = await mount("personality");
  await button(main, "Delete").click();
  assert.equal(env.calls.filter((c) => c.method === "DELETE").length, 0);
  assert.match(env.toasts().at(-1).text, /Switch the active personality first/);
  assert.equal(env.toasts().at(-1).error, true);
});

test("personality: another personality can be deleted and the page redraws from the server", async () => {
  const env = pageEnvironment(personalityServer());
  const main = await mount("personality");
  const picker = control(main, "Personality file");
  picker.value = "dry";
  await picker.dispatch("change");
  await button(main, "Delete").click();
  assert.deepEqual(env.calls.filter((c) => c.method === "DELETE").map((c) => c.path), ["personalities/dry"]);
  assert.equal(env.reloads, 0);
});

test("personality: an active name that no longer exists falls back to the first personality", async () => {
  const cfg = baseConfig();
  cfg.personality.active = "gone";
  const env = pageEnvironment(personalityServer({ cfg }));
  const main = await mount("personality");
  assert.equal(control(main, "Active personality").value, "minty");
  assert.equal(control(main, "Personality file").value, "minty");
  assert.deepEqual(env.calls.filter((c) => c.path === "personalities/gone"), []);
  await button(main, "Save").click();
  assert.equal(env.puts("config")[0].body.personality.active, "minty");
});

test("personality: with none at all, offer to create one and never request personalities/", async () => {
  const env = pageEnvironment(personalityServer({ names: [] }));
  const main = await mount("personality");
  assert.ok(button(main, "New…"));
  assert.match(main.textContent, /No personalities yet/);
  assert.deepEqual(env.calls.filter((c) => c.path.startsWith("personalities/")), []);
  env.answers.prompt = "first";
  await button(main, "New…").click();
  assert.deepEqual(env.puts("personalities/first").length, 1);
  assert.equal(control(main, "Personality file").value, "first");
});

test("personality: New refuses an existing name and selects a created one only in the file picker", async () => {
  const env = pageEnvironment(personalityServer());
  const main = await mount("personality");
  env.answers.prompt = "dry";
  await button(main, "New…").click();
  assert.equal(env.puts("personalities/dry").length, 0);
  assert.match(env.toasts().at(-1).text, /already exists/);
  env.answers.prompt = "Bad Name";
  await button(main, "New…").click();
  assert.equal(env.puts("personalities/Bad%20Name").length, 0);
  env.answers.prompt = "dry-wit";
  await button(main, "New…").click();
  assert.equal(env.puts("personalities/dry-wit").length, 1);
  assert.equal(control(main, "Personality file").value, "dry-wit");
  // The Active select keeps showing the saved active personality: creating a file must not change it.
  assert.equal(control(main, "Active personality").value, "minty");
  await button(main, "Save").click();
  assert.equal(env.puts("config")[0].body.personality.active, "minty");
});

test("personality: Save writes onto the live config, keeping unseen changes", async () => {
  const env = pageEnvironment(personalityServer({ cfg: driftingConfigs() }));
  const main = await mount("personality");
  control(main, "Humor").value = "9";
  await button(main, "Save").click();
  const body = env.puts("config")[0].body;
  assert.deepEqual(body.extra_top, { keep: true });
  assert.equal(body.plugins.weather.units, "imperial");
  assert.equal(body.personality.humor, 9);
  assert.equal(body.personality.active, "minty");
});

test("personality: a failed text load blocks Save text and clears the textarea", async () => {
  let failing = false;
  const env = pageEnvironment((call) => {
    if (failing && call.method === "GET" && call.path === "personalities/dry") return { status: 404, data: { detail: "no such personality" } };
    return personalityServer()(call);
  });
  const main = await mount("personality");
  const text = findAll(main, (e) => e.tagName === "TEXTAREA")[0];
  assert.equal(text.value, "text of minty");
  failing = true;
  const picker = control(main, "Personality file");
  picker.value = "dry";
  await picker.dispatch("change");
  assert.equal(text.value, "");
  assert.equal(button(main, "Save text").disabled, true);
  await button(main, "Save text").click();
  assert.equal(env.puts("personalities/dry").length, 0);
  assert.equal(env.puts("personalities/minty").length, 0);
  assert.match(env.toasts().at(-1).text, /no such personality/);
});

test("personality: Save text writes the loaded personality, not whatever the picker shows", async () => {
  const env = pageEnvironment(personalityServer());
  const main = await mount("personality");
  const picker = control(main, "Personality file");
  const text = findAll(main, (e) => e.tagName === "TEXTAREA")[0];
  text.value = "edited minty";
  picker.value = "dry"; // changed without a "change" event: the textarea still holds minty's text
  await button(main, "Save text").click();
  assert.equal(env.puts("personalities/dry").length, 0);
  assert.deepEqual(env.puts("personalities/minty")[0].body, { text: "edited minty" });
});

test("personality: Save text is blocked while the next personality is still loading", async () => {
  let release;
  const base = personalityServer();
  const env = pageEnvironment(async (call) => {
    if (release === undefined && call.method === "GET" && call.path === "personalities/dry") {
      await new Promise((resolve) => { release = resolve; });
    }
    return base(call);
  });
  const main = await mount("personality");
  const picker = control(main, "Personality file");
  picker.value = "dry";
  const loading = picker.dispatch("change");
  await new Promise((r) => setImmediate(r));
  assert.equal(button(main, "Save text").disabled, true);
  await button(main, "Save text").click();
  assert.equal(env.calls.filter((c) => c.method === "PUT").length, 0);
  release();
  await loading;
  assert.equal(button(main, "Save text").disabled, false);
  await button(main, "Save text").click();
  assert.equal(env.puts("personalities/dry").length, 1);
});

test("personality: an out-of-order text response does not overwrite the newer one", async () => {
  const release = {};
  const hold = (name) => new Promise((resolve) => { release[name] = resolve; });
  const base = personalityServer({ names: ["minty", "a", "b"] });
  const env = pageEnvironment(async (call) => {
    const m = call.method === "GET" && call.path.match(/^personalities\/(a|b)$/);
    if (m) { await hold(m[1]); return { name: m[1], text: `text of ${m[1]}` }; }
    return base(call);
  });
  const pending = mount("personality");
  await new Promise((r) => setImmediate(r));
  const settle = async () => { for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r)); };
  await settle();
  const main = await pending;
  const picker = control(main, "Personality file");
  const text = findAll(main, (e) => e.tagName === "TEXTAREA")[0];
  picker.value = "a"; const first = picker.dispatch("change");
  picker.value = "b"; const second = picker.dispatch("change");
  await settle();
  release.b(); await second;
  release.a(); await first;
  assert.equal(text.value, "text of b");
  await button(main, "Save text").click();
  assert.equal(env.puts("personalities/b").length, 1);
  assert.equal(env.puts("personalities/b")[0].body.text, "text of b");
});

function audioServer(cfg) {
  return (call) => {
    if (call.path === "config") return call.method === "PUT" ? undefined : (typeof cfg === "function" ? cfg() : cfg);
    throw new Error(`unexpected ${call.method} ${call.path}`);
  };
}

test("settings: Save writes onto the live config, keeping unseen changes", async () => {
  const env = pageEnvironment(audioServer(driftingConfigs()));
  const main = await mount("settings");
  control(main, "Silence that ends").value = "1200";
  await button(main, "Save").click();
  const body = env.puts("config")[0].body;
  assert.deepEqual(body.extra_top, { keep: true });
  assert.equal(body.plugins.weather.units, "imperial");
  assert.equal(body.audio.silence_ms, 1200);
  assert.equal(body.audio.input_device, null);
  assert.deepEqual(body.personality, baseConfig().personality);
});

test("settings: a blank or non-numeric number field is an error naming the field, and nothing is sent", async () => {
  const env = pageEnvironment(audioServer(baseConfig()));
  const main = await mount("settings");
  control(main, "Wake sensitivity threshold").value = "";
  await button(main, "Save").click();
  assert.equal(env.puts("config").length, 0);
  assert.equal(env.calls.filter((c) => c.path === "config" && c.method === "GET").length, 1);
  assert.match(env.toasts().at(-1).text, /Wake sensitivity threshold must be a number/);
  control(main, "Wake sensitivity threshold").value = "0.4";
  control(main, "Longest single request").value = "abc";
  await button(main, "Save").click();
  assert.equal(env.puts("config").length, 0);
  assert.match(env.toasts().at(-1).text, /Longest single request/);
});

test("settings: the newer options are on the page and are saved where they belong", async () => {
  const env = pageEnvironment(audioServer(baseConfig()));
  const main = await mount("settings");
  control(main, "24-hour clock").checked = true;
  control(main, "Microphone sensitivity").value = "2";
  control(main, "Turn other audio down while listening").checked = false;
  control(main, "How often to look (hours)").value = "12";
  control(main, "Answer simple questions at once").checked = true;
  control(main, "Reasoning effort for quick questions").value = "low";
  await button(main, "Save").click();
  const body = env.puts("config")[0].body;
  assert.equal(body.clock_24h, true);
  assert.equal(body.audio.mic_sensitivity, 2);
  assert.equal(body.audio.listen_duck, false);
  assert.equal(body.update.check_every_hours, 12);
  assert.equal(body.instant.enabled, true);
  assert.equal(body.router.fast_reasoning, "low");
});

test("settings: the filter hides rows that do not match, and sections left empty", async () => {
  pageEnvironment(audioServer(baseConfig()));
  const main = await mount("settings");
  const search = findAll(main, (e) => e.tagName === "INPUT" && e.getAttribute("type") === "search")[0];
  search.value = "duck";
  await search.dispatch("input");
  const labels = findAll(main, (e) => e.tagName === "LABEL");
  const visible = labels.filter((l) => !l.hidden).map((l) => l.textContent);
  assert.ok(visible.some((t) => t.includes("Turn other audio down while listening")));
  assert.ok(!visible.some((t) => t.includes("Wake sensitivity threshold")));
  const sections = findAll(main, (e) => e.tagName === "SECTION");
  assert.ok(sections.some((s) => s.hidden === true) && sections.some((s) => !s.hidden));
  search.value = "";
  await search.dispatch("input");
  assert.ok(findAll(main, (e) => e.tagName === "SECTION").every((s) => !s.hidden));
});

test("settings: a link from the search bar opens the page on that setting without error", async () => {
  pageEnvironment(audioServer(baseConfig()));
  const { default: page } = await load("pages/settings.js");
  const main = new FakeElement("main");
  await page(main, undefined, "audio.auto_sensitivity");
  assert.ok(findAll(main, (e) => e.tagName === "LABEL" && e.textContent.includes("Adapt the microphone to the room")).length >= 1);
});

test("settings: an unknown speech-to-text provider survives a save", async () => {
  const cfg = baseConfig();
  cfg.stt.provider = "whisper-cloud";
  const env = pageEnvironment(audioServer(cfg));
  const main = await mount("settings");
  await button(main, "Save").click();
  assert.equal(env.puts("config")[0].body.stt.provider, "whisper-cloud");
});

function brainServer({ cfg = baseConfig(), secrets = { groq: "gsk_…1234" } } = {}) {
  const state = { secrets: { ...secrets } };
  const handler = (call) => {
    if (call.path === "config") return call.method === "PUT" ? undefined : (typeof cfg === "function" ? cfg() : cfg);
    if (call.path === "secrets") return { ...state.secrets };
    if (call.path.startsWith("secrets/")) {
      const name = call.path.slice("secrets/".length);
      if (call.method === "PUT") state.secrets[name] = "…" + call.body.value.slice(-4);
      else delete state.secrets[name];
      return undefined;
    }
    throw new Error(`unexpected ${call.method} ${call.path}`);
  };
  return handler;
}

test("brain: Save replaces only the providers on the live config", async () => {
  const env = pageEnvironment(brainServer({ cfg: driftingConfigs() }));
  const main = await mount("brain");
  await button(main, "Save providers").click();
  const body = env.puts("config")[0].body;
  assert.deepEqual(body.extra_top, { keep: true });
  assert.equal(body.plugins.weather.units, "imperial");
  assert.deepEqual(body.providers.map((p) => p.name), ["groq", "cerebras"]);
});

test("brain: saving a key re-reads the server mask and never echoes the key", async () => {
  const env = pageEnvironment(brainServer());
  const main = await mount("brain");
  const keyBox = findAll(main, (e) => e.tagName === "INPUT" && e.getAttribute("type") === "password");
  assert.equal(keyBox[0].getAttribute("autocomplete"), "new-password");
  assert.equal(keyBox[0].getAttribute("placeholder"), "stored: gsk_…1234");
  assert.equal(keyBox[1].getAttribute("placeholder"), "paste API key");
  keyBox[1].value = "sk-secret-9876";
  const saveKeys = findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Save key");
  await saveKeys[1].click();
  assert.deepEqual(env.puts("secrets/cerebras")[0].body, { value: "sk-secret-9876" });
  assert.equal(env.calls.filter((c) => c.path === "secrets" && c.method === "GET").length, 2);
  assert.equal(keyBox[1].getAttribute("placeholder"), "stored: …9876");
  assert.equal(keyBox[1].value, "");
  assert.ok(!main.textContent.includes("sk-secret-9876"));
  const removeKeys = findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Remove key");
  await removeKeys[1].click();
  assert.equal(keyBox[1].getAttribute("placeholder"), "paste API key");
});

test("brain: adding a provider validates the name, uniqueness and required fields", async () => {
  const env = pageEnvironment(brainServer());
  const main = await mount("brain");
  const name = control(main, "Name");
  const url = control(main, "Base URL (OpenAI-compatible)");
  const model = findAll(main, (e) => e.tagName === "LABEL" && e.textContent.startsWith("Model")).at(-1);
  const modelInput = findAll(model, (e) => e.tagName === "INPUT")[0];
  const add = button(main, "Add");
  const count = () => findAll(main, (e) => e.tagName === "STRONG").length;
  const attempt = async (n, u, m) => { name.value = n; url.value = u; modelInput.value = m; await add.click(); return env.toasts().at(-1); };

  assert.match((await attempt("Bad Name", "https://x/v1", "m")).text, /Name: use lowercase/);
  assert.match((await attempt("groq", "https://x/v1", "m")).text, /already exists/);
  assert.match((await attempt("mistral", "https://x/v1", "  ")).text, /model must not be blank/);
  assert.match((await attempt("mistral", "", "m")).text, /base URL must not be blank/);
  assert.equal(count(), 2);
  assert.match((await attempt("mistral", "https://x/v1", "m")).text, /Added/);
  assert.equal(count(), 3);
  await button(main, "Save providers").click();
  assert.equal(env.puts("config")[0].body.providers.at(-1).name, "mistral");
});

test("brain: saving refuses a blank model on an existing card without sending", async () => {
  const env = pageEnvironment(brainServer());
  const main = await mount("brain");
  const models = findAll(main, (e) => e.tagName === "LABEL" && e.textContent.startsWith("Model")).map((l) => findAll(l, (e) => e.tagName === "INPUT")[0]);
  models[0].value = "";
  await button(main, "Save providers").click();
  assert.equal(env.puts("config").length, 0);
  assert.match(env.toasts().at(-1).text, /Provider groq: model must not be blank/);
});

test("brain: unknown API style is preserved, and move buttons are labelled and bounded", async () => {
  const cfg = baseConfig();
  cfg.providers[1].kind = "future_kind";
  const env = pageEnvironment(brainServer({ cfg }));
  const main = await mount("brain");
  const up = findAll(main, (e) => e.getAttribute("aria-label")?.endsWith(" up"));
  const down = findAll(main, (e) => e.getAttribute("aria-label")?.endsWith(" down"));
  assert.deepEqual(up.map((b) => b.getAttribute("aria-label")), ["Move groq up", "Move cerebras up"]);
  assert.deepEqual(up.map((b) => b.disabled), [true, false]);
  assert.deepEqual(down.map((b) => b.disabled), [false, true]);
  await button(main, "Save providers").click();
  assert.equal(env.puts("config")[0].body.providers[1].kind, "future_kind");
  await findAll(main, (e) => e.getAttribute("aria-label") === "Move cerebras up")[0].click();
  await button(main, "Save providers").click();
  assert.deepEqual(env.puts("config")[1].body.providers.map((p) => p.name), ["cerebras", "groq"]);
});

function sharedKeyConfig() {
  const cfg = baseConfig();
  const common = { kind: "openai_compat", enabled: true, supports_tools: true, requires_key: true, trains_on_data: false, allow_sensitive: true };
  cfg.providers = [
    { ...common, name: "nvidia", base_url: "https://n/v1", model: "fast-m", tier: "fast", key_name: "nvidia", extra: {} },
    { ...common, name: "nvidia-deep", base_url: "https://n/v1", model: "deep-m", tier: "deep", key_name: "nvidia", extra: { reasoning_effort: "max" } },
    { ...common, name: "groq", base_url: "https://g/v1", model: "g-m", tier: "fast", key_name: "", extra: {} },
  ];
  return cfg;
}
const cardField = (main, label, index) =>
  findAll(findAll(main, (e) => e.tagName === "LABEL" && e.textContent.startsWith(label))[index], (e) => ["INPUT", "SELECT"].includes(e.tagName))[0];
const keyBoxes = (main) => findAll(main, (e) => e.tagName === "INPUT" && e.getAttribute("type") === "password");

test("brain: a card with a shared key reads, writes and removes the key_name secret", async () => {
  const env = pageEnvironment(brainServer({ cfg: sharedKeyConfig(), secrets: { nvidia: "nvapi-…abcd" } }));
  const main = await mount("brain");
  assert.equal(main.textContent.match(/Uses the nvidia key/g).length, 2);
  const boxes = keyBoxes(main);
  assert.deepEqual(boxes.map((b) => b.getAttribute("placeholder")), ["stored: nvapi-…abcd", "stored: nvapi-…abcd", "paste API key"]);
  // Pasting on nvidia-deep writes the shared secret; nvidia-deep has no secret of its own.
  boxes[1].value = "new-secret-7777";
  await findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Save key")[1].click();
  assert.equal(env.puts("secrets/nvidia").length, 1);
  assert.deepEqual(env.puts("secrets/nvidia")[0].body, { value: "new-secret-7777" });
  assert.equal(env.puts("secrets/nvidia-deep").length, 0);
  // Every card that shares the key shows the new mask; an unrelated card is unchanged.
  assert.deepEqual(boxes.map((b) => b.getAttribute("placeholder")), ["stored: …7777", "stored: …7777", "paste API key"]);
  await findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Remove key")[1].click();
  assert.deepEqual(env.calls.filter((c) => c.method === "DELETE").map((c) => c.path), ["secrets/nvidia"]);
  assert.deepEqual(boxes.map((b) => b.getAttribute("placeholder")), ["paste API key", "paste API key", "paste API key"]);
});

test("brain: a blank key_name keeps using the provider's own name", async () => {
  const env = pageEnvironment(brainServer({ cfg: sharedKeyConfig(), secrets: {} }));
  const main = await mount("brain");
  keyBoxes(main)[2].value = "gsk-1111";
  await findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Save key")[2].click();
  assert.equal(env.puts("secrets/groq").length, 1);
  assert.equal(main.textContent.match(/Uses the/g).length, 2); // only the two cards with a key_name say so
});

test("brain: editing Share key of provider retargets the key actions", async () => {
  const env = pageEnvironment(brainServer({ cfg: sharedKeyConfig(), secrets: { groq: "gsk_…1234" } }));
  const main = await mount("brain");
  const share = cardField(main, "Share key of provider", 1);
  share.value = "groq";
  await share.dispatch("input");
  assert.equal(keyBoxes(main)[1].getAttribute("placeholder"), "stored: gsk_…1234");
  assert.ok(main.textContent.includes("Uses the groq key"));
  keyBoxes(main)[1].value = "k-2222";
  await findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Save key")[1].click();
  assert.equal(env.puts("secrets/groq").length, 1);
  assert.equal(env.puts("secrets/nvidia").length, 0);
});

test("brain: tier, key_name and extra survive a save, and editing the tier works", async () => {
  const env = pageEnvironment(brainServer({ cfg: sharedKeyConfig() }));
  const main = await mount("brain");
  assert.equal(cardField(main, "Tier", 1).value, "deep");
  assert.equal(cardField(main, "Share key of provider", 1).value, "nvidia");
  assert.equal(cardField(main, "Share key of provider", 2).value, "");
  // extra is shown read-only as formatted JSON, never as an editable control.
  assert.ok(main.textContent.includes(JSON.stringify({ reasoning_effort: "max" }, null, 2)));
  assert.equal(findAll(main, (e) => ["INPUT", "TEXTAREA"].includes(e.tagName) && String(e.value).includes("reasoning_effort")).length, 0);
  await button(main, "Save providers").click();
  assert.deepEqual(env.puts("config")[0].body.providers, sharedKeyConfig().providers);
  const tier = cardField(main, "Tier", 2);
  tier.value = "deep";
  await button(main, "Save providers").click();
  const saved = env.puts("config")[1].body.providers;
  assert.equal(saved[2].tier, "deep");
  assert.equal(saved[0].tier, "fast");
  assert.deepEqual(saved[1].extra, { reasoning_effort: "max" });
  assert.equal(saved[1].key_name, "nvidia");
});

test("brain: key_name is validated like a provider name; blank is allowed", async () => {
  const env = pageEnvironment(brainServer({ cfg: sharedKeyConfig() }));
  const main = await mount("brain");
  cardField(main, "Share key of provider", 2).value = "Bad Key!";
  await button(main, "Save providers").click();
  assert.equal(env.puts("config").length, 0);
  assert.match(env.toasts().at(-1).text, /Provider groq: .*key/i);
  cardField(main, "Share key of provider", 2).value = "  ";
  await button(main, "Save providers").click();
  assert.equal(env.puts("config")[0].body.providers[2].key_name, "");
});

test("brain: an added provider starts as tier fast with no shared key and no extra fields", async () => {
  const env = pageEnvironment(brainServer());
  const main = await mount("brain");
  control(main, "Name").value = "mistral";
  control(main, "Base URL (OpenAI-compatible)").value = "https://m/v1";
  const model = findAll(main, (e) => e.tagName === "LABEL" && e.textContent.startsWith("Model")).at(-1);
  findAll(model, (e) => e.tagName === "INPUT")[0].value = "m-1";
  await button(main, "Add").click();
  await button(main, "Save providers").click();
  const added = env.puts("config")[0].body.providers.at(-1);
  assert.equal(added.tier, "fast");
  assert.equal(added.key_name, "");
  assert.deepEqual(added.extra, {});
});

test("brain: an Anthropic provider with a blank base URL saves fine", async () => {
  const cfg = baseConfig();
  cfg.providers[1] = { ...cfg.providers[1], name: "claude", kind: "anthropic", base_url: "", model: "claude-x" };
  const env = pageEnvironment(brainServer({ cfg }));
  const main = await mount("brain");
  await button(main, "Save providers").click();
  assert.equal(env.puts("config").length, 1);
  assert.equal(env.puts("config")[0].body.providers[1].base_url, "");
  assert.equal(env.puts("config")[0].body.providers[1].kind, "anthropic");
  // ...but an OpenAI-compatible card still needs one.
  control(main, "Base URL (OpenAI-compatible)");
  const urls = findAll(main, (e) => e.tagName === "LABEL" && e.textContent.startsWith("Base URL") && !e.textContent.includes("OpenAI"))
    .map((l) => findAll(l, (e) => e.tagName === "INPUT")[0]);
  urls[0].value = "";
  await button(main, "Save providers").click();
  assert.equal(env.puts("config").length, 1);
  assert.match(env.toasts().at(-1).text, /Provider groq: base URL must not be blank/);
});

test("a 4401 close (unauthenticated socket) signals unauthorized instead of reconnecting", async () => {
  const clock = installEnvironment();
  const dispatched = [];
  globalThis.window = { dispatchEvent: (e) => dispatched.push(e.type) };
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  events.start();
  serverClose(FakeSocket.all[0], 4401);
  assert.deepEqual(dispatched, ["minty:unauthorized"]);
  assert.equal(clock.pending(), 0);
  clock.fire();
  assert.equal(FakeSocket.all.length, 1);
  // The socket is finished, so a later start() (after logging in again) opens a fresh one.
  events.start();
  assert.equal(FakeSocket.all.length, 2);
  // Any other close code still reconnects and does not signal.
  serverClose(FakeSocket.all[1], 1006);
  assert.equal(clock.pending(), 1);
  assert.equal(dispatched.length, 1);
  events.stop();
});

test("any other close reconnects without probing the API (4401 alone signals a dead session)", async () => {
  const clock = installEnvironment();
  const dispatched = [];
  globalThis.window = { dispatchEvent: (e) => dispatched.push(e.type) };
  const { makeEvents } = await load("events.js");
  const events = makeEvents();
  events.start();
  serverClose(FakeSocket.all[0], 1006);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(clock.probes, []);
  assert.deepEqual(dispatched, []);
  assert.equal(clock.pending(), 1);
  clock.fire();
  assert.equal(FakeSocket.all.length, 2);
  events.stop();
});

// ---- Plugins and Memory pages (fake DOM and fetch helpers are defined above) ----

const DEMO = {
  name: "demo", description: "Demo plugin", enabled: true, error: null, tools: ["demo_a", "demo_b"],
  settings_schema: {
    city: { type: "string", label: "Default city" },
    count: { type: "number", label: "Count" },
    loud: { type: "boolean", label: "Loud" },
    options: { type: "json", label: "Options" },
    token: { type: "string", label: "Token", secret: true },
  },
};
const secretName = "plugin.demo.token";
const getsOf = (env, path) => env.calls.filter((c) => c.method === "GET" && c.path === path);

// A fake core: the plugin list, config and masked secrets live in `state` and change when written.
function pluginsServer(state, { failPluginPut = null } = {}) {
  state.plugins ??= [DEMO];
  state.config ??= { plugins: {} };
  state.secrets ??= {};
  return (call) => {
    if (call.path === "plugins") return state.plugins;
    if (call.path === "config") return state.config;
    if (call.path === "secrets") return { ...state.secrets };
    if (call.path === `secrets/${secretName}`) { state.secrets[secretName] = "****" + call.body.value.slice(-4); return undefined; }
    if (call.path.startsWith("plugins/") && call.method === "PUT") {
      if (failPluginPut) return failPluginPut;
      return state.plugins;
    }
    throw new Error(`unexpected ${call.method} ${call.path}`);
  };
}
const mutations = (env) => env.calls.filter((c) => c.method !== "GET" && c.method !== undefined);

test("plugins page renders manifest text through text nodes, never markup", async () => {
  const evil = { ...DEMO, description: "<img src=x onerror=alert(1)>", error: "<script>x</script>", tools: ["<b>t</b>"] };
  pageEnvironment(pluginsServer({ plugins: [evil] }));
  const main = await mount("plugins");
  assert.equal(findAll(main, (e) => ["IMG", "SCRIPT", "B"].includes(e.tagName)).length, 0);
  assert.ok(main.textContent.includes("<img src=x onerror=alert(1)>"));
  assert.ok(main.textContent.includes("Failed to load: <script>x</script>"));
  assert.ok(main.textContent.includes("Tools: <b>t</b>"));
});

test("plugins page lists every plugin card, or says there are none", async () => {
  pageEnvironment(pluginsServer({ plugins: [DEMO, { ...DEMO, name: "other", settings_schema: {} }] }));
  const main = await mount("plugins");
  assert.deepEqual(findAll(main, (e) => e.tagName === "STRONG").map((e) => e.textContent), ["demo", "other"]);
  pageEnvironment(pluginsServer({ plugins: [] }));
  assert.match((await mount("plugins")).textContent, /No plugins found/);
});

test("plugins page: control kinds, saved values, and a write-only secret input", async () => {
  pageEnvironment(pluginsServer({
    config: { plugins: { demo: { city: "Leeds", count: 3, loud: true, options: { a: 1 } } } },
    secrets: { [secretName]: "****wxyz" },
  }));
  const main = await mount("plugins");
  assert.equal(control(main, "Default city").value, "Leeds");
  assert.equal(control(main, "Count").value, "3");
  assert.equal(control(main, "Loud").checked, true);
  const options = control(main, "Options");
  assert.equal(options.tagName, "TEXTAREA");
  assert.deepEqual(JSON.parse(options.value), { a: 1 });
  const token = control(main, "Token");
  assert.equal(token.getAttribute("type"), "password");
  assert.equal(token.getAttribute("autocomplete"), "new-password");
  assert.equal(token.getAttribute("placeholder"), "stored: ****wxyz");
  assert.equal(token.value, "");
  assert.ok(!main.textContent.includes("wxyz"));
});

test("plugins save: refetches fresh state, parses JSON, omits blank numbers, secret stays write-only", async () => {
  const state = { plugins: [{ ...DEMO, enabled: false }] };
  const env = pageEnvironment(pluginsServer(state));
  const main = await mount("plugins");
  assert.equal(control(main, "Token").getAttribute("placeholder"), "not set");
  env.calls.length = 0;
  control(main, "Default city").value = "  Leeds ";
  control(main, "Loud").checked = true;
  control(main, "Options").value = '{"a": [1, 2]}';
  control(main, "Token").value = " s3cret ";
  control(main, "Enabled").checked = true;
  await button(main, "Save").click();
  assert.deepEqual(env.calls.map((c) => `${c.method} ${c.path}`), [
    "GET plugins", "GET secrets", "GET config",
    `PUT secrets/${secretName}`,
    "PUT plugins/demo",
    "GET secrets",
  ]);
  assert.deepEqual(env.calls[3].body, { value: "s3cret" });
  assert.deepEqual(env.calls[4].body, {
    enabled: true,
    settings: { city: "Leeds", loud: true, options: { a: [1, 2] } },
  });
  const token = control(main, "Token");
  assert.equal(token.value, "");
  assert.equal(token.getAttribute("placeholder"), "stored: ****cret");
  assert.ok(!main.textContent.includes("s3cret"));
  assert.match(env.toasts().at(-1).text, /demo saved/);
});

test("plugins save: a number that is set is sent as a number, a blank one is omitted", async () => {
  const env = pageEnvironment(pluginsServer({}));
  const main = await mount("plugins");
  control(main, "Count").value = "2.5";
  await button(main, "Save").click();
  assert.equal(env.puts("plugins/demo")[0].body.settings.count, 2.5);
  control(main, "Count").value = "";
  await button(main, "Save").click();
  const settings = env.puts("plugins/demo")[1].body.settings;
  assert.ok(!("count" in settings), "blank number must be omitted, not 0");
  assert.ok(!("token" in settings), "secrets never travel in plugin settings");
});

const SMART = {
  name: "smart_home", description: "Home Assistant", enabled: true, error: null, tools: [],
  settings_schema: {
    url: { type: "string", label: "Home Assistant URL" },
    token: { type: "string", label: "Access token", secret: true },
    rooms: { type: "json", label: "Rooms" },
    port: { type: "number", label: "Port" },
  },
};

test("plugins save: clearing a setting that had a value sends an explicit empty string", async () => {
  const env = pageEnvironment(pluginsServer({
    plugins: [SMART],
    config: { plugins: { smart_home: { url: "http://ha.local:8123", rooms: { a: 1 }, port: 8123 } } },
  }));
  const main = await mount("plugins");
  control(main, "Home Assistant URL").value = "";
  await button(main, "Save").click();
  // Exactly the cleared field is sent; the untouched rooms and port are left alone.
  assert.deepEqual(env.puts("plugins/smart_home")[0].body, { settings: { url: "" } });
  // A cleared JSON setting is cleared the same way; a cleared number is still left alone.
  control(main, "Rooms").value = "  ";
  control(main, "Port").value = "";
  await button(main, "Save").click();
  const second = env.puts("plugins/smart_home")[1].body.settings;
  assert.equal(second.rooms, "");
  assert.ok(!("port" in second));
  // Once the clear is saved, a blank field is a no-op again.
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/smart_home")[2].body.settings, {});
});

test("plugins save: a blank setting that never had a value is still omitted", async () => {
  const env = pageEnvironment(pluginsServer({ plugins: [SMART], config: { plugins: {} } }));
  const main = await mount("plugins");
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/smart_home")[0].body.settings, {});
});

const FLAGGED = {
  ...DEMO, name: "flagged", enabled: true,
  settings_schema: {
    city: { type: "string", label: "Default city" },
    loud: { type: "boolean", label: "Loud" },
    chatty: { type: "boolean", label: "Chatty", default: true },
  },
};

test("plugins save: only changed fields are sent; an untouched unset boolean is not saved as false", async () => {
  const env = pageEnvironment(pluginsServer({ plugins: [FLAGGED], config: { plugins: { flagged: { city: "Leeds" } } } }));
  const main = await mount("plugins");
  // Unset booleans show the manifest default, else off.
  assert.equal(control(main, "Loud").checked, false);
  assert.equal(control(main, "Chatty").checked, true);
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[0].body, { settings: {} });
  control(main, "Loud").checked = true;
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[1].body, { settings: { loud: true } });
  // Saved once, the box is the new baseline: a second save sends nothing.
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[2].body, { settings: {} });
  control(main, "Chatty").checked = false;
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[3].body, { settings: { chatty: false } });
});

test("plugins save: enabled is sent only when toggled, so an outside change is not reverted", async () => {
  const state = { plugins: [FLAGGED], config: { plugins: { flagged: { city: "Leeds" } } } };
  const env = pageEnvironment(pluginsServer(state));
  const main = await mount("plugins");
  // Meanwhile (voice command, another tab) the plugin was disabled and the city kept.
  state.plugins = [{ ...FLAGGED, enabled: false }];
  control(main, "Default city").value = "York";
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[0].body, { settings: { city: "York" } });
  // Toggling it is sent, and only then.
  control(main, "Enabled").checked = false;
  state.plugins = [FLAGGED];
  await button(main, "Save").click();
  assert.deepEqual(env.puts("plugins/flagged")[1].body, { enabled: false, settings: {} });
});

test("plugins save: a changed field whose stored value moved on is refused, unchanged ones never block", async () => {
  const state = { plugins: [FLAGGED], config: { plugins: { flagged: { city: "Leeds" } } } };
  const env = pageEnvironment(pluginsServer(state));
  const main = await mount("plugins");
  state.config = { plugins: { flagged: { city: "Paris" } } };
  await button(main, "Save").click(); // nothing edited: the moved city is none of our business
  assert.equal(env.puts("plugins/flagged").length, 1);
  control(main, "Default city").value = "York";
  await button(main, "Save").click();
  assert.equal(env.puts("plugins/flagged").length, 1);
  assert.equal(env.toasts().at(-1).text, "flagged.city changed elsewhere — reload.");
  assert.equal(env.toasts().at(-1).error, true);
  // The same guard covers the enabled switch.
  control(main, "Default city").value = "Leeds";
  control(main, "Enabled").checked = false;
  state.plugins = [{ ...FLAGGED, enabled: false }];
  await button(main, "Save").click();
  assert.equal(env.puts("plugins/flagged").length, 1);
  assert.equal(env.toasts().at(-1).text, "flagged.enabled changed elsewhere — reload.");
});

test("plugins save: invalid JSON is an error toast and nothing is written", async () => {
  const env = pageEnvironment(pluginsServer({}));
  const main = await mount("plugins");
  control(main, "Options").value = "{not json";
  control(main, "Token").value = "s3cret";
  await button(main, "Save").click();
  assert.deepEqual(mutations(env), []);
  const toast = env.toasts().at(-1);
  assert.ok(toast.error);
  assert.match(toast.text, /Options.*JSON/);
  assert.equal(button(main, "Save").disabled, false);
  assert.equal(control(main, "Token").value, "s3cret");
});

test("plugins save: a plugin whose settings changed on the server is not saved from stale data", async () => {
  const state = {};
  const env = pageEnvironment(pluginsServer(state));
  const main = await mount("plugins");
  state.plugins = [{ ...DEMO, settings_schema: { city: { type: "number" } } }];
  control(main, "Default city").value = "Leeds";
  await button(main, "Save").click();
  assert.deepEqual(mutations(env), []);
  assert.match(env.toasts().at(-1).text, /changed on the server/);
  state.plugins = [];
  await button(main, "Save").click();
  assert.deepEqual(mutations(env), []);
  assert.match(env.toasts().at(-1).text, /no longer/);
});

test("plugins save: a rejected plugin write still shows the secret that was stored", async () => {
  const env = pageEnvironment(pluginsServer({}, { failPluginPut: { status: 422, data: { detail: "count: must be positive" } } }));
  const main = await mount("plugins");
  control(main, "Token").value = "s3cret";
  await button(main, "Save").click();
  assert.ok(env.toasts().at(-1).error);
  assert.match(env.toasts().at(-1).text, /count: must be positive/);
  assert.equal(control(main, "Token").getAttribute("placeholder"), "stored: ****cret");
  assert.equal(control(main, "Token").value, "");
});

test("plugins save: after enabling, the card shows the plugin's new load error and tools", async () => {
  const state = { plugins: [{ ...DEMO, enabled: false, tools: [] }] };
  const env = pageEnvironment(pluginsServer(state));
  const main = await mount("plugins");
  assert.ok(!main.textContent.includes("Tools:"));
  state.plugins = [{ ...DEMO, enabled: true, error: "ImportError: <nope>", tools: ["demo_a"] }];
  await button(main, "Save").click();
  assert.equal(getsOf(env, "plugins").length, 2);
  assert.ok(main.textContent.includes("Failed to load: ImportError: <nope>"));
  assert.ok(main.textContent.includes("Tools: demo_a"));
});

function memoryServer(state) {
  return (call) => {
    if (call.path === "memory" && call.method === "GET") return { notes: [...state.notes] };
    const match = call.path.match(/^memory\/(\d+)$/);
    if (match && call.method === "DELETE") { state.notes.splice(Number(match[1]), 1); return undefined; }
    throw new Error(`unexpected ${call.method} ${call.path}`);
  };
}
const forgetButtons = (main) => findAll(main, (e) => e.tagName === "BUTTON" && e.textContent === "Forget");

test("memory page: empty state, and notes render as text", async () => {
  pageEnvironment(memoryServer({ notes: [] }));
  assert.match((await mount("memory")).textContent, /Nothing remembered yet/);
  pageEnvironment(memoryServer({ notes: ["likes <b>tea</b>"] }));
  const main = await mount("memory");
  assert.equal(findAll(main, (e) => e.tagName === "B").length, 0);
  assert.ok(main.textContent.includes("likes <b>tea</b>"));
  assert.ok(!main.textContent.includes("Nothing remembered yet"));
});

test("memory forget needs confirmation and then redraws from the server", async () => {
  const state = { notes: ["likes tea", "owns a cat"] };
  const env = pageEnvironment(memoryServer(state));
  const main = await mount("memory");
  env.answers.confirm = false;
  await forgetButtons(main)[1].click();
  assert.deepEqual(mutations(env), []);
  assert.equal(forgetButtons(main).length, 2);
  let asked = "";
  globalThis.confirm = (message) => { asked = message; return true; };
  await forgetButtons(main)[1].click();
  assert.ok(asked.includes("owns a cat"));
  assert.deepEqual(mutations(env).map((c) => `${c.method} ${c.path}`), ["DELETE memory/1"]);
  assert.equal(forgetButtons(main).length, 1);
  assert.ok(!main.textContent.includes("owns a cat"));
});

test("memory forget refuses to delete a different note when the list changed underneath", async () => {
  const state = { notes: ["likes tea", "owns a cat"] };
  const env = pageEnvironment(memoryServer(state));
  const main = await mount("memory");
  state.notes.shift();
  await forgetButtons(main)[1].click();
  assert.deepEqual(mutations(env), []);
  assert.match(env.toasts().at(-1).text, /changed/);
  assert.ok(!main.textContent.includes("likes tea"));
  assert.equal(forgetButtons(main).length, 1);
});
