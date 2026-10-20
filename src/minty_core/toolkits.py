"""Toolkits: send the model the tools a request needs, not all of them.

Tools are grouped by plugin into toolkits. A small core set is always offered; a keyword router (no model
call) adds the one to three toolkits the request is about, toolkits used in the last two turns stay on for
follow-ups (a toolkit with an open session, like a browser window, stays while it is open), and
`need_toolkit` is the escape hatch when the router guessed wrong (that is logged, so wrong
routes are measurable).
"""
from __future__ import annotations

import re
from typing import Callable

from minty_core.llm.types import ToolSpec

NEED_TOOLKIT = "need_toolkit"
CORE_TOOLS = (
    "clock_get_time", "memory_remember", "memory_recall", "web_search_search", "weather_get_weather", "timers_set_timer",
    "jobs_status", "jobs_cancel", "browser_task", "deep_think_think_in_background", "display_show",
)
# toolkit -> (plugins, what it is for, keywords). A plugin not listed here becomes its own toolkit.
KITS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "browser": (("browser",), "web pages, forms, shopping sites",
                "browse website web page open chrome click type form fill login sign scroll tab url link site "
                "google amazon youtube flipkart shop buy cart checkout order search video watch"),
    "music": (("spotify", "music_id"), "play music, what song is this",
              "play song songs music spotify playlist album artist track volume pause resume skip next previous "
              "shuffle shazam louder quieter listen"),
    "home": (("smart_home",), "lights, locks, devices",
             "light lights lamp fan switch brightness dim scene lock unlock door garage blinds cover home "
             "device devices sensor thermostat temperature bedroom kitchen"),
    "files": (("files",), "files in the workspace folder",
              "file files folder directory download upload save write read delete remove move rename document "
              "workspace pdf text notes.txt"),
    "code": (("agents", "subagents"), "coding agents and helpers",
             "code coding program project app build develop repo repository agent claude hermes openclaw "
             "subagent helper implement script"),
    "system": (("installer", "n8n", "apis", "prompts"), "installs, n8n, web APIs, your standing rules",
               "install uninstall package pip npm pipx workflow workflows n8n automation api apis register "
               "webhook instruction instructions command commands rule rules forget learn teach always never"),
    "notes": (("journal", "history"), "journal and past conversations",
              "journal diary entry entries note yesterday earlier last week history recall conversation "
              "talked said discussed remember when"),
    "school": (("school",), "homework, school notices, the school portal",
               "homework school notice notices circular circulars portal schoolpad assignment assignments "
               "teacher class due exam timetable"),
    "mail": (("mail",), "your e-mail (read only)",
             "mail email emails inbox unread message messages sender gmail letter received"),
    "research": (("research",), "research notes and the library",
                 "research investigate sources notes library study literature deep dive findings cited"),
    "learn": (("learn",), "routines Minty can remember how to do",
              "routine routines remember approve approved learned forget quarantine macro saved replay"),
    "briefing": (("briefing",), "a short brief of the day",
                 "briefing brief summary overview agenda morning today schedule whats"),
    "health": (("health",), "your own workouts, sleep, food and body data",
               "health workout workouts gym lifting training sleep slept weight weigh food calories protein "
               "nutrition steps hevy cronometer strava body fitness exercise sets reps"),
    "coach": (("coach",), "explains your training numbers: progress, plateaus, next session, protein, sleep",
              "coach coaching progress progressing plateau stuck deload warm warmup next session routine "
              "personal record pr max estimated protein maintenance readiness recover recovery sets reps volume"),
    "finance": (("finance",), "your own money: spending, budgets, savings goals, subscriptions, bills",
                "money spend spent spending budget budgets savings saving goal bill bills subscription subscriptions "
                "expense expenses income salary pocket rupees rs afford cost paid statement"),
    "study": (("study",), "homework plan, exams, flashcards, checked maths, summaries, practice papers, mistakes",
              "study homework plan planner exam exams revision revise flashcard flashcards quiz card cards tutor "
              "check solve equation summarise summary chapter rehearse practice paper past mistake mistakes drill "
              "timetable class bag notices"),
    "productivity": (("productivity",), "habits, people and birthdays, trips and packing, meals and shopping, alarms, "
                     "the day's plan, recording a lesson",
                     "habit habits streak birthday birthdays occasion person people trip packing pack meal meals "
                     "shopping groceries alarm wake snooze plan day wind summary record recording lecture meeting "
                     "mark moment"),
    "web": (("web",), "watch a price or page, read a link safely, read-later, downloads in quarantine, captions, tabs",
            "watch watcher price prices drop stock wishlist changes changed link links safe safely suspicious phishing "
            "later queue quarantine download downloads release captions transcript youtube session tabs restore "
            "clickable handover login dry"),
    "selfcheck": (("selfcheck",), "what happened lately, the weekly self-report, failure proposals, the daily tip and card",
                  "timeline happened report weekly week self failures failure proposals proposal approve tip card summary today "
                  "problems errors what went wrong"),
    "world": (("integrations", "mcp"), "your calendar, Home Assistant scenes and energy, sleep timer, weather alerts, devices on the "
              "network, n8n templates, MCP servers",
              "calendar agenda schedule event events meeting free busy scene scenes movie night energy electricity kwh sleep timer "
              "fade weather alert storm heat lan network devices router wifi n8n workflow template mcp server tool"),
    "creator": (("create",), "news brief, new arXiv papers, idea inbox, mind maps and slides, pictures, translation glossary, "
                "claim evidence, audio and video clips, lecture notes",
                "news headlines brief feed rss arxiv paper papers research idea ideas inbox mind map mindmap slides slide "
                "deck powerpoint presentation outline image picture draw generate translate translation glossary hindi "
                "punjabi claim fact evidence source sources clip trim merge podcast lecture notes transcript chapters"),
    "developer": (("dev",), "your code projects: repo summary, git digest, tests, CI, vulnerable packages, review, shell command "
                  "explainer, snippets, plugin kit, ideas",
                  "repo repository project onboarding digest commits test tests pytest failing ci workflow vulnerable "
                  "vulnerability cve dependencies dependency review branch explain command shell snippet snippets idea "
                  "issue plugin scaffold trends traces"),
    "linux": (("system",), "this Linux computer: updates, crashes, services, volume and outputs, brightness, USB, notifications",
               "linux system computer pc laptop update updates upgrade pacman apt crash crashed service services "
               "systemctl volume mute louder quieter speaker headphones output brightness dim night light lock sleep "
               "suspend usb drive eject mount notification notifications process processes cpu memory ram battery "
               "load slow script setup file delete safe"),
    "memory": (("memory",), "what I remember about you: list it, correct it, forget it, what I learned this week",
               "memory remember forget forgot correct audit wrong learned know facts erase delete about me contradiction"),
    "vault": (("vault",), "your notes folder: capture, find, decisions, bookmarks, inbox filing",
              "note notes vault brain bookmark bookmarks decision decisions decide glossary inbox wrote written "
              "saw seen topic resume daily evergreen pros cons weigh"),
    "butler": (("butler",), "chores, follow-ups, where you put things, documents and renewals",
               "chore chores rota waiting follow followup put left keys passport document documents renewal renews "
               "expires expiry leaving checklist list done tick"),
    "reminders": (("reminders",), "reminders for a time or date",
                  "remind reminder reminders remember alarm later tonight tomorrow morning snooze schedule"),
    "display": (("display",), "the Minty screen",
                "screen display show clear panel table list open url"),
}
# what a follow-up on an open page needs; the rest of the browser toolkit comes back with need_toolkit
BROWSER_ESSENTIALS = ("browser_open", "browser_look", "browser_read", "browser_click", "browser_type",
                      "browser_press", "browser_scroll", "browser_back", "browser_tabs")
ESSENTIALS = {"browser": BROWSER_ESSENTIALS}
_WORD = re.compile(r"[a-z0-9]+")
MAX_KITS = 3
STICKY_TURNS = 2


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall((text or "").lower().replace("'", ""))}


class ToolkitRouter:
    def __init__(self, plugins: Callable[[], dict[str, list[str]]], *, tracer=None,
                 core: tuple[str, ...] = CORE_TOOLS, live: Callable[[], set[str]] | None = None,
                 diet: Callable[[], bool] = lambda: True):
        """plugins() -> {plugin name: [its full tool names]} for the plugins that are on right now.
        live() -> toolkits with an open session (a browser window): they stay offered while it is open."""
        self._plugins, self._tracer, self._core, self._live = plugins, tracer, set(core), live
        self._diet = diet  # live toolkits offer only their essentials until need_toolkit asks for the rest
        self._turn = 0
        self._kw_cache: dict[tuple, set[str]] = {}  # (kit, tools) -> keyword stems; the tool set rarely changes
        self._used: dict[str, int] = {}  # toolkit -> turn it last ran a tool
        self._loaded: set[str] = set()  # asked for with need_toolkit
        self.last_selected: list[str] = []  # toolkits offered by the latest tool_names() call

    # -- structure --------------------------------------------------------------------------------
    def kits(self) -> dict[str, list[str]]:
        """toolkit name -> tool names, for what is enabled now (core tools are not in any toolkit)."""
        by_plugin = {p: [t for t in tools if t not in self._core] for p, tools in self._plugins().items()}
        out: dict[str, list[str]] = {}
        claimed: set[str] = set()
        for kit, (plugins, _what, _kw) in KITS.items():
            tools = [t for p in plugins for t in by_plugin.get(p, [])]
            claimed.update(p for p in plugins if p in by_plugin)
            if tools:
                out[kit] = tools
        for plugin, tools in by_plugin.items():
            if plugin not in claimed and tools:
                out[plugin] = tools
        return out

    def _keywords(self, kit: str, tools: list[str]) -> set[str]:
        key = (kit, tuple(tools))
        if key not in self._kw_cache:
            self._kw_cache[key] = self._build_keywords(kit, tools)
        return self._kw_cache[key]

    def _build_keywords(self, kit: str, tools: list[str]) -> set[str]:
        words = _stems(kit.replace("_", " "))
        if kit in KITS:
            words |= _stems(KITS[kit][2])
        for t in tools:
            words |= _stems(t.replace("_", " "))
        return words - {"get", "set", "list", "add", "start", "stop", "status", "task", "read", "show"}

    # -- selection --------------------------------------------------------------------------------
    def _mentioned(self, text: str, kits: dict[str, list[str]], strict: bool) -> list[str]:
        words = _stems(text)
        scored = []
        need = 2 if strict else 1  # a matched skill already says what the request is about: others must be clear
        for order, (kit, tools) in enumerate(kits.items()):
            score = len(words & self._keywords(kit, tools))
            if score >= need:
                scored.append((-score, order, kit))
        return [kit for _s, _o, kit in sorted(scored)][:MAX_KITS]

    def _live_kits(self) -> list[str]:
        if self._live is None:
            return []
        try:
            return sorted(self._live())
        except Exception:  # noqa: BLE001 — a broken session check must not break tool selection
            return []

    def select(self, text: str, extra: tuple[str, ...] = (), strict: bool = False) -> list[str]:
        kits = self.kits()
        chosen = self._mentioned(text, kits, strict)
        for kit in list(extra) + [k for k, turn in self._used.items() if self._turn - turn < STICKY_TURNS] \
                + sorted(self._loaded) + self._live_kits():
            if kit in kits and kit not in chosen:
                chosen.append(kit)
        return chosen

    def tool_names(self, text: str, extra: tuple[str, ...] = (), strict: bool = False) -> set[str]:
        kits = self.kits()
        names = set(self._core)
        self.last_selected = self.select(text, extra, strict)
        live = set(self._live_kits()) if self._diet() else set()
        for kit in self.last_selected:
            tools = kits.get(kit, [])
            if kit in live and kit in ESSENTIALS and kit not in self._loaded:
                tools = [t for t in tools if t in ESSENTIALS[kit]]
            names.update(tools)
        return names

    def spec(self) -> ToolSpec:
        kits = self.kits()
        listing = "; ".join(f"{k} ({KITS[k][1] if k in KITS else 'more tools'})" for k in kits)
        return ToolSpec(NEED_TOOLKIT, "Load more tools when you need one you were not given. Groups: " + listing + ".",
                        {"type": "object", "properties": {"name": {"type": "string", "enum": list(kits)}},
                         "required": ["name"]})

    # -- bookkeeping ------------------------------------------------------------------------------
    def next_turn(self) -> None:
        self._turn += 1

    def reset(self) -> None:
        self._used.clear()
        self._loaded.clear()

    def used(self, tool_name: str) -> None:
        kits = self.kits()
        kit = next((k for k, tools in kits.items() if tool_name in tools), None)
        if kit is None:  # a core tool (browser_task) still means its plugin's toolkit is in use
            plugin = next((p for p, tools in self._plugins().items() if tool_name in tools), None)
            kit = next((k for k, (plugins, _w, _kw) in KITS.items() if plugin in plugins), plugin)
        if kit in kits:
            self._used[kit] = self._turn

    def kit_of(self, tool_name: str) -> str | None:
        """The toolkit a tool belongs to (None for core tools and tools that are not on)."""
        return next((kit for kit, tools in self.kits().items() if tool_name in tools), None)

    def load(self, kit: str) -> bool:
        """Offer this toolkit from now on in this conversation (as need_toolkit does, without the trace)."""
        if kit not in self.kits():
            return False
        self._loaded.add(kit)
        return True

    def need(self, name: str, wanted_for: str = "") -> str:
        kits = self.kits()
        key = str(name or "").strip().lower()
        if key not in kits:
            return f"There is no tool group called {name!r}. Groups: {', '.join(kits) or 'none'}."
        if self._tracer is not None:  # what the router had picked, before this correction
            self._tracer.event("toolkit_miss", wanted=key, selected=self.select(wanted_for), request=wanted_for[:120])
        self._loaded.add(key)
        return f"Loaded {key}: {', '.join(t.split('_', 1)[-1] for t in kits[key])}. They are available now."
