"""635: battery, heat and CPU-hog alerts that don't nag."""
from minty_core.system.alerts import Alerts


class Clock:
    t = 0.0


def make(**kw):
    clock = Clock()
    return Alerts(clock=lambda: clock.t, **kw), clock


def feed(a, battery=80, on_ac=True, temp=50.0, hogs=()):
    return a.feed(battery=battery, on_ac=on_ac, temp=temp, hogs=list(hogs))


def test_low_battery_speaks_once_and_waits_for_a_real_recovery():
    a, clock = make()
    assert feed(a, battery=19, on_ac=False) == ["The battery is at 19 percent and not charging."]
    for pct in (18, 19, 18, 20):  # hovering around the line
        clock.t += 60
        assert feed(a, battery=pct, on_ac=False) == []
    clock.t += 7200
    assert feed(a, battery=21, on_ac=False) == []  # still not recovered: the rate limit alone does not repeat it
    feed(a, battery=60, on_ac=True)  # charged up
    clock.t += 7200
    assert len(feed(a, battery=19, on_ac=False)) == 1


def test_critical_battery_is_its_own_stronger_message():
    a, clock = make()
    feed(a, battery=19, on_ac=False)
    clock.t += 60
    out = feed(a, battery=9, on_ac=False)
    assert len(out) == 1 and "very low" in out[0]


def test_charging_never_alerts_about_battery():
    a, _ = make()
    assert feed(a, battery=5, on_ac=True) == []
    assert feed(a, battery=None, on_ac=None) == []


def test_heat_uses_hysteresis():
    a, clock = make()
    assert "hot" in feed(a, temp=88.0)[0]
    for t in (86.0, 80.0, 78.0):  # still above the cool-down line
        clock.t += 60
        assert feed(a, temp=t) == []
    feed(a, temp=60.0)  # cooled
    clock.t += 7200
    assert len(feed(a, temp=90.0)) == 1


def test_a_cpu_hog_needs_to_stay_busy_and_is_named_once_per_gap():
    a, clock = make(hog_samples=3)
    hog = [("ffmpeg", 97.0)]
    assert feed(a, hogs=hog) == [] and feed(a, hogs=hog) == []
    out = feed(a, hogs=hog)
    assert len(out) == 1 and "ffmpeg" in out[0] and "97" in out[0]
    clock.t += 60
    assert feed(a, hogs=hog) == []  # rate limited
    clock.t += 3600
    assert len(feed(a, hogs=hog)) == 1
    spike, _ = make(hog_samples=3)
    feed(spike, hogs=hog), feed(spike, hogs=hog)
    assert feed(spike, hogs=[]) == []  # it calmed down before the third look
    assert feed(spike, hogs=hog) == [] and feed(spike, hogs=hog) == []


def test_ignored_programs_are_never_named():
    a, _ = make(hog_samples=1, ignore=("minty_core", "python"))
    assert feed(a, hogs=[("python", 99.0)]) == []
