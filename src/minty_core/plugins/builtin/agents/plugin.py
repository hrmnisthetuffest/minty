import asyncio
import logging
from dataclasses import asdict
from pathlib import Path

from minty_core.plugins.builtin.agents.adapters import ClaudeCodeAdapter, HermesAdapter, OpenClawAdapter, extra_tools
from minty_core.plugins.builtin.agents.jobs import MODES, JobManager
from minty_core.plugins.builtin.agents.verify import parse_check
from minty_core.plugins.host import Tool
from minty_core.policy import R1


log = logging.getLogger(__name__)
PERMISSION_MODES = {"default", "acceptEdits", "plan"}


def build_adapters(settings: dict) -> dict:
    mode = str(settings.get("claude_permission_mode") or "acceptEdits")
    if mode not in PERMISSION_MODES:
        log.warning("Ignoring unsafe claude_permission_mode %r; using acceptEdits", mode)
        mode = "acceptEdits"
    budget = settings.get("claude_max_budget_usd")
    extra, refused = extra_tools(settings.get("claude_extra_tools"))
    if refused:
        log.warning("Ignoring claude_extra_tools %s: a blanket allow or a program that can delete, escalate or download is never added", refused)
    adapters = [
        ClaudeCodeAdapter(permission_mode=mode, max_budget_usd=float(budget) if isinstance(budget, (int, float))
                          and not isinstance(budget, bool) else None, extra=extra),
        HermesAdapter(),
        OpenClawAdapter(agent_id=str(settings.get("openclaw_agent") or "main")),
    ]
    return {a.name: a for a in adapters}


def settings_flag(settings, key) -> bool:
    return bool((settings or {}).get(key) is True)


def configured_check(settings: dict) -> list[str] | None:
    """The tests command the user set (split without a shell), or None: then each project's own tests are found."""
    text = str((settings or {}).get("check_command") or "").strip()
    if not text:
        return None
    argv = parse_check(text)
    if argv is None:
        log.warning("Ignoring check_command %r: it must be one plain command, with no pipes, ';', '&&' or '$'", text)
    return argv


def register(ctx):
    auto_start = settings_flag(ctx.settings, "auto_start")
    settings = ctx.settings
    manager = JobManager(
        Path(str(settings.get("root") or "~/Documents/project")).expanduser(),
        build_adapters(settings),
        ctx.notify,
        state=ctx.state,
        log_dir=ctx.config_dir / "jobs",
        sessions_path=ctx.config_dir / "agent_sessions.json",
        max_concurrent=int(settings.get("max_concurrent") or 2),
        max_runtime_s=float(settings.get("max_runtime_s") or 1800),
        check=configured_check(settings),
        max_rounds=int(settings.get("max_rounds") or 2),
        verify_work=settings.get("verify") is not False,
        store_path=ctx.config_dir / "agent_jobs.json",
    )
    if notice := manager.interrupted_notice():  # a restart cut a coding job short: say so, once, instead of staying silent
        ctx.notify(notice)

    ctx.services["coding_jobs"] = lambda: [asdict(j) for j in list(manager._jobs.values())]  # the record, for missions_review

    mirrors = ctx.state.setdefault("mirrors", {})  # coding job id -> shared runner job id

    def mirror(job, prompt: str) -> None:
        """Show the coding job in the shared job list, so "what are you doing" and "cancel that" cover it."""
        runner = ctx.services.get("jobs")
        if runner is None:
            return

        async def follow(job_ctx):
            try:
                while job.status == "running":
                    await asyncio.sleep(1.0)
                return job.summary or job.status
            except asyncio.CancelledError:
                if job.status == "running":
                    await manager.stop(job.id)
                raise

        shown = runner.submit("coding", f"{job.agent.replace('_', ' ')} on {job.project}: {prompt}", follow,
                              budget_s=manager_runtime + 120, announce=False)  # the manager announces itself
        mirrors[job.id] = shown.id

    manager_runtime = float(settings.get("max_runtime_s") or 1800)

    async def start_task(args):
        job = await manager.start(args["agent"], args["project"], args["prompt"], mode=args.get("mode") or "work")
        mirror(job, args["prompt"])
        if args.get("wait") is True:  # for a mission: block until the files and tests have been looked at
            return await manager.wait(job.id)
        if job.mode == "ask":
            return f"Started job {job.id}: {job.agent} will look and answer, and change nothing."
        return (f"Started job {job.id}. I'll tell you when {job.agent} finishes, and whether the files and tests show "
                "it worked.")

    async def start_project(args):
        job = await manager.start(args["agent"], args["name"], args["prompt"], create=True)
        mirror(job, args["prompt"])
        return f"Created project {args['name']} and started job {job.id}."

    async def task_status(args):
        return manager.status(int(args["job_id"]) if args.get("job_id") is not None else None)

    async def stop_task(args):
        return await manager.stop(int(args["job_id"]))

    async def list_projects(args):
        return "\n".join(manager.list_projects()) or "No projects yet."

    async def list_agents(args):
        return "\n".join(manager.list_agents()) or "No coding agents are installed."

    def schema(**props):
        return {"type": "object", "properties": {k: {"type": t} for k, (t, _) in props.items()},
                "required": [k for k, (_, req) in props.items() if req]}

    def describe_start(args, *, target, kind=""):
        who = str(args.get("agent", "")).replace("_", " ")
        if args.get("mode") == "ask":
            return f"Should I ask {who} about {args.get(target)} (it will only read): {str(args.get('prompt', ''))[:80]}?"
        return f"Should I have {who} work on {kind}{args.get(target)}: {str(args.get('prompt', ''))[:80]}?"

    agent = ("string", True)
    return [
        Tool("start_task", "Run a coding agent (claude_code, hermes, openclaw) on a project; files and tests are checked "
                           "afterwards. mode=ask: read-only question. wait=true (in a mission): block, return the check.",
             {"type": "object", "properties": {"agent": {"type": "string"}, "project": {"type": "string"},
                                               "prompt": {"type": "string"},
                                               "mode": {"type": "string", "enum": list(MODES)},
                                               "wait": {"type": "boolean"}},
              "required": ["agent", "project", "prompt"]}, start_task, risky=not auto_start, risk=R1,
             describe=lambda a: describe_start(a, target="project")),
        Tool("start_project", "Create a new project folder and start a coding agent on it.",
             schema(agent=agent, name=("string", True), prompt=("string", True)), start_project, risky=not auto_start, risk=R1,
             describe=lambda a: describe_start(a, target="name", kind="new project ")),
        Tool("task_status", "Status of one coding job, or the most recent jobs.", schema(job_id=("integer", False)), task_status),
        Tool("stop_task", "Stop a running coding job.", schema(job_id=("integer", True)), stop_task, risk=R1),
        Tool("list_projects", "List project folders.", schema(), list_projects),
        Tool("list_agents", "List installed coding agents.", schema(), list_agents),
    ]
