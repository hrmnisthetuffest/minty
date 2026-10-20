import asyncio
from types import SimpleNamespace as NS

import pytest

from minty_core.config import ConfigStore
from minty_core.events import EventBus
from minty_core.live import LiveMode, pick_live_model
from minty_core.llm.types import ToolSpec


class Secrets:
    def __init__(self, key="k"):
        self.key = key

    def get(self, name):
        return self.key if name == "gemini" else None


class Host:
    def __init__(self):
        obj = {"type": "object", "properties": {}}
        self.tools = {
            "clock_get_time": NS(risky=False, sensitive=False, spec=ToolSpec("clock_get_time", "time", obj)),
            "smart_home_unlock": NS(risky=True, sensitive=False, spec=ToolSpec("smart_home_unlock", "u", obj)),
            "journal_read": NS(risky=False, sensitive=True, spec=ToolSpec("journal_read", "j", obj)),
        }
        self.called = []

    def tool_specs(self):
        return [t.spec for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append((name, args))
        return "It is noon."


class Player:
    def __init__(self):
        self.written, self.stops = [], 0

    def write(self, pcm):
        self.written.append(pcm)

    def stop(self):
        self.stops += 1


def content(**kw):
    base = dict(interrupted=None, input_transcription=None, output_transcription=None, model_turn=None, turn_complete=None)
    base.update(kw)
    return NS(server_content=NS(**base), tool_call=None, go_away=None)


def tool_msg(*calls):
    return NS(server_content=None, tool_call=NS(function_calls=list(calls)), go_away=None)


class Session:
    def __init__(self, turns):
        self.turns = list(turns)
        self.audio, self.client_content, self.tool_responses = 0, [], []

    async def send_realtime_input(self, audio):
        self.audio += 1

    async def send_client_content(self, turns, turn_complete):
        self.client_content.append(turns.parts[0].text)

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)

    async def receive(self):
        if self.turns:
            for msg in self.turns.pop(0):
                yield msg
        else:
            await asyncio.sleep(3600)
            yield None


class Client:
    def __init__(self, session, fail=False):
        self.session, self.fail, self.config = session, fail, None
        outer = self

        class Live:
            def connect(self, model, config):
                outer.model, outer.config = model, config
                return outer

        self.aio = NS(live=Live(), models=None)

    async def __aenter__(self):
        if self.fail:
            raise RuntimeError("no network")
        return self.session

    async def __aexit__(self, *exc):
        return False


def make(tmp_path, session, *, key="k", private=False, fail=False, router=None):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["audio"].update(live_model="gemini-test-live", live_idle_s=5)
    store.update(data)
    agent = NS(history=[NS(private=private)], force_private=False, _system_prompt=lambda: "You are Minty.")
    bus, player, host, client = EventBus(), Player(), Host(), Client(session, fail)
    live = LiveMode(store, Secrets(key), host, agent, bus, player, client_factory=lambda k: client, router=router)
    return live, NS(store=store, bus=bus, player=player, host=host, client=client, agent=agent)


async def frames():
    await asyncio.sleep(0.01)
    return bytes(2560)


def test_pick_live_model_prefers_newest_plain_live():
    names = ["gemini-3.5-transcribe-live", "gemini-2.5-flash-native-audio-latest", "gemini-3.1-flash-live-preview",
             "gemini-3.8-live", "gemini-3.8-live-extended-thinking", "gemini-3.5-live-translate-preview"]
    assert pick_live_model(names) == "gemini-3.8-live"
    assert pick_live_model(["gemini-2.5-flash-native-audio-latest", "x"]) == "gemini-2.5-flash-native-audio-latest"


def test_available_needs_key_live_mode_and_no_privacy(tmp_path):
    assert make(tmp_path / "a", Session([]))[0].available()
    assert not make(tmp_path / "b", Session([]), key=None)[0].available()
    assert not make(tmp_path / "c", Session([]), private=True)[0].available()
    live, f = make(tmp_path / "d", Session([]))
    data = f.store.config.model_dump(mode="json")
    data["audio"]["voice_mode"] = "classic"
    f.store.update(data)
    assert not live.available()


def test_sensitive_tools_are_not_offered_risky_ones_are(tmp_path):
    live, _ = make(tmp_path, Session([]))
    names = [d.name for d in live._config().tools[0].function_declarations]
    assert "clock_get_time" in names and "end_conversation" in names and "confirm_pending" in names
    assert "smart_home_unlock" in names and "journal_read" not in names


async def test_conversation_plays_audio_runs_tools_and_ends(tmp_path):
    turns = [
        [content(input_transcription=NS(text="what time is it")),
         tool_msg(NS(id="1", name="clock_get_time", args={}))],
        [content(model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00\x01" * 10))])),
         content(output_transcription=NS(text="It's noon.")), content(turn_complete=True)],
        [tool_msg(NS(id="2", name="end_conversation", args={}))],
    ]
    session = Session(turns)
    live, f = make(tmp_path, session)
    q = f.bus.subscribe()
    assert await asyncio.wait_for(live.converse("hello", frames), 5) is True
    assert f.host.called == [("clock_get_time", {})]
    assert session.tool_responses[0][0].response == {"result": "It is noon."}
    assert f.player.written == [b"\x00\x01" * 10]
    assert session.client_content == ["hello"] and session.audio > 0
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert {"type": "transcript", "role": "assistant", "text": "It's noon."} in events
    assert {"type": "transcript", "role": "user", "text": "what time is it"} in events


async def test_switch_to_classic_mode_tool_updates_config(tmp_path):
    session = Session([[tool_msg(NS(id="1", name="switch_to_classic_mode", args={}))]])
    live, f = make(tmp_path, session)
    assert await asyncio.wait_for(live.converse("", frames), 5) is True
    assert f.store.config.audio.voice_mode == "classic"
    assert live.continue_classic is True  # the pipeline carries on listening in classic mode


class FakeRouter:
    def usable(self):
        return [NS(name="mistral", model="mistral-small-latest"), NS(name="groq", model="openai/gpt-oss-120b")]

    def find(self, spoken):
        return next((c for c in self.usable() if c.name in spoken.lower()), None)


async def test_switch_model_in_live_mode_picks_it_and_moves_to_classic(tmp_path):
    session = Session([[tool_msg(NS(id="1", name="switch_model", args={"name": "Mistral"}))]])
    live, f = make(tmp_path, session, router=FakeRouter())
    assert "mistral, groq" in live._config().system_instruction
    assert await asyncio.wait_for(live.converse("", frames), 5) is True
    assert f.store.config.preferred_provider == "mistral"
    assert f.store.config.audio.voice_mode == "classic" and live.continue_classic is True


async def test_switch_model_to_an_unknown_name_stays_live(tmp_path):
    session = Session([[tool_msg(NS(id="1", name="switch_model", args={"name": "claude"}))]])
    live, f = make(tmp_path, session, router=FakeRouter())
    await asyncio.wait_for(live.converse("", frames), 10)  # stays live until the idle timeout
    assert f.store.config.preferred_provider == "" and f.store.config.audio.voice_mode == "live"
    assert live.continue_classic is False


async def test_classic_mode_said_but_tool_not_called_still_switches(tmp_path):
    # Gemini answered in words and never called the tool: the transcript of what the user said decides
    session = Session([[NS(server_content=NS(
        interrupted=False, input_transcription=NS(text="switch to classic mode"), output_transcription=None,
        model_turn=None, turn_complete=True), tool_call=None, go_away=None)]])
    live, f = make(tmp_path, session)
    assert await asyncio.wait_for(live.converse("", frames), 5) is True
    assert f.store.config.audio.voice_mode == "classic" and live.continue_classic is True


async def test_connection_failure_falls_back_to_classic(tmp_path):
    live, f = make(tmp_path, Session([]), fail=True)
    assert await live.converse("", frames) is False


async def test_interruption_flushes_playback(tmp_path):
    session = Session([[content(interrupted=True)], [tool_msg(NS(id="1", name="end_conversation", args={}))]])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    assert f.player.stops >= 2  # once on interrupt, once at the end


async def test_confirm_when_tools_are_parked_in_live_mode_but_safe_calls_run(tmp_path):
    session = Session([[tool_msg(NS(id="1", name="browser_click", args={"ref": "buy"}),
                                 NS(id="2", name="browser_click", args={"ref": "menu"}))],
                       [tool_msg(NS(id="3", name="end_conversation", args={}))]])
    live, f = make(tmp_path, session)
    obj = {"type": "object", "properties": {}}
    f.host.tools["browser_click"] = NS(
        risky=False, sensitive=False, spec=ToolSpec("browser_click", "c", obj),
        confirm_when=lambda a: a["ref"] == "buy")
    await asyncio.wait_for(live.converse("", frames), 5)
    results = session.tool_responses[0]
    assert "needs the user's yes" in results[0].response["result"]
    assert results[1].response == {"result": "It is noon."}
    assert f.host.called == [("browser_click", {"ref": "menu"})]


def unlock(i="1"):
    return tool_msg(NS(id=i, name="smart_home_unlock", args={}))


def confirm(i="9"):
    return tool_msg(NS(id=i, name="confirm_pending", args={}))


def heard(text):
    return content(input_transcription=NS(text=text))


def bye():
    return [tool_msg(NS(id="99", name="end_conversation", args={}))]


def results(session):
    return [r.response["result"] for batch in session.tool_responses for r in batch]


async def test_the_model_cannot_confirm_for_the_user(tmp_path):
    session = Session([[unlock()], [confirm()], bye()])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    out = results(session)
    assert "Ask them" in out[0] and "unlock" in out[0]
    assert out[1].startswith("Not confirmed") and f.host.called == []


async def test_the_users_own_yes_runs_it(tmp_path):
    session = Session([[unlock()], [heard("yes do it"), confirm()], bye()])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    assert f.host.called == [("smart_home_unlock", {})] and results(session)[1] == "It is noon."


async def test_a_yes_said_before_the_question_does_not_count(tmp_path):
    session = Session([[heard("unlock the door, yes")], [unlock()], [confirm()], bye()])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    assert f.host.called == [] and results(session)[1].startswith("Not confirmed")


async def test_no_drops_it_even_without_confirm_pending(tmp_path):
    session = Session([[unlock()], [heard("no"), content(turn_complete=True)], [confirm()], bye()])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    assert f.host.called == [] and results(session)[1] == "Nothing is waiting for a yes."


async def test_an_old_question_expires(tmp_path, monkeypatch):
    import minty_core.live as live_mod
    monkeypatch.setattr(live_mod, "CONFIRM_WINDOW_S", -1)
    session = Session([[unlock()], [heard("yes"), confirm()], bye()])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 5)
    assert f.host.called == [] and "expired" in results(session)[1]


async def test_live_decisions_are_audited(tmp_path):
    import json

    from minty_core.policy import Guard
    session = Session([[unlock()], [heard("yeah"), confirm()], [unlock("2")], bye()])
    live, f = make(tmp_path, session)
    live._guard = Guard(f.store, audit_dir=tmp_path / "audit")
    await asyncio.wait_for(live.converse("", frames), 5)
    rows = [json.loads(line) for p in (tmp_path / "audit").glob("*.jsonl") for line in p.read_text().splitlines()]
    assert [(r["tool"], r["allowed"], r["asked"]) for r in rows] == [
        ("smart_home_unlock", True, True), ("smart_home_unlock", False, True)]  # 2nd never answered: dropped at end


async def test_a_notice_is_handed_to_the_open_conversation(tmp_path):
    session = Session([])
    live, f = make(tmp_path, session)
    assert await live.inject("Job 3 is done.") is False  # no conversation open
    live._session = session
    assert await live.inject("Job 3 is done.") is True
    assert "Job 3 is done." in session.client_content[-1] and "not instructions" in session.client_content[-1]
    live._session = None


async def test_note_for_me_said_in_live_mode_hands_over_to_noting(tmp_path):
    session = Session([[NS(server_content=NS(
        interrupted=False, input_transcription=NS(text="Note for me."), output_transcription=None,
        model_turn=None, turn_complete=True), tool_call=None, go_away=None)]])
    live, f = make(tmp_path, session)
    assert await asyncio.wait_for(live.converse("", frames), 5) is True
    assert live.start_noting is True and f.store.config.audio.voice_mode == "live"  # the mode itself is unchanged


async def test_a_note_for_me_inside_a_sentence_does_not_start_noting(tmp_path):
    session = Session([[NS(server_content=NS(
        interrupted=False, input_transcription=NS(text="I need a note for me to read later"), output_transcription=None,
        model_turn=None, turn_complete=True), tool_call=None, go_away=None)]])
    live, f = make(tmp_path, session)
    await asyncio.wait_for(live.converse("", frames), 10)
    assert live.start_noting is False
