import { makeEvents } from "./events.js";
import { api, h, report, setMuted, Unauthorized } from "./lib.js";
import { createPalette } from "./palette.js";
import { createPinPad } from "./pinpad.js";
import { createStatusBar } from "./statusbar.js";
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
import studio from "./pages/studio.js";
import flows from "./pages/flows.js";
import mission from "./pages/mission.js";
import knowledge from "./pages/knowledge.js";
import { initBackdrop, stopBackdrop } from "./backdrop.js";

const PAGES = [
  ["dashboard", "Dashboard", dashboard],
  ["mission", "Mission Control", mission],
  ["timeline", "Timeline", timeline],
  ["logs", "Logs", logs],
  ["studio", "Studio", studio],
  ["flows", "Automations", flows],
  ["settings", "Settings", settings],
  ["profile", "Profile", profile],
  ["personality", "Personality", personality],
  ["brain", "Brain", brain],
  ["plugins", "Plugins", plugins],
  ["flags", "Switches", flags],
  ["prompts", "Prompts", prompts],
  ["knowledge", "Second Brain", knowledge],
  ["memory", "Memory", memory],
  ["notes", "Notes", notes],
  ["audit", "What I know", audit],
];
// The menu: pages in groups, each with a small line icon (a path in a 16 x 16 box).
const GROUPS = [
  ["Monitor", ["dashboard", "mission", "timeline", "logs", "studio"]],
  ["Automate", ["flows"]],
  ["Configure", ["settings", "profile", "personality", "brain", "plugins", "flags", "prompts"]],
  ["Memory", ["knowledge", "memory", "notes", "audit"]],
];
const ICONS = {
  dashboard: "M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z",
  mission: "M8 2l6 3v4c0 3-2.5 4.5-6 6-3.5-1.5-6-3-6-6V5zM5.5 8l2 2 3.5-3.5",
  timeline: "M3 2v12M3 4h9M3 8h6M3 12h8",
  logs: "M2 4l4 4-4 4M8 12h6",
  studio: "M2 3h12v10H2zM7 6l3.5 2L7 10z",
  flows: "M2 2h4v4H2zM10 10h4v4h-4zM6 4h2.5v6.5H10",
  settings: "M2 5h12M2 11h12M6 3v4M10 9v4",
  profile: "M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 14c0-3 2-4 5-4s5 1 5 4",
  personality: "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM5.5 9.5c1.5 2 3.5 2 5 0M6 6v1M10 6v1",
  brain: "M5 5h6v6H5zM8 2v3M8 11v3M2 8h3M11 8h3",
  plugins: "M3 6h4V3h3v3h3v4h-3v3H7v-3H3z",
  flags: "M2 5h12v6H2zM9 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  prompts: "M3 3h10v8H8l-3 3v-3H3z",
  knowledge: "M4 3.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zM12 6a1.6 1.6 0 1 0 0 3.2A1.6 1.6 0 0 0 12 6zM7 11a1.6 1.6 0 1 0 0 3.2A1.6 1.6 0 0 0 7 11zM5.4 6.4l1.2 4.7M10.8 9l-2.6 2.6M5.6 5.2l5 1.2",
  memory: "M3 4c0-1.5 10-1.5 10 0v8c0 1.5-10 1.5-10 0zM3 8c0 1.5 10 1.5 10 0",
  notes: "M4 2h8v12H4zM6 5h4M6 8h4M6 11h2",
  audit: "M1 8s3-5 7-5 7 5 7 5-3 5-7 5-7-5-7-5zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
};
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

async function render(ui) {
  const [name, arg = ""] = (location.hash.slice(1) || "dashboard").split(":");
  const key = ALIASES[name] || name;
  const page = PAGES.find((p) => p[0] === key) || PAGES[0];
  ui.nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.key === page[0]));
  ui.crumb.replaceChildren(h("span", { class: "muted" }, "Control / "), page[1]);
  // A fresh <main> per render: a page that finishes late can only write into its own detached element.
  const target = h("main", {}, h("p", { class: "muted" }, "Loading…"));
  ui.slot.current.replaceWith(target);
  ui.slot.current = target;
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

const SVG = "http://www.w3.org/2000/svg";
function icon(path, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("class", "icon");
  const line = document.createElementNS(SVG, "path");
  for (const [k, v] of Object.entries({ d: path, fill: "none", stroke: "currentColor", "stroke-width": 1.4, "stroke-linecap": "round", "stroke-linejoin": "round" })) line.setAttribute(k, v);
  svg.append(line);
  return svg;
}
const MARK = () => icon("M2 14V2h2.5L8 8l3.5-6H14v12h-2.2V6L8 12.5 4.2 6v8z", 26);

let statusBar = null;
let clockTimer = 0;

function showApp() {
  loggedIn = true;
  document.body.classList.add("in-app");
  if (loginKeys) window.removeEventListener("keydown", loginKeys);
  loginKeys = null;
  const slot = { current: h("main") };
  const nav = h("nav", {}, GROUPS.map(([title, keys]) => h("div", { class: "nav-group" },
    h("div", { class: "nav-title" }, title),
    keys.map((key) => {
      const page = PAGES.find((p) => p[0] === key);
      return h("a", { href: "#" + key, "data-key": key }, icon(ICONS[key]), h("span", {}, page[1]));
    }))));
  const side = h("aside", { class: "side" },
    h("a", { class: "brand", href: "#dashboard" }, MARK(), h("span", {}, "Minty")),
    nav,
    h("div", { class: "side-foot" }, h("span", { class: "muted" }, "Control center"),
      h("button", { class: "link", type: "button", onclick: logout }, "Log out")));
  const crumb = h("div", { class: "crumb" });
  const clock = h("span", { class: "clock" });
  const tick = () => clock.replaceChildren(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  tick();
  clockTimer = setInterval(tick, 15000);
  statusBar = createStatusBar({ events, setMuted });
  const search = h("button", { class: "cmdbar", type: "button", onclick: () => palette.open() },
    h("span", {}, "Search"), h("span", { class: "kbds" }, h("kbd", {}, "Ctrl"), h("kbd", {}, "K")));
  const top = h("header", { class: "topbar" }, crumb, statusBar.el, search, clock);
  app.replaceChildren(h("div", { class: "shell" }, side, top, slot.current));
  events.start();
  const ui = { slot, nav, crumb };
  window.onhashchange = () => render(ui);
  render(ui);
  statusBar.refresh().catch(() => {});
  initBackdrop();
}

function stopApp() {
  stopBackdrop();
  if (statusBar) statusBar.destroy();
  statusBar = null;
  clearInterval(clockTimer);
  document.body.classList.remove("in-app");
}

function showLogin(message = "") {
  loggedIn = false;
  stopApp();
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
