"""A reminder or the morning brief never takes over the display: it is a short notice, so the album card stays."""
from minty_core.context import build_context


def test_a_fired_reminder_is_a_notice_not_a_display_pane(tmp_path):
    ctx = build_context(tmp_path)
    panes = ctx.bus.subscribe({"display"})
    notices = ctx.bus.subscribe({"notice"})
    ctx.services["scheduler"]._show("Good morning. It's Wednesday.")
    assert panes.empty()  # no "For you" panel in place of the cover and the conversation
    assert notices.get_nowait()["message"] == "Good morning. It's Wednesday."


def test_the_wording_for_you_is_gone():
    import pathlib
    source = (pathlib.Path(__file__).parents[1] / "src" / "minty_core" / "context.py").read_text()
    assert "For you" not in source
