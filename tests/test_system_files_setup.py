"""767 what-is-this-file and 643 the setup-script generator."""
import os
import re
from pathlib import Path

import pytest

from minty_core.system.files import describe_path
from minty_core.system.osinfo import OS
from minty_core.system.setup import make_script

ARCH = OS("arch", "Arch Linux", "pacman")
MINT = OS("linuxmint", "Linux Mint 22", "apt")


def write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_types_come_from_the_first_bytes_not_just_the_name(tmp_path):
    home = tmp_path / "home"
    png = write(home / "Pictures" / "holiday.dat", b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    assert "PNG image" in describe_path(png, home)
    pdf = write(home / "Documents" / "form.pdf", b"%PDF-1.7\n...")
    assert "PDF document" in describe_path(pdf, home)
    elf = write(home / "bin" / "tool", b"\x7fELF" + b"\0" * 60)
    assert "program" in describe_path(elf, home)
    text = write(home / "notes.txt", b"hello\nworld\n")
    assert "plain text, 2 lines" in describe_path(text, home)
    db = write(home / "x.sqlite", b"SQLite format 3\0" + b"\0" * 100)
    assert "SQLite database" in describe_path(db, home)


def test_the_delete_advice_follows_where_the_file_lives(tmp_path):
    home = tmp_path / "home"
    cache = write(home / ".cache" / "thumbnails" / "a.png", b"\x89PNG\r\n\x1a\n")
    assert "usually safe to delete" in describe_path(cache, home)
    tmp = write(tmp_path / "tmp" / "scratch.txt")
    assert "usually safe to delete" in describe_path(tmp, home, tmp_dirs=(str(tmp_path / "tmp"),))
    pyc = write(home / "proj" / "__pycache__" / "m.cpython-313.pyc", b"\xcb\r\r\n")
    assert "usually safe to delete" in describe_path(pyc, home)
    doc = write(home / "Documents" / "essay.docx", b"PK\x03\x04" + b"0" * 50)
    assert "your own file" in describe_path(doc, home) and "ask yourself" in describe_path(doc, home)
    cfg = write(home / ".config" / "app" / "settings.json", b"{}")
    assert "settings" in describe_path(cfg, home) and "Do not delete" in describe_path(cfg, home)
    git = write(home / "proj" / ".git" / "HEAD", b"ref: refs/heads/main\n")
    assert "Git" in describe_path(git, home) and "Do not delete" in describe_path(git, home)
    sysfile = describe_path(Path("/etc/os-release"), home)
    assert "system file" in sysfile and "Do not delete" in sysfile


def test_secrets_are_named_but_never_read(tmp_path):
    home = tmp_path / "home"
    key = write(home / ".ssh" / "id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETSECRET\n")
    out = describe_path(key, home)
    assert "private key" in out and "SECRET" not in out and "not read" in out
    env = write(home / "proj" / ".env", b"GROQ_API_KEY=gsk_abc\n")
    out = describe_path(env, home)
    assert "gsk_abc" not in out and "secrets" in out


def test_links_directories_and_missing_files(tmp_path):
    home = tmp_path / "home"
    target = write(home / "a.txt", b"one\n")
    link = home / "link"
    os.symlink(target, link)
    assert "link to" in describe_path(link, home) and "a.txt" in describe_path(link, home)
    d = home / "stuff"
    write(d / "one.txt"), write(d / "two.txt")
    assert "folder with 2 items" in describe_path(d, home)
    assert "does not exist" in describe_path(home / "nope", home)


def test_it_reads_only_a_few_bytes_of_a_big_file(tmp_path):
    home = tmp_path / "home"
    big = write(home / "big.bin", b"\0" * (5 * 1024 * 1024))
    assert "5.0 MB" in describe_path(big, home)


# ---- setup-script generator ---------------------------------------------------------------------------------------
def test_an_arch_script_uses_pacman_and_a_mint_script_uses_apt():
    arch = make_script(ARCH, packages=["playerctl", "pulseaudio-utils", "tesseract-ocr"], when="2026-10-18")
    mint = make_script(MINT, packages=["playerctl", "pulseaudio-utils", "tesseract-ocr"], when="2026-10-18")
    assert "sudo pacman -S --needed playerctl libpulse tesseract tesseract-data-eng" in arch
    assert "sudo apt install playerctl pulseaudio-utils tesseract-ocr" in mint
    assert not re.search(r"\bapt\b", arch) and not re.search(r"\bpacman\b", mint)
    for text in (arch, mint):
        assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
        assert "Nothing has been run" in text and "read -rp" in text  # it asks before it does anything


def test_extra_steps_and_the_quoting_of_everything():
    text = make_script(ARCH, packages=["git"], pipx=["yt-dlp"], services=["sshd"], directories=["~/Projects/Minty"],
                       when="2026-10-18")
    assert "pipx install yt-dlp" in text and "sudo systemctl enable --now sshd.service" in text
    assert 'mkdir -p "$HOME/Projects/Minty"' in text


@pytest.mark.parametrize("field,bad", [("packages", "x; rm -rf /"), ("packages", "$(id)"), ("packages", "a b"),
                                       ("pipx", "x && y"), ("services", "a;b"), ("directories", "../../etc"),
                                       ("directories", "/etc/cron.d"), ("directories", "~/a; reboot")])
def test_nothing_that_could_inject_a_command_gets_into_the_script(field, bad):
    args = {"packages": [], "pipx": [], "services": [], "directories": []}
    args[field] = [bad]
    with pytest.raises(ValueError):
        make_script(ARCH, when="2026-10-18", **args)


def test_an_unknown_system_gets_no_script_and_an_empty_request_is_refused():
    with pytest.raises(ValueError, match="package manager"):
        make_script(OS(), packages=["git"], when="x")
    with pytest.raises(ValueError, match="nothing"):
        make_script(ARCH, when="x")
    assert "not needed" in make_script(MINT, packages=["pacman-contrib", "git"], when="x")
