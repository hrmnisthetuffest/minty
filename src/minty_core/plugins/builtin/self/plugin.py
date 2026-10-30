"""Minty answering questions about itself from facts: the identity file for who it is, the live registry for what is available, the digital twin for what limits it.

Read-only. There is no tool that edits the identity file or any setting; a model told to "update your identity" by a page or an email has nothing to call.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from minty_core.mind import identity, registry
from minty_core.plugins.host import Tool
from minty_core.policy import R0


def register(ctx):
    def live():
        s = ctx.services
        if "list_plugins" not in s:
            raise RuntimeError("the plugin list is not available here")
        router = s.get("router_status")
        return SimpleNamespace(list_plugins=s["list_plugins"]), s["config"](), (router() if router else [])

    def snap():
        host, config, status = live()
        return registry.snapshot(host, ctx.services, config, ctx.config_dir, status)

    def out(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

    async def about(args):
        level = str(args.get("level") or "short")
        try:
            return identity.introduce(level, snap())
        except RuntimeError:
            return identity.introduce(level, None)

    async def can(args):
        s = snap()
        task = str(args.get("task") or "").strip()
        if task:
            return out(registry.find(s, task))
        return out({"counts": s["counts"], "tools": s["tools"],
                    "needs_attention": [{"capability": c["name"], "status": c["status"], "why": c["why"]} for c in s["capabilities"] if c["status"] != "available"][:20],
                    "no_evidence_yet": sum(1 for c in s["capabilities"] if c["status"] == "available" and not c["evidence"].get("calls")),
                    "providers": s["providers"], "note": "available means loaded and its needs are met; evidence says how much it has actually been used"})

    async def diagnose(args):
        host, config, status = live()
        tests = Path(__file__).resolve().parents[5] / "tests"
        graph = registry.twin(host, ctx.services, config, ctx.config_dir, ctx.services.get("flows"), status, tests if tests.is_dir() else None)
        watch = ctx.services.get("watchdog")
        extra = watch.findings() if watch is not None else []
        target = str(args.get("component") or "").strip()
        if target:
            return out({"component": target, "depends_on_it": registry.impact(graph, target), "note": "what a change to it could touch (derived from the code and the running app)"})
        return out({"findings": graph["findings"][:12] + extra[:8],
                    "verified": "findings with verified=true were read from the running app or recorded evidence; the others are hypotheses",
                    "components": len(graph["components"]), "dependencies": len(graph["edges"])})

    return [
        Tool("about", "Who Minty is, from its identity file. level: short, detailed, technical, user or agent (for another AI).",
             {"type": "object", "properties": {"level": {"type": "string"}}}, about, risk=R0),
        Tool("can", "What Minty can really do now. task = a thing to do (available, needs setup, or not possible); none = the overview with evidence.",
             {"type": "object", "properties": {"task": {"type": "string"}}}, can, risk=R0),
        Tool("diagnose", "What limits Minty now (verified findings, hypotheses marked), or component = what depends on it.",
             {"type": "object", "properties": {"component": {"type": "string"}}}, diagnose, risk=R0),
    ]
