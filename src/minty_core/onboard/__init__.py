"""`minty onboard`: the guided first-run setup (see steps.py)."""
from __future__ import annotations

import argparse
import asyncio

from minty_core.onboard.steps import STEP_IDS, Onboarding, SetupError

__all__ = ["STEP_IDS", "Onboarding", "SetupError", "main"]


def main(argv: list[str] | None = None) -> bool:
    """Run the setup in this terminal. True when the person asked to start Minty at the end."""
    from minty_core.config import ConfigStore
    from minty_core.keystore import SecretsStore
    from minty_core.onboard.env import default_env
    from minty_core.onboard.ui import RichUI
    from minty_core.paths import config_dir

    parser = argparse.ArgumentParser(prog="minty onboard", description="guided setup, one step at a time")
    parser.add_argument("--step", choices=STEP_IDS, default="", help="run just one step")
    parser.add_argument("--again", action="store_true", help="start from the first step even if you have been here before")
    args = parser.parse_args(argv or [])
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(directory)
    store.load()
    setup = Onboarding(RichUI(), default_env(directory), store, SecretsStore(directory))
    return asyncio.run(setup.run(only=args.step, fresh=args.again))
