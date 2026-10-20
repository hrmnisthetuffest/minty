export class Unauthorized extends Error {}

// "body" is FastAPI's own prefix; provider list indexes are shown 1-based ("providers #3 model").
function describeLoc(loc) {
  const parts = loc[0] === "body" ? loc.slice(1) : loc;
  let out = "";
  parts.forEach((part, i) => {
    if (typeof part === "number" && parts[i - 1] === "providers") {
      out += ` #${part + 1}`;
      return;
    }
    out += (out === "" ? "" : /#\d+$/.test(out) ? " " : ".") + part;
  });
  return out;
}

export function describe(data) {
  if (!data) return "";
  if (typeof data === "string") return data;
  const detail = data.detail ?? data;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      const where = describeLoc(e.loc || []);
      return where ? `${where}: ${e.msg}` : String(e.msg);
    }).join("; ");
  }
  return JSON.stringify(detail);
}

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: { "X-Requested-With": "minty" }, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch("/api/" + path, options);
  if (res.status === 401) {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("minty:unauthorized"));
    throw new Unauthorized();
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error(describe(data) || `HTTP ${res.status}`);
  return data;
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (value === true) el.setAttribute(key, "");
    else if (value !== false && value != null) el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function toast(message, isError = false) {
  const el = h("div", { class: "toast" + (isError ? " error" : "") }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 2500);
}

// Surface an error as a toast; a 401 is handled globally by the "minty:unauthorized" listener.
export function report(e) {
  if (!(e instanceof Unauthorized)) toast(e.message, true);
}

export function action(label, fn, cls = "") {
  const button = h("button", { class: cls, type: "button" }, label);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const message = await fn();
      if (message) toast(message);
    } catch (e) {
      report(e);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

export const field = (label, input, help) =>
  h("label", { class: "field" }, h("span", { class: "label" }, label), input, help ? h("small", {}, help) : null);
export const textInput = (value, attrs = {}) => h("input", { type: "text", value: value ?? "", ...attrs });
export const numberInput = (value, attrs = {}) => h("input", { type: "number", value: value ?? "", ...attrs });
export function checkbox(checked) {
  const el = h("input", { type: "checkbox" });
  el.checked = !!checked;
  return el;
}
export function select(options, current) {
  // A value the list does not know (older config, hand edit) stays selectable so saving never rewrites it.
  const known = current == null || options.includes(current) ? options : [...options, current];
  const el = h("select", {}, known.map((o) => h("option", { value: o }, o)));
  el.value = current;
  return el;
}
export const encode = encodeURIComponent;

// While the microphone is muted the whole panel turns red, like the terminal display.
export function setMuted(on) {
  if (typeof document !== "undefined" && document.body && document.body.classList) document.body.classList.toggle("mic-off", !!on);
}

// A page heading: a small label, the big title and an optional line under it.
export function pageHead(kicker, title, lead) {
  return h("div", { class: "page-head" }, kicker ? h("p", { class: "kicker" }, kicker) : null, h("h2", {}, title),
    lead ? h("p", { class: "lead" }, lead) : null);
}
