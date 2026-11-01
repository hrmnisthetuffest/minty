"""Python 3.10 is the oldest supported version. The first Windows CI run on 3.10 found `import minty_core.app` failing (tomllib, 3.11+, imported directly) and the LLM router
using asyncio.timeout (3.11+). These tests run on every Python: they pin the replacements, and fail if a module goes back to the 3.11-only form."""
import ast
import asyncio
from pathlib import Path

import pytest

from minty_core import pycompat

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USERS_PYTHON = [ROOT / "scripts" / "doctor.py", ROOT / "scripts" / "install_check.py"]  # run by a friend with whatever Python they have
ELEVEN_ONLY = {("asyncio", "timeout"), ("asyncio", "TaskGroup"), ("asyncio", "Runner"), ("enum", "StrEnum"), ("datetime", "UTC")}


def direct_tomllib(source: str) -> list[int]:
    found = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import) and any(a.name == "tomllib" for a in n.names):
            found.append(n.lineno)
        elif isinstance(n, ast.ImportFrom) and n.module == "tomllib":
            found.append(n.lineno)
    return found


def eleven_only(source: str) -> list[str]:
    return [f"{n.value.id}.{n.attr} (line {n.lineno})" for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and (n.value.id, n.attr) in ELEVEN_ONLY]


def test_tomllib_is_imported_only_through_the_shim_that_falls_back_to_tomli():
    offenders = {}
    for path in [*SRC.rglob("*.py"), *(ROOT / "tests").rglob("*.py"), *USERS_PYTHON]:
        if path.name == "_toml.py":
            continue
        found = direct_tomllib(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(ROOT))] = found
    assert not offenders, f"use `from minty_core._toml import tomllib` (Python 3.10 has no tomllib): {offenders}"


def test_nothing_uses_the_python_311_only_asyncio_enum_or_datetime_features():
    offenders = {}
    for path in SRC.rglob("*.py"):
        if path.name == "pycompat.py":
            continue
        found = eleven_only(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(SRC))] = found
    assert not offenders, f"use minty_core/pycompat.py (or avoid the feature): {offenders}"


async def test_the_replacement_timeout_expires_a_slow_wait_and_says_it_was_this_timeout_that_did():
    with pytest.raises(TimeoutError):
        async with pycompat._Timeout(0.05) as budget:
            await asyncio.sleep(5)
    assert budget.expired()


async def test_the_replacement_timeout_leaves_a_wait_that_finishes_in_time_alone():
    async with pycompat._Timeout(5) as budget:
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)  # the timer must be cancelled: nothing may cancel this task afterwards
    assert not budget.expired()


async def test_the_replacement_timeout_with_no_delay_never_expires():
    async with pycompat._Timeout(None) as budget:
        await asyncio.sleep(0.05)
    assert not budget.expired()


async def test_cancelling_the_task_from_outside_is_still_a_cancellation_not_a_timeout():
    async def wait():
        async with pycompat._Timeout(5):
            await asyncio.sleep(5)

    task = asyncio.create_task(wait())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_timeout_in_use_raises_the_builtin_timeout_error():
    # asyncio.TimeoutError is a different class from the built-in on Python 3.10; the router catches the built-in one
    with pytest.raises(TimeoutError):
        async with pycompat.timeout(0.05):
            await asyncio.sleep(5)
