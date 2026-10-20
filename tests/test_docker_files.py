"""The Docker files: checked as text (an image cannot be built in the test run), so they stay safe and consistent."""
import re
import stat
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_the_dockerfile_builds_a_small_non_root_image_with_a_voice_variant():
    d = text("Dockerfile")
    assert d.startswith("# syntax=docker/dockerfile:1") and re.search(r"^FROM python:3\.1[0-3]-slim AS core$", d, re.M)
    assert re.search(r"^FROM core AS voice$", d, re.M)
    assert "pip install" in d and ".[voice" in d  # the voice libraries only in the voice variant
    assert re.search(r"^USER minty$", d, re.M) and "useradd" in d  # never root at run time
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in d and 'CMD ["minty"]' in d
    assert "HEALTHCHECK" in d and "7778" in d
    assert "sudo" not in d and "curl | " not in d.replace(" ", "")
    last = d.rsplit("\nFROM ", 1)[1]
    assert "USER minty" in last  # the final stage ends as the unprivileged user too
    assert not re.search(r"(?i)api[_-]?key|password\s*=|secret\s*=", d)


def test_the_entrypoint_makes_the_panel_reachable_only_on_first_start_and_is_executable():
    path = ROOT / "docker" / "entrypoint.sh"
    body = path.read_text()
    assert body.startswith("#!/bin/sh") and "set -e" in body and 'exec "$@"' in body
    assert 'host = "0.0.0.0"' in body and "panel.toml" in body and "! -e" in body  # never overwrites your own panel.toml
    assert path.stat().st_mode & stat.S_IXUSR


def test_dockerignore_keeps_secrets_history_and_junk_out_of_the_image():
    ignored = {line.strip() for line in text(".dockerignore").splitlines() if line.strip() and not line.startswith("#")}
    for needed in (".git", ".venv", "__pycache__", ".pytest_cache", "tests", ".env", "*.key", "apis and stuff*"):
        assert needed in ignored, needed


def test_compose_publishes_the_panel_on_this_computer_only_and_keeps_data_in_a_volume():
    compose = yaml.safe_load(text("docker-compose.yml"))
    service = compose["services"]["minty"]
    assert service["ports"] == ["127.0.0.1:7778:7778"]  # never 0.0.0.0: the panel speaks plain HTTP
    assert service["init"] is True and service["restart"] == "unless-stopped"
    assert any(str(v).startswith("minty-data:") for v in service["volumes"]) and "minty-data" in compose["volumes"]
    assert service["build"]["target"].startswith("${MINTY_IMAGE_TARGET") and service["build"]["context"] == "."
    assert not any("7777" in str(p) for p in service["ports"])  # the control API stays inside the container
    assert "environment" not in service or not any("KEY" in str(e).upper() for e in service["environment"])
    assert service["environment"]["MINTY_NO_ONBOARD"] in ("1", 1)


def test_the_voice_override_passes_the_sound_server_and_asks_for_the_voice_image():
    override = yaml.safe_load(text("docker-compose.voice.yml"))
    service = override["services"]["minty"]
    assert service["build"]["target"] == "voice"
    assert any("pulse" in str(v) for v in service["volumes"])
    assert any(str(e).startswith("PULSE_SERVER") or "PULSE_SERVER" in str(e) for e in (service["environment"] if isinstance(service["environment"], list) else service["environment"].keys()))
    assert "/dev/snd" in service["devices"][0] and service["user"]


def test_the_docker_guide_says_what_works_and_what_does_not():
    guide = text("docs/docker.md")
    for needed in ("docker compose up", "set-pin", "set-key", "127.0.0.1:7778", "docker-compose.voice.yml", "does not"):
        assert needed in guide, needed
    assert "Chrome" in guide and "microphone" in guide
