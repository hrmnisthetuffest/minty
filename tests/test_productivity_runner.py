"""The minute loop: alarms ring with a rising chime, then a short brief; the plan, wind-down and summary are each
pushed once a day inside a short window, and only if their own switch is on."""
from datetime import datetime

import pytest

from minty_core.config import Config
from minty_core.productivity.kit import ProductivityProvider
from minty_core.productivity.runner import DayRunner


class Rig:
    def __init__(self, tmp_path, **switches):
        self.cfg = Config()
        self.cfg.productivity.enabled = True
        for k, v in switches.items():
            setattr(self.cfg.productivity, k, v)
        self.provider = ProductivityProvider(lambda: self.cfg, tmp_path, {})
        self.said, self.urgent, self.chimes = [], [], []
        self.now = datetime(2026, 10, 19, 6, 30, 5)  # a Monday
        self.on_chime = lambda n: None

        async def chime(volume):
            self.chimes.append(volume)
            self.on_chime(len(self.chimes))

        async def nap(_s):
            return None

        self.runner = DayRunner(self.provider, self.said.append, self.urgent.append, chime,
                                now=lambda: self.now, sleep=nap)


async def test_an_alarm_rings_with_a_rising_chime_then_the_brief(tmp_path):
    rig = Rig(tmp_path)
    rig.provider.life().alarms.add("06:30", "weekdays")
    await rig.runner.tick()
    first_round = rig.chimes[:5]
    assert first_round == sorted(first_round) and first_round[0] < first_round[-1] == 1.0
    assert len(rig.urgent) == 1 and rig.urgent[0].startswith("Good morning. It's 6:30.")
    assert rig.provider.life().alarms.ringing == []  # it gives up after a minute or so, never rings for ever
    await rig.runner.tick()
    assert len(rig.urgent) == 1  # once a day


async def test_stop_in_the_middle_of_the_chimes_ends_the_ringing_and_skips_the_brief(tmp_path):
    rig = Rig(tmp_path)
    rig.provider.life().alarms.add("06:30")
    rig.on_chime = lambda n: rig.provider.life().alarms.stop() if n == 2 else None
    await rig.runner.tick()
    assert len(rig.chimes) == 2 and rig.urgent == []


async def test_an_alarm_without_the_brief_only_chimes(tmp_path):
    rig = Rig(tmp_path)
    rig.provider.life().alarms.add("06:30", brief=False)
    await rig.runner.tick()
    assert rig.chimes and rig.urgent == []


@pytest.mark.parametrize("switch,at,needle", [("plan_push", "07:30", "Nothing is planned"),
                                              ("wind_down", "21:30", "Time to wind down."),
                                              ("summary_push", "20:30", "Today")])
async def test_each_push_comes_once_a_day_inside_its_window(tmp_path, switch, at, needle):
    rig = Rig(tmp_path, **{switch: True})
    h, m = (int(x) for x in at.split(":"))
    rig.now = rig.now.replace(hour=h, minute=m + 1, second=0)
    await rig.runner.tick()
    await rig.runner.tick()
    assert len(rig.said) == 1 and needle in rig.said[0]
    rig.now = rig.now.replace(day=20)
    await rig.runner.tick()
    assert len(rig.said) == 2  # the next day's


async def test_a_push_time_we_were_asleep_for_is_not_sent_late(tmp_path):
    rig = Rig(tmp_path, plan_push=True)
    rig.now = rig.now.replace(hour=11, minute=0)
    await rig.runner.tick()
    assert rig.said == []


async def test_a_push_without_its_switch_and_everything_while_off_stay_quiet(tmp_path):
    rig = Rig(tmp_path)
    rig.now = rig.now.replace(hour=7, minute=31)
    await rig.runner.tick()
    assert rig.said == []
    off = Rig(tmp_path / "off", enabled=False)
    off.now = off.now.replace(hour=7, minute=31)
    off.cfg.productivity.plan_push = True
    await off.runner.tick()
    assert off.said == [] and not (tmp_path / "off" / "life.db").exists()


async def test_the_pushes_are_remembered_across_a_restart(tmp_path):
    rig = Rig(tmp_path, plan_push=True)
    rig.now = rig.now.replace(hour=7, minute=31)
    await rig.runner.tick()
    again = Rig(tmp_path, plan_push=True)
    again.now = rig.now
    await again.runner.tick()
    assert again.said == []
