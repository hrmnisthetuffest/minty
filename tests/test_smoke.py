import minty_core


def test_version():
    assert minty_core.__version__.startswith("1.0.0-alpha")  # the exact number changes with every release (test_version.py)
