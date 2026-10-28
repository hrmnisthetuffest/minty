"""The studio project folder: where Claude Code makes motion graphics, with a check it can run by itself."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from minty_core.plugins.builtin.agents import verify
from minty_core.studio import media
from tests.plugin_helpers import make_host

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
SCRIPT = Path(__import__("minty_core.studio", fromlist=["x"]).__file__).with_name("check_video_script.py")


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture
def clips(tmp_path):
    good = tmp_path / "good.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=5", "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(good))
    wide = tmp_path / "wide.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=5", "-pix_fmt", "yuv420p", str(wide))
    black = tmp_path / "black.mp4"
    ff("-f", "lavfi", "-i", "color=c=black:size=1080x1920:rate=30:duration=5", "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(black))
    return {"good": good, "wide": wide, "black": black}


def run_script(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_the_standalone_check_passes_a_good_reel_and_fails_a_bad_one_with_reasons(clips):
    ok = run_script(clips["good"])
    assert ok.returncode == 0 and ok.stdout.startswith("OK")
    bad = run_script(clips["wide"])
    assert bad.returncode == 1 and "9:16" in bad.stdout and "audio" in bad.stdout.lower()
    assert run_script(clips["black"]).returncode == 1 and "black" in run_script(clips["black"]).stdout.lower()


async def test_it_agrees_with_the_checks_minty_runs_itself(clips):
    for name, path in clips.items():
        mine = await media.check_video(path)
        theirs = run_script(path)
        assert (theirs.returncode == 0) == mine.ok, (name, mine.problems, theirs.stdout)


def test_given_a_folder_it_checks_the_newest_video_and_an_empty_folder_is_an_error(clips, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert run_script(out).returncode == 2 and "no video" in run_script(out).stdout.lower()
    shutil.copy(clips["wide"], out / "old.mp4")
    shutil.copy(clips["good"], out / "new.mp4")
    import os
    os.utime(out / "old.mp4", (1, 1))
    assert run_script(out).returncode == 0 and "new.mp4" in run_script(out).stdout


def test_the_script_needs_nothing_from_minty():
    text = SCRIPT.read_text()
    assert "minty_core" not in text and "import numpy" not in text


async def test_the_project_scaffold_is_made_once_and_never_overwrites_the_users_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "minty-studio"
    host = make_host(tmp_path, plugins={"studio": {"enabled": True, "project_dir": str(project)}})
    out = await host.call("studio_make", {"action": "project"})
    assert "ready" in out.lower() and str(project) in out
    for name in ("CLAUDE.md", "check_video.py", ".minty/check", "BRAND.md", "out"):
        assert (project / name).exists(), name
    assert (project / "check_video.py").read_text() == SCRIPT.read_text()
    assert verify.project_check(project) == ["python3", "check_video.py", "out"]
    brief = (project / "CLAUDE.md").read_text()
    assert "1080x1920" in brief and "out/" in brief and "never" in brief.lower() and "post" in brief.lower() and "BRAND.md" in brief
    (project / "CLAUDE.md").write_text("my own notes")
    again = await host.call("studio_make", {"action": "project"})
    assert "already" in again.lower() and (project / "CLAUDE.md").read_text() == "my own notes"


async def test_a_project_folder_outside_home_or_in_a_hidden_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for bad in ("/etc/studio", str(tmp_path / ".config" / "studio")):
        host = make_host(tmp_path, plugins={"studio": {"enabled": True, "project_dir": bad}})
        assert (await host.call("studio_make", {"action": "project"})).startswith("Error")
