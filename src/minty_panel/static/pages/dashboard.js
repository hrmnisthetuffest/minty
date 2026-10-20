import { action, api, h, pageHead, textInput } from "../lib.js";

export default async function dashboard(main, events) {
  const state = h("strong", {}, "…");
  const restartButton = action("Restart conversation", async () => {
    await api("restart", { method: "POST" });
    feed.replaceChildren();
    return "Fresh start. The conversation is saved.";
  });
  const restartApp = action("Restart Minty", async () => {
    if (!confirm("Restart Minty? It is back in a few seconds.")) return "Cancelled.";
    await api("restart-app", { method: "POST" });
    return "Restarting…";
  }, "danger");
  const providers = h("tbody");
  const jobsList = h("ul", { class: "feed" });
  const jobsEmpty = h("p", { class: "muted" }, "Nothing in the background.");
  const feed = h("ul", { class: "feed" });
  const reply = h("p", { class: "muted" });
  const meterFill = h("div", { class: "meter-fill" });
  const meterPeak = h("div", { class: "meter-peak" });
  const meterLine = h("div", { class: "meter-threshold", title: "speech threshold" });
  const meter = h("div", { class: "meter" }, meterFill, meterLine, meterPeak);
  const dbLabel = h("span", { class: "db" }, "-- dB");
  const heard = h("span", { class: "muted" }, "Mic idle");
  // dBFS -> 0..100 % across a -60..0 dB scale
  const pct = (db) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
  const meterState = { target: 0, shown: 0, peak: 0, peakAt: 0, speech: false, raf: 0 };
  function animate(now) {
    const m = meterState;
    m.shown += (m.target - m.shown) * (m.target > m.shown ? 0.6 : 0.15); // fast attack, slow release
    if (now - m.peakAt > 1200) m.peak = Math.max(m.shown, m.peak - 0.8); // peak hold, then fall
    meterFill.style.width = `${m.shown}%`;
    meterPeak.style.left = `${Math.min(99.5, m.peak)}%`;
    m.raf = requestAnimationFrame(animate);
  }
  meterState.raf = requestAnimationFrame(animate);
  const caption = h("p", { class: "caption muted" }, "");
  const displayTitle = h("h3", {}, "Display");
  const displayBody = h("pre", { class: "display-body" });
  const displayTile = h("section", { class: "tile wide" }, displayTitle, displayBody);
  displayTile.hidden = true;

  async function refreshJobs() {
    const jobs = await api("jobs");
    jobsEmpty.hidden = jobs.length > 0;
    jobsList.replaceChildren(...jobs.slice(0, 10).map((j) => {
      const active = ["queued", "running", "needs_user"].includes(j.status);
      const detail = j.status === "needs_user" ? j.question : active ? (j.progress[j.progress.length - 1] || "") : j.result;
      return h("li", { class: active ? "assistant" : "muted" },
        h("strong", {}, `${j.label} — ${j.status}`), ` ${j.goal}`,
        detail ? h("div", { class: "muted" }, detail) : "",
        active ? action("Cancel", async () => { await api(`jobs/${j.id}/cancel`, { method: "POST" }); await refreshJobs(); }) : "");
    }));
  }

  async function refresh() {
    await refreshJobs().catch(() => {});
    const status = await api("status");
    state.textContent = status.state;
    providers.replaceChildren(...status.providers.map((p) => h("tr", { class: p.cooling_for > 0 ? "cooling" : "" },
      h("td", {}, p.name), h("td", {}, p.enabled ? "on" : "off"), h("td", {}, p.has_key ? "key set" : "no key"),
      h("td", {}, p.cooling_for > 0 ? `cooling ${Math.round(p.cooling_for)}s` : "ready"), h("td", { class: "muted" }, p.last_error || ""))));
  }

  const sayInput = textInput("", { placeholder: "Make Minty say something" });
  const askInput = textInput("", { placeholder: "Ask Minty (text, no microphone)" });

  const tile = (title, ...children) => h("section", { class: "tile" }, h("h3", {}, title), ...children);

  main.replaceChildren(
    pageHead("Monitor", "Dashboard"),
    h("div", { class: "tiles" },
      tile("Microphone", h("div", { class: "row" }, meter, dbLabel, heard), caption),
      tile("System", h("p", { class: "row" }, h("span", { class: "muted" }, "State"), state),
        h("div", { class: "row" }, restartButton, restartApp)),
      tile("Brain providers", h("table", {}, h("thead", {}, h("tr", {}, ["Provider", "Enabled", "Key", "Status", "Last error"].map((t) => h("th", {}, t)))), providers)),
      tile("Background jobs", jobsEmpty, jobsList),
      tile("Talk to Minty",
        h("div", { class: "row" }, sayInput, action("Say", async () => { await api("say", { method: "POST", body: { text: sayInput.value } }); })),
        h("div", { class: "row gap-top" }, askInput,
          action("Ask", async () => {
            reply.textContent = "…";
            try {
              reply.textContent = (await api("ask", { method: "POST", body: { text: askInput.value } })).reply;
            } catch (e) {
              reply.textContent = "";
              throw e;
            }
          })),
        reply),
      tile("Live transcript", feed),
      displayTile),
  );

  await refresh();
  const timer = setInterval(() => refresh().catch(() => {}), 5000);
  const off = events.on((event) => {
    if (event.type === "state") state.textContent = event.state;
    if (event.type === "job") refreshJobs().catch(() => {});
    if (event.type === "level") {
      const db = typeof event.db === "number" ? event.db : -60;
      meterState.target = pct(db);
      const peak = pct(typeof event.peak_db === "number" ? event.peak_db : db);
      if (peak >= meterState.peak) { meterState.peak = peak; meterState.peakAt = performance.now(); }
      if (typeof event.threshold_db === "number") meterLine.style.left = `${pct(event.threshold_db)}%`;
      dbLabel.textContent = `${Math.round(db)} dB`;
      meterFill.classList.toggle("speech", Boolean(event.speech));
      heard.textContent = event.speech ? "Voice detected" : "Quiet";
    }
    if (event.type === "display") {
      displayTitle.textContent = event.title || "Display";
      displayBody.textContent = event.content || "";
      displayTile.hidden = !event.content;
    }
    if (event.type === "partial") caption.textContent = event.text ? `${event.text} …` : "";
    if (event.type === "transcript") {
      if (event.role === "user") caption.textContent = "";
      feed.prepend(h("li", { class: event.role }, `${event.role === "user" ? "You" : "Minty"}: ${event.text}`));
      while (feed.children.length > 50) feed.lastChild.remove();
    }
  });
  return () => { clearInterval(timer); cancelAnimationFrame(meterState.raf); off(); };
}
