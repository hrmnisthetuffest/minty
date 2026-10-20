import { action, api, h } from "./lib.js";

const dbPercent = (db) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100)); // a -60..0 dB scale

// The bar along the top of the control centre: what Minty is doing, the microphone level, and the two buttons that matter.
// `events` is the panel's event stream (events.on(fn) returns a function that stops listening). `every` is the poll interval in
// milliseconds (0: none, for tests).
export function createStatusBar({ events, setMuted, every = 5000 }) {
  let state = "idle";
  let muted = false;
  const pill = h("span", { class: "pill", "data-state": "idle" }, "IDLE");
  const fill = h("div", { class: "mini-fill" });
  fill.setAttribute("style", "width:0%");
  const db = h("span", { class: "db" }, "-- dB");
  const meter = h("div", { class: "mini-meter" }, fill);
  const muteButton = action("Mute", async () => {
    await api("mute", { method: "POST", body: { muted: !muted } });
    await refresh();
  }, "primary");
  const listenButton = action("Listen", async () => {
    await api("listen", { method: "POST" });
  });

  function draw() {
    const shown = muted ? "muted" : state;
    pill.replaceChildren(shown.toUpperCase());
    pill.setAttribute("data-state", shown);
    muteButton.replaceChildren(muted ? "Unmute" : "Mute");
  }

  async function refresh() {
    const status = await api("status");
    state = status.state || "idle";
    muted = !!status.muted;
    if (setMuted) setMuted(muted);
    draw();
    return status;
  }

  function onEvent(event) {
    if (event.type === "state") {
      state = event.state;
      draw();
    } else if (event.type === "level") {
      const level = typeof event.db === "number" ? event.db : -60;
      fill.setAttribute("style", `width:${dbPercent(level)}%`);
      fill.className = "mini-fill" + (event.speech ? " speech" : "");
      db.replaceChildren(`${Math.round(level)} dB`);
    }
  }

  const off = events.on(onEvent);
  const timer = every > 0 ? setInterval(() => refresh().catch(() => {}), every) : 0;
  draw();

  const el = h("div", { class: "statusbar" }, pill, meter, db, h("span", { class: "spacer" }), listenButton, muteButton);
  return { el, refresh, destroy: () => { off(); if (timer) clearInterval(timer); } };
}
