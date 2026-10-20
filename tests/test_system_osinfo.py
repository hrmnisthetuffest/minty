"""205: the right package commands for THIS machine, never the other family's."""
import re
from pathlib import Path

import pytest

from minty_core.system.osinfo import OS, detect, hint_for, install_command, remove_command, update_command

ARCH = 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\nBUILD_ID=rolling\n'
MINT = ('NAME="Linux Mint"\nVERSION="22 (Wilma)"\nID=linuxmint\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Linux Mint 22"\n'
        'UBUNTU_CODENAME=noble\n')
UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04"\n'
FEDORA = 'NAME="Fedora Linux"\nID=fedora\nPRETTY_NAME="Fedora Linux 40"\n'
MANJARO = 'NAME="Manjaro Linux"\nID=manjaro\nID_LIKE=arch\n'


def os_of(tmp_path, text):
    p = tmp_path / "os-release"
    p.write_text(text)
    return detect(p)


@pytest.mark.parametrize("text,manager,name", [(ARCH, "pacman", "Arch Linux"), (MINT, "apt", "Linux Mint 22"),
                                               (UBUNTU, "apt", "Ubuntu 24.04"), (FEDORA, "dnf", "Fedora Linux 40"),
                                               (MANJARO, "pacman", "Manjaro Linux")])
def test_detection(tmp_path, text, manager, name):
    o = os_of(tmp_path, text)
    assert o.manager == manager and o.pretty == name


def test_a_missing_or_odd_file_is_unknown_not_a_guess(tmp_path):
    assert detect(tmp_path / "nope").manager == "unknown"
    assert os_of(tmp_path, "garbage").manager == "unknown"


def test_arch_never_gets_apt_and_mint_never_gets_pacman(tmp_path):
    arch, mint = os_of(tmp_path, ARCH), os_of(tmp_path, MINT)
    for pkg in ("playerctl", "pulseaudio-utils", "x11-utils", "tesseract-ocr", "htop", "git"):
        for text in (install_command(arch, pkg), remove_command(arch, pkg), hint_for(arch, pkg)):
            assert not re.search(r"\b(apt|dpkg|dnf)\b", text), text
        for text in (install_command(mint, pkg), remove_command(mint, pkg), hint_for(mint, pkg)):
            assert not re.search(r"\b(pacman|yay|pamac)\b", text), text
    assert not re.search(r"\bapt\b", update_command(arch)) and "pacman" not in update_command(mint)


def test_package_names_are_translated_to_the_local_ones(tmp_path):
    arch, mint = os_of(tmp_path, ARCH), os_of(tmp_path, MINT)
    assert install_command(arch, "pulseaudio-utils") == "sudo pacman -S libpulse"
    assert install_command(mint, "pulseaudio-utils") == "sudo apt install pulseaudio-utils"
    assert install_command(arch, "tesseract-ocr") == "sudo pacman -S tesseract tesseract-data-eng"
    assert install_command(arch, "x11-utils") == "sudo pacman -S xorg-xprop"
    assert update_command(arch) == "sudo pacman -Syu" and update_command(mint) == "sudo apt update && sudo apt upgrade"


def test_a_package_the_other_family_does_not_have_says_so(tmp_path):
    assert "not needed" in hint_for(os_of(tmp_path, MINT), "pacman-contrib")
    assert "pacman -S pacman-contrib" in hint_for(os_of(tmp_path, ARCH), "pacman-contrib")


def test_an_unknown_system_gets_words_not_a_guessed_command(tmp_path):
    o = os_of(tmp_path, "garbage")
    assert install_command(o, "htop") == "Install htop with your system's package manager."
    assert "sudo" not in hint_for(o, "htop")


def test_no_plugin_hard_codes_an_apt_command():
    root = Path(__file__).resolve().parents[1] / "src" / "minty_core"
    offenders = []
    for path in root.rglob("*.py"):
        if "installer" in path.parts or path.name == "osinfo.py":
            continue  # the installer takes an explicit manager from the user; osinfo owns the tables
        if "sudo apt" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
