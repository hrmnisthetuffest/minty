"""Plugin template, validator and auto-generated docs (654 655). The validator reads the plugin's source as text (it never
imports or runs it) and reports what the host and the safety rules care about: a valid manifest, a `register(ctx)`, plain
tool names, short descriptions, no code-running calls, a declared network, and a risk class on anything that changes things."""
from __future__ import annotations

import ast
import re
from minty_core._toml import tomllib
from dataclasses import dataclass
from pathlib import Path

from minty_core.plugins.host import BUILTIN_PLUGIN_DIR

NAME = re.compile(r"[a-z][a-z0-9_]{1,30}")
TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")
MAX_DESCRIPTION = 250
NETWORK_MODULES = {"requests", "urllib3", "socket", "aiohttp", "httpx", "http.client", "urllib.request", "websockets"}
WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rmdir", "rename", "mkdir", "touch", "rmtree", "move", "copy",
               "copy2", "copytree", "remove", "post", "put", "delete", "patch", "send", "sendall"}

PLUGIN_PY = '''"""{description}"""
from minty_core.plugins.host import Tool


def register(ctx):
    async def say(args):
        who = str(args.get("who") or "world")[:40]
        return f"Hello, {{who}}."

    return [
        Tool("say", "Say hello to someone by name.",
             {{"type": "object", "properties": {{"who": {{"type": "string"}}}}}}, say),
    ]
'''
TEST_PY = '''"""Tests for the {name} plugin. Run: pytest {name}/test_{name}.py"""
import asyncio
import importlib.util
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location("{name}_plugin", Path(__file__).with_name("plugin.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_say_greets_by_name():
    tools = {{t.name: t for t in load().register(object())}}
    assert asyncio.run(tools["say"].func({{"who": "Alex"}})) == "Hello, Alex."
'''


@dataclass
class Problem:
    level: str  # error | warning
    where: str
    message: str


def builtin_names() -> set[str]:
    return {p.name for p in Path(BUILTIN_PLUGIN_DIR).iterdir() if (p / "plugin.toml").is_file()}


def scaffold(name: str, dest: Path, description: str = "") -> list[Path]:
    if not NAME.fullmatch(str(name)):
        raise ValueError("A plugin name is lowercase letters, digits and underscores, starting with a letter (2 to 31).")
    if name in builtin_names():
        raise ValueError(f"{name} is the name of a built-in plugin; pick another.")
    description = " ".join(str(description or f"The {name} plugin.").split())[:200].replace('"', "'")
    folder = Path(dest) / name
    if folder.exists():
        raise ValueError(f"{folder} already exists; I will not overwrite it.")
    folder.mkdir(parents=True)
    files = {"__init__.py": "", "plugin.py": PLUGIN_PY.format(description=description),
             "plugin.toml": f'name = "{name}"\ndescription = "{description}"\ndefault_enabled = false\n',
             f"test_{name}.py": TEST_PY.format(name=name)}
    out = []
    for fname, text in files.items():
        (folder / fname).write_text(text, encoding="utf-8")
        out.append(folder / fname)
    return out


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        return f"{base}.{f.attr}" if base else f.attr
    return ""


def _literal(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _tool_fields(call: ast.Call) -> tuple[ast.AST | None, ast.AST | None, dict]:
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    name = call.args[0] if call.args else kw.get("name")
    desc = call.args[1] if len(call.args) > 1 else kw.get("description")
    return name, desc, kw


def validate(folder: Path) -> list[Problem]:
    folder, problems = Path(folder), []

    def add(level, where, msg):
        problems.append(Problem(level, where, msg))

    network = False
    try:
        manifest = tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        manifest = {}
        add("error", "plugin.toml", f"plugin.toml cannot be read ({type(e).__name__}).")
    if manifest:
        if manifest.get("name", folder.name) != folder.name:
            add("error", "plugin.toml", f"The name in plugin.toml ({manifest.get('name')}) must match the folder name "
                                        f"({folder.name}).")
        if not str(manifest.get("description") or "").strip():
            add("error", "plugin.toml", "plugin.toml needs a description.")
        elif len(manifest["description"]) > 200:
            add("warning", "plugin.toml", "The plugin description is over 200 characters.")
        network = manifest.get("network") is True
    try:
        tree = ast.parse((folder / "plugin.py").read_text(encoding="utf-8"))
    except OSError:
        add("error", "plugin.py", "plugin.py is missing.")
        return problems
    except SyntaxError as e:
        add("error", "plugin.py", f"Syntax error on line {e.lineno}.")
        return problems
    if not any(isinstance(n, ast.FunctionDef) and n.name == "register" for n in tree.body):
        add("error", "plugin.py", "plugin.py has no register(ctx) function.")
    seen, has_risk, changes, imports = set(), False, False, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "Tool":
                tname, desc, kw = _tool_fields(node)
                has_risk = has_risk or bool(set(kw) & {"risk", "risky", "confirm_when"})
                lit = _literal(tname)
                if lit is None:
                    add("warning", "plugin.py", "A tool name is not a plain string, so I cannot check it.")
                else:
                    if not TOOL_NAME.fullmatch(lit):
                        add("error", "plugin.py", f"The tool name {lit!r} must be lowercase letters, digits and underscores.")
                    if lit in seen:
                        add("error", "plugin.py", f"The tool name {lit!r} is used twice.")
                    seen.add(lit)
                d = _literal(desc)
                if d is not None and len(d) > MAX_DESCRIPTION:
                    add("error", "plugin.py", f"The description of {lit!r} is {len(d)} characters; the limit is "
                                              f"{MAX_DESCRIPTION}.")
                if d is not None and not d.strip():
                    add("error", "plugin.py", f"The tool {lit!r} needs a description.")
            if name in ("eval", "exec", "compile", "__import__"):
                add("error", "plugin.py", f"`{name}` runs arbitrary code and is not allowed in a plugin.")
            elif name in ("os.system", "os.popen", "pickle.loads", "pickle.load", "marshal.loads"):
                add("error", "plugin.py", f"`{name}` is not allowed in a plugin.")
            if name.startswith("subprocess.") or name in ("create_subprocess_exec", "create_subprocess_shell",
                                                           "asyncio.create_subprocess_exec"):
                changes = True
                if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in node.keywords):
                    add("error", "plugin.py", "A subprocess with shell=True is not allowed; pass an argument list.")
            if name == "open" and len(node.args) > 1 and (_literal(node.args[1]) or "").strip("bt") in ("w", "a", "x", "w+", "a+"):
                changes = True
            if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_ATTRS:
                changes = True
        elif isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) \
                and node.value.id == "os":
            add("warning", "plugin.py", "Reading os.environ can expose secrets; use ctx.secret() for keys.")
    if "ctypes" in imports:
        add("error", "plugin.py", "ctypes is not allowed in a plugin.")
    used = sorted(m for m in imports if m in NETWORK_MODULES or m.split(".")[0] in {"requests", "urllib3", "aiohttp"})
    if used and not network:
        add("error", "plugin.py", f"It uses the network ({', '.join(used)}) but plugin.toml does not say network = true. "
                                  "Declare it and use minty_core.httpclient.get_client().")
    elif used and any(m.split(".")[0] in {"requests", "urllib3", "aiohttp", "socket"} for m in used):
        add("warning", "plugin.py", "Use minty_core.httpclient.get_client() so requests are checked and logged.")
    if changes and not has_risk:
        add("error", "plugin.py", "This plugin changes things (writes files, runs programs or sends data) but no tool "
                                  "declares risk=.")
    return problems


def _flags(kw: dict) -> str:
    out = []
    if isinstance(kw.get("sensitive"), ast.Constant) and kw["sensitive"].value:
        out.append("sensitive")
    if isinstance(kw.get("local_only"), ast.Constant) and kw["local_only"].value:
        out.append("local only")
    risk = kw.get("risk")
    if risk is not None:
        out.append(f"risk {risk.value}" if isinstance(risk, ast.Constant) else "risk varies")
    if kw.get("risky") is not None:
        out.append("asks first")
    if kw.get("confirm_when") is not None:
        out.append("asks when needed")
    if kw.get("egress") is not None:
        out.append("sends to a destination")
    return ", ".join(out)


def plugin_docs(folders: list[Path]) -> str:
    lines = ["# Plugins", "", "Generated by `scripts/gen_plugin_docs.py` from the plugin sources; do not edit by hand.", ""]
    for root in folders:
        for folder in sorted(Path(root).iterdir()):
            if not (folder / "plugin.toml").is_file():
                continue
            try:
                manifest = tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))
                tree = ast.parse((folder / "plugin.py").read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError, SyntaxError):
                continue
            name = manifest.get("name", folder.name)
            lines += [f"## {name}", "", str(manifest.get("description", "")),
                      "", f"On by default: {'yes' if manifest.get('default_enabled', True) else 'no'}."
                      + (f" Works on: {', '.join(str(x).capitalize() for x in manifest['platforms'])} only." if manifest.get("platforms") else "")]
            settings = manifest.get("settings") or {}
            if settings:
                lines.append("Settings: " + ", ".join(f"`{k}`" for k in settings) + ".")
            lines += ["", "| Tool | What it does | Flags |", "|---|---|---|"]
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node) == "Tool":
                    tname, desc, kw = _tool_fields(node)
                    lit = _literal(tname)
                    if lit is None:
                        continue
                    text = (_literal(desc) or "(described in the source)").replace("|", "/")
                    lines.append(f"| `{name}_{lit}` | {text} | {_flags(kw)} |")
            lines.append("")
    return "\n".join(lines)
