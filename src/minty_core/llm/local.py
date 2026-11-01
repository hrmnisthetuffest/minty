"""A small local model through llama.cpp, for titles, intents and summaries only (item 531, experimental).

It is adopted only if a measurement on this machine passes the gate (enough tokens per second, little enough memory)
and stays off otherwise. It never takes tool calls and is never a fallback for conversation. llama-cpp-python is an
optional install; without it (or without a model file) this module reports why and does nothing.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncIterator, Callable

from minty_core import osutil
from minty_core.llm.types import Event, LLMError, Message, TextDelta, ToolSpec

MIN_TOKENS_PER_S = 4.0
MAX_RAM_MB = 2500
BENCH_PROMPT = "Give a title of at most six words for a chat about planning a school science project."


def gate(tokens_per_s: float, ram_mb: float, *, min_tps: float = MIN_TOKENS_PER_S,
         max_ram_mb: float = MAX_RAM_MB) -> tuple[bool, str]:
    """(adopt?, why). Fails closed on anything unmeasured."""
    if tokens_per_s <= 0 or ram_mb <= 0:
        return False, "not measured"
    if tokens_per_s < min_tps:
        return False, f"too slow here: {tokens_per_s:.1f} tokens/s, needs {min_tps:g}"
    if ram_mb > max_ram_mb:
        return False, f"too much memory: {ram_mb:.0f} MB, limit {max_ram_mb:g}"
    return True, f"{tokens_per_s:.1f} tokens/s, {ram_mb:.0f} MB"


def _default_loader(path: str, n_ctx: int):
    from llama_cpp import Llama  # optional dependency

    return Llama(model_path=path, n_ctx=n_ctx, verbose=False)


class LocalModel:
    """Provider-shaped, text only. `loader` is injectable so the gate and the adapter test without llama.cpp."""

    name = "local"

    def __init__(self, model_path: str, n_ctx: int = 2048, loader: Callable[[str, int], object] | None = None):
        self._path, self._n_ctx, self._loader = model_path, n_ctx, loader or _default_loader
        self._llm = None

    def available(self) -> tuple[bool, str]:
        if not self._path or not Path(self._path).expanduser().exists():
            return False, "no model file is set"
        if self._loader is _default_loader:
            try:
                import llama_cpp  # noqa: F401
            except ImportError:
                return False, "llama-cpp-python is not installed (pip install -e '.[local-llm]')"
        return True, ""

    def _load(self):
        if self._llm is None:
            self._llm = self._loader(str(Path(self._path).expanduser()), self._n_ctx)
        return self._llm

    async def stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncIterator[Event]:
        if tools:
            raise LLMError("local: this model does not take tools", kind="other")
        ok, why = self.available()
        if not ok:
            raise LLMError(f"local: {why}", kind="other")
        prompt = [{"role": m.role, "content": m.content} for m in messages]
        text = await asyncio.to_thread(self._complete, prompt)
        yield TextDelta(text)

    def _complete(self, prompt: list[dict]) -> str:
        out = self._load().create_chat_completion(messages=prompt, max_tokens=96, temperature=0.2)
        return str(out["choices"][0]["message"]["content"]).strip()


def measure(model: LocalModel, clock: Callable[[], float] = time.perf_counter) -> tuple[float, float]:
    """(tokens per second, megabytes added by loading and running it). Words stand in for tokens (x1.3)."""
    before = osutil.rss_mb()
    start = clock()
    text = model._complete([{"role": "user", "content": BENCH_PROMPT}])
    seconds = max(1e-6, clock() - start)
    tokens = max(1.0, len(text.split()) * 1.3)
    after = osutil.rss_mb()
    return tokens / seconds, max(0.0, after - before)


def adopt(model: LocalModel, state_path: Path, **limits) -> tuple[bool, str]:
    """Measure, apply the gate, and record the verdict. Never raises."""
    try:
        tps, ram = measure(model)
    except Exception as e:  # noqa: BLE001 — a model that cannot even run is simply not adopted
        verdict = (False, f"could not run: {type(e).__name__}")
        tps = ram = 0.0
    else:
        verdict = gate(tps, ram, **limits)
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps({"adopted": verdict[0], "why": verdict[1], "tokens_per_s": round(tps, 2),
                                            "ram_mb": round(ram), "measured_at": time.time()}), encoding="utf-8")
    return verdict


def adopted(state_path: Path) -> bool:
    try:
        return bool(json.loads(Path(state_path).read_text(encoding="utf-8")).get("adopted"))
    except (OSError, ValueError):
        return False
