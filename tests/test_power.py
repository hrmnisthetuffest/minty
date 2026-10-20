"""Suspend/resume, battery and heat from sysfs (W1B-06)."""
from minty_core.power import PowerMonitor, read_battery, read_temperature


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_sysfs_readers(tmp_path):
    write(tmp_path, "ps/AC/type", "Mains")
    write(tmp_path, "ps/AC/online", "0")
    write(tmp_path, "ps/BAT0/type", "Battery")
    write(tmp_path, "ps/BAT0/capacity", "42")
    assert read_battery(str(tmp_path / "ps")) == (False, 42)
    write(tmp_path, "th/thermal_zone0/temp", "61000")
    write(tmp_path, "th/thermal_zone1/temp", "73500")
    assert read_temperature(str(tmp_path / "th")) == 73.5
    assert read_battery(str(tmp_path / "none")) == (None, None) and read_temperature(str(tmp_path / "none")) is None


def test_a_gap_between_the_wall_and_monotonic_clocks_is_a_suspend():
    t = {"wall": 1000.0, "mono": 50.0}
    m = PowerMonitor(wall=lambda: t["wall"], mono=lambda: t["mono"], battery=lambda: (True, 90),
                     temperature=lambda: None)
    t["wall"] += 30
    t["mono"] += 30  # an ordinary half minute
    assert m.check_resume() is None
    t["wall"] += 3600
    t["mono"] += 2  # an hour of wall time, two seconds of monotonic: the laptop slept
    assert round(m.check_resume()) == 3598
    assert m.check_resume() is None  # once
    assert m.clock_went_back(1000.0, 800.0) and not m.clock_went_back(1000.0, 990.0)


def test_heat_has_hysteresis_and_low_battery_needs_no_charger():
    temps = iter([70.0, 86.0, 80.0, 74.0])
    state = {"bat": (False, 40)}
    m = PowerMonitor(battery=lambda: state["bat"], temperature=lambda: next(temps))
    assert not m.sample().hot
    assert m.sample().hot and not m.state.heavy_work_ok()  # 86
    assert m.sample().hot  # 80: still above the cool-down point
    assert not m.sample().hot and m.state.heavy_work_ok()  # 74
    state["bat"] = (False, 10)
    m._temperature = lambda: 50.0
    assert m.sample().low_battery and not m.state.heavy_work_ok()
    state["bat"] = (True, 10)
    assert not m.sample().low_battery  # on the charger it is fine


def test_it_says_how_it_is_doing():
    m = PowerMonitor(battery=lambda: (False, 37), temperature=lambda: 88.0)
    m.sample()
    assert m.words() == "The battery is at 37 percent and not charging, it is running at 88 degrees and hot."
    d = PowerMonitor(battery=lambda: (None, None), temperature=lambda: None)
    d.sample()
    assert d.words() == "This computer has no battery."
