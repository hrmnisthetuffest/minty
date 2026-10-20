"""Every endpoint a panel page calls must be on the proxy's allow-list (the Switches page got 'unknown endpoint')."""
import re
from pathlib import Path

from minty_panel.server import ALLOWED

STATIC = Path(__file__).resolve().parents[1] / "src" / "minty_panel" / "static"


def test_every_endpoint_the_pages_call_is_allowed():
    called = set()
    for js in [*(STATIC / "pages").glob("*.js"), STATIC / "app.js"]:
        called |= set(re.findall(r"""(?:api|get|post|put|del)\(\s*["'`]/?([a-z-]+)""", js.read_text()))
    assert "flags" in called  # the pattern still finds the page that broke
    assert called - ALLOWED == set()
