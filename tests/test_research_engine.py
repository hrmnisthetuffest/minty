import asyncio
import json
import re
from datetime import date

import pytest

from minty_core.research.engine import DEPTHS, Research, ResearchError
from minty_core.research.library import Library
from minty_core.research.rank import Hit

TOPIC = "how coral reefs survive warming oceans"


def page(i: int) -> str:
    return (f"<html><head><title>Reef study {i}</title></head><body><article><h1>Reef study {i}</h1>"
            f"<p>Coral reefs near site {i} lost {10 + i} percent of live coral during the last warming event, "
            f"according to field surveys. Symbiotic algae leave corals when the water stays too warm for several "
            f"weeks in a row, which turns the coral white and weakens it.</p>"
            f"<p>Recovery depends on how fast the water cools and on local pollution levels near the reef "
            f"system number {i}. Researchers recommend protecting nearby mangroves to reduce stress on the reef.</p>"
            f"</article></body></html>")


async def fake_web(get, query, n):
    return [Hit(f"https://site{i}.example/reef/{i}", f"Reef study {i}", "coral reefs warming oceans", "web",
                "2024", "web") for i in range(10)]


async def fake_wiki(get, query, n):
    return [Hit("https://en.wikipedia.org/wiki/Coral_bleaching", "Coral bleaching",
                "coral bleaching warming oceans reefs", "reference", "2025", "wikipedia")]


async def fake_fetch(url):
    m = re.search(r"/(\d+)$", url)
    return ("Reef", page(int(m.group(1))) if m else page(99), "read")


class Model:
    """Plays planner, extractor and writer; every claim quote is copied from the chunk it was given."""

    def __init__(self):
        self.calls = {"plan": 0, "extract": 0, "write": 0}

    async def __call__(self, system, user):
        if "plan a web research" in system:
            self.calls["plan"] += 1
            return json.dumps({"subquestions": ["why do corals bleach", "can reefs recover"]})
        if "extract facts" in system:
            self.calls["extract"] += 1
            chunk = user.split("[chunk 0]\n", 1)[1].split("\n\n[chunk", 1)[0]
            quote = re.split(r"(?<=[.!?])\s+", chunk)[0]
            return json.dumps([{"claim": "Warm water makes corals lose their algae and turn white", "chunk": 0,
                                "quote": quote}])
        self.calls["write"] += 1
        cites = sorted(set(re.findall(r"\[(S\d+)\]", user)))
        return ("## Summary\nCorals bleach when water stays warm, and recovery depends on cooling and pollution.\n\n"
                "## Key findings\n- Warm water drives bleaching " + "".join(f"[{c}]" for c in cites[:3]) +
                " [S999]\n\n## Open questions\n- How fast can reefs adapt?")


def engine(tmp_path, ask, providers=None, fetch=fake_fetch):
    return Research(ask=ask, get=None, fetch=fetch, library=Library(tmp_path / "lib"), state_dir=tmp_path / "state",
                    providers=providers or {"web": fake_web, "wikipedia": fake_wiki}, today=lambda: date(2026, 10, 12))


async def test_a_light_run_finishes_with_distinct_sources_and_checkable_citations(tmp_path):
    model = Model()
    ticks = []
    r = engine(tmp_path, model)
    r._progress = ticks.append
    note = await r.run(TOPIC, "light")
    assert 5 <= note.n_sources <= DEPTHS["light"].sources and note.n_claims >= 5 and note.used_model
    assert note.confidence in ("medium", "high")
    md = note.markdown
    assert md.startswith("# how coral reefs") and "## Sources" in md and "[S999]" not in md  # invented one removed
    cited = set(re.findall(r"\[(S\d+)\]", md.split("## Sources")[0]))
    listed = set(re.findall(r"^(S\d+)\.", md.split("## Sources")[1], re.M))
    assert cited and cited <= listed  # every citation points at a listed source
    assert note.summary.startswith("Finished the light research") and note.summary.count(".") <= 4
    assert (note.ref.path / "note.md").exists() and "plan" in ticks and any("read" in t for t in ticks)
    # claims in the library carry verbatim quotes that really are in the stored source text
    sources = {json.loads(l)["id"]: json.loads(l) for l in (note.ref.path / "sources.jsonl").read_text().splitlines()}
    for line in (note.ref.path / "claims.jsonl").read_text().splitlines():
        c = json.loads(line)
        assert " ".join(c["quote"].lower().split()) in " ".join(" ".join(sources[c["source"]]["chunks"]).lower().split())


async def test_invented_quotes_never_become_claims(tmp_path):
    async def liar(system, user):
        if "plan a web research" in system:
            return "{}"
        if "extract facts" in system:
            return json.dumps([{"claim": "Reefs are fine", "chunk": 0,
                                "quote": "Scientists unanimously agree that reefs are completely fine."}])
        return ""

    with pytest.raises(ResearchError, match="none of the sources gave a claim"):
        await engine(tmp_path, liar).run(TOPIC, "light")


async def test_without_a_model_the_note_is_assembled_from_the_sources_own_sentences(tmp_path):
    note = await engine(tmp_path, None).run(TOPIC, "light")
    assert not note.used_model and "written without a model" in note.markdown
    assert note.n_claims >= 3 and "## Key findings" in note.markdown


async def test_one_failing_engine_or_page_does_not_end_the_research(tmp_path):
    async def broken(get, query, n):
        raise RuntimeError("search engine down")

    async def flaky_fetch(url):
        if url.endswith("/3"):
            raise TimeoutError("slow")
        return await fake_fetch(url)

    note = await engine(tmp_path, Model(), {"web": fake_web, "wikipedia": broken}, flaky_fetch).run(TOPIC, "light")
    assert note.n_sources >= 5
    all_down = {"web": broken, "wikipedia": broken}
    with pytest.raises(ResearchError, match="no search engine returned anything"):
        await engine(tmp_path / "b", Model(), all_down).run(TOPIC, "light")


async def test_a_killed_run_resumes_without_repeating_finished_work(tmp_path):
    fetched = []

    async def counting_fetch(url):
        fetched.append(url)
        return await fake_fetch(url)

    model = Model()
    seen = {"n": 0}

    async def dies_on_the_third_extraction(system, user):
        if "extract facts" in system:
            seen["n"] += 1
            if seen["n"] == 3:
                raise asyncio.CancelledError  # the program was stopped mid-run
        return await model(system, user)

    r = engine(tmp_path, dies_on_the_third_extraction, fetch=counting_fetch)
    run_id = "run-1"
    with pytest.raises(asyncio.CancelledError):
        await r.run(TOPIC, "light", run_id)
    assert r.load(run_id)["stage"] == "extract" and r.unfinished()[0][0] == run_id
    fetched_before = len(fetched)
    extracted_before = model.calls["extract"]

    resumed = engine(tmp_path, model, fetch=counting_fetch)
    note = await resumed.run(TOPIC, "light", run_id)
    assert len(fetched) == fetched_before  # pages are not fetched again
    assert note.n_sources >= 5 and resumed.load(run_id)["stage"] == "done" and resumed.unfinished() == []
    assert model.calls["plan"] == 1  # the plan was not asked for again


async def test_depth_changes_the_budget(tmp_path):
    async def many(get, query, n):
        return [Hit(f"https://s{i}-{abs(hash(query)) % 997}.example/p/{i}", f"Reef paper {i}", "coral reefs warming",
                    "academic", "2023", "x") for i in range(40)]

    light = await engine(tmp_path / "l", Model(), {"web": many}).run(TOPIC, "light")
    normal = await engine(tmp_path / "n", Model(), {"web": many}).run(TOPIC, "normal")
    assert light.n_sources <= 6 and 15 <= normal.n_sources <= 18
