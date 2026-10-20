import { makeEvents } from "./events.js";
import { api, h, report, setMuted, Unauthorized } from "./lib.js";
import { createPalette } from "./palette.js";
import { createPinPad } from "./pinpad.js";
import { SECTIONS } from "./settings-schema.js";
import { createRunner } from "./runner.js";
import dashboard from "./pages/dashboard.js";
import logs from "./pages/logs.js";
import personality from "./pages/personality.js";
import settings from "./pages/settings.js";
import profile from "./pages/profile.js";
import brain from "./pages/brain.js";
import plugins from "./pages/plugins.js";
import memory from "./pages/memory.js";
import notes from "./pages/notes.js";
import audit from "./pages/audit.js";
import prompts from "./pages/prompts.js";
import flags from "./pages/flags.js";
import timeline from "./pages/timeline.js";

const PAGES = [
  ["dashboard", "Dashboard", dashboard],
  ["settings", "Settings", settings],
  ["profile", "Profile", profile],
  ["brain", "Brain", brain],
  ["personality", "Personality", personality],
  ["plugins", "Plugins", plugins],
  ["memory", "Memory", memory],
  ["notes", "Notes", notes],
  ["timeline", "Timeline", timeline],
  ["audit", "What I know", audit],
  ["prompts", "Prompts", prompts],
  ["flags", "Switches", flags],
  ["logs", "Logs", logs],
];
const ALIASES = { audio: "settings" }; // the old address of the voice and safety page

// What the search bar can jump to: every page, and every setting.
const SEARCH_ITEMS = [
  ...PAGES.map(([key, label]) => ({ label, hint: "Page", page: true, hash: key, text: `${label} ${key}`.toLowerCase() })),
  ...SECTIONS.flatMap((sec) => sec.rows.map(([section, key, label, , help]) => ({
    label, hint: sec.title, hash: `settings:${section}.${key}`, text: `${label} ${help} ${key} ${sec.title}`.toLowerCase(),
  }))),
];

const app = document.getElementById("app");

const events = makeEvents({ onError: report });
const runner = createRunner();
let loggedIn = false;
let loginKeys = null; // the keyboard handler of the PIN pad while the login screen is showing

// Session expired (any 401 from api()): back to the login screen, once.
function expire() {
  if (loggedIn) showLogin("Session expired. Please log in again.");
}
window.addEventListener("minty:unauthorized", expire);

async function render(slot, nav) {
  const [name, arg = ""] = (location.hash.slice(1) || "dashboard").split(":");
  const key = ALIASES[name] || name;
  const page = PAGES.find((p) => p[0] === key) || PAGES[0];
  nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.key === page[0]));
  // A fresh <main> per render: a page that finishes late can only write into its own detached element.
  const target = h("main", {}, h("p", { class: "muted" }, "Loading…"));
  nav.after(target);
  slot.current.remove();
  slot.current = target;
  try {
    await runner.run(() => page[2](target, events, arg));
  } catch (e) {
    if (e instanceof Unauthorized) return expire();
    target.replaceChildren(h("h2", {}, page[1]), h("p", { class: "error" }, e.message));
  }
}

async function logout() {
  try {
    await fetch("/logout", { method: "POST", headers: { "X-Requested-With": "minty" } });
  } catch {
    // Network failure: leave the panel anyway; the session cookie expires on its own.
  }
  showLogin();
}

const palette = createPalette(document.getElementById("palette"), SEARCH_ITEMS, (hash) => { location.hash = hash; });
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (loggedIn) palette.toggle();
  } else if (event.key === "Escape" && palette.isOpen()) palette.close();
});

const MARK = () => {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 512 512");
  const frame = document.createElementNS(ns, "rect");
  for (const [k, v] of Object.entries({ x: 12, y: 12, width: 488, height: 488, fill: "none", stroke: "currentColor", "stroke-width": 28 })) frame.setAttribute(k, v);
  const m = document.createElementNS(ns, "path");
  m.setAttribute("d", "M120 392V120h56l80 132 80-132h56v272h-52V212l-84 138-84-138v180z");
  m.setAttribute("fill", "currentColor");
  svg.append(frame, m);
  return svg;
};

function showApp() {
  loggedIn = true;
  if (loginKeys) window.removeEventListener("keydown", loginKeys);
  loginKeys = null;
  const slot = { current: h("main") };
  const nav = h("header", { class: "top" },
    h("div", { class: "bar" },
      h("a", { class: "brand", href: "#dashboard" }, MARK(), h("span", {}, "Minty")),
      h("nav", {}, PAGES.map(([key, label]) => h("a", { href: "#" + key, "data-key": key }, label))),
      h("div", { class: "bar-right" }, h("button", { class: "link", type: "button", onclick: logout }, "Log out"))),
    h("div", { class: "cmdwrap" },
      h("button", { class: "cmdbar", type: "button", onclick: () => palette.open() },
        h("span", {}, "Search settings and pages"), h("span", { class: "hint" }, "Toggle"), h("span", {}, h("kbd", {}, "Ctrl"), h("kbd", {}, "K")))));
  app.replaceChildren(nav, slot.current);
  events.start();
  window.onhashchange = () => render(slot, nav);
  render(slot, nav);
  api("status").then((status) => setMuted(status.muted)).catch(() => {});
}

function showLogin(message = "") {
  loggedIn = false;
  events.stop();
  runner.stop();
  window.onhashchange = null;
  setMuted(false);
  const pad = createPinPad({ onSubmit: attempt });
  // Returns "" when logged in, else the reason it was refused (the pad shows it).
  async function attempt(secret) {
    try {
      const res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "minty" },
        body: JSON.stringify({ password: secret }),
      });
      if (res.status === 204) {
        showApp();
        return "";
      }
      const data = await res.json().catch(() => ({}));
      return data.detail || `Login failed (HTTP ${res.status})`;
    } catch {
      return "Cannot reach the panel server. Check that it is running and try again.";
    }
  }
  if (loginKeys) window.removeEventListener("keydown", loginKeys);
  loginKeys = (event) => {
    if (!loggedIn && !event.ctrlKey && !event.metaKey && !event.altKey && pad.handleKey(event)) event.preventDefault();
  };
  window.addEventListener("keydown", loginKeys);
  if (message) pad.setError(message);
  app.replaceChildren(h("div", { class: "login card" }, h("p", { class: "kicker" }, "Control panel"), h("h1", {}, "Minty"), pad.el));
}

try {
  await api("status");
  showApp();
} catch (e) {
  if (e instanceof Unauthorized) showLogin();
  else showApp();
}

// Installable and quick to open where the browser allows it (localhost or HTTPS). The worker never touches the API.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
