import re

import minty_core


def test_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", minty_core.__version__)  # the exact number changes with every release (test_version.py)
