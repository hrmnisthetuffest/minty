import { api, h, report, select } from "../lib.js";

export default async function logs(main) {
  const level = select(["DEBUG", "INFO", "WARNING", "ERROR"], "INFO");
  const box = h("div", { class: "card log" });

  async function refresh() {
    const rows = await api(`logs?level=${level.value}&limit=300`);
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    box.replaceChildren(...rows.map((r) => h("div", { class: r.level },
      `${new Date(r.time * 1000).toLocaleTimeString()} ${r.level.padEnd(7)} ${r.name}: ${r.message}`)));
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  level.addEventListener("change", () => refresh().catch(report));
  main.replaceChildren(h("h2", {}, "Logs"), h("div", { class: "row" }, h("span", { class: "muted" }, "Minimum level"), level), box);
  await refresh();
  const timer = setInterval(() => refresh().catch(() => {}), 3000);
  return () => clearInterval(timer);
}
