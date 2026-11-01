"""Which Linux is this, and so which package commands are right (205). Arch gets pacman, Mint gets apt; an unknown system
gets words, never a guessed command. Package names are kept in their Debian spelling and translated for the others."""
from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path

from minty_core import osutil

OS_RELEASE = Path("/etc/os-release")
_ARCH_LIKE = {"arch", "manjaro", "endeavouros", "cachyos", "garuda", "artix"}
_APT_LIKE = {"debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin", "raspbian"}
_DNF_LIKE = {"fedora", "rhel", "centos", "rocky", "almalinux"}
_ZYPPER_LIKE = {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "suse", "sles"}

# Debian spelling -> the other managers' spelling (a missing entry means the same name everywhere)
PACKAGES: dict[str, dict[str, str]] = {
    "pulseaudio-utils": {"pacman": "libpulse", "dnf": "pulseaudio-utils"},
    "x11-utils": {"pacman": "xorg-xprop", "dnf": "xorg-x11-utils"},
    "tesseract-ocr": {"pacman": "tesseract tesseract-data-eng", "dnf": "tesseract"},
    "nodejs": {"pacman": "nodejs npm"},
    "python3-venv": {"pacman": "python"},
    "build-essential": {"pacman": "base-devel", "dnf": "gcc make"},
    "libnotify-bin": {"pacman": "libnotify", "dnf": "libnotify"},
    "usbutils": {"pacman": "usbutils", "dnf": "usbutils"},
    "gh": {"pacman": "github-cli", "dnf": "gh"},
}
# Windows: winget ids for what Minty's tools may ask for (a package missing here is installed by its own name)
WINGET = {"ffmpeg": "Gyan.FFmpeg", "git": "Git.Git", "nodejs": "OpenJS.NodeJS.LTS", "gh": "GitHub.cli", "tesseract-ocr": "UB-Mannheim.TesseractOCR", "yt-dlp": "yt-dlp.yt-dlp",
          "uv": "astral-sh.uv", "python3": "Python.Python.3.12"}
# only some families have these
ARCH_ONLY = {"pacman-contrib"}


@dataclass(frozen=True)
class OS:
    id: str = ""
    pretty: str = "this computer"
    manager: str = "unknown"  # pacman | apt | dnf | zypper | winget | unknown


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in str(text).splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().isidentifier():
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def detect(path: Path = OS_RELEASE) -> OS:
    if osutil.IS_WINDOWS and path == OS_RELEASE:  # there is no /etc/os-release; winget is the package manager every Windows 10 and 11 has
        return OS("windows", f"Windows {platform.release()}", "winget")
    try:
        fields = _parse(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return OS()
    ident = fields.get("ID", "").lower()
    family = {ident, *fields.get("ID_LIKE", "").lower().split()}
    for names, manager in ((_ARCH_LIKE, "pacman"), (_APT_LIKE, "apt"), (_DNF_LIKE, "dnf"), (_ZYPPER_LIKE, "zypper")):
        if family & names:
            return OS(ident, fields.get("PRETTY_NAME") or fields.get("NAME") or ident, manager)
    return OS(ident, fields.get("PRETTY_NAME") or ident or "this computer", "unknown")


def local_name(os_: OS, package: str) -> str:
    if os_.manager == "winget":
        return WINGET.get(package, package)
    return PACKAGES.get(package, {}).get(os_.manager, package)


def _verb(os_: OS, package: str, action: str) -> str:
    name = local_name(os_, package)
    table = {"install": {"pacman": "sudo pacman -S {p}", "apt": "sudo apt install {p}", "dnf": "sudo dnf install {p}",
                         "zypper": "sudo zypper install {p}", "winget": "winget install -e --id {p}"},
             "remove": {"pacman": "sudo pacman -R {p}", "apt": "sudo apt remove {p}", "dnf": "sudo dnf remove {p}",
                        "zypper": "sudo zypper remove {p}", "winget": "winget uninstall -e --id {p}"}}[action]
    template = table.get(os_.manager)
    if template is None:
        return f"{action.capitalize()} {package} with your system's package manager."
    return template.format(p=name)


def install_command(os_: OS, package: str) -> str:
    return _verb(os_, package, "install")


def install_many(os_: OS, packages: list[str]) -> str:
    """One install line for several packages (skipping what is already installed where the manager can)."""
    names = " ".join(local_name(os_, p) for p in packages)
    if os_.manager == "pacman":
        return f"sudo pacman -S --needed {names}"
    if os_.manager == "apt":
        return f"sudo apt install {names}"
    if os_.manager == "winget":
        return "; ".join(f"winget install -e --id {local_name(os_, p)}" for p in packages)
    raise ValueError("I do not know this system's package manager.")


def remove_command(os_: OS, package: str) -> str:
    return _verb(os_, package, "remove")


def update_command(os_: OS) -> str:
    return {"pacman": "sudo pacman -Syu", "apt": "sudo apt update && sudo apt upgrade", "dnf": "sudo dnf upgrade",
            "zypper": "sudo zypper update", "winget": "winget upgrade --all"}.get(os_.manager, "Update with your system's package manager.")


def hint_for(os_: OS, package: str) -> str:
    """One sentence for a missing tool: how to get it on this machine."""
    if package in ARCH_ONLY and os_.manager != "pacman":
        return f"{package} is not needed on {os_.pretty} (it is an Arch package)."
    command = install_command(os_, package)
    if command.startswith("sudo"):
        return f"{package} is not installed. Install it with: {command}"
    return f"{package} is not installed. {command}"
