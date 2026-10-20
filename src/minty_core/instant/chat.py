"""Small talk and questions about the assistant itself: greetings, thanks, who and what it is, "say that again"."""
from __future__ import annotations

import re

from minty_core.instant.base import Query, Rule

CATEGORY = "chat"

_HELLO = re.compile(r"^(?:hello|hi|hiya|hey there|hello there|hi there|howdy|greetings|yo|sup|hey)$")
_GREET_TIME = re.compile(r"^good (morning|afternoon|evening|day)$")
_GOOD_NIGHT = re.compile(r"^(?:good ?night|night night|nighty night|sleep well|sweet dreams)$")
_THANKS = re.compile(r"^(?:thanks|thank you|thank you so much|thanks so much|thanks a lot|thank you very much|many thanks|cheers|"
                     r"much appreciated|i appreciate it|appreciate it|thx|thank u|ta)(?: a lot| so much| very much| man| mate)?$")
_SORRY = re.compile(r"^(?:sorry|my bad|oops|my mistake|apologies)$")
_PRAISE = re.compile(r"^(?:good job|well done|nice one|nice work|great job|good work|nicely done|"
                     r"you are (?:awesome|great|amazing|the best|smart|brilliant|good)|love you|i love you)$")  # not "perfect": that can be a yes
_HOW_ARE_YOU = re.compile(r"^(?:how are you(?: doing)?(?: today)?|how is it going|how are things|how do you do|what is up|"
                          r"how is your day(?: going)?|how have you been|you good|are you ok(?:ay)?)$")
_WHO = re.compile(r"^(?:who are you|what are you|what is your name|what are you called|who is this|introduce yourself|"
                  r"tell me about yourself|what should i call you|what do i call you|who am i talking to|your name)$")
_CAN_DO = re.compile(r"^(?:what can you do|what do you do|help|help me|what are your features|what can i ask you|"
                     r"what can i ask|what can i say|what are your commands|what commands do you know|what are you able to do|"
                     r"what are you good at|how do you work)$")
_MAKER = re.compile(r"^(?:who (?:made|created|built|wrote|designed|programmed|developed) you|who is your "
                    r"(?:creator|maker|developer|author|owner)|who built this)$")
_AI = re.compile(r"^(?:are you (?:a )?(?:real|human|robot|ai|bot|alive|a person|an ai|a machine|a computer|chatgpt|siri|alexa)|"
                 r"are you a real person)$")
_THERE = re.compile(r"^(?:are you (?:there|awake|listening|up|here|working)|(?:can you )?hear me|do you hear me|"
                    r"hello can you hear me|testing(?: testing)?(?: one two three| 1 2 3)?|test|mic check|check check|"
                    r"is this (?:on|working)|is the mic (?:on|working)|anyone there|you there)$")
_VERSION = re.compile(r"^(?:what version are you|which version are you(?: on| running)?|what is your version|version|"
                      r"what version is this|which version is this|what version am i (?:using|running)|what build are you)$")
_FORGET = re.compile(r"^(?:never ?mind|forget it|forget that|scratch that|ignore that|skip it|drop it|it does not matter|"
                     r"it is nothing|nothing mind)$")
_REPEAT = re.compile(r"^(?:say that again|say it again|repeat that|repeat|what did you say|come again|can you repeat that|"
                     r"repeat yourself|pardon|sorry what|what was that|i did not catch that|i missed that|one more time)$")
_ASKED = re.compile(r"^(?:what did i (?:just )?(?:ask|say)(?: you)?|what was my (?:last|previous) (?:question|request)|"
                    r"what was i asking)$")
_LANG = re.compile(r"^(?:do you speak (?:english|hindi)|what languages do you speak|which languages do you speak)$")


def hello(q: Query) -> str | None:
    if _HELLO.match(q.cmd):
        return q.pick("Hi.", "Hello.", "Hey. What do you need?", "Hi there. I'm listening.")
    m = _GREET_TIME.match(q.cmd)
    if m:
        return f"Good {m[1]}."
    if _GOOD_NIGHT.match(q.cmd):
        return q.pick("Good night. Sleep well.", "Good night.")
    return None


def thanks(q: Query) -> str | None:
    if _THANKS.match(q.cmd):
        return q.pick("You're welcome.", "Any time.", "Happy to help.", "No problem.")
    if _SORRY.match(q.cmd):
        return q.pick("No problem.", "That's fine.", "Don't worry about it.")
    if _PRAISE.match(q.cmd):
        return q.pick("Thank you. Happy to help.", "Thanks. Glad that worked.", "Thank you.")
    return None


def how_are_you(q: Query) -> str | None:
    if _HOW_ARE_YOU.match(q.cmd):
        return q.pick("Doing well, thanks. What can I do for you?", "All good here. What do you need?",
                      "Running smoothly. How about you?")
    return None


def who_are_you(q: Query) -> str | None:
    if _WHO.match(q.cmd):
        return f"I'm {q.env.name}, a voice assistant that runs on your computer. Ask me anything, or tell me what to do."
    if _MAKER.match(q.cmd):
        return f"I'm open source. The {q.env.name} contributors built me, and you can read all of my code."
    if _AI.match(q.cmd):
        return "I'm an AI assistant running on your computer, not a person."
    if _LANG.match(q.cmd):
        return "I understand and answer in English."
    return None


def what_can_you_do(q: Query) -> str | None:
    if _CAN_DO.match(q.cmd):
        return ("I can answer questions, set timers and reminders, take notes, control music and volume, browse the web and "
                "more. Quick things like the time, the date, maths, conversions, dice and system facts I answer instantly. "
                "Just ask.")
    return None


def are_you_there(q: Query) -> str | None:
    if _THERE.match(q.cmd):
        return q.pick("Yes, I'm here and I can hear you.", "I'm here. Loud and clear.", "Yes, I hear you.")
    return None


def version(q: Query) -> str | None:
    if _VERSION.match(q.cmd):
        stage = q.env.version.partition("-")[2].partition(".")[0]
        tail = f" It's an {stage} release." if stage == "alpha" else (f" It's a {stage} release." if stage else "")
        return f"I'm {q.env.name} version {q.env.version}.{tail}"
    return None


def never_mind(q: Query) -> str | None:
    return q.pick("Okay.", "Okay, forgotten.", "No problem.") if _FORGET.match(q.cmd) else None


def repeat(q: Query) -> str | None:
    if _REPEAT.match(q.cmd):
        return q.env.last_reply() or "I haven't said anything yet."
    if _ASKED.match(q.cmd):
        last = q.env.last_request()
        return f"You asked: {last}" if last else "You haven't asked me anything yet."
    return None


RULES = (
    Rule("greeting", CATEGORY, ("hello", "good morning", "good afternoon", "good evening", "good night", "hi there", "howdy"), hello),
    Rule("thanks", CATEGORY, ("thanks", "thank you so much", "thanks a lot", "cheers", "sorry", "good job", "well done",
                              "you are awesome", "i love you"), thanks),
    Rule("how are you", CATEGORY, ("how are you doing today", "how is it going", "what's up", "how's your day going"), how_are_you),
    Rule("who are you", CATEGORY, ("who are you", "what's your name", "introduce yourself", "who made you", "are you a robot",
                                   "who created you", "are you real", "do you speak english"), who_are_you),
    Rule("what can you do", CATEGORY, ("what can you do", "what can I ask you", "what are your features", "help"), what_can_you_do),
    Rule("are you there", CATEGORY, ("are you there", "can you hear me", "testing one two three", "mic check", "are you listening"),
         are_you_there),
    Rule("version", CATEGORY, ("what version are you", "which version are you running", "what's your version"), version),
    Rule("never mind", CATEGORY, ("never mind", "forget it", "forget that", "scratch that"), never_mind),
    Rule("repeat", CATEGORY, ("say that again", "repeat that", "what did you say", "what did I just ask", "come again"), repeat),
)
