import re

from minty_core.brownout import capacity_reply
from minty_core.jobs import short

# Mode state lives on the agent in memory only: it resets to normal/non-private on restart.
_FILLER_PAIRS = (("hey", "minty"),)
_FILLER_WORDS = {"minty", "please"}

THINKING = {"force thinking mode", "thinking mode", "thinking mode on", "think mode", "deep mode"}
NORMAL = {"normal mode", "fast mode", "thinking mode off", "stop thinking mode"}
PRIVATE_ON = {"private mode", "private mode on"}
PRIVATE_OFF = {"private mode off"}
STATUS = {"what mode are you in", "which mode"}


def normalise(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("’", "'"))
    words = cleaned.split()
    while words:
        if tuple(words[:2]) in _FILLER_PAIRS:
            del words[:2]
        elif words[0] in _FILLER_WORDS:
            del words[:1]
        else:
            break
    return " ".join(words)


CAPACITY = re.compile(r"^(?:how much (?:capacity |quota )?(?:is |do you have )?left|how many calls (?:are |do you "
                      r"have )?left|how much quota (?:is |do you have )?left|capacity left|quota left)$")

WHERE = re.compile(r"^(?:where did (?:that|it|my (?:last )?(?:request|question)|that (?:request|question)) go|"
                   r"which (?:model|provider)s? saw (?:that|it)|who saw (?:that|it))$")

RECEIPT = re.compile(r"^(?:how many tokens (?:was that|did that (?:use|take|cost))|token receipt|"
                     r"how much did that cost)$")

EXPLAIN = re.compile(r"^(?:explain (?:this|that|the) permission|why (?:are you|did you) ask(?:ing)?(?: me)?"
                     r"(?: that| this)?|why do you need (?:my )?permission|what is this permission)$")
REVIEW = re.compile(r"^(?:permission review|review (?:my |your )?permissions|weekly permission review)$")
DESTINATIONS = re.compile(r"^(?:where did (?:the )?(?:plugins?|you) (?:connect|go)(?: to)?|what (?:servers|sites|hosts) "
                          r"(?:have|did) (?:you|the plugins?) (?:contacted?|connected? to|talked? to))$")
DESTINATIONS_OF = re.compile(r"^which (?:sites|servers|hosts) (?:did|has|have) (?:the )?([a-z_ ]+?) "
                             r"(?:contact|contacted|connect to|connected to|talk to|talked to)$")
WHY = re.compile(r"^(?:why did you (?:do|run|make) (?:that|it|this)|why (?:did you|have you) just (?:do|done) "
                 r"(?:that|it)|what did you just do|explain what you just did|why was that done)$")
TODAY = re.compile(r"^(?:what have you (?:changed|done) today|what did you (?:change|do) today|"
                   r"what changes (?:did|have) you made today)$")
ENGAGE_LOCKDOWN = {"lockdown", "lock down", "lock everything down", "lock it down", "emergency stop",
                   "emergency lockdown", "stop everything and lock down"}
WAITING = re.compile(r"^(?:what(?: is|s)? (?:needs|waiting for|pending for|waiting on) (?:my )?approval|"
                     r"what(?: is|s)? waiting for (?:my )?approval|(?:any|which|show (?:me )?(?:the )?) ?"
                     r"(?:pending |waiting )?approvals?(?: pending| waiting)?|anything (?:pending|waiting) "
                     r"for (?:my )?approval)$")
APPROVE = re.compile(r"^(?:approve|allow) (?:it|that|this|(?:the )?(?:last|latest|first|only) one|"
                     r"(?:number|request|approval) (\d+))$")
DENY = re.compile(r"^(?:deny|reject|decline|refuse) (?:it|that|this|(?:the )?(?:last|latest|first|only) one|"
                  r"(?:number|request|approval) (\d+))$")

RESTART_APP = {"restart minty", "restart yourself", "reload yourself", "reboot", "restart the app",
               "restart the assistant", "reload minty", "reload"}
RESTART = {"restart", "reset", "start over", "new conversation", "new chat", "fresh start",
           "restart conversation", "reset conversation", "clear the conversation", "begin again"}
# "Stop listening": end the conversation and go back to waiting for "Minty". (Not the program shutting down.)
SLEEP = {"shut down", "shutdown", "stop listening", "go to sleep", "sleep", "go to sleep mode", "that's all",
         "that is all", "that's it", "that will be all", "goodbye", "good bye", "bye", "bye bye", "stop",
         "never mind", "nevermind", "nothing", "dismissed", "you can go", "go away", "be quiet", "quiet",
         "shut up", "stand by", "standby", "thanks that's all", "thank you that's all", "okay bye", "ok bye"}
_NEGATIONS = {"don't", "dont", "do", "not", "never", "no"}


def is_sleep(phrase: str) -> bool:
    """The whole utterance is a dismissal, or a short one that ends with one ('nothing shut down', 'ok bye')."""
    if phrase in SLEEP:
        return True
    words = phrase.split()
    if not words or len(words) > 5 or _NEGATIONS & set(words[:-2] if len(words) > 2 else words[:-1]):
        return False
    return " ".join(words[-2:]) in SLEEP or (words[-1] in SLEEP and words[-1] not in {"stop", "quiet", "nothing"})


LIVE_ON = {"live mode", "live mode on", "voice to voice mode", "classic mode off"}
CLASSIC = {"classic mode", "live mode off", "classic mode on", "normal voice mode"}

# "switch to live mode", "can you go to classic", "turn off live mode", "put it in thinking mode", "use classic"
_MODE_WORDS = {"live", "classic", "thinking", "normal", "private", "fast", "deep", "voice to voice", "frugal",
               "privacy"}
FRUGAL_ON = {"frugal mode", "frugal mode on", "save tokens", "token saving mode", "token saving mode on"}
FRUGAL_OFF = {"frugal mode off", "stop saving tokens", "token saving mode off"}
_MODE_LEAD = r"^(?:(?:ok|okay|now|so|hey|please|(?:can|could|would|will) you|i want (?:you )?to|i'd like to|let's|lets) )*"
_TURN = re.compile(_MODE_LEAD + r"(?:turn|switch|set|put) (on|off) (?:the )?(.+?)(?: please| now)?$")
_GO = re.compile(_MODE_LEAD + r"(?:(?:switch|go|change|move|put|set|turn|swap|get|enable|activate|start|use)"
                 r"(?: it| yourself| me| us| minty)?(?: back| over)?(?: (?:to|into|in|on))?) (?:the )?(.+?)(?: please| now)?$")


def mode_phrase(phrase: str) -> str:
    """Rewrite how people ask for a mode into the plain phrase the mode sets know:
    'switch to live mode' / 'go to classic' -> 'live mode', 'turn off live mode' -> 'live mode off'."""
    m = _TURN.match(phrase)
    if m:
        state, rest = m.group(1), m.group(2)
        word = rest.removesuffix(" mode").strip()
        if word in _MODE_WORDS:
            return f"{word} mode {state}"
    m = _GO.match(phrase)
    if m:
        word = m.group(1).strip().removesuffix(" mode").strip()
        if word not in _MODE_WORDS:
            word = word.removesuffix(" voice").strip()  # "classic voice"
        if word in _MODE_WORDS:
            return f"{word} mode"
    return phrase


def voice_mode_for(text: str) -> str | None:
    """'live' or 'classic' when the whole utterance asks for that voice mode, else None."""
    phrase = mode_phrase(normalise(text))
    if phrase in LIVE_ON:
        return "live"
    if phrase in CLASSIC:
        return "classic"
    return None

JOB_STATUS = {"what are you doing", "what are you working on", "how's it going", "how is it going", "any updates",
              "any update", "what's the status", "job status", "what's happening", "are you done", "are you done yet",
              "status update", "what's going on", "how far are you"}
CANCEL_ONE = {"cancel that", "stop that", "cancel the job", "stop the job", "cancel it", "stop it", "abort",
              "abort that", "cancel", "forget it", "forget that"}
CANCEL_ALL = {"cancel everything", "stop everything", "cancel all jobs", "stop all jobs", "cancel all", "stop all",
              "stop all of it", "cancel all of it"}
QUICK_ONLY = {"just the quick answer", "quick answers only", "just quick answers", "no need to think more",
              "don't think more", "dont think more", "just answer quickly"}
THINK_MORE = {"you can think more again", "think more again", "deep answers again", "follow up again"}
SOFT_STOP = {"stop", "never mind", "nevermind"}  # cancel work if there is any, otherwise end the conversation
RESUME_LIST = {"what can you resume", "what can you carry on with", "what jobs can you resume",
               "which jobs can you resume", "anything to resume"}
RESUME_ONE = {"resume it", "resume that", "resume the job", "carry on with it", "carry on with the job",
              "resume the interrupted job", "carry on"}
_RESUME_ID = re.compile(r"^(?:resume|continue|carry on with)(?: the)? (?:job|task|helper|browser job) (\d+)$")
_CANCEL_ID = re.compile(r"^(?:cancel|stop|abort)(?: the)? (?:job|task|helper|browser job) (\d+)$")

# "switch to gemini", "change the model to kimi", "use groq instead"
SWITCH = re.compile(r"^(?:(?:can|could|would|will) you |i want (?:you )?to |i'd like to |let's |lets |ok |okay |now )*"
                    r"(?:(?:switch|change|swap|move|go)(?: back)?(?: the)?(?: model| brain| ai)?(?: back| over)? to|use|"
                    r"talk to|go with)(?: the)? (.+?)(?: model| ai| instead| please| now)*$")
AUTO = {"auto", "automatic", "default", "any", "auto mode", "automatic mode", "the default"}
WHICH_MODEL = {"which model", "what model", "which model are you", "what model are you", "which model is this",
               "what model is this", "which model are you using", "what model are you using", "list models",
               "list the models", "what models do you have", "which models do you have", "which ai are you",
               "what ai are you", "what's powering you", "what is powering you", "who is powering you"}


def choose_model(store, router, wanted: str) -> str | None:
    """Make a spoken model choice stick; the reply to say. None when `wanted` names no usable model.

    Picking a model in live mode also leaves live mode: live conversations only run on Gemini Live."""
    if wanted.strip() in AUTO:
        choice = None
    else:
        choice = router.find(wanted)
        if choice is None:
            return None
    data = store.config.model_dump(mode="json")
    data["preferred_provider"] = choice.name if choice else ""
    left_live = choice is not None and data["audio"]["voice_mode"] == "live"
    if left_live:
        data["audio"]["voice_mode"] = "classic"
    store.update(data)
    if choice is None:
        return "Okay, I'll pick the model automatically again."
    note = " I've switched to classic mode for that, since live mode only runs on Gemini Live." if left_live else ""
    return f"Switched to {choice.name}, {spoken_model(choice.model)}.{note}"


def spoken_model(model: str) -> str:
    """'moonshotai/kimi-k3' -> 'kimi k3': the part worth saying aloud."""
    return re.sub(r"[-_:/]+", " ", model.rsplit("/", 1)[-1]).replace(" free", "").strip()


class CommandRouter:
    """Turns whole-utterance mode commands into agent state changes. Anything else returns None."""

    def __init__(self, agent, store=None, restart: bool = False, prompts=None, router=None, jobs=None,
                 proactive=None, capacity=None, where=None, receipt=None, approvals=None,
                 lockdown=None, allow_unlock: bool = False, receipts=None, permissions=None, bootlog=None,
                 destinations=None, offline=None, config_changes=None, backup_now=None,
                 health=None, power=None, canary=None, health_data=None, coach=None, finance=None, focus_end=None,
                 butler=None, productivity=None):
        self._agent = agent
        self._canary = canary  # () -> Awaitable[str]: check every provider now (the router tests it)
        self._focus_end = focus_end  # () -> str: end focus mode and say so
        self._butler = butler  # (text) -> str | None: "add X to my chores", "where are my keys", "I'm leaving"
        self._productivity = productivity  # (text) -> str | None: "set an alarm for 6:30", "I did reading", "start recording"
        self._finance = finance  # finance_commands.FinanceCommands: "spent 200 on lunch", "categorize X as Y"
        self._coach = coach  # coach.commands.CoachCommands: "pause coaching", "why did you say that"
        self._health_data = health_data  # health.HealthCommands: "stop collecting my health data", "delete my ... data"
        self._power = power  # () -> str: battery and temperature in words
        self._health = health  # () -> str: the health digest in words
        self._backup_now = backup_now  # () -> str: make an encrypted backup now and say how it went
        self._config_changes = config_changes  # () -> str: what the last settings change was
        self._offline = offline  # offline.NetworkProbe: "go offline" / "go online" (for testing and travel)
        self._destinations = destinations  # (plugin | None) -> str
        self._bootlog = bootlog  # safemode.BootLog: "leave safe mode"
        self._permissions = permissions  # (kind: "explain" | "review") -> str
        self._receipts = receipts  # receipts.ReceiptStore: "why did you do that?"
        self._lockdown = lockdown  # lockdown.Lockdown: starting it is always allowed
        self._allow_unlock = allow_unlock  # only the voice pipeline can end it (never a chat message)
        self._approvals = approvals  # ApprovalStore: only the voice pipeline passes it (a chat message never approves)
        self._receipt = receipt  # () -> str: tokens the last request used (from the traces)
        self._where = where  # () -> str: which providers the last request went to (from the traces)
        self._capacity = capacity  # brownout.Brownout: "how much is left?"
        self._store = store  # needed for live/classic voice mode switching and the model pick
        self._router = router  # ProviderRouter: which models exist and which one answered last
        self._jobs = jobs  # JobRunner: background work the user can ask about or cancel
        self._proactive = proactive  # ProactiveControl: "stop the morning brief", "turn proactive on"
        self._prompts = prompts  # PromptLayers: fixed-reply custom commands ("say my name")
        self._restart_enabled = restart  # only the real voice pipeline can restart the process
        self.went_to_sleep = False  # set by 'shut down' / 'stop listening'; nothing else to do, the turn just ends
        self.pending_restart = False  # set when "restart minty" was said; the pipeline acts after speaking
        self.switch_voice: str | None = None  # "live"/"classic" after a voice-mode command; the pipeline carries on in it

    def _set_audio(self, key: str, value) -> None:
        data = self._store.config.model_dump(mode="json")
        data["audio"][key] = value
        self._store.update(data)

    def _set_privacy(self, on: bool) -> None:
        data = self._store.config.model_dump(mode="json")
        data["safety"]["privacy_mode"] = on
        self._store.update(data)

    def _lockdown_command(self, phrase: str) -> str | None:
        cfg = self._store.config.safety if self._store is not None else None
        engage = ENGAGE_LOCKDOWN | ({cfg.lockdown_phrase.lower()} if cfg else set())
        unlock = (cfg.unlock_phrase.lower() if cfg else "end lockdown")
        if phrase in engage:
            self._lockdown.engage("asked by voice or text")
            return "Lockdown. Everything is stopped, and nothing changes or goes out until you say end lockdown."
        if phrase == unlock and self._allow_unlock:
            return "Lockdown lifted." if self._lockdown.release("voice") else "Lockdown is not on."
        return None

    def _approval_command(self, phrase: str) -> str | None:
        approve, deny, waiting = APPROVE.match(phrase), DENY.match(phrase), WAITING.match(phrase)
        if not (approve or deny or waiting):
            return None
        pending = self._approvals.pending()
        if waiting:
            return self._waiting_reply(pending)
        match = approve or deny
        if match.group(1):  # "approve number 12"
            ident = int(match.group(1))
            if all(p.id != ident for p in pending):
                return "I can't find that one, or it was already decided."
        elif not pending:
            return "Nothing is waiting for your approval."
        elif len(pending) > 1:  # never guess which one a bare "approve it" means
            return "Which one? " + self._waiting_reply(pending)
        else:
            ident = pending[0].id
        done = self._approvals.decide(ident, bool(approve), via="voice")
        if not done:
            return "I can't find that one, or it was already decided."
        return "Approved." if approve else "Denied."

    @staticmethod
    def _waiting_reply(pending) -> str:
        if not pending:
            return "Nothing is waiting for your approval."
        names = {1: "One thing is", 2: "Two things are", 3: "Three things are"}
        head = names.get(len(pending), f"{len(pending)} things are") + " waiting:"
        return head + " " + " ".join(f"Number {p.id}: {p.question}" for p in pending[:3])

    def handle(self, text: str) -> str | None:
        phrase = mode_phrase(normalise(text))
        agent = self._agent
        if self._canary is not None and phrase in ("check the models", "check the providers", "run the canary",
                                                   "test the models", "are the models working"):
            return self._canary()
        if self._power is not None and phrase in ("how is the battery", "how's the battery", "battery level",
                                                  "how hot are you", "are you overheating", "power status"):
            return self._power()
        if self._health is not None and phrase in ("how are you doing", "how are you", "health report", "how healthy are you",
                                                   "are you okay", "are you ok", "system health", "health digest"):
            return self._health()
        if self._backup_now is not None and phrase in ("back up now", "backup now", "make a backup", "back yourself up",
                                                       "back up my memory"):
            return "Backup: " + self._backup_now() + "."
        if self._config_changes is not None and re.match(
                r"^(?:what changed in my settings|what did i change|what was the last settings change|"
                r"what settings changed)$", phrase):
            return self._config_changes()
        if self._destinations is not None:
            if phrase in ("what left the machine today", "what did you send out today", "what left this computer today",
                          "what have you sent out today"):
                return self._destinations("__today__")
            if DESTINATIONS.match(phrase):
                return self._destinations(None)
            m = DESTINATIONS_OF.match(phrase)
            if m:
                return self._destinations(m.group(1).strip().replace(" ", "_"))
        if self._permissions is not None:
            if EXPLAIN.match(phrase):
                return self._permissions("explain")
            if REVIEW.match(phrase):
                return self._permissions("review")
        if self._receipts is not None:
            from minty_core.receipts import today_reply, why_reply
            if WHY.match(phrase):
                return why_reply(self._receipts)
            if TODAY.match(phrase):
                return today_reply(self._receipts)
        if self._offline is not None:
            if phrase in ("go offline", "offline mode", "offline mode on", "work offline"):
                self._offline.forced = True
                return "Offline mode on. I'll only do what needs no internet."
            if phrase in ("go online", "offline mode off", "stop working offline"):
                self._offline.forced = None
                return "Back online."
        if self._lockdown is not None:
            reply = self._lockdown_command(phrase)
            if reply is not None:
                return reply
        if self._approvals is not None:
            reply = self._approval_command(phrase)
            if reply is not None:
                return reply
        if self._capacity is not None and CAPACITY.search(phrase):
            return capacity_reply(self._capacity.capacity())
        if self._where is not None and WHERE.search(phrase):
            return self._where()
        if self._receipt is not None and RECEIPT.search(phrase):
            return self._receipt()
        if self._prompts is not None:
            fixed = self._prompts.say_for(phrase)
            if fixed:
                return fixed
        if self._finance is not None:
            reply = self._finance.handle(text)
            if reply is not None:
                return reply
        if self._coach is not None:
            reply = self._coach.handle(text)
            if reply is not None:
                return reply
        if self._butler is not None:
            reply = self._butler(text)
            if reply is not None:
                return reply
        if self._productivity is not None:
            reply = self._productivity(text)
            if reply is not None:
                return reply
        if self._store is not None and phrase in ("turn on push to talk", "use push to talk", "push to talk on",
                                                  "turn push to talk on", "switch to push to talk"):
            self._set_audio("push_to_talk", True)
            return (f"Push to talk is on: hold {self._store.config.audio.ptt_key.upper()} (or the panel button) "
                    "to talk. I won't listen for my name until you turn it off.")
        if self._store is not None and phrase in ("turn off push to talk", "stop using push to talk",
                                                  "push to talk off", "turn push to talk off",
                                                  "switch to wake word", "listen for your name again"):
            self._set_audio("push_to_talk", False)
            return "Push to talk is off. Say my name and I'll listen."
        if phrase in ("end focus", "stop focus", "end focus mode", "stop focus mode", "i'm done focusing",
                      "im done focusing") and self._focus_end is not None:
            return self._focus_end()
        if self._health_data is not None:
            reply = self._health_data.handle(text)
            if reply is not None:
                return reply
        if self._proactive is not None:
            reply = self._proactive.handle(text)
            if reply is not None:
                return reply
        if self._jobs is not None:
            reply = self._job_command(phrase)
            if reply is not None:
                return reply
        if is_sleep(phrase):
            self.went_to_sleep = True
            return "Okay."
        if self._bootlog is not None and phrase in ("enter safe mode", "start safe mode", "restart in safe mode",
                                                    "go into safe mode", "safe mode on"):
            if not self._restart_enabled:
                return None
            self._bootlog.request_safe()
            self.pending_restart = True
            return "Restarting in safe mode, with only the basics. Say leave safe mode to come back."
        if self._bootlog is not None and phrase in ("leave safe mode", "exit safe mode", "end safe mode"):
            if not self._restart_enabled:
                return None
            self._bootlog.clear()
            self.pending_restart = True
            return "Leaving safe mode. Restarting with everything on."
        if self._restart_enabled and phrase in RESTART_APP:
            self.pending_restart = True
            return "Restarting. Back in a few seconds."
        if phrase in RESTART:
            agent.reset_conversation()
            return "Fresh start. Our last conversation is saved."
        if phrase in THINKING:
            agent.tier = "deep"
            return "Thinking mode on. I'll take longer but think harder."
        if phrase in NORMAL:
            agent.tier = "fast"
            return "Back to normal mode."
        if self._store is not None and phrase in ("privacy mode on", "privacy mode", "turn privacy mode on"):
            self._set_privacy(True)
            return "Privacy mode on. Nothing leaves this computer except to providers that don't train on it."
        if self._store is not None and phrase in ("privacy mode off", "turn privacy mode off"):
            self._set_privacy(False)
            return "Privacy mode off."
        if phrase in PRIVATE_ON:
            agent.force_private = True
            return "Private mode on. I'll only use providers that don't train on your data."
        if phrase in PRIVATE_OFF:
            agent.force_private = False
            return "Private mode off."
        if self._store is not None and phrase in (LIVE_ON | CLASSIC):
            data = self._store.config.model_dump(mode="json")
            mode = "live" if phrase in LIVE_ON else "classic"
            data["audio"]["voice_mode"] = mode
            self._store.update(data)
            self.switch_voice = mode
            return "Live mode on." if mode == "live" else "Classic mode on."
        if self._store is not None and phrase in (FRUGAL_ON | FRUGAL_OFF):
            data = self._store.config.model_dump(mode="json")
            data["frugal"]["level"] = "F1" if phrase in FRUGAL_ON else "F0"
            self._store.update(data)
            return ("Frugal mode on: shorter thinking and replies to save tokens." if phrase in FRUGAL_ON
                    else "Frugal mode off.")
        if phrase in QUICK_ONLY:
            agent.quick_only = True
            return "Okay, quick answers only."
        if phrase in THINK_MORE:
            agent.quick_only = False
            return "Okay, I'll follow up on hard questions again."
        if phrase in STATUS:
            tier = "thinking" if agent.tier == "deep" else "normal"
            return f"I'm in {tier} mode, and private mode is {'on' if agent.force_private else 'off'}."
        if self._router is not None and self._store is not None:
            return self._model_command(phrase)
        return None

    def _job_command(self, phrase: str) -> str | None:
        jobs = self._jobs
        if phrase in JOB_STATUS:
            return jobs.summary()
        match = _RESUME_ID.match(phrase)
        if match or phrase in RESUME_ONE or phrase in RESUME_LIST:
            can = jobs.resumable()
            if phrase in RESUME_LIST:
                if not can:
                    return "There's nothing to resume."
                return "I can resume: " + "; ".join(f"job {j.id}, {short(j.goal, 50)}" for j in can[:3]) + "."
            if match:
                job = jobs.resume(int(match.group(1)))
                return (f"Resuming {job.label()}." if job is not None
                        else f"I can't resume job {match.group(1)}.")
            if not can:
                return "There's nothing to resume."
            if len(can) > 1:
                return "Which one? " + "; ".join(f"job {j.id}, {short(j.goal, 40)}" for j in can[:3]) + "."
            job = jobs.resume(can[0].id)
            return f"Resuming {job.label()}." if job is not None else "I couldn't resume that."
        match = _CANCEL_ID.match(phrase)
        if match:
            job = jobs.get(int(match.group(1)))
            if job is None or not jobs.cancel_nowait(job.id):
                return f"There's no running job {match.group(1)}."
            return f"Stopped {job.label()}."
        if phrase in CANCEL_ALL:
            stopped = [j for j in jobs.running() if jobs.cancel_nowait(j.id)]
            if not stopped:
                return "There's nothing running."
            return f"Stopped {len(stopped)} job{'s' * (len(stopped) != 1)}."
        if phrase in CANCEL_ONE or phrase in SOFT_STOP:
            job = jobs.latest_active()
            if job is None:
                return None if phrase in SOFT_STOP else "There's nothing running."
            jobs.cancel_nowait(job.id)
            return f"Stopped {job.label()}."
        return None

    def _model_command(self, phrase: str) -> str | None:
        router, cfg = self._router, self._store.config
        names = ", ".join(c.name for c in router.usable())
        if phrase in WHICH_MODEL:
            if cfg.audio.voice_mode == "live":
                now = "Gemini Live is talking to you in live mode"
            elif router.active:
                now = f"{router.active[0]}, {spoken_model(router.active[1])}, answered last"
            else:
                now = "Nothing has answered yet"
            pick = f"you picked {cfg.preferred_provider}" if cfg.preferred_provider else "I'm choosing automatically"
            return f"{now}, and {pick}. I can switch to: {names}, or automatic."
        match = SWITCH.match(phrase)
        if match is None:
            return None
        wanted = match.group(1)
        reply = choose_model(self._store, router, wanted)
        if reply is None:
            # "use the browser to...", "switch to dark mode": not about models, so the agent handles it
            if not re.search(r"\bmodel|\bbrain\b|\bai\b", phrase):
                return None
            return f"I don't have a model called {wanted}. I can switch to: {names}, or automatic."
        return reply
