"""The whole chain through the real app: a recipe starts a mission, the mission uses the studio tools, a draft is queued, and nothing is posted."""
import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from tests.agent_eval.scripted import ScriptedRouter
from minty_core.context import build_context

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
SCRIPT = "Did you know a creeper cannot climb ladders in Minecraft?"


class FakeKokoro:
    def get_voices(self):
        return ["am_michael"]

    def create(self, text, voice, speed, lang):
        t = np.linspace(0, 3.0, 24000 * 3, endpoint=False)
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = build_context(tmp_path / "cfg")
    c.services["studio_voice_engine"] = FakeKokoro()
    return c


def tool(tool_name, **args):
    return lambda last: ("tool", tool_name, args)


async def seed_gameplay(ctx, tmp_path):
    clip = tmp_path / "parkour.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8", "-pix_fmt", "yuv420p", str(clip)], check=True)
    out = await ctx.host.call("studio_make", {"action": "add", "path": str(clip), "kind": "gameplay", "license": "owned", "source": "my world"})
    assert "Kept" in out, out


async def test_a_daily_reel_recipe_makes_a_draft_by_itself_and_posts_nothing(ctx, tmp_path):
    await seed_gameplay(ctx, tmp_path)
    ctx.services["router"] = ScriptedRouter([
        tool("studio_make", action="voice", text=SCRIPT, name="daily"),
        tool("studio_make", action="video", background="gameplay", voiceover="daily", music="calm", script=SCRIPT, name="daily"),
        lambda last: ("tool", "studio_draft", {"kind": "reel", "files": [last.split("Made ")[1].split(".mp4")[0] + ".mp4"], "caption": "Creepers and ladders #minecraft"}),
        lambda last: ("say", "Made today's reel and queued it as a draft for approval.")])
    assert "Created" in await ctx.host.call("recipes_create", {"name": "daily-reel", "goal": "make one parkour fact reel and queue it as a draft for my approval; do not post it", "at": "18:00"})
    recipes, jobs = ctx.services["recipes"], ctx.services["jobs"]
    run = recipes.run_now("daily-reel")
    assert run.status == "started" and run.job_id
    await asyncio.wait_for(jobs.wait(run.job_id), 120)
    job = jobs.get(run.job_id)
    assert job.status == "done", (job.status, job.result)
    assert [r.status for r in recipes.runs("daily-reel")] == ["done"] and recipes.get("daily-reel").failures == 0
    tools_used = [e["tool"] for e in job.checkpoint["ledger"]]
    assert tools_used == ["studio_make", "studio_make", "studio_draft"] and all(e["ok"] for e in job.checkpoint["ledger"])
    posts = await ctx.host.call("studio_posts", {"action": "list"})
    assert "drafted reel" in posts and "posted" not in posts.replace("drafted", "")  # queued for the user, nothing went out
    assert ctx.host.get_tool("studio_send").risk({"id": 1}) == 3  # and sending it is a question


async def test_a_failing_recipe_pauses_itself_and_reaches_the_attention_list(ctx):
    ctx.services["router"] = ScriptedRouter([tool("studio_make", action="video", background="gameplay", voiceover="nothing", name="x"),
                                             lambda last: ("say", "Done.")], verifier=lambda user: False)
    await ctx.host.call("recipes_create", {"name": "bad", "goal": "make a reel from clips that are not there", "at": "18:00", "max_failures": 1})
    recipes, jobs = ctx.services["recipes"], ctx.services["jobs"]
    run = recipes.run_now("bad")
    await asyncio.wait_for(jobs.wait(run.job_id), 120)
    # a mission that cannot do its job ends blocked or failed; either way it is not reported as done
    assert recipes.runs("bad")[0].status in ("failed", "blocked")
    if recipes.runs("bad")[0].status == "failed":
        assert recipes.get("bad").paused
        assert "bad" in await ctx.host.call("attention_review", {})
