import json

from tests.agent_eval.bakeoff import bakeoff, markdown, single_provider_router
from tests.agent_eval.tasks import BY_ID


async def test_bakeoff_tabulates_resumes_and_retries_infra(tmp_path):
    calls = []
    outages = {"down": 1}

    async def run(task, router):
        calls.append((router, task.id))
        if router == "down" and outages["down"]:
            outages["down"] -= 1
            return {"task": task.id, "passed": False, "forbidden_hit": False, "steps": 0, "seconds": 0, "infra": True}
        return {"task": task.id, "passed": router == "good", "forbidden_hit": False, "steps": 4, "seconds": 3.0,
                "tokens_est": 100, "infra": False}

    tasks = [BY_ID["delayed"], BY_ID["scroll"]]
    out = tmp_path / "run.jsonl"
    table = await bakeoff(["good", "down"], tasks, 2, lambda m: m, out, run, pause_s=0, infra_wait_s=0)
    assert table["good"]["pass_at_1"] == 1.0 and table["good"]["pass_hat_2"] == 1.0
    assert table["down"]["pass_at_1"] == 0.0 and table["down"]["infra_errors"] == 0  # retried after the outage
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 8 and {r["model"] for r in rows} == {"good", "down"}
    before = len(calls)
    await bakeoff(["good", "down"], tasks, 2, lambda m: m, out, run, pause_s=0)
    assert len(calls) == before  # everything already done: resume skips it
    md = markdown(table, 2)
    assert md.splitlines()[0].startswith("| model | pass@1 | pass^2") and "| good | 1.0 | 1.0 |" in md


def test_single_provider_router_has_no_failover(tmp_path):
    from minty_core.config import ConfigStore
    from minty_core.keystore import SecretsStore
    store = ConfigStore(tmp_path)
    store.load()
    router = single_provider_router(store, SecretsStore(tmp_path), "gemini")
    assert [p.name for p in router._store.config.providers] == ["gemini"]
    assert len(store.config.providers) > 1  # the real config is untouched
