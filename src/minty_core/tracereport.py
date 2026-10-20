"""Summarise trace files: how fast, how often things fail, what it costs."""
import statistics
from collections import Counter


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)


def per_turn(events: list[dict]) -> list[dict]:
    """One row per turn id: outcome, time, model calls and which providers failed inside it."""
    grouped: dict[str, list[dict]] = {}
    for e in events:
        if e.get("turn_id"):
            grouped.setdefault(e["turn_id"], []).append(e)
    rows = []
    for turn_id, evs in grouped.items():
        turn = next((e for e in evs if e.get("kind") == "turn"), {})
        calls = [e for e in evs if e.get("kind") == "llm_call"]
        failed = [e for e in calls if e.get("outcome") == "error"]
        rows.append({"turn_id": turn_id, "outcome": turn.get("outcome"), "total_ms": turn.get("total_ms"),
                     "llm_calls": len(calls), "failed_calls": len(failed),
                     "failed_providers": [e.get("provider") for e in failed],
                     "tool_calls": sum(1 for e in evs if e.get("kind") == "tool_call")})
    return rows


def summarise(events: list[dict]) -> dict:
    turns = [e for e in events if e.get("kind") == "turn"]
    llm = [e for e in events if e.get("kind") == "llm_call"]
    tools = [e for e in events if e.get("kind") == "tool_call"]
    voice = [t for t in turns if t.get("mode") == "classic" and t.get("outcome") == "answered"]
    first_word = [t["first_audio_ms"] for t in voice if t.get("first_audio_ms") is not None]
    by_provider = Counter(e.get("provider") for e in llm if e.get("outcome") == "ok")
    failures = Counter(e.get("provider") for e in llm if e.get("outcome") == "error")
    tool_errors = Counter(e.get("name") for e in tools if e.get("ok") is False)
    chars = sum((e.get("chars_in") or 0) + (e.get("chars_out") or 0) for e in llm)
    turns_by_id = per_turn(events)
    tokens_in = sum(int(e.get("tokens_in") or 0) for e in llm)
    first_tokens = [e["first_token_ms"] for e in llm  # older traces hold a mask here: only real numbers count
                    if isinstance(e.get("first_token_ms"), (int, float)) and e["first_token_ms"]]
    cached = sum(int(e.get("cached_tokens") or 0) for e in llm)

    def stage(key: str) -> list[float]:
        return [t[key] for t in voice if isinstance(t.get(key), (int, float))]

    instant = sum(1 for e in events if e.get("kind") == "instant")
    return {
        "silence_p50_ms": _pct(stage("silence_ms"), 0.5), "stt_p50_ms": _pct(stage("stt_ms"), 0.5),
        "first_sentence_p50_ms": _pct(stage("first_sentence_ms"), 0.5),
        "spec_turns": dict(Counter(t["spec"] for t in voice if t.get("spec"))),
        "instant_answers": instant, "instant_share": round(instant / len(turns), 3) if turns else None,
        "tokens_in": tokens_in,
        "tokens_out": sum(int(e.get("tokens_out") or 0) for e in llm),
        "reasoning_tokens": sum(int(e.get("reasoning_tokens") or 0) for e in llm),
        "cached_share": round(cached / tokens_in, 3) if tokens_in else None,
        "turns": len(turns),
        "outcomes": dict(Counter(t.get("outcome") for t in turns)),
        "first_word_p50_ms": _pct(first_word, 0.5),
        "first_word_p95_ms": _pct(first_word, 0.95),
        "llm_calls": len(llm),
        "llm_calls_per_turn": round(len(llm) / len(turns), 2) if turns else None,
        "answers_by_provider": dict(by_provider),
        "failovers_by_provider": dict(failures),
        "first_token_median_ms": statistics.median(first_tokens) if first_tokens else None,
        "tool_calls": len(tools),
        "tool_error_rate": round(sum(tool_errors.values()) / len(tools), 3) if tools else None,
        "tools_by_count": dict(Counter(e.get("name") for e in tools).most_common(10)),
        "tool_errors": dict(tool_errors),
        "est_tokens": chars // 4,
        "confirmations": sum(1 for e in events if e.get("kind") == "confirm"),
        "turns_traced": len(turns_by_id),
        "worst_turns": sorted((r for r in turns_by_id if r["failed_calls"]),
                              key=lambda r: (-r["failed_calls"], -(r["total_ms"] or 0)))[:5],
        "slowest_turns": sorted(({"total_ms": t.get("total_ms"), "outcome": t.get("outcome"), "mode": t.get("mode")}
                                 for t in turns if t.get("total_ms") is not None),
                                key=lambda t: -t["total_ms"])[:5],
    }


def format_report(s: dict) -> str:
    def fmt(v):
        return "—" if v is None else v

    lines = [
        f"Turns: {s['turns']}  outcomes: {s['outcomes'] or '—'}",
        f"Time to first spoken word: p50 {fmt(s['first_word_p50_ms'])} ms, p95 {fmt(s['first_word_p95_ms'])} ms",
        f"Where the wait goes (p50): end-of-speech pause {fmt(s['silence_p50_ms'])} ms, recognition {fmt(s['stt_p50_ms'])} ms, "
        f"to the first sentence {fmt(s['first_sentence_p50_ms'])} ms; "
        + ("guess " + ", ".join(f"{k} {v}" for k, v in sorted(s["spec_turns"].items(), reverse=True)) if s["spec_turns"]
           else "no recognition started in the pause"),
        f"Instant answers (no model): {s['instant_answers']}" + (
            f" ({round(s['instant_share'] * 100)}% of turns)" if s["instant_share"] is not None else ""),
        f"Model calls: {s['llm_calls']} ({fmt(s['llm_calls_per_turn'])} per turn), first token median "
        f"{fmt(s['first_token_median_ms'])} ms",
        f"Answered by: {s['answers_by_provider'] or '—'}   failovers: {s['failovers_by_provider'] or '—'}",
        f"Tool calls: {s['tool_calls']}  error rate: {fmt(s['tool_error_rate'])}  top: {s['tools_by_count'] or '—'}",
        f"Tool errors: {s['tool_errors'] or '—'}",
        f"Estimated tokens: {s['est_tokens']:,}   confirmations asked: {s['confirmations']}",
        f"Tokens: {s.get('tokens_in', 0):,} in, {s.get('tokens_out', 0):,} out "
        f"({s.get('reasoning_tokens', 0):,} reasoning); prompt-cache hits: "
        + (f"{round(s['cached_share'] * 100)}% of input" if s.get("cached_share") is not None else "—"),
        "Turns with failed model calls: " + (", ".join(
            f"{t['turn_id']} ({t['outcome'] or '?'}, {t['failed_calls']} failed: "
            f"{', '.join(map(str, t['failed_providers']))})" for t in s.get("worst_turns", [])) or "—"),
        "Slowest turns: " + (", ".join(f"{t['total_ms']:.0f} ms ({t['mode']}/{t['outcome']})"
                                       for t in s["slowest_turns"]) or "—"),
    ]
    return "\n".join(lines)


def where_it_went(events: list[dict], policy: dict[str, str]) -> str:
    """'Where did that go?': which providers the latest request with model calls was sent to, and what each does
    with prompts (policy: provider -> privacy label)."""
    calls = [e for e in events if e.get("kind") == "llm_call"]
    if not calls:
        return "Nothing has been sent to a model yet today."
    last = max(calls, key=lambda e: e.get("ts", 0))
    tid = last.get("turn_id")
    group = [e for e in calls if tid and e.get("turn_id") == tid] or [last]
    parts, seen = [], set()
    for e in sorted(group, key=lambda e: e.get("ts", 0)):
        name = e.get("provider", "?")
        if (name, e.get("outcome")) in seen:
            continue
        seen.add((name, e.get("outcome")))
        label = policy.get(name, "data use unknown").split(";")[0]
        if e.get("outcome") == "ok":
            parts.append(f"it went to {name} ({label})")
        elif e.get("error_kind") in ("rate_limit", "too_large", "auth"):
            parts.append(f"{name} refused it ({str(e.get('error_kind')).replace('_', ' ')}; {label})")
        else:
            parts.append(f"{name} got it but failed ({label})")
    return "Your last request: " + "; ".join(parts) + "."


def receipt(events: list[dict]) -> str:
    """'How many tokens was that?': the latest request with model calls, from the traces."""
    calls = [e for e in events if e.get("kind") == "llm_call"]
    if not calls:
        return "Nothing has been sent to a model yet today."
    last = max(calls, key=lambda e: e.get("ts", 0))
    tid = last.get("turn_id")
    group = [e for e in calls if (tid and e.get("turn_id") == tid) or e is last]
    served = [e for e in group if e.get("tokens_in")]
    if not served:
        return "That request was refused before it used any tokens."
    tin = sum(int(e.get("tokens_in") or 0) for e in served)
    tout = sum(int(e.get("tokens_out") or 0) for e in served)
    reasoning = sum(int(e.get("reasoning_tokens") or 0) for e in served)
    cached = sum(int(e.get("cached_tokens") or 0) for e in served)
    estimate = any(e.get("usage_source") != "provider" for e in served)
    text = (f"That took {'about ' if estimate else ''}{tin:,} tokens in and {tout:,} out across {len(served)} model "
            f"call{'s' * (len(served) != 1)}")
    if reasoning:
        text += f", {reasoning:,} for reasoning"
    if cached:
        text += f"; {round(100 * cached / tin)} percent of the input came from the provider's cache"
    return text + "."
