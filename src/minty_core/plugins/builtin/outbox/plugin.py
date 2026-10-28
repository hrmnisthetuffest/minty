import asyncio
import logging
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from minty_core import mailpolicy, outbox
from minty_core.outbox import OutboxStore
from minty_core.plugins.host import Tool
from minty_core.secretscan import SecretScanner

log = logging.getLogger(__name__)
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SMTP_TIMEOUT_S = 30
NOT_SENT = (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
            smtplib.SMTPConnectError, smtplib.SMTPNotSupportedError, ConnectionRefusedError, socket.gaierror,
            ssl.SSLError)
DAY_S = 86400
EARNED_AFTER = 5  # yeses to replies to one person, with nothing going wrong, before independence is offered
DEFAULT_SIGNATURE = "(Sent automatically by Minty, my assistant.)"


def _positive(value, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default


def register(ctx):
    settings = ctx.settings or {}
    user = str(settings.get("user") or "").strip()
    host = str(settings.get("smtp_host") or "smtp.gmail.com").strip()
    security = str(settings.get("smtp_security") or "ssl").strip().lower()
    if security not in ("ssl", "starttls", "none") or (security == "none" and host not in LOOPBACK):
        log.warning("Ignoring outbox.smtp_security %r for %s: mail goes over an encrypted connection (ssl or starttls)",
                    security, host)
        security = "ssl"
    port = _positive(settings.get("smtp_port"), {"ssl": 465, "starttls": 587, "none": 25}[security])
    policy = mailpolicy.Policy(
        allow=mailpolicy.parse_list(settings.get("auto_reply_to")), never=mailpolicy.parse_list(settings.get("never_write_to")),
        max_per_day=_positive(settings.get("auto_max_per_day"), 5), max_chars=_positive(settings.get("auto_max_chars"), 800),
        sensitive=mailpolicy.parse_list(settings.get("auto_blocked_words")) or mailpolicy.DEFAULT_SENSITIVE,
        require_authenticated=settings.get("auto_require_authenticated") is not False)
    signature = DEFAULT_SIGNATURE if settings.get("auto_signature") is None else str(settings.get("auto_signature")).strip()

    store = ctx.state.get("store")  # one open database across reloads (every settings change reloads the plugins)
    if store is None:
        store = ctx.state["store"] = OutboxStore(ctx.config_dir / "outbox.sqlite")
    scanner = ctx.services.get("scanner") or SecretScanner()
    gate_mode: dict[int, str] = {}  # what the safety gate was told about each item when it last asked

    def has_secret(text: str) -> bool:
        return bool(scanner.scan(text))

    def facts(item) -> mailpolicy.Draft:
        auth = None if item.authenticated is None else bool(item.authenticated)
        return mailpolicy.Draft(item.recipients, item.subject, item.body, item.reply, auth, item.original_subject)

    def judge(item) -> mailpolicy.Decision:
        return mailpolicy.decide(policy, facts(item), store.sent_today("auto"), has_secret)

    def verdict_text(decision) -> str:
        if decision.mode == "auto":
            return "Your rules let me send this without asking."
        return "I will ask you first: " + "; ".join(decision.reasons) + "."

    def show(item) -> str:
        return f"To {', '.join(item.recipients)}, subject '{item.subject}': {item.body}"

    # ---- tools ---------------------------------------------------------------------------------
    async def draft(args):
        body = str(args.get("body") or "").replace("\x00", "").strip()
        follow_up = max(0, min(_positive(args.get("follow_up_days"), 0), 60)) if args.get("follow_up_days") else 0
        if args.get("reply_to") is not None:
            if args.get("to"):
                return "Error: in a reply the recipient is the person who wrote to you; leave 'to' out."
            lookup = ctx.services.get("mail_thread")
            if lookup is None:
                return "Error: replying needs the mail plugin switched on, so I can read the message I am answering."
            thread = await lookup(str(args["reply_to"]))
            if not thread:
                return f"Error: I could not find message {args['reply_to']} to answer."
            original = str(thread.get("subject") or "")
            subject = original if original.lower().startswith("re:") else f"Re: {original}".strip()
            auth = None if thread.get("reply_to_differs") else thread.get("authenticated")
            if thread.get("reply_to_differs"):
                auth = False  # the message asks for answers to go somewhere else: not a plain reply from that sender
            fields = dict(to=(str(thread["from_addr"]),), subject=subject, reply=True, authenticated=auth,
                          original_subject=original, in_reply_to=str(thread.get("message_id") or ""),
                          references=" ".join(x for x in (str(thread.get("references") or ""), str(thread.get("message_id") or "")) if x))
        else:
            to = tuple(a for a in mailpolicy.parse_list(str(args.get("to") or "")) if a)
            fields = dict(to=to, subject=str(args.get("subject") or "").strip(), reply=False, authenticated=None)
        candidate = outbox.Item(0, 0.0, ",".join(fields["to"]), fields["subject"], body, fields["reply"],
                                None if fields["authenticated"] is None else int(bool(fields["authenticated"])),
                                fields.get("original_subject", ""), "", "", follow_up, outbox.DRAFTED, "", "", "", 0.0)
        decision = judge(candidate)
        if decision.mode == "refuse":
            return "Error: I won't send this: " + "; ".join(decision.reasons) + "."
        item = store.add(body=body, follow_up_days=follow_up, **fields)
        return (f"Draft {item.id} ready. {show(item)}\n{verdict_text(decision)} Nothing is sent until outbox_send "
                f"{item.id}.")

    def _policy_for(args):
        item = store.get(int(args.get("id") or 0)) if str(args.get("id") or "").lstrip("-").isdigit() else None
        return item, (judge(item) if item is not None else None)

    def send_risk(args) -> int:
        item, decision = _policy_for(args)
        mode = decision.mode if decision is not None else "ask"
        if item is not None:
            gate_mode[item.id] = mode
        return 1 if mode == "auto" else 3  # the user's own rule makes a send routine; anything else is always a question

    def send_describe(args) -> str:
        item, decision = _policy_for(args)
        if item is None:
            return f"Send outbox item {args.get('id')}?"
        why = "" if decision.mode == "auto" else f" (asking because {'; '.join(decision.reasons)})"
        return f"Send this e-mail? {show(item)}{why}"

    def not_ready(args) -> str | None:
        if not user or not ctx.secret("password"):
            return "set outbox.user and the key plugin.outbox.password (`minty set-key plugin.outbox.password`) first"
        return None

    def build_message(item, auto: bool) -> EmailMessage:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = user, ", ".join(item.recipients), item.subject
        msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain=user.split("@")[-1] or None)
        if item.in_reply_to:
            msg["In-Reply-To"] = item.in_reply_to
            msg["References"] = item.references or item.in_reply_to
        msg.set_content(item.body + (f"\n\n{signature}" if auto and signature else ""))
        return msg

    def deliver(msg: EmailMessage) -> None:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_S, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S)
        with smtp:
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(user, ctx.secret("password"))
            smtp.send_message(msg)

    def remember_follow_up(item) -> str:
        provider = ctx.services.get("butler")
        if not item.follow_up_days or provider is None:
            return ""
        try:
            provider.butler().add("waiting", f"{', '.join(item.recipients)}: {item.subject}"[:120],
                                  f"in {item.follow_up_days} days", {"who": ", ".join(item.recipients)}, source="agent")
        except Exception as e:  # noqa: BLE001 — a missing reminder must not turn a sent message into an error
            log.warning("could not note the follow-up: %s", type(e).__name__)
            return " (I could not add the follow-up reminder.)"
        return f" I'll remind you to follow up in {item.follow_up_days} days."

    async def send(args):
        item, decision = _policy_for(args)
        if item is None:
            return f"No outbox item {args.get('id')}."
        notes = {outbox.SENT: f"Outbox item {item.id} was already sent to {', '.join(item.recipients)}; nothing more was sent.",
                 outbox.DISCARDED: f"Outbox item {item.id} was discarded.",
                 outbox.UNKNOWN: (f"Outbox item {item.id} might already have been delivered (the connection was lost while "
                                  "sending). Check your Sent folder; I won't send it again. Discard it to clear it."),
                 outbox.SENDING: f"Outbox item {item.id} is being sent right now."}
        if item.status in notes:
            return notes[item.status]
        if decision.mode == "refuse":
            return "Error: I won't send this: " + "; ".join(decision.reasons) + "."
        at_gate = gate_mode.pop(item.id, None)  # one safety check covers one send
        if at_gate is None:
            return "Nothing was sent: a send has to pass the safety check first, and nothing has asked it about this one."
        auto = at_gate == "auto"
        if auto and decision.mode != "auto":
            return "Nothing was sent: your rules changed since I checked them. Ask me again."
        if not store.claim(item.id, "auto" if auto else "approved"):
            return f"Outbox item {item.id} is already being handled; nothing more was sent."
        try:
            msg = build_message(item, auto)
            await asyncio.to_thread(deliver, msg)
        except NOT_SENT as e:
            store.finish(item.id, outbox.FAILED, error=f"{type(e).__name__}")
            return f"Error: the e-mail was not sent ({type(e).__name__}). Check the account settings, then ask again."
        except Exception as e:  # noqa: BLE001 — after the message may have left, the honest answer is "not sure"
            store.finish(item.id, outbox.UNKNOWN, error=f"{type(e).__name__}")
            return (f"The e-mail may or may not have been delivered ({type(e).__name__}). Check your Sent folder before "
                    "sending it again.")
        store.finish(item.id, outbox.SENT, message_id=str(msg["Message-ID"]))
        how = "automatically, under your auto-reply rule" if auto else "after your yes"
        return f"Sent to {', '.join(item.recipients)} {how}. Outbox item {item.id}.{remember_follow_up(item)}"

    async def list_(args):
        items = store.recent(10)
        if not items:
            return "The outbox is empty."
        def line(i):
            how = f", {i.how}" if i.status == outbox.SENT and i.how else ""
            err = f" ({i.error})" if i.error and i.status in (outbox.FAILED, outbox.UNKNOWN) else ""
            return f"#{i.id} {i.status}{how} -> {', '.join(i.recipients)}: {i.subject[:60]}{err}"
        return "\n".join(line(i) for i in items)

    async def discard(args):
        ident = args.get("id")
        if store.get(int(ident or 0)) is None:
            return f"No outbox item {ident}."
        return f"Discarded outbox item {ident}." if store.discard(int(ident)) else f"Outbox item {ident} cannot be discarded now."

    def attention() -> list:
        """What in the outbox needs the user: a message that may not have gone, one that failed, a draft left waiting."""
        from minty_core.attention import Loop

        now, out = time.time(), []
        for item in store.recent(30):
            to = ", ".join(item.recipients)
            if item.status == outbox.UNKNOWN:
                out.append(Loop(f"outbox:{item.id}:unknown", "outbox", f"The e-mail to {to} may not have gone out. Check "
                                f"your Sent folder, then discard outbox item {item.id} or ask me to send it.", item.updated, "today"))
            elif item.status == outbox.FAILED:
                out.append(Loop(f"outbox:{item.id}:failed", "outbox", f"The e-mail to {to} was not sent ({item.error}). "
                                f"Say send outbox item {item.id} to try again.", item.updated, "today"))
            elif item.status == outbox.DRAFTED and now - item.created > DAY_S:
                out.append(Loop(f"outbox:{item.id}:waiting", "outbox",
                                f"The draft to {to} ('{item.subject[:50]}') is still waiting for you to decide.", item.created, "later"))
        return out

    ctx.services["outbox_attention"] = attention

    def lessons() -> list:
        """Independence is earned: after enough yeses with nothing going wrong, offer (never grant) letting someone be answered
        without asking. Only the user can add them to auto_reply_to."""
        from minty_core.worklessons import Lesson

        out = []
        for addr, r in sorted(store.track_record().items()):
            if r["approved_replies"] < EARNED_AFTER or r["bad"] or mailpolicy._matches(addr, policy.allow) \
                    or mailpolicy._matches(addr, policy.never):
                continue
            out.append(Lesson(f"outbox:{addr}:autoreply",
                              f"You said yes to {r['approved_replies']} replies to {addr} and none of them went wrong",
                              f"To let me answer {addr} without asking, add it to the outbox setting auto_reply_to yourself. I "
                              "would still ask for anything long, with links, with a sensitive word, or from a sender I cannot check.",
                              "confirmed", r["approved_replies"]))
        return out

    ctx.services["outbox_lessons"] = lessons

    s, i = {"type": "string"}, {"type": "integer"}
    return [
        Tool("draft", "Write an e-mail into the outbox without sending it. reply_to = the number of a message in your mail "
                      "(the recipient is then the person who wrote, never anyone else); or to + subject for a new one. "
                      "follow_up_days adds a reminder to follow up.",
             {"type": "object", "properties": {"body": s, "reply_to": s, "to": s, "subject": s, "follow_up_days": i},
              "required": ["body"]}, draft),
        Tool("send", "Send an outbox item. Within the user's auto-reply rules it goes by itself; anything else asks the user "
                     "with the whole message shown.", {"type": "object", "properties": {"id": i}, "required": ["id"]}, send,
             risk=send_risk, describe=send_describe, blocked_when=not_ready, sensitive=True),
        Tool("list", "What is in the outbox: drafted, sent (and how), failed or unknown.", {"type": "object", "properties": {}}, list_),
        Tool("discard", "Throw away an unsent outbox item.", {"type": "object", "properties": {"id": i}, "required": ["id"]},
             discard, risk=1),
    ]
