"""One snapshot of "what is Minty doing / how is it set up", for the terminal display."""
import time


def banner(cfg) -> str:
    """The line shown while privacy mode is on, empty otherwise."""
    if cfg.safety.privacy_mode:
        return "PRIVACY MODE: nothing leaves this computer except to providers that don't train on it"
    return ""


def snapshot(ctx) -> dict:
    cfg = ctx.store.config
    agent, pipeline = ctx.agent, ctx.pipeline
    now = time.time()
    state = ctx.host._state  # plugin name -> plugin state dict
    work = []
    runner = ctx.services.get("jobs") if hasattr(ctx, "services") else None
    if runner is not None:
        for j in runner.running():
            work.append({"id": j.id, "kind": j.kind, "what": j.goal, "secs": int(j.elapsed(now)), "status": j.status,
                         "progress": j.progress[-1] if j.progress else "", "question": j.question})
    for j in state.get("subagents", {}).get("jobs", {}).values():  # helpers started before the runner existed
        if isinstance(j, dict) and j.get("status") == "running":
            work.append({"id": j["id"], "kind": "subagent", "what": j["task"],
                         "secs": int(now - j.get("started", now)), "status": "running", "progress": "", "question": ""})
    mirrored = state.get("agents", {}).get("mirrors", {})  # already listed through the runner
    for j in state.get("agents", {}).get("jobs", {}).values():
        if getattr(j, "status", "") == "running" and j.id not in mirrored:
            work.append({"id": j.id, "kind": "coding", "what": f"{j.agent}: {j.prompt}", "secs": int(now - j.started),
                         "status": "running", "progress": "", "question": ""})
    subagents = [w for w in work if w["kind"] == "subagent"]
    jobs = [w for w in work if w["kind"] == "coding"]
    providers = [p for p in ctx.router.status() if p.get("enabled") and p.get("has_key", True)]
    cooling = [p["name"] for p in providers if p.get("cooling_for", 0) > 0]
    music, track = False, None
    watcher = getattr(pipeline, "music", None)
    if watcher is not None and cfg.audio.music_aware:
        try:
            music = bool(watcher.is_playing())
        except Exception:
            music = False
        try:
            track = watcher.now_playing()  # the card on the display; never blocks
        except Exception:  # noqa: BLE001 - a player that misbehaves must not take the display down
            track = None
        if track is not None and track.get("status") == "Playing":
            music = True  # the track and the playing flag come from separate refreshes: never contradict each other
    active = getattr(ctx.router, "active", None)
    usable = ctx.router.usable() if hasattr(ctx.router, "usable") else []
    return {
        "model": f"{active[0]} · {active[1].rsplit('/', 1)[-1]}" if active else "",
        "picked": cfg.preferred_provider,
        "models": [c.name for c in usable],
        "voice_mode": cfg.audio.voice_mode,
        "tier": getattr(agent, "tier", "fast"),
        "private": bool(getattr(agent, "force_private", False)),
        "muted": bool(getattr(pipeline, "muted", False)),
        "noise_ignored": int(getattr(pipeline, "noise_ignored", 0) or 0),
        "music": music,
        "now_playing": track,
        "tts": cfg.tts.engine,
        "wake": "name" if cfg.audio.wake_mode == "words" else cfg.audio.wake_word,
        "brain_ready": len(providers) - len(cooling),
        "brain_total": len(providers),
        "cooling": cooling,
        "context_msgs": len(getattr(agent, "history", [])),
        "rules": len(ctx.services["prompts"].rules()) if "prompts" in ctx.services else 0,
        "commands": len(ctx.services["prompts"].commands()) if "prompts" in ctx.services else 0,
        "work": work,
        "subagents": subagents,
        "jobs": jobs,
        "health": ctx.router.board() if hasattr(ctx.router, "board") else [],
        "capacity": _capacity(ctx),
        "privacy_banner": banner(cfg),
        "agenda": coming_up(ctx.services.get("scheduler"), ctx.services.get("timers")),
    }


def coming_up(scheduler, timers, limit: int = 6) -> list[dict]:
    """Your reminders and running timers, soonest first: {"kind", "text", "due" (epoch seconds), "remaining_s"}. Minty's own
    nudges are not listed; a store that fails just gives fewer items."""
    out: list[dict] = []
    now = time.time()
    try:
        for item in (scheduler.waiting() if scheduler is not None else []):
            if item.user_set:
                out.append({"kind": "reminder", "text": item.text, "due": item.due.timestamp(),
                            "remaining_s": max(0.0, item.due.timestamp() - now)})
    except Exception:  # noqa: BLE001
        pass
    try:
        for t in (timers.all() if timers is not None else []):
            out.append({"kind": "timer", "text": t.label or "timer", "due": t.due, "remaining_s": t.remaining(now)})
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda x: x["due"])
    return out[:limit]


def _capacity(ctx) -> dict:
    brownout = ctx.services.get("brownout") if hasattr(ctx, "services") else None
    if brownout is None:
        return {}
    try:
        cap = brownout.capacity()
    except Exception:  # noqa: BLE001 — the meter is a nicety
        return {}
    return {"share": cap.share, "calls_left": cap.calls_left, "forecast": cap.forecast}


def selfcheck_line(ctx, probe: dict[str, str] | None = None) -> str:
    """One line at start-up: what is set up, and anything that will not work."""
    cfg = ctx.store.config
    enabled = [c for c in cfg.providers if c.enabled]
    keyed = ctx.router.usable()
    groq = bool(ctx.secrets.get("groq")) and not cfg.stt.local_only and cfg.stt.provider == "groq"
    problems = []
    if not keyed:
        problems.append("no model has a key (minty set-key <provider>)")
    problems += [f"{name} {state}" for name, state in (probe or {}).items() if state != "ok"]
    return (f"Self-check: {len(keyed)}/{len(enabled)} models keyed · speech {'groq, then local' if groq else 'local'}"
            f" ({cfg.stt.language or 'any language'}) · voice {cfg.tts.engine} · wake "
            f"{'name' if cfg.audio.wake_mode == 'words' else cfg.audio.wake_word} · "
            f"problems: {'; '.join(problems) if problems else 'none'}")
