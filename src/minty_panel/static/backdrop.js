// Optional: today's clip, dimmed, behind the whole panel. Off unless switched on in the studio page. It waits until the panel is idle,
// does nothing on a data-saving connection or with reduced motion, pauses while the tab is hidden, and gives up quietly on any error.
import { api } from "./lib.js";
import { clipUrl, dataSaving, reducedMotion } from "./player.js";

let video = null;
const wanted = () => localStorage.getItem("minty.backdrop") === "1" && !dataSaving() && !reducedMotion();

function remove() {
  if (!video) return;
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.remove();
  video = null;
}

async function start() {
  if (video || !wanted()) return;
  try {
    const { today } = await api("studio/catalog");
    if (!today || video || !wanted()) return;
    video = document.createElement("video");
    video.className = "backdrop";
    Object.assign(video, { muted: true, loop: true, playsInline: true, preload: "auto" });
    video.setAttribute("aria-hidden", "true");
    video.addEventListener("error", remove);
    video.src = clipUrl(today.id);
    document.body.prepend(video);
    video.play().catch(remove);
  } catch {
    remove();
  }
}

export function initBackdrop() {
  const later = window.requestIdleCallback || ((fn) => setTimeout(fn, 1500));
  later(() => start());
  window.addEventListener("minty:backdrop", () => (wanted() ? start() : remove()));
  document.addEventListener("visibilitychange", () => {
    if (video) document.hidden ? video.pause() : video.play().catch(() => {});
  });
}

export function stopBackdrop() {
  remove();
}
