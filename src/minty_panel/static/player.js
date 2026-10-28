// Plays one background clip at a time and loads at most one more ahead. Two stacked <video> elements: the next clip is buffered in the
// hidden one and the switch is a fade, so changing clips does not flash. Every transfer stops when the player is released, the tab is
// hidden or the page is left. Nothing loads by itself on a slow or data-saving connection (a picture and a button instead).

export function dataSaving() {
  const c = navigator.connection;
  return !!(c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || "")));
}
export function reducedMotion() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
export const clipUrl = (id) => `/media/clip/${id}`;
export const thumbUrl = (id) => `/media/thumb/${id}`;

function release(video) {
  video.pause();
  video.removeAttribute("src");
  video.load(); // a removed source otherwise keeps downloading
}

export class ClipPlayer {
  // front/back: two <video> elements in the same box. onState(state, detail): loading | playing | paused | saving | error | offline.
  constructor(front, back, onState = () => {}) {
    this.front = front;
    this.back = back;
    this.onState = onState;
    this.current = null; // {id}
    this.ahead = null; // {id} buffering in this.back
    this.firstFrameMs = null;
    this._onHide = () => (document.hidden ? this._pause() : this._resume());
    this._onOffline = () => this.onState("offline", "You are offline. The clip will carry on when the connection is back.");
    this._onOnline = () => (this.current ? this._resume() : null);
    document.addEventListener("visibilitychange", this._onHide);
    window.addEventListener("offline", this._onOffline);
    window.addEventListener("online", this._onOnline);
    for (const v of [front, back]) {
      v.muted = true;
      v.loop = true;
      v.playsInline = true;
      v.addEventListener("error", () => this._failed(v));
    }
  }

  _failed(video) {
    if (video !== this.front || !this.front.getAttribute("src")) return;
    this.onState(navigator.onLine === false ? "offline" : "error", "This clip cannot be played (it may have been removed or is not supported).");
  }

  _pause() {
    this.front.pause();
    this.back.pause();
  }

  _resume() {
    if (this.current && this.front.getAttribute("src") && !reducedMotion()) this.front.play().catch(() => {});
  }

  // Show a clip. With data saving or reduced motion only the picture is shown until the person asks (play()).
  show(clip, { autoplay = true } = {}) {
    this.current = clip;
    this.front.poster = thumbUrl(clip.id);
    if (!autoplay || dataSaving() || reducedMotion()) {
      release(this.front);
      this.onState("saving", "Playing is paused to save data or motion. Press Play to load this clip.");
      return;
    }
    this._start(clip);
  }

  play() {
    if (this.current) this._start(this.current);
  }

  _start(clip) {
    const t0 = performance.now();
    this.onState("loading", "");
    if (this.ahead && this.ahead.id === clip.id && this.back.getAttribute("src")) return this._swap(clip, t0);
    this.front.preload = "auto";
    this.front.src = clipUrl(clip.id);
    const done = () => {
      this.firstFrameMs = Math.round(performance.now() - t0);
      this.onState("playing", `First frame in ${this.firstFrameMs} ms.`);
    };
    this.front.addEventListener("loadeddata", done, { once: true });
    this.front.play().catch(() => {});
  }

  // The clip is already buffered in the hidden element: make it the visible one.
  _swap(clip, t0) {
    const old = this.front;
    this.front = this.back;
    this.back = old;
    this.front.classList.add("on");
    old.classList.remove("on");
    this.front.play().catch(() => {});
    this.firstFrameMs = Math.round(performance.now() - t0);
    this.onState("playing", `Switched in ${this.firstFrameMs} ms (loaded ahead).`);
    setTimeout(() => release(old), 400); // after the fade
    this.ahead = null;
  }

  // Load one clip ahead, quietly. Never on a data-saving connection, never while the tab is hidden.
  preload(clip) {
    if (!clip || dataSaving() || document.hidden || reducedMotion()) return;
    if (this.ahead && this.ahead.id === clip.id) return;
    this.ahead = clip;
    this.back.preload = "auto";
    this.back.src = clipUrl(clip.id);
    this.back.load();
  }

  destroy() {
    document.removeEventListener("visibilitychange", this._onHide);
    window.removeEventListener("offline", this._onOffline);
    window.removeEventListener("online", this._onOnline);
    release(this.front);
    release(this.back);
    this.current = this.ahead = null;
  }
}
