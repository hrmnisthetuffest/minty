import subprocess
import sys


def test_perf_script_measures_and_enforces_budget(tmp_path):
    loose = tmp_path / "loose.toml"
    loose.write_text("import_app_ms = 1e9\nwake_frame_us = 1e9\nendpointer_frame_us = 1e9\nbus_publish_us = 1e9\n"
                     "orb_frame_ms = 1e9\ntui_render_ms = 1e9\nrss_after_import_mb = 1e9\n")
    ok = subprocess.run([sys.executable, "scripts/perf.py", "--budget", str(loose)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "orb_frame_ms" in ok.stdout and "OVER" not in ok.stdout
    tight = tmp_path / "tight.toml"
    tight.write_text("orb_frame_ms = 0.000001\n")
    bad = subprocess.run([sys.executable, "scripts/perf.py", "--budget", str(tight)], capture_output=True, text=True)
    assert bad.returncode == 1 and "OVER" in bad.stdout
