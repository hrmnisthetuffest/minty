"""Mind maps (661): an indented outline becomes Mermaid mindmap text (to keep) and a radial SVG (to look at, drawn here, no
external tool). Labels are cleaned and escaped; size is capped."""
from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field

MAX_NODES, MAX_DEPTH, MAX_LABEL = 120, 5, 60
_BULLET = re.compile(r"^[-*•]\s+")


@dataclass
class Node:
    label: str
    children: list["Node"] = field(default_factory=list)
    depth: int = 0

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def leaves(self) -> int:
        return 1 if not self.children else sum(c.leaves() for c in self.children)


def _label(text: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f]", " ", text).split())[:MAX_LABEL]


def parse_outline(text: str) -> Node:
    root, stack, count = None, [], 0
    for raw in str(text).splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        indent += 3 * raw[:indent].count("\t")
        label = _label(_BULLET.sub("", raw.strip()))
        if not label:
            continue
        if root is None:
            root = Node(label)
            stack = [(-1, root)]
            count = 1
            continue
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if parent.depth + 1 > MAX_DEPTH or count >= MAX_NODES:
            continue
        node = Node(label, depth=parent.depth + 1)
        parent.children.append(node)
        stack.append((indent, node))
        count += 1
    if root is None:
        raise ValueError("Give me an outline: a title, then indented lines under it.")
    return root


def _safe(label: str) -> str:
    return " ".join(re.sub(r"[()\[\]{}\"'`;:#<>]", " ", label).split()) or "-"


def to_mermaid(root: Node) -> str:
    lines = ["mindmap", f"  root(({_safe(root.label)}))"]
    for n in list(root.walk())[1:]:
        lines.append(" " * (2 + 2 * n.depth) + _safe(n.label))
    return "\n".join(lines) + "\n"


def to_svg(root: Node, ring: float = 150.0) -> str:
    pos: dict[int, tuple[float, float]] = {}

    def place(node: Node, a0: float, a1: float) -> None:
        mid = (a0 + a1) / 2
        r = node.depth * ring
        pos[id(node)] = (r * math.cos(mid), r * math.sin(mid))
        total = max(1, sum(c.leaves() for c in node.children))
        cur = a0
        for c in node.children:
            span = (a1 - a0) * c.leaves() / total
            place(c, cur, cur + span)
            cur += span

    place(root, 0.0, 2 * math.pi)
    sized = {id(n): (max(46.0, 7.0 * len(n.label) + 16.0), 24.0) for n in root.walk()}
    xs = [pos[i][0] - sized[i][0] / 2 for i in pos] + [pos[i][0] + sized[i][0] / 2 for i in pos]
    ys = [pos[i][1] - 12 for i in pos] + [pos[i][1] + 12 for i in pos]
    margin = 12.0
    dx, dy = margin - min(xs), margin - min(ys)
    width, height = max(xs) - min(xs) + 2 * margin, max(ys) - min(ys) + 2 * margin
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
             f'aria-label="{html.escape(root.label, quote=True)}">',
             '<style>rect{fill:#e8f0fe;stroke:#0b57d0}text{font:12px system-ui,sans-serif;fill:#1b1b1b}'
             'line{stroke:#8a8a8a;stroke-width:1.2}.root rect{fill:#0b57d0}.root text{fill:#fff}</style>']
    for n in root.walk():
        for c in n.children:
            (x1, y1), (x2, y2) = pos[id(n)], pos[id(c)]
            parts.append(f'<line x1="{x1 + dx:.1f}" y1="{y1 + dy:.1f}" x2="{x2 + dx:.1f}" y2="{y2 + dy:.1f}"/>')
    for n in root.walk():
        (x, y), (w, h) = pos[id(n)], sized[id(n)]
        cls = ' class="root"' if n is root else ""
        parts.append(f'<g{cls}><rect x="{x + dx - w / 2:.1f}" y="{y + dy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6"/>'
                     f'<text x="{x + dx:.1f}" y="{y + dy + 4:.1f}" text-anchor="middle">{html.escape(n.label)}</text></g>')
    parts.append("</svg>")
    return "".join(parts)
