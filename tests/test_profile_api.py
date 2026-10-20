"""The profile over the local API, the panel's allowlist, and the wiring into the assistant's prompt."""
from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.config import Config
from minty_core.context import build_context
from minty_panel.server import ALLOWED


def client_for(tmp_path):
    ctx = build_context(tmp_path)
    return ctx, TestClient(create_app(ctx)), {"Authorization": f"Bearer {ctx.token}"}


def test_the_panel_may_reach_the_profile():
    assert "profile" in ALLOWED


def test_list_answer_skip_clear(tmp_path):
    ctx, client, auth = client_for(tmp_path)
    data = client.get("/profile", headers=auth).json()
    assert data["progress"] == {"answered": 0, "skipped": 0, "total": 500}
    assert len(data["questions"]) == 500 and data["questions"][0]["id"] == 1
    assert [c["id"] for c in data["categories"]][:2] == ["about", "home"]
    assert all(set(c) >= {"id", "title", "blurb", "count"} for c in data["categories"]) and data["categories"][0]["count"] == 35

    got = client.put("/profile/1", headers=auth, json={"text": " Sam "})
    assert got.status_code == 200 and got.json()["answer"] == "Sam" and got.json()["progress"]["answered"] == 1
    assert ctx.services["profile"].get(1) == "Sam"
    assert client.post("/profile/2/skip", headers=auth).json()["progress"]["skipped"] == 1
    assert client.get("/profile", headers=auth).json()["questions"][1]["skipped"] is True
    assert client.delete("/profile/1", headers=auth).status_code == 204
    assert ctx.services["profile"].get(1) is None


def test_bad_requests_are_refused(tmp_path):
    _, client, auth = client_for(tmp_path)
    assert client.put("/profile/501", headers=auth, json={"text": "x"}).status_code == 404
    assert client.post("/profile/0/skip", headers=auth).status_code == 404
    assert client.put("/profile/1", headers=auth, json={"text": "   "}).status_code == 422
    assert client.put("/profile/1", headers=auth, json={"text": "x" * 601}).status_code == 422
    assert client.get("/profile").status_code == 401  # no token


def test_export_is_readable_text(tmp_path):
    _, client, auth = client_for(tmp_path)
    client.put("/profile/1", headers=auth, json={"text": "Sam"})
    out = client.get("/profile/export", headers=auth)
    assert out.status_code == 200 and "Sam" in out.text and out.text.startswith("# Minty profile")


def test_the_answers_reach_the_prompt_when_related_and_the_setting_turns_it_off(tmp_path):
    ctx = build_context(tmp_path)
    diet = next(q.id for q in __import__("minty_core.profile", fromlist=["QUESTIONS"]).QUESTIONS if "Do you follow any diet" in q.text)
    ctx.services["profile"].answer(1, "Sam")
    ctx.services["profile"].answer(diet, "vegetarian, no eggs")
    prompt = ctx.agent._system_prompt("suggest a dinner that fits my diet")
    assert "Sam" in prompt and "vegetarian" in prompt and "About the user" in prompt
    assert "vegetarian" not in ctx.agent._system_prompt("what time is it")  # unrelated: only the basics go along
    data = ctx.store.config.model_dump(mode="json")
    data["profile"]["enabled"] = False
    ctx.store.update(data)
    assert "About the user" not in ctx.agent._system_prompt("suggest a dinner that fits my diet")


def test_the_profile_settings_exist_with_sane_defaults():
    cfg = Config()
    assert cfg.profile.enabled is True and cfg.profile.max_chars == 1800
