"""Success metrics. pass^k follows τ-bench: the chance that k independent tries ALL succeed."""
from collections import defaultdict
from math import comb
from statistics import median


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(all k of k tries succeed) from n tries with c successes."""
    if k > n:
        raise ValueError("k cannot exceed the number of trials")
    return comb(c, k) / comb(n, k)


def summarise(results: list[dict], k: int = 3) -> dict:
    """results: dicts with task, passed, forbidden_hit, steps, tokens_est, seconds, infra."""
    by_task = defaultdict(list)
    for r in results:
        by_task[r["task"]].append(r)
    scored = [r for r in results if not r.get("infra")]
    trials = {t: [r for r in rs if not r.get("infra")] for t, rs in by_task.items()}
    usable = {t: rs for t, rs in trials.items() if len(rs) >= k}
    return {
        "tasks": len(by_task),
        "trials": len(results),
        "infra_errors": sum(1 for r in results if r.get("infra")),
        "pass_at_1": round(sum(r["passed"] for r in scored) / len(scored), 3) if scored else None,
        f"pass_hat_{k}": round(sum(pass_hat_k(len(rs), sum(r["passed"] for r in rs), k) for rs in usable.values())
                               / len(usable), 3) if usable else None,
        "forbidden_hits": sum(1 for r in scored if r["forbidden_hit"]),
        "median_steps": median(r["steps"] for r in scored) if scored else None,
        "median_seconds": round(median(r["seconds"] for r in scored), 1) if scored else None,
        "tokens_est": sum(r.get("tokens_est", 0) for r in scored),
        "failed_tasks": sorted({r["task"] for r in scored if not r["passed"]}),
    }
