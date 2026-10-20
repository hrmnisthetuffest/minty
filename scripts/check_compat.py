"""Report whether this machine can run Minty's heavy dependencies."""
import importlib
import sys

from minty_core.compat import build_report, parse_cpu_flags

MODULES = ["numpy", "onnxruntime", "ctranslate2", "faster_whisper", "sounddevice", "openwakeword", "piper"]


def can_import(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def main() -> int:
    try:
        flags = parse_cpu_flags(open("/proc/cpuinfo").read())
    except OSError:
        flags = set()
    rows = build_report(flags, sys.version_info[:2], {m: can_import(m) for m in MODULES})
    for check, status, note in rows:
        print(f"[{status:4}] {check:24} {note}")
    return 1 if any(s == "fail" for _, s, _ in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
