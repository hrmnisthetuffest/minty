"""Keys never leave in a tool call; personal data is masked in traces (W1-11)."""
import json

from tests.safety_helpers import build, call, say
from minty_core.safety import SafetyKit
from minty_core.secretscan import HoneyTokens, SecretScanner
from minty_core.trace import Tracer, read_events

KEY = "gsk_" + "q1W2e3R4" * 6


def kit(tmp_path, notify=None, tracer=None, known=(), honey=()):
    def make(store):
        return SafetyKit(store, SecretScanner(known_values=lambda: list(known), honeytokens=lambda: list(honey)),
                         tracer=tracer, notify=notify)
    return make


async def test_a_stored_key_in_the_arguments_is_stopped_and_the_tool_does_not_run(tmp_path):
    agent, host, asked = build(tmp_path, {"web_post": (1, None)},
                               [[call("web_post", {"body": f"here is my key {KEY}"})]],
                               safety=kit(tmp_path, known=[KEY]))
    await say(agent, "post that")
    assert host.called == []
    result = next(m.content for m in agent.history if m.role == "tool")
    assert result.startswith("Blocked by a safety check") and "never leave" in result and KEY not in result


async def test_a_planted_fake_key_raises_the_alarm(tmp_path):
    ht = HoneyTokens(tmp_path / "ht.json")
    said, tracer = [], Tracer(tmp_path / "traces")
    agent, host, _ = build(tmp_path, {"web_post": (1, None)},
                           [[call("web_post", {"body": f"key {ht.tokens()[0]}"})]],
                           safety=kit(tmp_path, notify=said.append, tracer=tracer, honey=ht.tokens()))
    await say(agent, "post that")
    assert host.called == [] and any("planted fake key" in s for s in said)
    events = [e for e in read_events(tmp_path / "traces") if e["kind"] == "secret_blocked"]
    assert events and events[0]["alarm"] is True and ht.tokens()[0] not in json.dumps(events)


async def test_normal_calls_pass_and_the_check_can_be_switched_off(tmp_path):
    agent, host, _ = build(tmp_path, {"web_post": (1, None)}, [[call("web_post", {"body": "hello"})]],
                           safety=kit(tmp_path, known=[KEY]))
    await say(agent, "post hello")
    assert host.called == [("web_post", {"body": "hello"})]
    agent, host, _ = build(tmp_path / "off", {"web_post": (1, None)}, [[call("web_post", {"body": KEY})]],
                           settings={"safety": {"block_secret_egress": False}}, safety=kit(tmp_path, known=[KEY]))
    await say(agent, "post it")
    assert host.called


def test_personal_data_is_masked_in_the_traces_but_not_in_the_numbers(tmp_path):
    from minty_core.secretscan import mask_pii
    tracer = Tracer(tmp_path / "t", redactor=mask_pii)
    tracer.event("tool_call", name="mail_read", args={"from": "teacher@school.example", "n": 5},
                 note="call me on +91 98765 43210, order 402-5551234")
    event = read_events(tmp_path / "t")[0]
    assert event["args"] == {"from": "[email]", "n": 5}
    assert "[phone]" in event["note"] and "98765" not in event["note"] and "402-5551234" in event["note"]
