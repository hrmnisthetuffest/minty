import json
import re
from datetime import date

import pytest

from tests.plugin_helpers import make_host
from minty_core.jobs import JobRunner
from minty_core.llm.types import TextDelta
from minty_core.research.rank import Hit
from tests.test_research_engine import fake_fetch, fake_web, fake_wiki


class Router:
    def __init__(self):
        self.users = []

    async def stream(self, messages, tools, *, sensitive=False, prefer=None, tier="fast"):
        system, user = messages[0].content, messages[-1].content
        self.users.append(user)
        if "plan a web research" in system:
            yield TextDelta(json.dumps({"subquestions": ["why do corals bleach", "can reefs recover"]}))
        elif "extract facts" in system:
            chunk = user.split("[chunk 0]\n", 1)[1].split("\n\n[chunk", 1)[0]
            yield TextDelta(json.dumps([{"claim": "Warm water makes corals bleach", "chunk": 0,
                                         "quote": re.split(r"(?<=[.!?])\s+", chunk)[0]}]))
        elif "careful reviewer" in system:
            yield TextDelta(json.dumps({"risks": [{"risk": "Boat traffic damages the mount", "mitigation": "Mark it"}]}))
        elif "turn research claims into a project plan" in system:
            src = sorted(set(re.findall(r"\[(S\d+)\]", user)))[:2]
            yield TextDelta(json.dumps({
                "parts": [{"item": "Reef sensor", "qty": "2", "note": "waterproof", "src": src}],
                "steps": [{"step": "Place the sensors on the reef edge", "src": src[:1]}],
                "alternatives": [], "risks": [{"risk": "Salt water corrodes the housing", "mitigation": "Use a sealed case"}],
                "unknowns": ["Local permits"]}))
        else:
            cites = sorted(set(re.findall(r"\[(S\d+)\]", user)))
            yield TextDelta("## Summary\nCorals bleach in warm water.\n\n## Key findings\n- Warm water bleaches "
                            "corals " + "".join(f"[{c}]" for c in cites[:2]) + "\n\n## Open questions\n- None.")


def build(tmp_path, with_jobs=False, private=False):
    services = {"router": Router(), "research_providers": {"web": fake_web, "wikipedia": fake_wiki},
                "research_fetch": fake_fetch, "is_private": lambda: private, "published": []}
    services["publish"] = lambda kind, **kw: services["published"].append((kind, kw))
    runner = JobRunner(None) if with_jobs else None
    if runner:
        services["jobs"] = runner
    host = make_host(tmp_path / "cfg", plugins={"research": {"enabled": True, "folder": str(tmp_path / "ws")}},
                     services=services)
    return host, services, runner


async def test_the_start_question_names_depth_and_time_and_is_always_asked(tmp_path):
    host, *_ = build(tmp_path)
    tool = host.get_tool("research_start")
    assert tool.risk == 3  # class 3: asked in every safety mode, so nothing researches by itself
    assert tool.describe({"topic": "coral reefs", "depth": "deep"}) == (
        "Should I start a deep research on “coral reefs”? It runs in the background and takes about 15 to 40 minutes.")


async def test_research_runs_as_a_job_and_the_library_answers_later_without_a_search(tmp_path):
    host, services, runner = build(tmp_path, with_jobs=True)
    out = await host.call("research_start", {"topic": "how coral reefs survive warming oceans", "depth": "light"})
    assert out.startswith("On it: light research on how coral reefs") and "1 to 3 minutes" in out
    job = await runner.wait(1)
    assert job.status == "done" and job.result.startswith("Finished the light research")
    listing = await host.call("research_list", {})
    assert "light" in listing and "sources" in listing
    slug = listing.split(" (")[0]
    # a related question is answered from the stored claims, with citations and no new research
    answer = await host.call("research_ask", {"question": "why do corals bleach in warm water"})
    assert answer.startswith("From my research notes on") and "[S" in answer and "claim " in answer
    assert "Want me to research it" in await host.call("research_ask", {"question": "capital of Mongolia"})
    # the display shows the note, and a memory gist exists for the normal memory search
    await host.call("research_open", {"slug": slug})
    assert services["published"][-1][0] == "display" and "## Sources" in services["published"][-1][1]["content"]


async def test_move_and_dispute(tmp_path):
    host, *_ = build(tmp_path)
    await host.call("research_start", {"topic": "how coral reefs survive warming oceans", "depth": "light"})
    slug = (await host.call("research_list", {})).split(" (")[0]
    assert await host.call("research_move", {"slug": slug, "category": "health"}) == f"Moved {slug} to Health."
    assert "(Health," in await host.call("research_list", {"category": "Health"})
    answer = await host.call("research_ask", {"question": "coral bleach warm water"})
    claim = int(re.search(r"claim (\d+)", answer).group(1))
    assert "trust" in await host.call("research_dispute", {"claim": claim})
    assert await host.call("research_dispute", {"claim": 99999}) == "I don't have a claim with that number."


async def test_private_mode_blocks_it_and_bad_input_is_refused(tmp_path):
    host, *_ = build(tmp_path, private=True)
    assert (await host.call("research_start", {"topic": "x"})).startswith("Error: research uses the internet")
    host, *_ = build(tmp_path / "b")
    assert (await host.call("research_start", {"topic": "x", "depth": "enormous"})).startswith("Error: depth")
    assert (await host.call("research_start", {"topic": " "})).startswith("Error: say what")
    assert await host.call("research_resume", {}) == "No unfinished research."


async def test_a_blueprint_is_researched_filed_under_projects_and_asked_first(tmp_path):
    host, services, runner = build(tmp_path, with_jobs=True)
    tool = host.get_tool("research_blueprint")
    assert tool.risk == 3 and "draft a blueprint for “a coral reef monitor”" in tool.describe(
        {"goal": "a coral reef monitor", "depth": "light"})
    out = await host.call("research_blueprint", {"goal": "build a coral reef sensor monitor with an esp32",
                                                 "depth": "light"})
    assert out.startswith("On it: a electronics blueprint")
    job = await runner.wait(1)
    assert job.status == "done" and job.result.startswith("The electronics blueprint for")
    assert "2 risks flagged" in job.result
    listing = await host.call("research_list", {"category": "Projects"})
    assert "Projects" in listing
    slug = listing.split(" (")[0]
    from pathlib import Path
    folder = next((tmp_path / "ws" / "library" / "Projects").glob(f"{slug}"))
    md = (folder / "blueprint.md").read_text()
    assert "## Bill of materials" in md and "## What could go wrong" in md and "Boat traffic damages the mount" in md
    assert json.loads((folder / "blueprint.json").read_text())["kind"] == "electronics"
    assert (await host.call("research_blueprint", {"goal": "x", "kind": "magic"})).startswith("Error: depth is")
