"""747 637 638: power profile, audio devices and volume, brightness, lock, sleep, night light. Exact commands only."""
import pytest

from minty_core.system.controls import Controls
from minty_core.system.runner import Result, Runner

WPCTL = """PipeWire 'pipewire-0' [1.2.7, user@arch, cookie:123]
 └─ Clients:
        33. WirePlumber                         [1.2.7, user@arch, pid:800]

Audio
 ├─ Devices:
 │      45. Built-in Audio                      [alsa]
 │
 ├─ Sinks:
 │  *   46. Built-in Audio Analog Stereo        [vol: 0.40]
 │      52. HDMI / DisplayPort                  [vol: 1.00 MUTED]
 │      60. Boat Rockerz 450                    [vol: 0.70]
 │
 ├─ Sink endpoints:
 │      70. Fake endpoint                       [vol: 1.00]
 │
 ├─ Sources:
 │  *   47. Built-in Audio Analog Stereo        [vol: 1.00]
 │
 └─ Streams:
"""


class Fake:
    def __init__(self, answers=None, have=()):
        self.answers, self.calls, self.installed = answers or {}, [], have

    def have(self, name):
        return name in self.installed

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        for key, value in self.answers.items():
            if " ".join(argv).startswith(key):
                return value
        return Result(0, "")


# ---- power profile -----------------------------------------------------------------------------------------
async def test_power_profile_reads_and_sets_only_the_three_known_profiles():
    f = Fake({"powerprofilesctl get": Result(0, "balanced\n")}, have=("powerprofilesctl",))
    c = Controls(f)
    assert "balanced" in await c.power_profile()
    assert "performance" in await c.power_profile("performance")
    assert f.calls[-1] == ["powerprofilesctl", "set", "performance"]
    assert "power-saver" in await c.power_profile("battery saver") and f.calls[-1][-1] == "power-saver"
    assert "power-saver" in await c.power_profile("saver") and f.calls[-1][-1] == "power-saver"
    with pytest.raises(ValueError):
        await c.power_profile("turbo; rm -rf /")


async def test_a_missing_power_profile_service_is_said_not_crashed():
    c = Controls(Fake({"powerprofilesctl": Result(127, "", "", missing=True)}))
    assert "not available" in await c.power_profile()


# ---- audio -------------------------------------------------------------------------------------------------
async def test_the_output_devices_are_listed_with_the_default_marked():
    c = Controls(Fake({"wpctl status": Result(0, WPCTL)}))
    sinks = await c.sinks()
    assert [(s.id, s.name, s.default) for s in sinks] == [
        (46, "Built-in Audio Analog Stereo", True), (52, "HDMI / DisplayPort", False), (60, "Boat Rockerz 450", False)]
    text = await c.audio_devices()
    assert "Built-in Audio Analog Stereo (current)" in text and "Boat Rockerz 450" in text and "Fake endpoint" not in text


async def test_switching_output_picks_the_device_by_name_and_uses_the_listed_id():
    f = Fake({"wpctl status": Result(0, WPCTL)})
    c = Controls(f)
    assert "Boat Rockerz 450" in await c.switch_output("boat")
    assert f.calls[-1] == ["wpctl", "set-default", "60"]
    assert "No output matches" in await c.switch_output("nonexistent")
    f2 = Fake({"wpctl status": Result(0, WPCTL)})
    out = await Controls(f2).switch_output("e")  # matches all three: ask which, change nothing
    assert "Which one" in out and all(call[:2] != ["wpctl", "set-default"] for call in f2.calls)
    assert "already" in await Controls(Fake({"wpctl status": Result(0, WPCTL)})).switch_output("built-in")


async def test_volume_is_read_set_nudged_and_capped():
    f = Fake({"wpctl get-volume": Result(0, "Volume: 0.40\n")})
    c = Controls(f)
    assert "40 percent" in await c.volume()
    assert "55 percent" in await c.volume(set_to=55) and f.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.55"]
    await c.volume(change=+10)
    assert f.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "10%+"]
    await c.volume(change=-10)
    assert f.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "10%-"]
    await c.volume(set_to=250)  # never above 100 percent: that is how speakers and ears get hurt
    assert f.calls[-1][-1] == "1.00"
    await c.volume(set_to=-5)
    assert f.calls[-1][-1] == "0.00"
    f.answers = {"wpctl get-volume": Result(0, "Volume: 0.40 [MUTED]\n")}
    assert "muted" in await c.volume()


async def test_mute_and_unmute():
    f = Fake()
    c = Controls(f)
    await c.mute(True)
    assert f.calls[-1] == ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]
    await c.mute(False)
    assert f.calls[-1][-1] == "0"


# ---- brightness, lock, sleep, night light --------------------------------------------------------------------
async def test_brightness_uses_brightnessctl_when_there_is_one():
    f = Fake({"brightnessctl -m": Result(0, "intel_backlight,backlight,12000,50%,24000\n")}, have=("brightnessctl",))
    c = Controls(f)
    assert "50 percent" in await c.brightness()
    assert "30 percent" in await c.brightness(set_to=30) and f.calls[-1] == ["brightnessctl", "set", "30%"]
    await c.brightness(set_to=500)
    assert f.calls[-1] == ["brightnessctl", "set", "100%"]
    await c.brightness(set_to=0)  # never fully dark: you could not see to undo it
    assert f.calls[-1] == ["brightnessctl", "set", "5%"]


XRANDR = "Screen 0: minimum 8 x 8\nHDMI-1 connected primary 1920x1080+0+0 (normal) 527mm x 296mm\nDP-1 disconnected\n"


async def test_without_brightnessctl_it_dims_in_software_and_says_so():
    f = Fake({"xrandr --query": Result(0, XRANDR)})
    c = Controls(f)
    out = await c.brightness(set_to=60)
    assert f.calls[-1] == ["xrandr", "--output", "HDMI-1", "--brightness", "0.60"]
    assert "software" in out and "not the real backlight" in out and "brightnessctl" in out
    assert "needs brightnessctl" in await Controls(Fake({"xrandr --query": Result(0, XRANDR)})).brightness()


async def test_lock_and_sleep_use_the_session_manager():
    f = Fake()
    c = Controls(f)
    await c.lock()
    assert f.calls[-1] == ["loginctl", "lock-session"]
    await c.sleep()
    assert f.calls[-1] == ["systemctl", "suspend"]


async def test_night_light_only_touches_its_own_two_settings():
    keys = Result(0, "night-light-enabled\nnight-light-temperature\nnight-light-schedule-mode\n")
    f = Fake({"gsettings list-keys": keys, "gsettings get": Result(0, "true\n")})
    c = Controls(f)
    assert "on" in await c.night_light(True)
    assert f.calls[-1] == ["gsettings", "set", "org.cinnamon.settings-daemon.plugins.color", "night-light-enabled", "true"]
    await c.night_light(True, kelvin=3500)
    assert f.calls[-1][-2:] == ["night-light-temperature", "3500"]
    await c.night_light(True, kelvin=100)  # clamped into a sensible range
    assert f.calls[-1][-1] == "2700"
    missing = Controls(Fake({"gsettings list-keys": Result(1, "")}))
    assert "not available" in await missing.night_light(True)


async def test_the_runner_refuses_anything_outside_these_exact_shapes():
    r = Runner(which=lambda n: None)
    bad = [["powerprofilesctl", "set", "turbo"], ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "9.0"],
           ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.5"], ["wpctl", "set-default", "abc"],
           ["wpctl", "set-volume", "46; reboot", "0.5"], ["xrandr", "--output", "HDMI-1", "--mode", "800x600"],
           ["xrandr", "--output", "HDMI-1", "--brightness", "5"], ["gsettings", "set", "org.gnome.desktop.lockdown",
                                                               "disable-user-switching", "true"],
           ["brightnessctl", "set", "-5%"], ["brightnessctl", "set", "100%", "--device=x"]]
    for argv in bad:
        with pytest.raises(ValueError):
            await r.run(argv)
    good = [["powerprofilesctl", "set", "power-saver"], ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.55"],
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "10%+"], ["wpctl", "set-default", "60"],
            ["xrandr", "--output", "HDMI-1", "--brightness", "0.60"], ["brightnessctl", "set", "30%"],
            ["gsettings", "set", "org.cinnamon.settings-daemon.plugins.color", "night-light-enabled", "true"]]
    for argv in good:
        assert (await r.run(argv)).missing
