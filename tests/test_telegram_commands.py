"""Quick-action keyboard, command menu from skills, capture commands (W3-04)."""
from types import SimpleNamespace

from tests.test_telegram_gateway import OWNER, STRANGER, build
from minty_core.telegram.commands import CORE_MENU, KEYBOARD, install, skill_command


class Jobs:
    def __init__(self):
        self.cancelled = 0

    def summary(self):
        return "Nothing running."

    async def cancel_all(self):
        self.cancelled += 1
        return [object(), object()]


class Lock:
    def __init__(self):
        self.reasons = []

    def engage(self, reason):
        self.reasons.append(reason)


def setup(tmp_path, **extra):
    gw, fake, store, pairing = build(tmp_path)
    calls = []

    async def host_call(name, args):
        calls.append((name, args))
        return "Saved."

    async def remind(text):
        calls.append(("remind", text))
        return "Okay, reminder set." if "in 20 minutes" in text else None

    skills = SimpleNamespace(skills=lambda: {"shop-search": SimpleNamespace(description="Find a product"),
                                             "status": SimpleNamespace(description="clashes with /status")})
    jobs, lock = Jobs(), Lock()
    menu = install(gw, host_call=host_call, jobs=jobs, lockdown=lock, remind=remind, skills=skills,
                   brief=extra.get("brief"), capacity=lambda: "About 60 percent of today's capacity is left.",
                   state=lambda: "listening")
    return gw, fake, calls, jobs, lock, menu


async def test_capture_commands_use_the_same_local_tools(tmp_path):
    gw, fake, calls, *_ = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "/note buy a notebook for chemistry"))
    await gw.handle_update(fake.say(OWNER, "/todo email the teacher"))
    await gw.handle_update(fake.say(OWNER, "/idea a pot-hole filler that glows"))
    await gw.handle_update(fake.say(OWNER, "/remind call dad in 20 minutes"))
    await gw.handle_update(fake.say(OWNER, "/remind call dad"))
    assert calls[:3] == [("journal_add_entry", {"text": "buy a notebook for chemistry"}),
                         ("journal_add_entry", {"text": "To do: email the teacher"}),
                         ("journal_add_entry", {"text": "Idea: a pot-hole filler that glows"})]
    assert calls[3] == ("remind", "remind me to call dad in 20 minutes")
    assert fake.texts(OWNER)[:4] == ["[laptop] Saved.", "[laptop] Saved.", "[laptop] Saved.",
                                      "[laptop] Okay, reminder set."]
    assert "I didn't catch when" in fake.texts(OWNER)[4]
    await gw.handle_update(fake.say(OWNER, "/note"))
    assert "Say what to save" in fake.texts(OWNER)[-1]


async def test_status_where_jobs_stop_and_lockdown(tmp_path):
    gw, fake, calls, jobs, lock, _ = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "/status"))
    await gw.handle_update(fake.say(OWNER, "/where"))
    await gw.handle_update(fake.say(OWNER, "/stop all"))
    await gw.handle_update(fake.say(OWNER, "/lockdown"))
    texts = fake.texts(OWNER)
    assert texts[0] == "[laptop] I'm listening on laptop. Nothing running. About 60 percent of today's capacity is left."
    assert texts[1] == "[laptop] This is Minty on laptop. Listening."
    assert texts[2] == "[laptop] Stopped 2 jobs." and jobs.cancelled == 1
    assert texts[3].startswith("[laptop] Lockdown.") and lock.reasons == ["asked from Telegram"]


async def test_strangers_cannot_use_any_command(tmp_path):
    gw, fake, calls, jobs, lock, _ = setup(tmp_path)
    for text in ("/stop", "/lockdown", "/note hello"):
        await gw.handle_update(fake.say(STRANGER, text))
    assert fake.sent == [] and calls == [] and jobs.cancelled == 0 and lock.reasons == []


async def test_the_menu_comes_from_the_commands_and_the_skills(tmp_path):
    gw, fake, calls, jobs, lock, menu = setup(tmp_path)
    names = [c for c, _ in menu]
    assert names[:len(CORE_MENU)] == [c for c, _ in CORE_MENU]
    assert "shop_search" in names and names.count("status") == 1  # a skill cannot shadow a core command
    await gw.api.set_commands(menu)
    assert any(c["command"] == "shop_search" for c in fake.commands)
    assert skill_command("Fill-Form From Profile!") == "fill_form_from_profile"
    await gw.handle_update(fake.say(OWNER, "/shop_search a desk lamp"))
    assert gw.agent.asked == ["Use the shop-search skill. a desk lamp"]


async def test_start_shows_the_quick_keyboard(tmp_path):
    gw, fake, *_ = setup(tmp_path)
    gw.keyboard = KEYBOARD
    await gw.handle_update(fake.say(OWNER, "/start"))
    assert fake.sent[0]["reply_markup"]["keyboard"][0][0]["text"] == "/status"
    assert fake.sent[0]["reply_markup"]["is_persistent"] is True


async def test_help_lists_the_commands(tmp_path):
    gw, fake, *_ = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "/help"))
    text = fake.texts(OWNER)[0]
    assert "/note" in text and "/lockdown" in text and "Or just write to me." in text
