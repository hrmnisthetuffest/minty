import math

from minty_core.jobs import JobRunner
from minty_core.plugins.host import Tool
from minty_core.policy import R1

PROMPT = ("You are a background helper working for Minty. Nobody can answer questions while you work. "
          "Use your tools to complete the task, then give the result in plain spoken sentences, under 150 words."
          "\n\nTask: {task}")


def _positive(value, default, cast):
    """A positive finite number from a setting; anything else (text, bool, NaN, zero, negative) is the default."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number < 1e-3 or (cast is int and number < 1):
        return default
    return cast(number)


def register(ctx):
    max_concurrent = _positive(ctx.settings.get("max_concurrent"), 3, int)
    max_runtime = _positive(ctx.settings.get("max_runtime_s"), 600.0, float)

    def runner():
        shared = ctx.services.get("jobs")
        if shared is not None:
            return shared
        if "runner" not in ctx.state:  # standalone (tests, old setups): a private runner with the same behaviour
            ctx.state["runner"] = JobRunner(None, notify=ctx.notify)
        return ctx.state["runner"]

    def announce(job):
        if job.status == "done":
            return f"Subagent {job.id} finished. {job.result[:400]}"
        if job.status == "timeout":
            return f"Subagent {job.id} ran out of time."
        return f"Subagent {job.id} failed: {job.result[:200]}"

    async def spawn_subagent(args):
        factory = ctx.services.get("agent_factory")
        if factory is None:
            return "Subagents are unavailable."
        task = str(args.get("task") or "").strip()
        if not task:
            raise ValueError("empty task")
        jobs = runner()
        if len(jobs.select(active_only=True, kinds={"subagent"})) >= max_concurrent:
            raise RuntimeError(f"{max_concurrent} subagent(s) already running")
        agent = factory(tier="deep" if args.get("deep") else "fast")
        is_private = ctx.services.get("is_private")
        if is_private is not None:
            agent.private_probe = is_private  # live: a helper turns private if the conversation does mid-run
            if is_private():
                agent.force_private = True  # a private conversation's helpers never reach a training-on-prompts provider

        async def work(job_ctx):
            sentences: list[str] = []
            async for sentence in agent.respond(PROMPT.format(task=task)):
                sentences.append(sentence)
                job_ctx.progress(sentence)
            return " ".join(sentences).strip() or "No result."

        job = jobs.submit("subagent", task, work, budget_s=max_runtime, private=bool(agent.force_private),
                          announce=announce)
        return f"Started subagent {job.id}. I'll tell you when it's done."

    def _mine(job_id=None):
        jobs = runner()
        if job_id is not None:
            job = jobs.get(int(job_id))
            return [job] if job is not None and job.kind == "subagent" else []
        return jobs.select(kinds={"subagent"})

    async def subagent_status(args):
        selected = _mine(args.get("id")) if args.get("id") is not None else _mine()[-5:]
        if not selected:
            return f"No subagent {args['id']}." if args.get("id") is not None else "No subagents yet."
        return "\n".join(
            f"Subagent {j.id} ({j.goal[:60]}): {j.status}" + (f" - {j.result[:200]}" if j.result else "")
            for j in selected)

    async def stop_subagent(args):
        found = _mine(args.get("id"))
        if not found or not found[0].active:
            return f"No running subagent {args['id']}."
        await runner().cancel(found[0].id)
        return f"Stopped subagent {found[0].id}."

    return [
        Tool("spawn_subagent", "Start a background helper agent on a self-contained task (research, comparison, "
                               "drafting); it reports back by voice. deep=true for hard reasoning.",
             {"type": "object", "properties": {"task": {"type": "string"}, "deep": {"type": "boolean"}},
              "required": ["task"]}, spawn_subagent),
        Tool("subagent_status", "Status of one subagent, or the most recent ones.",
             {"type": "object", "properties": {"id": {"type": "integer"}}}, subagent_status),
        Tool("stop_subagent", "Stop a running subagent.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, stop_subagent, risk=R1),
    ]
