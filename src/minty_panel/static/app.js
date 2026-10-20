import { makeEvents } from "./events.js";
import { api, h, report, Unauthorized } from "./lib.js";
import { createRunner } from "./runner.js";
import dashboard from "./pages/dashboard.js";
import logs from "./pages/logs.js";
import personality from "./pages/personality.js";
import audio from "./pages/audio.js";
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
  ["timeline", "Timeline", timeline],
  ["personality", "Personality", personality],
  ["audio", "Voice & Safety", audio],
  ["brain", "Brain", brain],
  ["plugins", "Plugins", plugins],
  ["memory", "Memory", memory],
  ["notes", "Notes", notes],
  ["audit", "What I know", audit],
  ["prompts", "Prompts", prompts],
  ["flags", "Switches", flags],
  ["logs", "Logs", logs],
];

const app = document.getElementById("app");

const events = makeEvents({ onError: report });
const runner = createRunner();
let loggedIn = false;

// Session expired (any 401 from api()): back to the login screen, once.
function expire() {
  if (loggedIn) showLogin("Session expired. Please log in again.");
}
window.addEventListener("minty:unauthorized", expire);

async function render(slot, nav) {
  const key = location.hash.slice(1) || "dashboard";
  const page = PAGES.find((p) => p[0] === key) || PAGES[0];
  nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.key === page[0]));
  // A fresh <main> per render: a page that finishes late can only write into its own detached element.
  const target = h("main", {}, h("p", { class: "muted" }, "Loading…"));
  nav.after(target);
  slot.current.remove();
  slot.current = target;
  try {
    await runner.run(() => page[2](target, events));
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

function showApp() {
  loggedIn = true;
  const slot = { current: h("main") };
  const nav = h("nav", {}, h("h1", {}, "Minty"),
    PAGES.map(([key, label]) => h("a", { href: "#" + key, "data-key": key }, label)),
    h("div", { class: "spacer" }),
    h("button", { class: "link", type: "button", onclick: logout }, "Log out"));
  app.replaceChildren(nav, slot.current);
  events.start();
  window.onhashchange = () => render(slot, nav);
  render(slot, nav);
}

function showLogin(message = "") {
  loggedIn = false;
  events.stop();
  runner.stop();
  window.onhashchange = null;
  const password = h("input", { type: "password", autocomplete: "current-password", autofocus: true });
  const error = h("p", { class: "error" }, message);
  const form = h("form", { class: "login card" }, h("h1", {}, "Minty"),
    h("label", { class: "field" }, h("span", { class: "label" }, "Panel password"), password),
    error, h("button", { class: "primary", type: "submit" }, "Log in"));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "minty" },
        body: JSON.stringify({ password: password.value }),
      });
      if (res.status === 204) return showApp();
      const data = await res.json().catch(() => ({}));
      error.textContent = data.detail || `Login failed (HTTP ${res.status})`;
    } catch {
      error.textContent = "Cannot reach the panel server. Check that it is running and try again.";
    }
  });
  app.replaceChildren(form);
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
