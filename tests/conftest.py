import pytest

from tests import timeshift

timeshift.install()  # only with MINTY_TEST_SHIFT_DAYS set (see tests/timeshift.py)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests away from voice models installed in the real home directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


NEWS_FIXTURE_MODULES = {"test_create_plugin", "test_wp19_acceptance"}  # their feeds are from one fixed day


@pytest.fixture(autouse=True)
def _news_feeds_are_read_on_the_day_they_were_written(request, monkeypatch):
    """The news brief only shows the last 72 hours. Feeds in these tests are dated 18 October 2026, so the clock is held there."""
    if request.module.__name__.rsplit(".", 1)[-1] not in NEWS_FIXTURE_MODULES:
        return
    from datetime import datetime, timezone

    from tests.test_create_feeds_papers import NOW

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.astimezone(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr("minty_core.create.feeds.datetime", Frozen)


def pytest_collection_modifyitems(config, items):
    """MINTY_TEST_SHUFFLE=<seed> runs the test files in a different order (the tests inside a file keep theirs). A test that only passes
    after another one has run is leaking state between them, which this finds."""
    import os
    import random

    seed = os.environ.get("MINTY_TEST_SHUFFLE")
    if not seed:
        return
    modules: dict[str, list] = {}
    for item in items:
        modules.setdefault(item.module.__name__, []).append(item)
    order = list(modules)
    random.Random(int(seed)).shuffle(order)
    items[:] = [item for name in order for item in modules[name]]
