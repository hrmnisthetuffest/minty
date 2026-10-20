import { h } from "./lib.js";

// The search bar (Ctrl+K or Cmd+K): jump to a page or straight to a setting.
// `items` are {label, hint, text, hash}; `go(hash)` navigates.
export function createPalette(root, items, go) {
  const input = h("input", { type: "search", placeholder: "Search settings and pages", autocomplete: "off", spellcheck: "false" });
  const list = h("ul", { class: "pal-list" });
  const box = h("div", { class: "pal-box" }, input, list);
  root.replaceChildren(box);
  let shown = [];
  let at = 0;

  function matches(query) {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.filter((i) => i.page).slice(0, 12);
    return items.filter((i) => words.every((w) => i.text.includes(w))).slice(0, 40);
  }

  function draw() {
    shown = matches(input.value);
    at = Math.min(at, Math.max(0, shown.length - 1));
    list.replaceChildren(...(shown.length
      ? shown.map((item, i) => h("li", {
        class: "pal-item" + (i === at ? " sel" : ""),
        onmousedown: (event) => { event.preventDefault(); choose(item); },
      }, h("span", {}, item.label), h("span", { class: "hint" }, item.hint)))
      : [h("li", { class: "pal-empty" }, "Nothing matches.")]));
    const selected = list.children[at];
    if (selected && selected.scrollIntoView) selected.scrollIntoView({ block: "nearest" });
  }

  function choose(item) {
    close();
    go(item.hash);
  }

  function open() {
    root.hidden = false;
    input.value = "";
    at = 0;
    draw();
    input.focus();
  }

  function close() { root.hidden = true; }

  input.addEventListener("input", () => { at = 0; draw(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { at = Math.min(shown.length - 1, at + 1); draw(); event.preventDefault(); }
    else if (event.key === "ArrowUp") { at = Math.max(0, at - 1); draw(); event.preventDefault(); }
    else if (event.key === "Enter") { if (shown[at]) choose(shown[at]); event.preventDefault(); }
    else if (event.key === "Escape") { close(); event.preventDefault(); }
  });
  root.addEventListener("mousedown", (event) => { if (event.target === root) close(); });

  return { open, close, toggle: () => (root.hidden ? open() : close()), isOpen: () => !root.hidden };
}
