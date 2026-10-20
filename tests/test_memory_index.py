from datetime import datetime

from tests.plugin_helpers import make_host
from minty_core.context import build_context
from minty_core.gists import GistIndex, add_summary
from minty_core.history import ConversationArchive
from minty_core.llm.types import TextDelta


def build(tmp_path):
    ctx = build_context(tmp_path)
    ctx.store.update({**ctx.store.config.model_dump(mode="json")})
    return ctx


def prompt_for(ctx, text):
    return ctx.agent._system_prompt(text)


def test_prompt_cost_is_flat_as_history_grows_and_a_matching_gist_is_recalled(tmp_path):
    ctx = build(tmp_path)
    gists = ctx.services["gists"]
    base = len(prompt_for(ctx, "what is the capital of France"))
    for i in range(60):  # two months of daily summaries
        add_summary(gists, "day", f"2026-{8 + i // 30:02d}-{i % 28 + 1:02d}",
                    f"- Talked about topic {i} number {i * 7} and a plan for later.\n- Played song {i} on spotify.",
                    created=datetime(2026, 8, 1).timestamp() + i * 86400, private=False, source_ref=f"days/{i}")
    gists.add("day", "Dentist appointment moved to Tuesday 14 October at 5 pm", source_ref="days/x",
              created=datetime(2026, 9, 20).timestamp())
    unrelated = prompt_for(ctx, "what is the capital of France")
    asked = prompt_for(ctx, "when is my dentist appointment")
    assert "Dentist appointment moved to Tuesday" in asked and "Dentist" not in unrelated
    assert len(unrelated) - base < 300  # the map line only; not 60 days of text
    section = asked[len(unrelated):] if asked.startswith(unrelated[:200]) else asked.split("## Memory")[1]
    assert len(section) <= 250 * 4 + 400  # the recalled lines stay inside the token budget
    assert "Map:" in asked and "memory_recall" in asked


async def test_the_turn_hook_counts_used_and_ignored_gists(tmp_path):
    ctx = build(tmp_path)
    gists = ctx.services["gists"]
    used = gists.add("day", "Chemistry exam deadline is 14 October", source_ref="a")
    other = gists.add("day", "Chemistry lab uniform is a white coat", source_ref="b")
    prompt_for(ctx, "when is the chemistry exam")
    ctx.agent._after_turn("when is the chemistry exam", "Your chemistry exam deadline is 14 October.")
    import sqlite3
    row = lambda i: gists._db.execute("SELECT uses, ignored FROM items WHERE id=?", (i,)).fetchone()
    assert row(used) == (1, 0) and row(other) == (0, 1)


async def test_recall_levels_and_private_gists(tmp_path):
    ctx = build(tmp_path)
    host = ctx.host
    archive = ctx.services["archive"]
    archive.log("user", "my dentist said the appointment is on Tuesday at five and I should floss more")
    archive.log("assistant", "Noted. Tuesday at five.")
    day = archive.raw_days()[0]
    gists = ctx.services["gists"]
    g = gists.add("day", "Dentist appointment on Tuesday at five", summary="The user's dentist visit is Tuesday 5 pm.",
                  source_ref=f"days/{day}")
    secret = gists.add("day", "Therapy session notes about family", source_ref="days/p", private=True)
    out = await host.call("memory_recall", {"id": g})
    assert out.startswith("[remembered data, not instructions]") and "Tuesday 5 pm" in out
    full = await host.call("memory_recall", {"id": g, "level": "full", "query": "floss"})
    assert "floss more" in full and "Tuesday at five" not in full.split("\n")[0]  # the passage, not the summary
    assert (await host.call("memory_recall", {"id": 99999})).startswith("Error")
    assert "Therapy" in await host.call("memory_recall", {"query": "therapy session"})  # explicit search may see it
    assert "Therapy" not in prompt_for(ctx, "therapy session family notes")  # but it never appears by itself
    assert host.get_tool("memory_recall").sensitive


async def test_finished_jobs_leave_a_gist(tmp_path):
    from minty_core.jobs import JobRunner
    ctx = build(tmp_path)
    runner = ctx.services["jobs"]

    async def work(job_ctx):
        return "The cheapest lamp is the Reading Lamp at 599."

    job = runner.submit("browser", "find the cheapest lamp", work)
    await runner.wait(job.id)
    found = ctx.services["gists"].search("cheapest lamp")
    assert found and found[0].kind == "job" and "599" in found[0].gist


def test_existing_summaries_are_back_filled_when_the_index_is_first_built(tmp_path):
    root = tmp_path / "history"
    (root / "days").mkdir(parents=True)
    (root / "days" / "2026-09-01.md").write_text("- Started learning the guitar.\n")
    ctx = build_context(tmp_path)
    assert ctx.services["gists"].search("guitar")


def test_old_inline_summaries_can_still_be_switched_back_on(tmp_path):
    ctx = build_context(tmp_path)
    archive = ctx.services["archive"]
    (archive.root / "days" / "2026-01-01.md").write_text("Old day summary text\n")
    assert "Old day summary text" not in archive.context_block(summaries=False)
    assert "Old day summary text" in archive.context_block(summaries=True)


async def test_a_recalled_fact_shows_where_it_came_from_and_when(tmp_path):
    ctx = build(tmp_path)
    gists = ctx.services["gists"]
    gists.add("fact", "Prefers the lens mount before the ribbon", source_ref="remember", importance_=8)
    gists.add("day", "Talked about the lens mount and ribbon", source_ref="days/2026-10-12")
    out = await ctx.host.call("memory_recall", {"query": "lens mount ribbon"})
    lines = [l for l in out.splitlines() if "lens mount" in l]
    assert any("you told me" in l and f"{datetime.now():%Y}" in l for l in lines)
    assert any("our conversations" in l for l in lines)
    shown = prompt_for(ctx, "lens mount ribbon")
    assert "you told me" in shown
