#!/usr/bin/env python3
"""Write docs/plugins.md from the plugin sources (WP-18, item 655). Run it with the project's Python from the repository root: .venv/bin/python scripts/gen_plugin_docs.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minty_core.dev.plugin_kit import plugin_docs  # noqa: E402
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR  # noqa: E402

if __name__ == "__main__":
    text = plugin_docs([BUILTIN_PLUGIN_DIR])
    (ROOT / "docs" / "plugins.md").write_text(text, encoding="utf-8")
    print(f"wrote docs/plugins.md: {text.count('| `')} tools")
