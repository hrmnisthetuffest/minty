import argparse
import getpass
import ipaddress
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from minty_core._toml import tomllib
from minty_core.paths import config_dir
from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app

log = logging.getLogger("minty.panel")


@dataclass
class PanelSettings:
    host: str = "127.0.0.1"
    port: int = 7778
    core_url: str = "http://127.0.0.1:7777"
    lan_only: bool = True  # answer only requests from your own network, whatever the bind address
    extra_hosts: tuple = ()  # more names the phone may use in the address bar, e.g. "mypc.local:7778" (panel.toml extra_hosts)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Error parsing {path.name}: {e}")


def load_settings(directory: Path) -> PanelSettings:
    directory = Path(directory)
    core_port = _read(directory / "config.toml").get("api_port", 7777)
    data = _read(directory / "panel.toml")
    try:
        port = int(data.get("port", 7778))
    except (TypeError, ValueError):
        sys.exit("Error in panel.toml: port must be a number")
    return PanelSettings(
        host=str(data.get("host", "127.0.0.1")),
        port=port,
        core_url=str(data.get("core_url", f"http://127.0.0.1:{core_port}")),
        lan_only=bool(data.get("lan_only", True)),
        extra_hosts=tuple(str(h) for h in data.get("extra_hosts", []) if isinstance(h, str) and h.strip()),
    )


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_allowed_hosts(settings: PanelSettings) -> list[str]:
    port = settings.port
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    if not is_loopback(settings.host):
        hosts.append(f"{settings.host}:{port}")
    return hosts + list(settings.extra_hosts)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="minty-panel")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="start the panel (default)")
    for name, text in (("set-pin", "set the panel login PIN (4 to 8 digits)"), ("set-password", "the same as set-pin")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--allow-easy", action="store_true", help="accept a PIN that is easy to guess (0000, 1234)")
    args = parser.parse_args(argv)
    args.command = {"set-password": "set-pin"}.get(args.command, args.command or "run")
    args.allow_easy = bool(getattr(args, "allow_easy", False))
    return args


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    directory = config_dir()
    passwords = PasswordStore(directory)
    if args.command == "set-pin":
        first = getpass.getpass("New panel PIN (4 to 8 digits; 6 is a good length): ")
        if first != getpass.getpass("Repeat the PIN: "):
            sys.exit("the PINs did not match, nothing saved")
        try:
            passwords.set_pin(first, allow_easy=args.allow_easy)
        except ValueError as e:
            sys.exit(str(e))
        print("panel PIN saved" + (" (it is easy to guess: anyone who can reach the panel can try it)" if args.allow_easy else ""))
        return
    uvicorn.run(**build_panel_app(directory), log_level="info", proxy_headers=False)


def build_panel_app(directory: Path) -> dict:
    """uvicorn arguments (app, host, port) for the panel. Shared by `minty-panel` and the combined `minty`."""
    passwords = PasswordStore(directory)
    settings = load_settings(directory)
    if not passwords.is_set():
        log.warning("no panel PIN set yet; run: minty set-pin")
    if not is_loopback(settings.host):
        log.warning("panel is listening on %s, which is reachable from your network. It speaks plain HTTP: "
                    "put it behind HTTPS or keep it on 127.0.0.1.", settings.host)
    core = CoreClient(settings.core_url, directory / "api_token")
    if not settings.lan_only:
        log.warning("panel.toml lan_only = false: the panel will answer requests from ANY address that can reach it.")
    app = create_panel_app(passwords, SessionStore(), LoginThrottle(), core,
                           allowed_hosts=build_allowed_hosts(settings), lan_only=settings.lan_only)
    return {"app": app, "host": settings.host, "port": settings.port}
