import pytest

from minty_core.instant.system import Machine
from tests.instant_fakes import engine, machine, say


@pytest.fixture
def e(tmp_path):
    return engine(tmp_path)


@pytest.mark.parametrize("said, reply", [
    ("how much battery do I have", "The battery is at 87 percent and running on battery."),
    ("how much ram do I have", "5 GB of 16 GB is in use, so 11 GB is free."),
    ("how much disk space is left", "140 GB free of 500 GB, so 72 percent is used."),
    ("cpu usage", "The processor has been about 25 percent busy over the last minute."),
    ("how many cpu cores do I have", "You have Test CPU 3000, with 4 threads."),
    ("how hot is the cpu", "The hottest sensor reads 52 degrees Celsius."),
    ("how long has the computer been on", "The computer has been on for 3 hours 12 minutes."),
    ("what is my ip address", "Your local address is 192.168.1.12."),
    ("what is my computer name", "This computer is called laptop."), ("who am I logged in as", "You're logged in as alex."),
    ("what operating system am I using", "You're running Arch Linux, kernel 7.2.11-arch1-1."),
    ("what kernel version am I running", "The kernel is 7.2.11-arch1-1."),
])
def test_this_computer(e, said, reply):
    assert say(e, said) == reply


def test_a_desktop_has_no_battery_and_no_sensor(tmp_path):
    m = machine(tmp_path)
    (tmp_path / "sys/class/power_supply/BAT0/capacity").unlink()
    for zone in (tmp_path / "sys/class/thermal").glob("thermal_zone*"):
        (zone / "temp").unlink()
    e = engine(system=m)
    assert say(e, "how much battery do I have") == "This computer has no battery."
    assert say(e, "how hot is the cpu") == "I can't read a temperature sensor on this computer."


def test_a_charging_laptop(tmp_path):
    m = machine(tmp_path)
    (tmp_path / "sys/class/power_supply/BAT0/status").write_text("Charging\n")
    assert say(engine(system=m), "battery level") == "The battery is at 87 percent and charging."


def test_missing_files_mean_the_model_answers_instead_of_a_wrong_number(tmp_path):
    e = engine(tmp_path)
    for name in ("proc/meminfo", "proc/loadavg", "proc/uptime", "proc/cpuinfo"):
        (tmp_path / name).unlink()
    assert say(e, "how much ram do I have") is None and say(e, "cpu usage") is None and say(e, "uptime") is None
    assert say(e, "what cpu do I have") is None


def test_the_machine_reads_a_tree_of_files(tmp_path):
    m = machine(tmp_path)
    assert m.memory_mb() == (5120, 16384) and m.threads() == 4 and m.load_percent() == 25 and m.uptime_s() == pytest.approx(11520.5)
    assert m.temperature_c() == 52.0 and m.battery() == (87, "discharging") and m.os_name() == "Arch Linux"
    assert Machine(root=tmp_path / "nowhere").battery() is None and Machine(root=tmp_path / "nowhere").memory_mb() is None


def test_the_real_machine_answers_without_crashing():
    from minty_core.instant import Env, Instant

    e = Instant(Env())
    for said in ("how much disk space is left", "what is my computer name", "what operating system am I using", "uptime"):
        e.answer(said)  # whatever this computer is, no exception and no wrong type


def test_what_is_playing(e):
    assert say(e, "what song is this") == "“Astronaut in the Ocean” by Masked Wolf."
    assert say(e, "who sings this") == "“Astronaut in the Ocean” by Masked Wolf."


def test_a_paused_player_says_so():
    e = engine(now_playing=lambda: {"title": "Song", "artist": "Band", "status": "Paused"})
    assert say(e, "what's playing") == "Paused: “Song” by Band."


def test_nothing_playing_leaves_it_to_song_recognition():
    assert say(engine(now_playing=lambda: None), "what song is this") is None
    assert say(engine(now_playing=lambda: {}), "what song is this") is None
    assert say(engine(now_playing=None), "what song is this") is None


def test_the_local_address_probe_sends_nothing(monkeypatch):
    import socket

    from minty_core.instant.system import _local_ip

    sent = []

    class Fake:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def connect(self, addr): sent.append(("connect", addr))
        def getsockname(self): return ("10.0.0.5", 5555)
        def send(self, data): sent.append(("send", data))
        def sendto(self, *a): sent.append(("sendto", a))

    monkeypatch.setattr(socket, "socket", lambda *a, **k: Fake())
    assert _local_ip() == "10.0.0.5" and [kind for kind, _ in sent] == ["connect"]
