"""Standing approvals (1065): a limited, expiring, user-made yes for one small action. Strictly narrower than a rule."""
import json
import os
import stat
from types import SimpleNamespace

import pytest

from minty_core.approvals import ApprovalStore
from minty_core.butler.standing import StandingApprovals, StandingError
from minty_core.config import ButlerConfig, SafetyConfig
from minty_core.policy import R2, R3, Guard

DAY = 86400


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def standing(tmp_path, clock):
    return StandingApprovals(tmp_path / "standing.json", clock=clock)


class Tool:
    name = "scenes_launch"
    risk = R2
    risky = False
    confirm_when = None
    egress = None
    describe = None


class SendTool(Tool):
    name = "telegram_send"
    risk = R3


class WebTool(Tool):
    name = "web_post"
    risk = R2
    egress = staticmethod(lambda args: args.get("url"))


def guard_for(tmp_path, standing, *, on=True, lockdown=None):
    store = SimpleNamespace(config=SimpleNamespace(safety=SafetyConfig(), butler=ButlerConfig(standing_approvals=on)))
    g = Guard(store, approvals=ApprovalStore(tmp_path / "approvals.sqlite"))
    g.standing = standing
    g.lockdown = lockdown
    return g


async def no_one_answers(question):  # a call that reaches the user fails the test
    raise AssertionError(f"asked: {question}")


async def says_no(question):
    return False


# ---- the store --------------------------------------------------------------------------------------------
def test_a_grant_needs_an_exact_tool_name_a_cap_and_an_expiry(standing):
    for bad in ("scenes_*", "", "scenes launch", "a?b"):
        with pytest.raises(StandingError):
            standing.grant(bad)
    with pytest.raises(StandingError):
        standing.grant("scenes_launch", max_per_day=0)
    with pytest.raises(StandingError):
        standing.grant("scenes_launch", days=0)
    with pytest.raises(StandingError):
        standing.grant("scenes_launch", days=400)
    g = standing.grant("scenes_launch", args={"name": "study"}, max_per_day=3, days=7, note="study scene")
    assert g.id == 1 and g.expires == pytest.approx(standing._clock() + 7 * DAY)


def test_the_file_is_owner_only_and_survives_a_restart(tmp_path, clock):
    path = tmp_path / "standing.json"
    StandingApprovals(path, clock=clock).grant("scenes_launch")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [g.tool for g in StandingApprovals(path, clock=clock).active()] == ["scenes_launch"]


def test_matching_is_exact_on_tool_and_arguments(standing):
    standing.grant("scenes_launch", args={"name": "study"})
    assert standing.match("scenes_launch", {"name": "study"}, R2) is not None
    assert standing.match("scenes_launch", {"name": "STUDY"}, R2) is not None  # case does not matter
    assert standing.match("scenes_launch", {"name": "games"}, R2) is None
    assert standing.match("scenes_launch", {}, R2) is None
    assert standing.match("scenes_open", {"name": "study"}, R2) is None


def test_class_three_is_never_covered(standing):
    standing.grant("telegram_send")
    assert standing.match("telegram_send", {}, R3) is None


def test_the_daily_cap_and_the_expiry_close_a_grant(standing, clock):
    g = standing.grant("scenes_launch", max_per_day=2, days=2)
    for _ in range(2):
        standing.use(standing.match("scenes_launch", {}, R2))
    assert standing.match("scenes_launch", {}, R2) is None  # cap reached today
    clock.t += DAY + 60
    assert standing.match("scenes_launch", {}, R2) is not None  # a new day
    clock.t += 2 * DAY
    assert standing.match("scenes_launch", {}, R2) is None  # expired
    assert standing.active() == []


def test_revoking_ends_a_grant_at_once(standing):
    g = standing.grant("scenes_launch")
    assert standing.revoke(g.id) is True and standing.revoke(g.id) is False
    assert standing.match("scenes_launch", {}, R2) is None


def test_the_weekly_review_lists_use_and_unused_grants(standing, clock):
    used = standing.grant("scenes_launch", note="study scene")
    standing.grant("vault_inbox_decide", note="filing")
    standing.use(standing.match("scenes_launch", {}, R2))
    text = standing.review()
    assert "study scene" in text and "1 time" in text and "filing" in text and "not used" in text


# ---- the gate ---------------------------------------------------------------------------------------------
async def test_a_covered_call_runs_without_asking_and_says_why(tmp_path, standing):
    standing.grant("scenes_launch", args={"name": "study"})
    d = await guard_for(tmp_path, standing).authorize(Tool(), "scenes_launch", {"name": "study"}, no_one_answers)
    assert d.allowed and not d.asked and d.reason.startswith("standing approval #1")


async def test_without_the_switch_it_still_asks(tmp_path, standing):
    standing.grant("scenes_launch")
    d = await guard_for(tmp_path, standing, on=False).authorize(Tool(), "scenes_launch", {}, says_no)
    assert not d.allowed and d.asked


async def test_a_call_that_is_not_covered_still_asks(tmp_path, standing):
    standing.grant("scenes_launch", args={"name": "study"})
    d = await guard_for(tmp_path, standing).authorize(Tool(), "scenes_launch", {"name": "games"}, says_no)
    assert not d.allowed and d.asked


async def test_a_tainted_call_still_asks_even_when_covered(tmp_path, standing):
    standing.grant("scenes_launch")
    d = await guard_for(tmp_path, standing).authorize(Tool(), "scenes_launch", {}, says_no, extra_risk=1)
    assert not d.allowed and d.asked


async def test_a_new_destination_is_class_three_and_still_asks(tmp_path, standing):
    standing.grant("web_post")
    g = guard_for(tmp_path, standing)
    d = await g.authorize(WebTool(), "web_post", {"url": "https://evil.example/x"}, says_no, known=set())
    assert not d.allowed and d.asked and d.risk == R3


async def test_lockdown_still_blocks(tmp_path, standing):
    standing.grant("scenes_launch")
    lock = SimpleNamespace(active=True)
    d = await guard_for(tmp_path, standing, lockdown=lock).authorize(Tool(), "scenes_launch", {}, no_one_answers)
    assert not d.allowed and not d.asked


async def test_each_covered_call_is_counted_and_the_cap_brings_the_question_back(tmp_path, standing):
    standing.grant("scenes_launch", max_per_day=2)
    g = guard_for(tmp_path, standing)
    for _ in range(2):
        assert (await g.authorize(Tool(), "scenes_launch", {}, no_one_answers)).allowed
    d = await g.authorize(Tool(), "scenes_launch", {}, says_no)
    assert d.asked and not d.allowed


# ---- only you can make one --------------------------------------------------------------------------------
def test_no_model_callable_tool_can_create_or_widen_a_standing_approval():
    from tests.plugin_helpers import make_host
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        host = make_host(pathlib.Path(d))
        names = [t for t in host._plugins_tool_names()] if hasattr(host, "_plugins_tool_names") else \
            [n for p in host._plugins.values() for n in p.tools]
        assert not [n for n in names if "standing" in n or "grant" in n]


def test_the_terminal_command_makes_lists_and_revokes_grants(tmp_path, capsys):
    from minty_core.butler.standing import run_cli

    path = tmp_path / "standing.json"
    assert run_cli(["grant", "scenes_launch", "--arg", "name=study", "--per-day", "3", "--days", "7",
                    "--note", "study scene"], path) == 0
    out = capsys.readouterr().out
    assert "#1" in out and "scenes_launch" in out and "3 a day" in out
    assert run_cli(["list"], path) == 0 and "study scene" in capsys.readouterr().out
    assert run_cli(["revoke", "1"], path) == 0 and "Revoked" in capsys.readouterr().out
    assert run_cli(["grant", "scenes_*"], path) == 1 and "exact" in capsys.readouterr().out


def test_the_sunday_review_is_said_once_and_only_when_the_switch_is_on(tmp_path):
    from datetime import datetime

    from minty_core.butler.kit import ButlerProvider
    from minty_core.config import Config

    cfg = Config()
    sunday = datetime(2026, 10, 18, 19, 0).timestamp()
    clock = type("C", (), {"t": sunday, "__call__": lambda self: self.t})()
    standing = StandingApprovals(tmp_path / "s.json", clock=clock)
    standing.grant("scenes_launch", note="study scene")
    provider = ButlerProvider(config=lambda: cfg, directory=tmp_path, clock=clock)
    said: list[str] = []
    assert provider.weekly_review(standing, said.append) is False  # the switch is off
    cfg.butler.standing_approvals = True
    assert provider.weekly_review(standing, said.append) is True and "study scene" in said[0]
    assert provider.weekly_review(standing, said.append) is False  # once
    clock.t = datetime(2026, 10, 19, 19, 0).timestamp()  # Monday
    assert provider.weekly_review(standing, said.append) is False


async def test_telegram_can_list_and_revoke_but_not_make_one(tmp_path, standing):
    from minty_core.telegram import commands as tg

    gw = SimpleNamespace(handlers={}, machine="laptop")

    async def journal(name, args):
        return "ok"

    tg.install(gw, host_call=journal, standing=standing)
    g = standing.grant("scenes_launch", note="study scene")
    listed = await gw.handlers["/standing"](1, "")
    assert "study scene" in listed and "terminal" in listed
    again = await gw.handlers["/standing"](1, "grant scenes_launch")  # not a command: it is still just the list
    assert again.count("study scene") == 1
    assert len(standing.active()) == 1
    assert await gw.handlers["/standing"](1, f"revoke {g.id}") == "Revoked."
