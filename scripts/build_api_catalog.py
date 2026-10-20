"""Build src/minty_core/plugins/builtin/apis/catalog.json from the public-apis project.

Sources: the public-apis README tables, merged with the JSON database behind https://free-apis.github.io
(public-apis-dev/public-apis db/resources.json), deduplicated by link. Run manually; the plugin only reads
the bundled file and never touches the network at search time.

    .venv/bin/python scripts/build_api_catalog.py
"""
import json
import re
import sys
from pathlib import Path

import httpx

README = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
RESOURCES = "https://raw.githubusercontent.com/public-apis-dev/public-apis/main/db/resources.json"
OUT = Path(__file__).resolve().parent.parent / "src/minty_core/plugins/builtin/apis/catalog.json"

NAME_LINK = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")


def normalise_auth(raw: str) -> str:
    value = raw.strip().strip("`").strip()
    return "none" if value.lower() in ("", "no", "none") else value


def parse_readme(text: str) -> list[dict]:
    entries, category = [], ""
    for line in text.splitlines():
        if line.startswith("### "):
            category = line[4:].strip()
            continue
        if not category or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        while cells and not cells[-1]:  # a few tables carry a stray empty trailing column
            cells.pop()
        match = NAME_LINK.match(cells[0]) if len(cells) >= 5 else None
        if not match:
            continue
        auth, https = cells[-3], cells[-2]
        entries.append({
            "name": match.group(1).strip(), "link": match.group(2).strip(),
            "description": " | ".join(cells[1:-3]), "auth": normalise_auth(auth),
            "https": https.lower() == "yes", "category": category,
        })
    return entries


def parse_resources(data: dict) -> list[dict]:
    entries = []
    for row in data.get("entries", []):
        link = str(row.get("Link", "")).strip()
        if not link or not row.get("API"):
            continue
        entries.append({
            "name": str(row["API"]).strip(), "link": link, "description": str(row.get("Description", "")).strip(),
            "auth": normalise_auth(str(row.get("Auth", ""))), "https": link.startswith("https://"),
            "category": str(row.get("Category", "")).strip(),
        })
    return entries


def merge(*sources: list[dict]) -> list[dict]:
    seen, merged = set(), []
    for source in sources:
        for entry in source:
            key = entry["link"].rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                merged.append(entry)
    return merged


def main() -> int:
    readme = httpx.get(README, timeout=60, follow_redirects=True)
    readme.raise_for_status()
    from_readme = parse_readme(readme.text)
    from_site: list[dict] = []
    try:
        site = httpx.get(RESOURCES, timeout=60, follow_redirects=True)
        site.raise_for_status()
        from_site = parse_resources(site.json())
    except (httpx.HTTPError, ValueError) as e:
        print(f"free-apis.github.io data unavailable ({type(e).__name__}); using README only", file=sys.stderr)
    catalog = merge(from_readme, from_site)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(from_readme)} from README, {len(from_site)} from site, {len(catalog)} total -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
