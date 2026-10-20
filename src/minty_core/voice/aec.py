"""Echo cancellation with PipeWire's own module (502): WebRTC AEC through `pactl load-module module-echo-cancel`.

Minty creates a virtual microphone and speaker with its own names and never touches the system's default devices.
Only this process is pointed at them, through PULSE_SOURCE / PULSE_SINK, which PortAudio's pulse device honours.
Speakers need it; headphones do not."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

SOURCE = "minty_aec_source"
SINK = "minty_aec_sink"


def _run(argv: list[str]) -> tuple[int, str]:
    if shutil.which(argv[0]) is None:
        return 127, f"{argv[0]} is not installed"
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return done.returncode, (done.stdout or done.stderr).strip()


@dataclass
class Status:
    loaded: bool
    module_id: str = ""
    source: str = SOURCE
    sink: str = SINK


@dataclass
class Result:
    ok: bool
    message: str = ""
    module_id: str = ""


class EchoCancel:
    def __init__(self, run: Callable[[list[str]], tuple[int, str]] = _run):
        self._run = run

    def status(self) -> Status:
        code, out = self._run(["pactl", "list", "short", "modules"])
        if code == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1] == "module-echo-cancel" and SOURCE in line:
                    return Status(True, parts[0])
        return Status(False)

    def enable(self) -> Result:
        st = self.status()
        if st.loaded:
            return Result(True, "Echo cancellation was already on.", st.module_id)
        code, out = self._run(["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc",
                               f"source_name={SOURCE}", f"sink_name={SINK}", "use_master_format=1"])
        if code != 0:
            return Result(False, f"Could not load the echo-cancel module: {out}")
        return Result(True, f"Echo cancellation is on (virtual devices {SOURCE} and {SINK}).", out.strip())

    def disable(self) -> Result:
        st = self.status()
        if not st.loaded:
            return Result(True, "Echo cancellation was not on.")
        code, out = self._run(["pactl", "unload-module", st.module_id])
        return Result(code == 0, "Echo cancellation is off." if code == 0 else f"Could not unload it: {out}")

    def apply_environment(self) -> bool:
        """Point this process (only) at the virtual devices. False, and nothing set, when the module is not loaded."""
        if not self.status().loaded:
            return False
        os.environ["PULSE_SOURCE"] = SOURCE
        os.environ["PULSE_SINK"] = SINK
        return True
