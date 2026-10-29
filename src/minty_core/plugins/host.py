import asyncio
import dataclasses
import importlib.util
import inspect
import time
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from minty_core.destinations import plugin_scope
from minty_core._toml import tomllib
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.types import ToolSpec

log = logging.getLogger(__name__)
BUILTIN_PLUGIN_DIR = Path(__file__).parent / "builtin"
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[[dict], Any]
    risky: bool = False
    sensitive: bool = False
    # the result is for the user's eyes only (residency.LOCAL_ONLY): shown, never given to any model or kept
    local_only: bool = False
    describe: Callable[[dict], str] | None = None  # spoken confirmation prompt for risky tools
    # decided per call from the arguments (e.g. a browser click on a 'Buy' button); true means ask first
    confirm_when: Callable[[dict], bool] | None = None
    # risk class 0-3 (read / reversible change / visible to others / money-credentials-deletion), or a function of
    # the arguments; `risky` and `confirm_when` still mean class 2
    risk: Any = 0
    # where this call sends data (a URL host, an e-mail address), for the destination check; None = nowhere
    egress: Callable[[dict], str | None] | None = None
    # seconds the answer to the same arguments may be reused (idempotent lookups only); 0 = never cached
    cache_ttl: float = 0
    # a side effect (send, download, install): the same call inside safety.idempotency_window_s runs once and the
    # repeat is answered with the first result (idempotency.py)
    idempotent: bool = False
    # a reason this call must not run right now, or None; checked by the safety layer before anyone is asked (the terminal is
    # off, the command is refused)
    blocked_when: Callable[[dict], str | None] | None = None
    # what the user's own request must say for the question to be redundant (policy.Mandate); None = the class decides, as always
    mandate: Any = None
    # true when this call puts what it carries into a fixed place the user set up and trusts (their own mailbox or sheet through a server they
    # allowed): then where that content came from does not make a read or a write into a question; a call that reaches anyone else still does
    content_ok: Callable[[dict], bool] | None = None


# Tools whose repeat must not repeat the side effect (send, upload, download, install, fire a webhook). One list,
# so it can be audited in one place; a plugin can also set Tool.idempotent itself.
IDEMPOTENT_TOOLS = frozenset({"apis_send", "files_download", "files_upload", "installer_install",
                              "n8n_trigger_webhook"})


# Lookups that need no confirmation and whose answers do not depend on the order they run in (no page to move, nothing
# to change): when the model asks for several of them in one round they run at the same time. One list, so it can be
# audited in one place. A "read" that moves state (a browser scroll or click) must never be added.
PARALLEL_SAFE_TOOLS = frozenset({"weather_get_weather", "web_search_search", "clock_get_time", "memory_recall",
                                 "files_list", "files_read"})


def _noop_notify(text: str) -> None:
    return None


@dataclass
class PluginContext:
    name: str
    settings: dict
    config_dir: Path
    secrets: SecretsStore
    notify: Callable[[str], None] = _noop_notify
    state: dict = field(default_factory=dict)
    services: dict = field(default_factory=dict)

    context_providers: list = field(default_factory=list)

    def secret(self, key: str) -> str | None:
        return self.secrets.get(f"plugin.{self.name}.{key}")

    def prompt_context(self, provider: Callable[[], str]) -> None:
        """Register a short live description (e.g. the open web page) appended to the system prompt."""
        self.context_providers.append(provider)


@dataclass
class _Loaded:
    name: str
    description: str = ""
    enabled: bool = False
    schema: dict = field(default_factory=dict)
    error: str | None = None
    tools: dict[str, Tool] = field(default_factory=dict)
    context: list = field(default_factory=list)


class PluginHost:
    def __init__(
        self, search_dirs: list[Path], store: ConfigStore, secrets: SecretsStore, config_dir: Path, notify=None,
        *, services=None,
    ):
        self._dirs = [Path(d) for d in search_dirs]
        self._store = store
        self._secrets = secrets
        self._config_dir = Path(config_dir)
        self._notify = notify or _noop_notify
        self._services = services if services is not None else {}
        self._services.setdefault("config", lambda: self._store.config)  # plugins read global settings through this
        self._state: dict[str, dict] = {}
        self._plugins: dict[str, _Loaded] = {}
        self._once_locks: dict[str, asyncio.Lock] = {}
        self.only: set[str] | None = None  # safe mode: just these plugins run (saved settings are not changed)

    def reload(self) -> None:
        loaded: dict[str, _Loaded] = {}
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for folder in sorted(directory.iterdir()):
                if (folder / "plugin.toml").is_file():
                    item = self._load(folder)
                    loaded[item.name] = item
        self._plugins = loaded

    def _load(self, folder: Path) -> _Loaded:
        try:
            manifest = tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))
        except Exception as e:
            return _Loaded(folder.name, error=f"bad manifest: {e}")
        name = manifest.get("name", folder.name)
        entry = self._store.config.plugins.get(name, {})
        item = _Loaded(
            name=name,
            description=manifest.get("description", ""),
            enabled=bool(entry.get("enabled", manifest.get("default_enabled", True))),
            schema=manifest.get("settings", {}),
        )
        if not _NAME.fullmatch(name):
            item.enabled, item.error = False, f"invalid plugin name {name!r}"
            return item
        if not item.enabled:
            return item
        try:
            ctx = PluginContext(
                name, {k: v for k, v in entry.items() if k != "enabled"}, self._config_dir, self._secrets,
                self._notify, self._state.setdefault(name, {}), services=self._services,
            )
            spec = importlib.util.spec_from_file_location(f"minty_plugin_{name}", folder / "plugin.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for tool in module.register(ctx):
                if not _NAME.fullmatch(tool.name):
                    raise ValueError(f"invalid tool name {tool.name!r}")
                full = f"{name}_{tool.name}"
                item.tools[full] = dataclasses.replace(
                    tool, name=full, idempotent=tool.idempotent or full in IDEMPOTENT_TOOLS)
            item.context = ctx.context_providers
        except Exception as e:
            log.exception("plugin %s failed to load", name)
            item.tools, item.error = {}, f"{type(e).__name__}: {e}"
        return item

    def _active(self) -> list[_Loaded]:
        return [p for p in self._plugins.values() if p.enabled and p.error is None
                and (self.only is None or p.name in self.only)]

    def tool_specs(self) -> list[ToolSpec]:
        return [ToolSpec(t.name, t.description, t.parameters) for p in self._active() for t in p.tools.values()]

    def prompt_context(self) -> str:
        """Live blocks plugins add to the system prompt right now (empty when there is nothing to say)."""
        blocks = []
        for p in self._active():
            for provider in p.context:
                try:
                    text = provider()
                except Exception:  # noqa: BLE001 — a broken block is skipped, never a broken turn
                    log.warning("prompt context from %s failed", p.name, exc_info=True)
                    continue
                if text:
                    blocks.append(text)
        return "\n".join(blocks)

    def get_tool(self, full_name: str) -> Tool | None:
        for p in self._active():
            if full_name in p.tools:
                return p.tools[full_name]
        return None

    async def call(self, full_name: str, args: dict) -> str:
        tool = self.get_tool(full_name)
        tracer = self._services.get("tracer")
        if tool is None:
            if tracer is not None:
                tracer.event("tool_call", name=full_name, ok=False, error="unknown tool")
            return f"Error: unknown tool {full_name}"
        started = time.perf_counter()
        with plugin_scope(self._plugin_of(full_name)):
            return await self._call_in_scope(full_name, tool, args, tracer, started)

    def _plugin_of(self, full_name: str) -> str:
        for p in self._plugins.values():
            if full_name in p.tools:
                return p.name
        return "core"

    async def _call_in_scope(self, full_name: str, tool: Tool, args: dict, tracer, started: float) -> str:
        ok, result = False, ""
        once = self._services.get("idempotency") if tool.idempotent else None
        window = float(self._services.get("idempotency_window", lambda: 0)()) if once is not None else 0.0
        if once is not None and window > 0:
            key = once.key(full_name, args)
            async with self._once_locks.setdefault(key, asyncio.Lock()):  # a second identical call waits for the first
                prior = once.lookup(key, window)
                if prior is not None:
                    if tracer is not None:
                        tracer.event("tool_call", name=full_name, args=args, ok=True, repeated=True,
                                     ms=round((time.perf_counter() - started) * 1000, 2), result_chars=len(prior[0]))
                    return f"{prior[0]} (already done {prior[1]} seconds ago; not repeated)"
                result = await self._call_once(full_name, tool, args, tracer, started)
                if not result.startswith("Error"):
                    once.record(key, result)
                return result
        return await self._call_once(full_name, tool, args, tracer, started)

    async def _call_once(self, full_name: str, tool: Tool, args: dict, tracer, started: float) -> str:
        ok, result = False, ""
        cache = self._services.get("answer_cache") if tool.cache_ttl > 0 else None
        if cache is not None:
            hit = cache.get(full_name, args)
            if hit is not None:
                if tracer is not None:
                    tracer.event("tool_call", name=full_name, args=args, ok=True, cached=True,
                                 ms=round((time.perf_counter() - started) * 1000, 2), result_chars=len(hit))
                return hit
        try:
            if inspect.iscoroutinefunction(tool.func):
                result = str(await tool.func(args))
            else:
                result = str(await asyncio.to_thread(tool.func, args))
            ok = not result.startswith("Error")
            if ok and cache is not None:
                cache.put(full_name, args, result, tool.cache_ttl)
            return result
        except Exception as e:
            log.exception("tool %s failed", full_name)
            result = f"Error: {type(e).__name__}: {e}"
            return result
        finally:
            if tracer is not None:
                tracer.event("tool_call", name=full_name, args=args, ok=ok, ms=round((time.perf_counter() - started) * 1000, 1),
                             result_chars=len(result), error=result[:160] if not ok else None)

    def list_plugins(self) -> list[dict]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "enabled": p.enabled,
                "settings_schema": p.schema,
                "error": p.error,
                "tools": sorted(p.tools),
            }
            for p in self._plugins.values()
        ]


class FilteredHost:
    """Read-only view of a plugin host for subagents: hides risky tools and tools of excluded plugins."""

    NEVER = ("shell_",)  # whatever else is allowed, a background helper never gets the terminal

    def __init__(self, host, *, exclude_plugins=(), allow_risky: bool = False):
        self._host = host
        self._exclude = tuple(f"{p}_" for p in exclude_plugins) + self.NEVER
        self._allow_risky = allow_risky

    def _allowed(self, tool: Tool | None) -> bool:
        if tool is None or tool.name.startswith(self._exclude):
            return False
        # a risk worked out from the arguments (a send, a delete) can turn out to be a question, and a helper cannot answer
        always_asked = tool.risky or tool.confirm_when is not None or callable(tool.risk) or \
            (isinstance(tool.risk, int) and tool.risk >= 2)
        return self._allow_risky or not always_asked  # helpers cannot confirm

    def tool_specs(self) -> list[ToolSpec]:
        return [s for s in self._host.tool_specs() if self._allowed(self._host.get_tool(s.name))]

    def get_tool(self, full_name: str) -> Tool | None:
        tool = self._host.get_tool(full_name)
        return tool if self._allowed(tool) else None

    async def call(self, full_name: str, args: dict) -> str:
        if self.get_tool(full_name) is None:
            return f"Error: tool {full_name} is not available to subagents"
        return await self._host.call(full_name, args)
