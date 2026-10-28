import { api, checkbox, h, pageHead, report, select, toast } from "../lib.js";
import { ClipPlayer, thumbUrl } from "../player.js";

// The studio: today's background style, a player, and the controls for what gets shown. Clips are fetched one at a time, with one more
// loaded ahead, and nothing downloads until you look at it (a picture first).
export default async function studio(main) {
  const front = h("video", { class: "clip on", "aria-label": "Today's background clip" });
  const back = h("video", { class: "clip", "aria-hidden": "true" });
  const note = h("p", { class: "muted clip-note" }, "");
  const status = h("div", { class: "clip-status" });
  const box = h("div", { class: "clip-box" }, front, back, status);
  const caption = h("p", { class: "muted" }, "");
  const stylesBox = h("div");
  const gridBox = h("div", { class: "clip-grid" });
  let data = null;
  let queue = [];
  const seen = new Set();

  const player = new ClipPlayer(front, back, (state, detail) => {
    status.textContent = state === "playing" || state === "loading" ? "" : detail;
    status.classList.toggle("show", state === "error" || state === "offline" || state === "saving");
    note.textContent = state === "playing" ? detail : "";
    if (state === "saving") status.append(h("button", { type: "button", class: "primary", onclick: () => player.play() }, "Play"));
    if (state === "error") status.append(h("button", { type: "button", onclick: () => skipCurrent() }, "Skip this clip"));
  });

  const describe = (c) => `${c.category}${c.credit ? " · " + c.credit : ""}`;

  async function refill() {
    const next = await api("studio/next?count=3");
    queue = next.filter((c) => !seen.has(c.id));
    if (!queue.length) {
      seen.clear();
      queue = next;
    }
  }

  function present(clip) {
    seen.add(clip.id);
    caption.textContent = `Now: ${describe(clip)}`;
    player.show(clip);
    queue = queue.filter((c) => c.id !== clip.id);
    player.preload(queue[0]);
  }

  async function next() {
    if (queue.length < 1) await refill();
    const clip = queue.shift();
    if (clip) present(clip);
    if (queue.length < 2) refill().then(() => player.preload(queue[0])).catch(() => {});
  }

  async function shuffle() {
    const clip = await api("studio/shuffle", { method: "POST" });
    queue = [];
    present(clip);
    await load(false);
    refill().then(() => player.preload(queue[0])).catch(() => {});
  }

  async function prefer(body) {
    const out = await api("studio/prefer", { method: "POST", body });
    toast(out.message);
    await load(false);
  }

  async function skipCurrent() {
    if (player.current) await prefer({ asset: String(player.current.id), choice: "skip" });
    await next();
  }

  function drawStyles() {
    stylesBox.replaceChildren(h("h3", {}, "Styles"), ...data.styles.map((s) => {
      const on = checkbox(!s.off);
      on.addEventListener("change", () => prefer({ category: s.key, choice: on.checked ? "on" : "off" }).catch((e) => { on.checked = !on.checked; report(e); }));
      const more = h("button", { type: "button", onclick: () => prefer({ category: s.key, choice: "more" }).catch(report) }, "More");
      const less = h("button", { type: "button", onclick: () => prefer({ category: s.key, choice: "less" }).catch(report) }, "Less");
      return h("label", { class: "card row" }, on, h("span", {}, `${s.label}`), h("span", { class: "muted" }, s.own_only && !s.count ? "your own recordings only" : `${s.count} clip${s.count === 1 ? "" : "s"}`),
        s.count ? [more, less] : null);
    }));
    const mode = select(["daily", "each_video"], data.mode);
    mode.addEventListener("change", () => prefer({ choice: mode.value }).catch(report));
    const backdrop = checkbox(localStorage.getItem("minty.backdrop") === "1");
    backdrop.addEventListener("change", () => {
      localStorage.setItem("minty.backdrop", backdrop.checked ? "1" : "0");
      window.dispatchEvent(new Event("minty:backdrop"));
    });
    stylesBox.append(
      h("label", { class: "card row" }, h("span", {}, "A new style"), mode, h("span", { class: "muted" }, "daily: one style a day; each_video: a new one every time")),
      h("label", { class: "card row" }, backdrop, h("span", {}, "Show today's clip dimmed behind the panel"), h("span", { class: "muted" }, "off by default; never on data saving")));
  }

  function drawGrid() {
    gridBox.replaceChildren(...data.clips.map((c) => {
      const play = h("button", { type: "button", class: "clip-thumb", onclick: () => present(c), title: describe(c) },
        h("img", { src: thumbUrl(c.id), loading: "lazy", alt: describe(c), width: "90", height: "160" }));
      const fav = h("button", { type: "button", class: c.favourite ? "primary" : "", onclick: () => prefer({ asset: String(c.id), choice: c.favourite ? "unfavourite" : "favourite" }).catch(report) }, c.favourite ? "★" : "☆");
      const skip = h("button", { type: "button", class: c.skipped ? "primary" : "", onclick: () => prefer({ asset: String(c.id), choice: c.skipped ? "unskip" : "skip" }).catch(report) }, c.skipped ? "skipped" : "skip");
      return h("div", { class: "clip-cell" }, play, h("div", { class: "row" }, fav, skip));
    }));
  }

  async function load(first) {
    data = await api("studio/catalog");
    drawStyles();
    drawGrid();
    if (first && data.today) {
      queue = [];
      present(data.today);
      refill().then(() => player.preload(queue[0])).catch(() => {});
    }
  }

  main.replaceChildren(
    pageHead("Studio", "Background footage", "A different style each day, from your own recordings and from providers whose licences allow it. Ask Minty to discover more."),
    h("div", { class: "card" }, box, note, caption,
      h("div", { class: "row gap-top" },
        h("button", { type: "button", onclick: () => next().catch(report) }, "Another clip"),
        h("button", { type: "button", class: "primary", onclick: () => shuffle().catch(report) }, "Shuffle the style"))),
    stylesBox, h("h3", {}, "Clips"), gridBox);
  await load(true);
  if (!data.today) box.replaceWith(h("p", { class: "muted" }, "No clips yet. Ask Minty to \"discover background footage\", or add your own recordings."));
  return () => player.destroy();
}
