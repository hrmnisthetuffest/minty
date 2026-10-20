"""644: removable drives only, chosen from what the system listed."""
import json

import pytest

from minty_core.system.runner import Result, Runner
from minty_core.system.usb import Usb

LSBLK = json.dumps({"blockdevices": [
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "rm": False, "hotplug": False, "tran": "nvme",
     "model": "Samsung SSD", "size": "476G", "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "rm": False, "hotplug": False, "tran": None,
          "mountpoint": "/boot", "label": None, "size": "512M", "fstype": "vfat"},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "rm": False, "hotplug": False, "tran": None,
          "mountpoint": "/", "label": "root", "size": "475G", "fstype": "ext4"}]},
    {"name": "sda", "path": "/dev/sda", "type": "disk", "rm": True, "hotplug": True, "tran": "usb", "model": "Cruzer",
     "size": "14.5G", "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "rm": True, "hotplug": True, "tran": "usb",
          "mountpoint": None, "label": "NOTES", "size": "14.5G", "fstype": "vfat"}]},
    {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "tran": "usb", "model": "Passport",
     "size": "931G", "children": [
         {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "rm": True, "hotplug": True, "tran": "usb",
          "mountpoint": "/run/media/user/BACKUP", "label": "BACKUP", "size": "931G", "fstype": "ntfs"}]},
]})


class Fake:
    def __init__(self, extra=None):
        self.calls, self.extra = [], extra or {}

    def have(self, name):
        return True

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        if argv[0] == "lsblk":
            return Result(0, LSBLK)
        for key, value in self.extra.items():
            if " ".join(argv).startswith(key):
                return value
        return Result(0, "")


async def test_only_removable_partitions_are_listed_and_the_system_disk_never_is():
    drives = await Usb(Fake()).drives()
    assert [d.path for d in drives] == ["/dev/sda1", "/dev/sdb1"]
    text = await Usb(Fake()).describe()
    assert "NOTES" in text and "BACKUP" in text and "(mounted)" in text and "nvme" not in text and "root" not in text


async def test_mount_picks_by_label_and_uses_the_listed_path():
    f = Fake({"udisksctl mount": Result(0, "Mounted /dev/sda1 at /run/media/user/NOTES\n")})
    out = await Usb(f).mount("notes")
    assert f.calls[-1] == ["udisksctl", "mount", "-b", "/dev/sda1"] and "/run/media/user/NOTES" in out


async def test_the_system_disk_cannot_be_mounted_or_ejected_even_by_name():
    f = Fake()
    out = await Usb(f).mount("root")
    assert "No removable drive matches" in out
    assert not any(c[0] == "udisksctl" for c in f.calls)


async def test_ambiguous_names_ask_and_change_nothing():
    f = Fake()
    out = await Usb(f).mount("")
    assert "Which one" in out and not any(c[0] == "udisksctl" for c in f.calls)


async def test_eject_unmounts_then_powers_the_disk_off():
    f = Fake()
    out = await Usb(f).eject("backup")
    assert [c for c in f.calls if c[0] == "udisksctl"] == [["udisksctl", "unmount", "-b", "/dev/sdb1"],
                                                            ["udisksctl", "power-off", "-b", "/dev/sdb"]]
    assert "safe to unplug" in out


async def test_a_busy_drive_is_not_forced():
    f = Fake({"udisksctl unmount": Result(1, "", "Error unmounting: target is busy")})
    out = await Usb(f).eject("backup")
    assert "busy" in out and not any(c[1] == "power-off" for c in f.calls if c[0] == "udisksctl")


async def test_the_runner_only_takes_device_paths_that_look_like_disks():
    r = Runner(which=lambda n: None)
    for bad in (["udisksctl", "mount", "-b", "/etc/passwd"], ["udisksctl", "mount", "-b", "/dev/sda1; reboot"],
                ["udisksctl", "unlock", "-b", "/dev/sda1"], ["udisksctl", "mount", "/dev/sda1"],
                ["udisksctl", "mount", "-b", "/dev/../etc"]):
        with pytest.raises(ValueError):
            await r.run(bad)
    for good in (["udisksctl", "mount", "-b", "/dev/sda1"], ["udisksctl", "power-off", "-b", "/dev/mmcblk0"],
                 ["udisksctl", "unmount", "-b", "/dev/nvme1n1p1"]):
        assert (await r.run(good)).missing
