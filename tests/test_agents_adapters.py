import json
from pathlib import Path

import pytest

from minty_core.plugins.builtin.agents.adapters import ClaudeCodeAdapter


def test_claude_argv_and_prompt_safety(tmp_path):
    adapter = ClaudeCodeAdapter(permission_mode="acceptEdits", allowed_tools=["Read", "Bash(git:*)"])
    inv = adapter.build(tmp_path, "build a thing; $(rm -rf /)", None)
    assert inv.cwd == tmp_path
    assert inv.argv[:3] == ["claude", "-p", "build a thing; $(rm -rf /)"]
    assert inv.argv[3:5] == ["--output-format", "json"]
    assert "--resume" not in inv.argv
    assert inv.argv[-3:] == ["--allowedTools", "Read", "Bash(git:*)"]
    assert inv.argv[inv.argv.index("--permission-mode") + 1] == "acceptEdits"


def test_claude_resume_flag(tmp_path):
    inv = ClaudeCodeAdapter().build(tmp_path, "continue", "sess-123")
    assert inv.argv[inv.argv.index("--resume") + 1] == "sess-123"


def test_claude_parse_json_result():
    out = json.dumps({"result": "Created app.py and 3 tests. All pass.", "session_id": "abc", "is_error": False})
    parsed = ClaudeCodeAdapter().parse(out)
    assert parsed.summary == "Created app.py and 3 tests. All pass." and parsed.session_id == "abc"


def test_claude_parse_garbage_falls_back_to_tail():
    parsed = ClaudeCodeAdapter().parse("not json " * 400)
    assert 0 < len(parsed.summary) <= 400 and parsed.session_id is None


def test_claude_is_error_marks_failure():
    out = json.dumps({"result": "Session not found", "session_id": "abc", "is_error": True})
    parsed = ClaudeCodeAdapter().parse(out)
    assert parsed.failed is True and parsed.summary == "Session not found"
    assert ClaudeCodeAdapter().parse(json.dumps({"result": "ok"})).failed is False


from minty_core.plugins.builtin.agents.adapters import HermesAdapter, OpenClawAdapter


def test_hermes_argv_and_parse(tmp_path):
    inv = HermesAdapter().build(tmp_path, "summarise this repo", "ignored-session")
    assert inv.argv == ["hermes", "-z", "summarise this repo"] and inv.cwd == tmp_path
    parsed = HermesAdapter().parse("  The repo is small.\n")
    assert parsed.summary == "The repo is small." and parsed.failed is False


def test_hermes_empty_output_is_failed():
    assert HermesAdapter().parse("").failed is True
    assert HermesAdapter().parse("  \n").failed is True


def test_openclaw_argv(tmp_path):
    inv = OpenClawAdapter(agent_id="helper").build(tmp_path, "check the logs", None)
    assert inv.argv == ["openclaw", "agent", "--agent", "helper", "--message", "check the logs", "--json"]
    assert OpenClawAdapter().build(tmp_path, "x", None).argv[3] == "main"


def test_openclaw_parse_known_keys_and_fallback():
    assert OpenClawAdapter().parse(json.dumps({"reply": "All good."})).summary == "All good."
    assert OpenClawAdapter().parse(json.dumps({"result": "Done."})).summary == "Done."
    assert OpenClawAdapter().parse(json.dumps({"weird": 1})).summary.startswith('{"weird"')
    assert OpenClawAdapter().parse("plain text").summary == "plain text"
    assert OpenClawAdapter().parse(json.dumps({"reply": "ok"})).failed is False


def test_openclaw_error_indicators_are_failed():
    p = OpenClawAdapter().parse(json.dumps({"error": "gateway down"}))
    assert p.failed is True and "gateway down" in p.summary
    p = OpenClawAdapter().parse(json.dumps({"ok": False, "reply": "partial"}))
    assert p.failed is True and p.summary == "partial"  # no error text, so the reply is all there is
    assert OpenClawAdapter().parse(json.dumps({"ok": True, "reply": "fine"})).failed is False


def test_openclaw_failed_summary_is_the_error_text_not_the_reply():
    p = OpenClawAdapter().parse(json.dumps({"error": "gateway down", "reply": "partial"}))
    assert p.failed is True and p.summary == "gateway down"
    p = OpenClawAdapter().parse(json.dumps({"error": {"message": "rate limited", "code": 429}, "reply": "x"}))
    assert p.failed is True and p.summary == "rate limited"
    p = OpenClawAdapter().parse(json.dumps({"error": {"code": 429}}))
    assert p.failed is True and p.summary == '{"code": 429}'


@pytest.mark.parametrize("out", ["", "  \n\t"])
def test_openclaw_empty_output_is_failed(out):
    assert OpenClawAdapter().parse(out).failed is True


@pytest.mark.parametrize("bad", ["--help", "a b", "--message=x", " --help ", "", "x" * 65, "-x"])
def test_openclaw_agent_id_is_validated(tmp_path, bad, caplog):
    inv = OpenClawAdapter(agent_id=bad).build(tmp_path, "hi", None)
    assert inv.argv[3] == "main"
    if bad.strip():
        assert "agent id" in caplog.text


def test_openclaw_agent_id_is_stripped_and_valid_ids_kept(tmp_path):
    assert OpenClawAdapter(agent_id=" main ").build(tmp_path, "hi", None).argv[3] == "main"
    assert OpenClawAdapter(agent_id="helper-2").build(tmp_path, "hi", None).argv[3] == "helper-2"
    assert OpenClawAdapter(agent_id="my.agent_1").build(tmp_path, "hi", None).argv[3] == "my.agent_1"


def test_openclaw_tolerates_log_noise_around_json():
    p = OpenClawAdapter().parse('[warn] foo\n{"error": "boom"}')
    assert p.failed is True and p.summary == "boom"
    p = OpenClawAdapter().parse('{"reply": "fine"}\n[info] done in 3s')
    assert p.failed is False and p.summary == "fine"
    p = OpenClawAdapter().parse('[warn] a\n{"ok": false, "error": {"message": "bad"}}\n[warn] b')
    assert p.failed is True and p.summary == "bad"


def test_claude_tolerates_a_warning_line_before_json():
    out = '(node:1) Warning: something\n' + json.dumps({"result": "Built it.", "session_id": "s-9"})
    p = ClaudeCodeAdapter().parse(out)
    assert p.summary == "Built it." and p.session_id == "s-9" and p.failed is False
    out = '[warn] x\n' + json.dumps({"result": "oops", "is_error": True}) + '\ntrailing log'
    assert ClaudeCodeAdapter().parse(out).failed is True


def test_claude_is_told_never_to_push_or_rewrite_history(tmp_path):
    from minty_core.plugins.builtin.agents.adapters import DENIED_TOOLS
    inv = ClaudeCodeAdapter().build(tmp_path, "do it", None)
    denied = inv.argv[inv.argv.index("--disallowedTools") + 1:inv.argv.index("--allowedTools")]
    assert denied == DENIED_TOOLS and "Bash(git push:*)" in denied and "Bash(git reset:*)" in denied
    assert "Bash(git push:*)" not in inv.argv[inv.argv.index("--allowedTools"):]


def test_claude_asked_a_question_can_only_read(tmp_path):
    inv = ClaudeCodeAdapter().build(tmp_path, "why is it slow?", None, readonly=True)
    assert inv.argv[inv.argv.index("--permission-mode") + 1] == "plan"
    assert inv.argv[inv.argv.index("--allowedTools") + 1:] == ["Read", "Glob", "Grep"]
    assert ClaudeCodeAdapter.readonly_capable is True
    assert not getattr(HermesAdapter, "readonly_capable", False) and not getattr(OpenClawAdapter, "readonly_capable", False)


def test_claude_spending_can_be_capped(tmp_path):
    inv = ClaudeCodeAdapter(max_budget_usd=2.5).build(tmp_path, "x", None)
    assert inv.argv[inv.argv.index("--max-budget-usd") + 1] == "2.5"
    for none in (None, 0, -1):
        assert "--max-budget-usd" not in ClaudeCodeAdapter(max_budget_usd=none).build(tmp_path, "x", None).argv
