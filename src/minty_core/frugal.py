"""Frugal mode: spend fewer tokens per turn without losing a capability.

F0 is off. F1 asks reasoning models (Groq's gpt-oss) for low reasoning effort on simple turns and medium on hard
ones, caps the reply length, and leaves the reasoning text out of the response. Thinking mode is never frugal.
"""
from __future__ import annotations

# per turn size: (reasoning effort, most tokens the reply may use, reasoning included)
F1_POLICY = {"small": ("low", 600), "large": ("medium", 1500)}


def frugal_params(level: str, size: str, tier: str) -> dict:
    """Extra request fields for one model call; empty when frugal mode is off or the turn is a thinking turn."""
    if level == "F0" or tier != "fast":
        return {}
    effort, max_tokens = F1_POLICY.get(size, F1_POLICY["large"])
    return {"reasoning_effort": effort, "max_completion_tokens": max_tokens, "include_reasoning": False}


def frugal_gate(f0_rows: list[dict], f1_rows: list[dict], *, min_trials: int = 10, min_saving: float = 0.25,
                max_loss_points: float = 2.0) -> dict:
    """Whether the compressed prompts may be used under F1: the same evaluation run with F0 and with F1 must show
    at least min_saving fewer tokens and at most max_loss_points lower success (rows: passed, tokens_est, infra)."""
    def stats(rows):
        scored = [r for r in rows if not r.get("infra")]
        if not scored:
            return 0, 0.0, 0.0
        return (len(scored), 100.0 * sum(1 for r in scored if r.get("passed")) / len(scored),
                sum(float(r.get("tokens_est") or 0) for r in scored) / len(scored))
    n0, success0, tokens0 = stats(f0_rows)
    n1, success1, tokens1 = stats(f1_rows)
    saving = round(1 - tokens1 / tokens0, 3) if tokens0 else 0.0
    loss = round(success0 - success1, 1)
    passed = min(n0, n1) >= min_trials and saving >= min_saving and loss <= max_loss_points
    return {"passed": passed, "evaluated": True, "trials": [n0, n1], "token_saving": saving,
            "success_loss_points": loss}
