"""`minty tune`: what this computer is, which tier it belongs to and the settings that suit it (docs/performance-plan.md).

  minty tune                 describe the computer and say how Minty adapts to it; nothing is changed
  minty tune --lite          show the lite profile (light voice and recogniser, no captions, still display, no terminal pictures)
  minty tune --lite --apply  use it, after asking (--yes: without asking); `minty tune --full --apply` goes back
  minty tune --system        also print operating-system tips (words only; nothing is run)

By default Minty looks and works the same on every computer; the lite profile is a choice you make, never a default.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from minty_core.config import ConfigStore
from minty_core.hwprobe import Hardware, Plan, probe, recommend


def describe(hw: Hardware) -> list[str]:
    gpus = ", ".join(g.name + (f" ({g.vram_mb} MB)" if g.vram_mb else "") for g in hw.gpus) or "none found"
    disk = {True: "spinning disk", False: "solid-state", None: "unknown"}[hw.rotational]
    return [f"Processor : {hw.cpu_model}, {hw.threads} threads" + ("" if not hw.x86 or hw.avx2 else " (no AVX2)"),
            f"Memory    : {hw.ram_mb / 1024:.1f} GB ({hw.available_mb / 1024:.1f} GB free), swap {hw.swap_mb / 1024:.1f} GB",
            f"Disk      : {disk}", f"Graphics  : {gpus}",
            f"Power     : {'battery' if hw.on_battery else 'mains' if hw.on_battery is False else 'unknown'}"]


def _get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _set(data: dict, dotted: str, value) -> None:
    *path, last = dotted.split(".")
    node = data
    for part in path:
        node = node[part]
    node[last] = value


def apply(store: ConfigStore, plan: Plan) -> tuple[list[str], list[str]]:
    """Set what differs. Returns (lines for the changes made, names already as recommended)."""
    data = store.config.model_dump(mode="json")
    changed, already = [], []
    for dotted, value in plan.changes.items():
        old = _get(data, dotted)
        if old == value:
            already.append(dotted)
        else:
            _set(data, dotted, value)
            changed.append(f"  {dotted}: {_show(old)} -> {_show(value)}")
    if changed:
        store.update(data)
    return changed, already


def _show(value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def system_tips(hw: Hardware) -> list[str]:
    """Operating-system settings that help this computer. Words only: nothing here runs a command or changes a file."""
    tips = []
    if hw.ram_mb and hw.ram_mb < 8000:
        tips.append("Memory is tight: turn on compressed swap (zram), about half of your RAM, and lower vm.swappiness to "
                    "around 10 so the kernel keeps programs in memory. Your distribution's zram package does the first part.")
    if hw.rotational:
        tips.append("You have a spinning disk: keep the voice models folder (~/.local/share/minty/voices) and the config folder on "
                    "it only if there is no faster disk; start Minty once at login so the first use is not the slow one.")
    if hw.threads <= 2:
        tips.append("Two threads or fewer: close the browser while talking to Minty, and set MALLOC_ARENA_MAX=2 in the service "
                    "environment so memory is not spread over many arenas.")
    if hw.on_battery:
        tips.append("On battery the processor runs slower: expect longer replies from local models and use the cloud brain.")
    tips.append("For the smoothest audio let PipeWire use real-time scheduling (the rtkit service) and keep its buffer small.")
    return tips


def cli(argv: list[str], directory: Path, probe_fn: Callable[[], Hardware] = probe, say: Callable[[str], None] = print,
        ask: Callable[[str], str] = input) -> int:
    parser = argparse.ArgumentParser(prog="minty tune", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lite", action="store_true", help="the lite profile: trade looks for speed")
    parser.add_argument("--full", action="store_true", help="go back to the full look and the default voice and recogniser")
    parser.add_argument("--apply", action="store_true", help="change the settings that differ")
    parser.add_argument("--yes", action="store_true", help="do not ask before changing")
    parser.add_argument("--system", action="store_true", help="also print operating-system tips")
    args = parser.parse_args(argv)
    hw = probe_fn()
    plan = recommend(hw, lite=args.lite)
    if args.full:
        from minty_core.config import Config

        defaults = Config().model_dump(mode="json")
        plan = Plan(plan.tier, {k: _get(defaults, k) for k in recommend(hw, lite=True).changes},
                    ["Full profile: the default voice, recogniser, captions, animation and terminal pictures."])
    store = ConfigStore(directory)
    store.load()
    for line in describe(hw):
        say(line)
    say("")
    say(f"Tier      : {plan.tier}  ({ {'t0': 'very old or small', 't1': 'ordinary', 't2': 'modern'}[plan.tier] })")
    for note in plan.notes:
        say(f"  - {note}")
    say("")
    data = store.config.model_dump(mode="json")
    differs = {k: v for k, v in plan.changes.items() if _get(data, k) != v}
    if plan.changes:
        say("Settings in this profile:")
    for dotted, value in plan.changes.items():
        mark = "ok" if dotted not in differs else f"{_show(_get(data, dotted))} -> {_show(value)}"
        say(f"  {dotted} = {_show(value)}   [{mark}]")
    if args.system:
        say("")
        say("Operating system tips:")
        for tip in system_tips(hw):
            say(f"  - {tip}")
    say("")
    if not plan.changes:
        say("Minty looks and works the same on this computer as on any other; nothing needs to change. Nothing was changed.")
        return 0
    if not differs:
        say("These settings are already in use." + (" Nothing was changed." if not args.apply else ""))
        return 0
    if not args.apply:
        say("Nothing was changed. Add --apply to use these settings.")
        return 0
    if not args.yes and ask(f"Change {len(differs)} setting(s) as shown? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Nothing was changed.")
        return 0
    changed, already = apply(store, plan)
    say("Changed:")
    for line in changed:
        say(line)
    if already:
        say(f"Already as recommended: {', '.join(already)}")
    say("Restart Minty to apply them.")
    return 0
