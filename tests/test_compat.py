from minty_core.compat import build_report, parse_cpu_flags


def status(rows, check):
    return next(s for c, s, _ in rows if c == check)


def test_parse_cpu_flags():
    text = "processor\t: 0\nflags\t\t: fpu avx sse4_2\nbogomips\t: 1"
    assert parse_cpu_flags(text) == {"fpu", "avx", "sse4_2"}


def test_parse_without_flags_line():
    assert parse_cpu_flags("nothing here") == set()


def test_missing_avx2_warns_missing_avx_fails():
    assert status(build_report({"avx"}, (3, 11), {}), "avx2") == "warn"
    assert status(build_report({"avx", "avx2"}, (3, 11), {}), "avx2") == "ok"
    assert status(build_report(set(), (3, 11), {}), "avx") == "fail"


def test_python_version_rules():
    assert status(build_report({"avx"}, (3, 9), {}), "python") == "fail"
    assert status(build_report({"avx"}, (3, 12), {}), "python") == "warn"
    assert status(build_report({"avx"}, (3, 11), {}), "python") == "ok"


def test_import_rows():
    rows = build_report({"avx"}, (3, 11), {"numpy": True, "ctranslate2": False})
    assert status(rows, "import numpy") == "ok"
    assert status(rows, "import ctranslate2") == "warn"
