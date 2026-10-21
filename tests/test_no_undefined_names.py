"""Every name a function uses from its module (a global or a builtin) must exist: a typo or a missing import would otherwise only fail
when that line runs, which for rarely used paths can be months later."""
import builtins
import dis
import importlib
import pkgutil
import types
import warnings

import pytest

import minty_core
import minty_panel

OPTIONAL = ("sounddevice", "onnxruntime", "faster_whisper", "piper", "kokoro_onnx", "google", "playwright", "shazamio", "cryptography",
            "pynput", "pypdf", "llama_cpp", "openwakeword", "sherpa_onnx", "PIL", "pytesseract")


def modules():
    for package in (minty_core, minty_panel):
        for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if info.name.endswith("__main__"):
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    yield importlib.import_module(info.name)
            except ImportError as e:
                if any(name in str(e) for name in OPTIONAL):
                    continue  # an optional library that is not installed
                raise


def code_objects(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from code_objects(const)


def global_names_used(code: types.CodeType) -> set[str]:
    """Names looked up as globals inside functions (LOAD_GLOBAL). Module-level and class-body lookups (LOAD_NAME) are skipped: a class
    body reads its own names, and a module-level line fails at import, which the test already does."""
    return {ins.argval for c in code_objects(code) for ins in dis.get_instructions(c) if ins.opname == "LOAD_GLOBAL"}


def global_names_stored(code: types.CodeType) -> set[str]:
    return {ins.argval for c in code_objects(code) for ins in dis.get_instructions(c) if ins.opname in ("STORE_GLOBAL", "DELETE_GLOBAL")}


def test_no_function_uses_a_name_that_does_not_exist():
    problems = []
    for module in modules():
        path = getattr(module, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        with open(path, "rb") as f:
            code = compile(f.read(), path, "exec")
        known = set(vars(module)) | set(dir(builtins)) | global_names_stored(code)
        for name in sorted(global_names_used(code) - known - {"__class__"}):
            problems.append(f"{module.__name__}: {name}")
    assert problems == [], problems
