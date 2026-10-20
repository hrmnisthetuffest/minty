import { h } from "./lib.js";

const MIN = 4;
const SHOWN = 6; // the dots drawn when fewer digits than this are typed
const MAX = 8;

// The login screen's PIN pad: dots that fill as you type, a keypad for touch, the keyboard for everything else. A PIN is four to eight
// digits. An older, longer password can still be typed with "Use a password instead". `onSubmit(secret)` resolves to "" on success
// or to the reason it was refused.
export function createPinPad({ onSubmit }) {
  let pin = "";
  let password = false;
  let busy = false;
  const root = h("div", { class: "pinpad" });
  const message = h("p", { class: "error pin-message" }, "");
  const dotsRow = h("div", { class: "pin-dots", "aria-label": "PIN" });
  const field = h("input", { type: "password", autocomplete: "current-password", placeholder: "Password" });

  function say(text) {
    message.replaceChildren(...(text ? [text] : []));
  }

  function drawDots() {
    const count = Math.max(SHOWN, pin.length);
    dotsRow.replaceChildren(...Array.from({ length: count }, (_, i) => h("span", { class: i < pin.length ? "pin-dot on" : "pin-dot" })));
  }

  function press(digit) {
    if (password || typeof digit !== "string" || !/^[0-9]$/.test(digit) || pin.length >= MAX) return;
    pin += digit;
    say("");
    drawDots();
  }
  function back() { pin = pin.slice(0, -1); say(""); drawDots(); }
  function clear() { pin = ""; say(""); drawDots(); }
  function value() { return password ? field.value : pin; }

  async function submit() {
    if (busy) return;
    const secret = value();
    if (!password && secret.length < MIN) { say(`Enter your PIN: ${MIN} to ${MAX} digits.`); return; }
    if (password && !secret) { say("Enter your password."); return; }
    busy = true;
    try {
      const refused = await onSubmit(secret);
      if (refused) {
        say(refused);
        if (password) field.value = ""; else { pin = ""; drawDots(); }
        root.setAttribute("data-shake", "1");
      }
    } finally {
      busy = false;
    }
  }

  function handleKey(event) {
    if (password) return false;
    const key = event.key;
    if (/^[0-9]$/.test(key)) { press(key); return true; }
    if (key === "Backspace") { back(); return true; }
    if (key === "Enter") { submit(); return true; }
    if (key === "Escape") { clear(); return true; }
    return false;
  }

  const key = (label, onclick, extra = "") => h("button", { type: "button", class: "pin-key " + extra, "aria-label": label === "⌫" ? "Delete" : undefined, onclick }, label);
  function draw() {
    if (password) {
      field.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
      root.replaceChildren(
        h("label", { class: "field" }, h("span", { class: "label" }, "Panel password"), field),
        message,
        h("div", { class: "row" }, h("button", { type: "button", class: "primary", onclick: submit }, "Log in"),
          h("button", { type: "button", class: "link", onclick: () => { password = false; draw(); } }, "Use the PIN pad")));
      return;
    }
    drawDots();
    const digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => key(d, () => press(d)));
    root.replaceChildren(
      h("p", { class: "kicker" }, "Enter your PIN"),
      h("div", { class: "pin-row" }, dotsRow, h("button", { type: "button", class: "pin-back", title: "Delete", onclick: back }, "Delete")),
      message,
      h("div", { class: "pin-keys" }, digits,
        key("Clear", clear, "pin-side"), key("0", () => press("0")), key("Enter", submit, "pin-enter")),
      h("button", { type: "button", class: "link", onclick: () => { password = true; draw(); } }, "Use a password instead"));
  }
  draw();

  return { el: root, press, back, clear, submit, value, handleKey, setError: say };
}
