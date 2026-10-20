"""A pretend model that follows a fixed plan, for testing the harness and the agent loop without spending tokens."""
import json

from minty_core.agentloop import PLANNER_MARK, VERIFIER_MARK
from minty_core.llm.types import LLMError, TextDelta, ToolCall, ToolCallEvent


def ref_of(snapshot: str, label: str) -> str:
    for line in snapshot.splitlines():
        if label in line and line.startswith("["):
            return line[1:line.index("]")]
    raise LookupError(f"{label!r} not on the page")


class ScriptedRouter:
    """steps: callables(last_tool_result) -> ("tool", name, args) | ("say", text), one per executor turn.

    The planner and verifier roles are answered separately so they never use up executor steps:
    planner(user_text) -> str (default: not a plan, so the loop falls back to one step);
    verifier(user_text) -> bool | (bool, reason) | None (None: the verifier is down). Default: everything passes."""

    def __init__(self, steps, planner=None, verifier=None):
        self.steps, self.turn, self.tracer = list(steps), 0, None
        self.planner, self.verifier = planner, verifier
        self.calls = {"executor": 0, "planner": 0, "verifier": 0}
        self.executor_inputs: list[str] = []  # the user message each executor turn started from

    async def stream(self, messages, tools, sensitive=False, tier="fast", prefer=None):
        system = messages[0].content if messages and messages[0].role == "system" else ""
        user = messages[-1].content if messages else ""
        if system.startswith(PLANNER_MARK):
            role, reply = "planner", (self.planner(user) if self.planner else "")
        elif system.startswith(VERIFIER_MARK):
            role = "verifier"
            got = True if self.verifier is None else self.verifier(user)
            if got is None:
                self.calls[role] += 1
                raise LLMError("verifier model down")
            passed, reason = got if isinstance(got, tuple) else (got, "ok" if got else "not shown")
            reply = json.dumps({"passed": passed, "reason": reason})
        else:
            role, reply = "executor", None
        self.calls[role] += 1
        if self.tracer is not None:
            self.tracer.event("llm_call", provider="scripted", outcome="ok", chars_in=400, chars_out=40)
        if role != "executor":
            yield TextDelta(reply)
            return
        last = messages[-1].content if messages and messages[-1].role == "tool" else ""
        if messages and messages[-1].role == "user":
            self.executor_inputs.append(messages[-1].content)
        step = self.steps[min(self.turn, len(self.steps) - 1)]
        self.turn += 1
        kind, *rest = step(last)
        if kind == "tool":
            yield ToolCallEvent(ToolCall(str(self.turn), rest[0], rest[1]))
        else:
            yield TextDelta(rest[0])
