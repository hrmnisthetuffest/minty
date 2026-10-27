import pytest

from tests.test_agent_toolkits import NAMES, Host, Router, make, say
from minty_core.llm.types import TextDelta
from minty_core.skills import BUILTIN_SKILLS_DIR, SkillError, SkillIndex, load_skill, parse_frontmatter


def write(root, name, front, body="Do the thing.\n"):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n{body}")
    return folder


def test_frontmatter_subset():
    meta, body = parse_frontmatter(
        '---\nname: demo\ndescription: >\n  one line\n  second line\nallowed-tools: a b\nmetadata:\n  keywords: x y\n'
        '  author: "me"\nlicense: MIT\n---\n# Body\ntext\n')
    assert meta["description"] == "one line second line" and meta["metadata"] == {"keywords": "x y", "author": "me"}
    assert meta["allowed-tools"] == "a b" and body == "# Body\ntext"
    for bad in ("no frontmatter", "---\nname: x\n", "---\n  indented: first\n---\nbody"):
        with pytest.raises(SkillError):
            parse_frontmatter(bad)


def test_spec_limits_are_enforced(tmp_path):
    ok = write(tmp_path, "good-skill", "name: good-skill\ndescription: Does a thing when asked.")
    assert load_skill(ok).name == "good-skill"
    cases = {
        "Bad_Name": "name: Bad_Name\ndescription: x",
        "other-folder": "name: not-the-folder\ndescription: x",
        "no-desc": "name: no-desc\ndescription:",
        "long-desc": "name: long-desc\ndescription: " + "x" * 1025,
        "a" * 65: "name: " + "a" * 65 + "\ndescription: x",
    }
    for folder, front in cases.items():
        with pytest.raises(SkillError):
            load_skill(write(tmp_path / "bad", folder, front))
    with pytest.raises(SkillError):
        load_skill(write(tmp_path / "bad", "long-body", "name: long-body\ndescription: x", "line\n" * 501))


def test_the_starter_skills_are_valid_and_match_their_requests():
    index = SkillIndex([BUILTIN_SKILLS_DIR])
    assert set(index.skills()) == {"shop-search", "youtube-find-and-play", "compare-products", "fill-form-from-profile",
                                   "health-intake", "explain-three-levels", "verified-tutor", "mood-music",
                                   "handle-my-email", "delegate-coding-job", "hand-over-a-goal"}
    assert index.problems == []
    for request, name in [("find me the cheapest desk lamp", "shop-search"),
                          ("play the avengers trailer on youtube", "youtube-find-and-play"),
                          ("compare the desk lamp and the floor lamp", "compare-products"),
                          ("fill in my address on this form", "fill-form-from-profile"),
                          ("set up my health tracking for workouts and sleep", "health-intake"),
                          ("explain photosynthesis so I understand it simply", "explain-three-levels"),
                          ("help me solve this equation step by step", "verified-tutor"),
                          ("play something calm to help me study", "mood-music"),
                          ("handle my emails that need attention today", "handle-my-email"),
                          ("have claude code fix the failing tests in my project", "delegate-coding-job"),
                          ("work on this in the background and tell me when it is done", "hand-over-a-goal")]:
        assert index.match(request).name == name
    for request in ("what time is it", "play some music", "turn off the light", "tell me a joke"):
        assert index.match(request) is None


def test_the_workflow_skills_only_name_tools_that_exist_and_say_where_the_user_decides(tmp_path):
    from tests.plugin_helpers import make_host
    host = make_host(tmp_path, plugins={"outbox": {"enabled": True}, "mail": {"enabled": True}})
    index = SkillIndex([BUILTIN_SKILLS_DIR])
    for name in ("handle-my-email", "delegate-coding-job", "hand-over-a-goal"):
        skill = index.skills()[name]
        assert skill.allowed_tools and all(host.get_tool(t) is not None for t in skill.allowed_tools), (name, skill.allowed_tools)
    email = index.skills()["handle-my-email"].body
    assert "outbox_draft" in email and "never" in email.lower() and "decisions" in email.lower()
    assert "tests" in index.skills()["delegate-coding-job"].body.lower()


def test_user_skill_wins_and_new_skills_are_picked_up_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('minty_core.skills.RESCAN_S', 0)  # the 2 s rescan throttle is not under test
    user = tmp_path / "user"
    index = SkillIndex([BUILTIN_SKILLS_DIR, user])
    assert "pet-feeding" not in index.skills()
    write(user, "pet-feeding", "name: pet-feeding\ndescription: Feed the cat.\nmetadata:\n  keywords: feed cat dinner",
          "Open the cat feeder page and press Feed.\n")
    assert index.match("feed the cat its dinner").name == "pet-feeding"
    write(user, "shop-search", "name: shop-search\ndescription: My own way.\nmetadata:\n  keywords: shop cheapest",
          "My steps.\n")
    assert index.skills()["shop-search"].body == "My steps."
    write(user, "broken", "name: wrong\ndescription: x")
    index.skills()
    assert any("broken" in p for p in index.problems)


def test_a_broken_skill_never_breaks_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr('minty_core.skills.RESCAN_S', 0)
    write(tmp_path, "broken", "name: broken")
    write(tmp_path, "fine", "name: fine\ndescription: Works.")
    index = SkillIndex([tmp_path])
    assert list(index.skills()) == ["fine"] and len(index.problems) == 1


async def test_a_matched_skill_is_added_to_the_end_of_that_turns_prompt_only(tmp_path):
    user = tmp_path / "skills"
    write(user, "pet-feeding", "name: pet-feeding\ndescription: Feed the cat.\nallowed-tools: smart_home_set_device\n"
          "metadata:\n  keywords: feed cat dinner", "Press the big green button.\n")
    agent, router, _ = make(tmp_path / "cfg", [[TextDelta("Fed.")], [TextDelta("Noon.")]])
    agent.skills = SkillIndex([user])
    agent.turn_extras.append(agent.skills.section)
    seen = []
    original = agent._system_prompt
    agent._system_prompt = lambda request="": (seen.append(original(request)), seen[-1])[1]
    await say(agent, "feed the cat its dinner")
    await say(agent, "what time is it")
    assert "## Skill: pet-feeding" in seen[0] and seen[0].endswith("Press the big green button.")
    assert "## Skill" not in seen[1]
    assert "smart_home_set_device" in router.offered[0]  # the tool group the skill names is on for this request
    assert "smart_home_set_device" not in router.offered[1]


def test_a_new_skill_is_noticed_within_the_rescan_window_not_on_every_call(tmp_path, monkeypatch):
    import minty_core.skills as skills_mod
    clock = [1000.0]
    monkeypatch.setattr(skills_mod.time, "monotonic", lambda: clock[0])
    index = SkillIndex([tmp_path])
    assert index.skills() == {}
    write(tmp_path, "late-skill", "name: late-skill\ndescription: Arrives late.")
    clock[0] += 0.5
    assert index.skills() == {}  # not looked at again yet: a directory scan per request is waste
    clock[0] += 2.0
    assert "late-skill" in index.skills()
