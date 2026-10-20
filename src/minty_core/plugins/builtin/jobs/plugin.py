from minty_core.jobs import short
from minty_core.plugins.host import Tool
from minty_core.policy import R1


def register(ctx):
    def runner():
        return ctx.services.get("jobs")

    async def status(args):
        jobs = runner()
        if jobs is None:
            return "Background jobs are unavailable."
        if args.get("id") is not None:
            job = jobs.get(int(args["id"]))
            if job is None:
                return f"There is no job {args['id']}."
            lines = [f"{job.label()} ({short(job.goal, 80)}): {job.status}"]
            if job.progress:
                lines.append("Recent steps: " + "; ".join(job.progress[-5:]))
            if job.question:
                lines.append(f"Waiting for the user: {job.question}")
            if job.result and not job.active:
                lines.append(f"Result: {job.result[:400]}")
            return "\n".join(lines)
        return jobs.summary()

    async def cancel(args):
        jobs = runner()
        if jobs is None:
            return "Background jobs are unavailable."
        if args.get("all") is True:
            stopped = await jobs.cancel_all()
            return f"Stopped {len(stopped)} job(s)." if stopped else "Nothing was running."
        job = jobs.get(int(args["id"])) if args.get("id") is not None else jobs.latest_active()
        if job is None or not await jobs.cancel(job.id):
            return "Nothing to cancel."
        return f"Stopped {job.label()}."

    return [
        Tool("status", "What background work is running, or one job's details and result by id (when the user asks "
                       "what you're doing or how a job is going).",
             {"type": "object", "properties": {"id": {"type": "integer"}}}, status),
        Tool("cancel", "Cancel a background job: by id, the latest one (no arguments), or all=true.",
             {"type": "object", "properties": {"id": {"type": "integer"}, "all": {"type": "boolean"}}}, cancel, risk=R1),
    ]
