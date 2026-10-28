"""The studio page's files are wired in and their promises are visible in the code: bounded loading, nothing automatic on a data-saving
connection or with reduced motion, release on leaving. (Checked in a real browser in the session that wrote them: first frame, the switch
to a clip loaded ahead, the failure message, data saving, the backdrop.)"""
import shutil
import subprocess

import pytest

from minty_panel.server import STATIC


def test_the_page_is_in_the_menu_and_the_shell_starts_and_stops_the_backdrop():
    app = (STATIC / "app.js").read_text()
    assert 'import studio from "./pages/studio.js"' in app and '["studio", "Studio", studio]' in app
    assert '"logs", "studio"' in app and "studio:" in app  # in the Monitor group, with an icon
    assert "initBackdrop()" in app and "stopBackdrop()" in app


def test_the_player_loads_at_most_one_clip_ahead_and_lets_go_of_everything():
    js = (STATIC / "player.js").read_text()
    assert js.count("this.back.src") == 1  # the only thing loaded ahead is one hidden element
    for needed in ("saveData", "prefers-reduced-motion", "visibilitychange", "removeAttribute(\"src\")", "destroy()", "offline", "loadeddata"):
        assert needed in js, needed
    assert "setInterval" not in js  # no polling


def test_the_page_gives_back_a_cleanup_and_never_shows_a_file_path():
    js = (STATIC / "pages" / "studio.js").read_text()
    assert "return () => player.destroy()" in js and "/media/" not in js.replace("thumbUrl", "")  # addresses come from player.js, by number
    assert "thumbUrl(c.id)" in js and "loading: \"lazy\"" in js


def test_the_backdrop_is_off_unless_switched_on_and_waits_for_the_panel_to_be_idle():
    js = (STATIC / "backdrop.js").read_text()
    assert 'getItem("minty.backdrop") === "1"' in js and "requestIdleCallback" in js and "dataSaving()" in js and "reducedMotion()" in js


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("name", ["app.js", "player.js", "backdrop.js", "pages/studio.js"])
def test_the_scripts_parse(name):
    done = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
