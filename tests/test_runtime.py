"""Start-up tuning of the Python runtime: after everything is loaded, freeze it so the collector stops re-scanning it."""
import gc

from minty_core.runtime import tune_gc


def test_it_freezes_what_is_loaded_and_raises_the_collection_threshold():
    before = gc.get_threshold()
    frozen_before = gc.get_freeze_count()
    try:
        info = tune_gc()
        assert gc.get_threshold()[0] >= 10_000 and info["threshold"][0] == gc.get_threshold()[0]
        assert gc.get_freeze_count() >= frozen_before and info["frozen"] == gc.get_freeze_count()
    finally:
        gc.set_threshold(*before)
        gc.unfreeze()


def test_a_collector_that_was_already_tuned_is_left_alone():
    before = gc.get_threshold()
    gc.set_threshold(123_456, 11, 11)
    try:
        assert tune_gc()["threshold"][0] == 123_456  # someone chose a number: not replaced
    finally:
        gc.set_threshold(*before)
        gc.unfreeze()
