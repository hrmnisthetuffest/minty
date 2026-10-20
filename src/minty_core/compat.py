def parse_cpu_flags(cpuinfo: str) -> set[str]:
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            return set(line.split(":", 1)[1].split())
    return set()


def build_report(flags, py_version, importable):
    rows = []
    if py_version < (3, 10):
        rows.append(("python", "fail", "need Python 3.10+"))
    elif py_version >= (3, 12):
        rows.append(("python", "warn", "piper-phonemize wheels target <=3.11; use a 3.11 venv"))
    else:
        rows.append(("python", "ok", f"{py_version[0]}.{py_version[1]}"))
    rows.append(("avx", "ok" if "avx" in flags else "fail", "required by onnxruntime/ctranslate2 builds"))
    if "avx2" in flags:
        rows.append(("avx2", "ok", ""))
    else:
        rows.append(("avx2", "warn", "no AVX2: confirm wheels run; fallback is whisper.cpp"))
    for module, ok in sorted(importable.items()):
        rows.append((f"import {module}", "ok" if ok else "warn", "" if ok else "not importable"))
    return rows
