import asyncio
import logging
from pathlib import Path

from minty_core.plugins.builtin.agents.adapters import ClaudeCodeAdapter, HermesAdapter, OpenClawAdapter
from minty_core.plugins.builtin.agents.jobs import JobManager
from minty_core.plugins.host import Tool
from minty_core.policy import R1


log = logging.getLogger(__name__)
PERMISSION_MODES = {"default", "acceptEdits", "plan"}


def build_adapters(settings: dict) -> dict:
    mode = str(settings.get("claude_permission_mode") or "acceptEdits")
    if mode not in PERMISSION_MODES:
        log.warning("Ignoring unsafe claude_permission_mode %r; using acceptEdits", mode)
        mode = "acceptEdits"
    adapters = [
        ClaudeCodeAdapter(permission_mode=mode),
        HermesAdapter(),
        OpenClawAdapter(agent_id=str(settings.get("openclaw_agent") or "main")),
    ]
    return {a.name: a for a in adapters}


def settings_flag(settings, key) -> bool:
    return bool((settings or {}).get(key) is True)


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
    )

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
        job = await manager.start(args["agent"], args["project"], args["prompt"])
        mirror(job, args["prompt"])
        return f"Started job {job.id}. I'll tell you when {job.agent} finishes."

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
        return f"Should I have {who} work on {kind}{args.get(target)}: {str(args.get('prompt', ''))[:80]}?"

    agent = ("string", True)
    return [
        Tool("start_task", "Run a coding agent on an existing project. agent is claude_code, hermes or openclaw.",
             schema(agent=agent, project=("string", True), prompt=("string", True)), start_task, risky=not auto_start, risk=R1,
             describe=lambda a: describe_start(a, target="project")),
        Tool("start_project", "Create a new project folder and start a coding agent on it.",
             schema(agent=agent, name=("string", True), prompt=("string", True)), start_project, risky=not auto_start, risk=R1,
             describe=lambda a: describe_start(a, target="name", kind="new project ")),
        Tool("task_status", "Status of one coding job, or the most recent jobs.", schema(job_id=("integer", False)), task_status),
        Tool("stop_task", "Stop a running coding job.", schema(job_id=("integer", True)), stop_task, risk=R1),
        Tool("list_projects", "List project folders.", schema(), list_projects),
        Tool("list_agents", "List installed coding agents.", schema(), list_agents),
    ]
