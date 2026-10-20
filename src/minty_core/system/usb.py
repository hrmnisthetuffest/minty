"""Safe USB mount and eject (644). Only removable drives that `lsblk` itself reports are ever offered; the system disk and
anything mounted at / or /boot is never listed; the device path always comes from that list, never from what you said."""
from __future__ import annotations

import json
from dataclasses import dataclass

SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/home", "/usr", "/var", "/etc", "[SWAP]")


@dataclass
class Drive:
    path: str
    disk: str
    label: str
    size: str
    mountpoint: str
    fstype: str
    model: str


class Usb:
    def __init__(self, runner):
        self.r = runner

    async def drives(self) -> list[Drive]:
        res = await self.r.run(["lsblk", "-J", "-o", "NAME,PATH,TYPE,RM,HOTPLUG,TRAN,MOUNTPOINT,LABEL,SIZE,FSTYPE,MODEL"])
        if res.missing:
            return []
        try:
            disks = json.loads(res.out).get("blockdevices", [])
        except ValueError:
            return []
        found: list[Drive] = []
        for d in disks:
            if not (d.get("rm") or d.get("hotplug") or d.get("tran") == "usb"):
                continue
            parts = d.get("children") or ([d] if d.get("type") == "part" else [])
            for p in parts:
                mount = p.get("mountpoint") or ""
                if p.get("type") != "part" or mount in SYSTEM_MOUNTS or mount.startswith(("/boot", "/usr", "/var")):
                    continue
                found.append(Drive(p["path"], d["path"], p.get("label") or p["name"], p.get("size") or "", mount,
                                   p.get("fstype") or "", (d.get("model") or "").strip()))
        return found

    async def describe(self) -> str:
        drives = await self.drives()
        if not drives:
            return "No USB drives or cards are plugged in."
        return "Removable drives: " + "; ".join(
            f"{d.label} ({d.size}{', ' + d.fstype if d.fstype else ''}){' (mounted)' if d.mountpoint else ''}"
            for d in drives) + "."

    async def _pick(self, query: str):
        drives = await self.drives()
        q = " ".join(str(query).lower().split())
        hits = [d for d in drives if q and (q in d.label.lower() or q in d.path.lower())] if q else drives
        if not drives:
            return None, "No USB drives or cards are plugged in."
        if not hits:
            return None, f"No removable drive matches {query!r}. I can see: " + ", ".join(d.label for d in drives) + "."
        if len(hits) > 1:
            return None, "Which one: " + ", ".join(d.label for d in hits) + "?"
        return hits[0], ""

    async def mount(self, query: str) -> str:
        drive, why = await self._pick(query)
        if drive is None:
            return why
        if drive.mountpoint:
            return f"{drive.label} is already mounted at {drive.mountpoint}."
        res = await self.r.run(["udisksctl", "mount", "-b", drive.path])
        if res.missing:
            return "udisksctl is not available on this computer."
        return res.out.strip() or f"Mounted {drive.label}." if res.ok else f"I could not mount {drive.label}: {_why(res.err)}"

    async def eject(self, query: str) -> str:
        drive, why = await self._pick(query)
        if drive is None:
            return why
        if drive.mountpoint:
            res = await self.r.run(["udisksctl", "unmount", "-b", drive.path])
            if res.missing:
                return "udisksctl is not available on this computer."
            if not res.ok:
                return f"I could not unmount {drive.label}: {_why(res.err)} I left it alone."
        res = await self.r.run(["udisksctl", "power-off", "-b", drive.disk])
        if not res.ok:
            return f"{drive.label} is unmounted, but I could not power it off: {_why(res.err)}"
        return f"{drive.label} is safe to unplug."


def _why(err: str) -> str:
    text = " ".join(str(err).split())[:160]
    return text or "no reason was given."
