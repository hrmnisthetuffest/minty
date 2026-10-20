"""Power profile (747), audio devices and volume (637), brightness, lock, sleep and night light (638). Each is one exact
command from the runner's rules; a device is chosen only from what the system itself listed, and the volume never goes above
100 percent. Missing tools are said, not crashed on."""
from __future__ import annotations

import re
from dataclasses import dataclass

PROFILES = {"performance": "performance", "fast": "performance", "balanced": "balanced", "normal": "balanced",
            "power-saver": "power-saver", "power saver": "power-saver", "saver": "power-saver", "battery saver": "power-saver",
            "battery": "power-saver", "save power": "power-saver", "quiet": "power-saver"}
SINK = "@DEFAULT_AUDIO_SINK@"
NIGHT_SCHEMA = "org.cinnamon.settings-daemon.plugins.color"
_DEVICE = re.compile(r"^[\s│├└─]*(\*)?\s*(\d+)\.\s+(.+?)\s+\[vol: ([\d.]+)(\s+MUTED)?\]\s*$")


@dataclass
class Sink:
    id: int
    name: str
    default: bool
    volume: float
    muted: bool


class Controls:
    def __init__(self, runner):
        self.r = runner

    # ---- power profile --------------------------------------------------------------------------------
    async def power_profile(self, name: str | None = None) -> str:
        if name is None:
            res = await self.r.run(["powerprofilesctl", "get"])
            return "powerprofilesctl is not available on this computer." if res.missing else \
                f"The power profile is {res.out.strip()}."
        profile = PROFILES.get(" ".join(str(name).lower().split()))
        if profile is None:
            raise ValueError("The profiles are performance, balanced and power saver.")
        res = await self.r.run(["powerprofilesctl", "set", profile])
        if res.missing:
            return "powerprofilesctl is not available on this computer."
        return f"Power profile set to {profile}." if res.ok else "The power profile could not be changed."

    # ---- audio ----------------------------------------------------------------------------------------
    async def _list(self, heading: str) -> list[Sink]:
        res = await self.r.run(["wpctl", "status"])
        if res.missing:
            return []
        inside, found = False, []
        for line in res.out.splitlines():
            if re.search(r"[├└]─\s+\w", line):
                inside = line.split("─", 1)[1].strip() == heading
                continue
            if inside:
                m = _DEVICE.match(line)
                if m:
                    found.append(Sink(int(m.group(2)), m.group(3).strip(), bool(m.group(1)), float(m.group(4)),
                                      bool(m.group(5))))
        return found

    async def sinks(self) -> list[Sink]:
        return await self._list("Sinks:")

    async def audio_devices(self) -> str:
        sinks = await self.sinks()
        if not sinks:
            return "I cannot list the sound outputs (wpctl is not available or shows none)."
        return "Sound outputs: " + ", ".join(s.name + (" (current)" if s.default else "") for s in sinks) + "."

    async def switch_output(self, query: str) -> str:
        sinks = await self.sinks()
        if not sinks:
            return "I cannot list the sound outputs (wpctl is not available or shows none)."
        q = " ".join(str(query).lower().split())
        hits = [s for s in sinks if q and q in s.name.lower()]
        if not hits:
            return f"No output matches {query!r}. I can see: " + ", ".join(s.name for s in sinks) + "."
        if len(hits) > 1:
            return "Which one: " + ", ".join(s.name for s in hits) + "?"
        chosen = hits[0]
        if chosen.default:
            return f"{chosen.name} is already the current output."
        res = await self.r.run(["wpctl", "set-default", str(chosen.id)])
        return f"Sound now plays on {chosen.name}." if res.ok else "I could not switch the output."

    async def volume(self, set_to: int | None = None, change: int | None = None) -> str:
        if set_to is not None:
            level = max(0, min(100, int(set_to))) / 100
            res = await self.r.run(["wpctl", "set-volume", SINK, f"{level:.2f}"])
        elif change:
            step = max(1, min(30, abs(int(change))))
            res = await self.r.run(["wpctl", "set-volume", SINK, f"{step}%{'+' if change > 0 else '-'}"])
        else:
            res = await self.r.run(["wpctl", "get-volume", SINK])
            m = re.search(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?", res.out)
            if res.missing or not m:
                return "I cannot read the volume (wpctl is not available)."
            return f"The volume is {round(float(m.group(1)) * 100)} percent" + (" and muted." if m.group(2) else ".")
        if res.missing:
            return "I cannot change the volume (wpctl is not available)."
        if set_to is not None:
            return f"The volume is now {round(level * 100)} percent."
        return f"Volume {'up' if change > 0 else 'down'} {step} percent."

    async def mute(self, on: bool) -> str:
        res = await self.r.run(["wpctl", "set-mute", SINK, "1" if on else "0"])
        if res.missing:
            return "I cannot change the sound (wpctl is not available)."
        return "Muted." if on else "Unmuted."

    # ---- brightness, lock, sleep, night light ---------------------------------------------------------
    async def brightness(self, set_to: int | None = None) -> str:
        if self.r.have("brightnessctl"):
            if set_to is None:
                res = await self.r.run(["brightnessctl", "-m"])
                parts = res.out.strip().split(",")
                return f"The brightness is {parts[3].rstrip('%')} percent." if len(parts) >= 4 else \
                    "I could not read the brightness."
            level = max(5, min(100, int(set_to)))  # never fully dark: you could not see to undo it
            res = await self.r.run(["brightnessctl", "set", f"{level}%"])
            return f"Brightness set to {level} percent." if res.ok else "I could not change the brightness."
        if set_to is None:
            return "Reading the brightness needs brightnessctl (not installed)."
        shown = await self.r.run(["xrandr", "--query"])
        m = re.search(r"^([A-Za-z0-9-]+) connected", shown.out, re.M)
        if not m:
            return "Changing the brightness needs brightnessctl (not installed), and no screen was found for software dimming."
        level = max(30, min(100, int(set_to)))
        res = await self.r.run(["xrandr", "--output", m.group(1), "--brightness", f"{level / 100:.2f}"])
        if not res.ok:
            return "I could not dim the screen."
        return (f"Dimmed {m.group(1)} to {level} percent in software, which is not the real backlight. Install "
                "brightnessctl for that.")

    async def lock(self) -> str:
        res = await self.r.run(["loginctl", "lock-session"])
        return "Locked." if res.ok else "I could not lock the screen."

    async def sleep(self) -> str:
        res = await self.r.run(["systemctl", "suspend"])
        return "Going to sleep." if res.ok else "I could not put the computer to sleep."

    async def night_light(self, on: bool, kelvin: int | None = None) -> str:
        keys = await self.r.run(["gsettings", "list-keys", NIGHT_SCHEMA])
        if not keys.ok or "night-light-enabled" not in keys.out:
            return "Night light is not available on this desktop."
        res = await self.r.run(["gsettings", "set", NIGHT_SCHEMA, "night-light-enabled", "true" if on else "false"])
        if not res.ok:
            return "I could not change night light."
        if kelvin is not None and "night-light-temperature" in keys.out:
            warm = max(2700, min(6500, int(kelvin)))
            await self.r.run(["gsettings", "set", NIGHT_SCHEMA, "night-light-temperature", str(warm)])
        return "Night light is on." if on else "Night light is off."
