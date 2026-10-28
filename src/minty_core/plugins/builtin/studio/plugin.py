import hashlib
import logging
import math
import re
import time
from datetime import date, datetime
from pathlib import Path

from minty_core.attention import Loop
from minty_core.plugins.host import Tool
from minty_core.safefetch import FetchFailed
from minty_core.studio import assets as assets_mod
from minty_core.studio import fetch, media, posts as posts_mod, voice as voice_mod
from minty_core.studio.assets import AssetError, AssetLibrary
from minty_core.studio.captions import chunk_text
from minty_core.studio.instagram import Instagram, InstagramError
from minty_core.studio.postpolicy import PostFacts, PostPolicy, decide
from minty_core.studio.posts import DuplicatePost, PostStore
from minty_core.worklessons import Lesson

log = logging.getLogger(__name__)
EARNED_AFTER = 5
DAY_S = 86400
ACTIONS = ("stock", "url", "add", "voice", "music", "video", "check", "library", "project")
NAME = re.compile(r"[^a-z0-9-]+")
SCRIPT = Path(__file__).resolve().parents[3] / "studio" / "check_video_script.py"
BRIEF = """# Minty studio

This folder is where videos and pictures for Minty's social posts are made. You are Claude Code, asked by Minty to make something here.

## Output
- A vertical video: 1080x1920 (9:16), H.264 video and AAC audio, 30 fps, 5 to 60 seconds, as an .mp4 in `out/`. Give it a clear name
  (`out/2026-10-28-parkour-fact.mp4`).
- Pictures for a carousel: 1080x1350 PNG in `out/` (Minty converts them to JPEG).
- Always include an audio track, even a quiet one: a silent video fails the check.

## What you can use
Python 3, ffmpeg and ffprobe. Draw frames with ffmpeg filters (drawtext, drawbox, overlay, zoompan, xfade) or write frames from
Python and encode them with ffmpeg. Take a bold sans font from `fc-match 'sans:bold'`. Make the motion intentional: easing, kinetic
text, a clear first second that makes people stay.

## Before you say you are done
Run `python3 check_video.py out`. It must print OK; if it lists problems, fix them and run it again. Do not report success if it fails.

## Rules
- Never post, upload or publish anything, and never touch accounts, tokens, keys, or files outside this folder. Minty does the posting,
  after the user has seen it.
- Use only material that is yours to use: what you generate here, files in this folder, or clips Minty gives you with a licence. No
  music or footage taken from other people.
- Do not delete files you did not make.
- Finish with exactly three short lines: Changed: (files), Tests: (what check_video.py printed), Left: (anything not done, or nothing).
"""


def _num(value, default, cast=float, low=0.0):
    if isinstance(value, bool):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return cast(n) if math.isfinite(n) and n >= low else default


def _words(text) -> tuple[str, ...]:
    return tuple(w.strip().lower() for w in re.split(r"[,;\n]", str(text or "")) if w.strip())


def register(ctx):
    settings = ctx.settings or {}
    root = Path(ctx.config_dir) / "studio"
    work = root / "work"
    lib = ctx.state.get("library")  # one open database each across reloads (every settings change reloads the plugins)
    if lib is None:
        lib = ctx.state["library"] = AssetLibrary(root / "library")
    store = ctx.state.get("posts")
    if store is None:
        store = ctx.state["posts"] = PostStore(root / "posts.sqlite")
    user_id = str(settings.get("ig_user_id") or "").strip()
    host = str(settings.get("ig_host") or "graph.instagram.com").strip()
    voice_name = str(settings.get("voice") or "am_michael").strip()
    public_dir, public_url = str(settings.get("public_dir") or "").strip(), str(settings.get("public_base_url") or "").strip().rstrip("/")
    mode = str(settings.get("post_mode") or "ask").strip().lower()
    policy = PostPolicy(mode="auto" if mode == "auto" else "ask", max_per_day=_num(settings.get("max_posts_per_day"), 1, int, 1),
                        min_gap_hours=_num(settings.get("min_gap_hours"), 6.0), blocked_words=_words(settings.get("blocked_words")) or PostPolicy().blocked_words)
    gate_mode: dict[int, str] = {}

    # ---- where files may be ---------------------------------------------------------------------
    def inside(path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def outside_ok(path: Path) -> str | None:
        """A file the user brings in must be a real file in their home folder, not in a hidden folder and not a link."""
        p = Path(path).expanduser()
        try:
            home = Path.home().resolve()
            resolved = p.resolve()
            resolved.relative_to(home)
        except (ValueError, OSError):
            home = None
        base = home if home is not None else Path("/")
        if home is None or p.is_symlink() or any(part.startswith(".") for part in resolved.relative_to(home).parts):
            return f"{p.name} is outside the places I may read from (your home folder, not hidden folders)"
        return None if resolved.is_file() else f"{p.name} is not a file"

    def ref(value: str, kinds=()) -> Path | None:
        """A file named by asset id, by the name of something made in the studio, or by a path inside the studio."""
        value = str(value or "").strip()
        if not value:
            return None
        if value.isdigit() and (a := lib.get(int(value))):
            return Path(a.path)
        for ext in (".wav", ".mp4", ".mp3"):
            if (work / f"{NAME.sub('-', value.lower())}{ext}").is_file():
                return work / f"{NAME.sub('-', value.lower())}{ext}"
        if value in assets_mod.KINDS and (a := lib.pick(value)):
            return Path(a.path)
        p = Path(value).expanduser()
        return p if p.is_file() and inside(p) else None

    project_dir = Path(str(settings.get("project_dir") or "~/Documents/project/minty-studio")).expanduser()

    def scaffold() -> str:
        """The folder where Claude Code makes the motion graphics: a brief, the check it must pass, and an output folder."""
        home = Path.home().resolve()
        try:
            rel = project_dir.resolve().relative_to(home)
        except ValueError:
            return "Error: the studio project folder must be inside your home folder."
        if project_dir.is_symlink() or any(part.startswith(".") for part in rel.parts):
            return "Error: the studio project folder must not be a link or inside a hidden folder."
        made = []
        for relative, text in (("CLAUDE.md", BRIEF), ("check_video.py", SCRIPT.read_text(encoding="utf-8")), (".minty/check", "python3 check_video.py out\n")):
            target = project_dir / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                made.append(relative)
        (project_dir / "out").mkdir(parents=True, exist_ok=True)
        if not made:
            return f"The studio project at {project_dir} is already set up; I kept your files."
        return (f"Studio project ready at {project_dir} (made: {', '.join(made)}). Ask Claude Code to work there; its job is checked "
                "with check_video.py. Its folder name for the agents tool is " + project_dir.name + ".")

    # ---- the media tool ---------------------------------------------------------------------------
    def kept(a) -> str:
        return f"Kept {a.kind} asset {a.id} ({a.license}): {a.path}"

    async def make(args):
        action = str(args.get("action") or "")
        work.mkdir(parents=True, exist_ok=True)
        try:
            if action == "stock":
                return kept(await fetch.fetch_stock(lib, str(args.get("query") or ""), ctx.secret("pexels_key") or "", str(args.get("kind") or "video")))
            if action == "url":
                return kept(await fetch.fetch_url(lib, str(args.get("url") or ""), str(args.get("kind") or "video"), args.get("license"),
                                                  source=str(args.get("source") or ""), attribution=str(args.get("credit") or "")))
            if action == "add":
                src = Path(str(args.get("path") or "")).expanduser()
                if (why := outside_ok(src)) and not inside(src):
                    return f"Error: {why}."
                return kept(lib.add(src, str(args.get("kind") or ""), args.get("license"), source=str(args.get("source") or ""),
                                    attribution=str(args.get("credit") or ""), note=str(args.get("note") or "")))
            if action == "voice":
                name = NAME.sub("-", str(args.get("name") or f"voice-{int(time.time())}").lower()).strip("-") or "voice"
                seconds = await voice_mod.voiceover(str(args.get("text") or ""), work / f"{name}.wav", voice=str(args.get("voice") or voice_name),
                                                    engine=ctx.services.get("studio_voice_engine"))
                lib.add(work / f"{name}.wav", "voice", "generated", source="spoken by the local voice from your script", note=name)
                return f"Voiceover '{name}' ready: {seconds:.1f} seconds."
            if action == "music":
                mood = str(args.get("mood") or "calm")
                name = NAME.sub("-", str(args.get("name") or f"bed-{mood}").lower()).strip("-")
                await media.music_bed(work / f"{name}.wav", _num(args.get("seconds"), 20.0, float, 1.0), mood)
                lib.add(work / f"{name}.wav", "music", "generated", source=f"a plain {mood} backing sound made here", note=name)
                return f"Music bed '{name}' ready (a simple generated backing sound, not a song)."
            if action == "video":
                return await video(args)
            if action == "check":
                path = ref(str(args.get("path") or ""))
                if path is None:
                    return "Error: I can only check files in the studio."
                return (await media.check_video(path)).summary()
            if action == "project":
                return scaffold()
            if action == "library":
                found = lib.list(str(args.get("kind") or "") or None)
                return "\n".join(f"{a.id}. {a.kind} [{a.license}] {a.path}" + (f" ({a.note})" if a.note else "") for a in found[:40]) or "The library is empty."
        except (AssetError, media.MediaError, ValueError) as e:
            return f"Error: {e}"
        return f"Error: action must be one of: {', '.join(ACTIONS)}."

    async def video(args) -> str:
        backgrounds = [ref(b) for b in str(args.get("background") or "").split(",") if b.strip()]
        if not backgrounds or any(b is None for b in backgrounds):
            return "Error: give a background clip: an asset id, or gameplay/video to pick one from the library."
        vo = ref(str(args.get("voiceover") or "")) if args.get("voiceover") else None
        if args.get("voiceover") and vo is None:
            return "Error: I could not find that voiceover; make one first with action voice."
        total = (await media.probe(vo)).duration + 0.6 if vo else _num(args.get("seconds"), 15.0, float, 1.0)
        music_ref, mood = str(args.get("music") or "").strip(), None
        music = None
        if music_ref in media.MOODS:
            mood, music = music_ref, work / f"bed-{music_ref}-{int(total)}.wav"
            await media.music_bed(music, total, mood)
            lib.add(music, "music", "generated", source=f"a plain {mood} backing sound made here", note=music.stem)
        elif music_ref:
            music = ref(music_ref)
            if music is None:
                return "Error: I could not find that music; use a mood (calm, upbeat, tense) or an asset id."
        name = NAME.sub("-", str(args.get("name") or f"reel-{int(time.time())}").lower()).strip("-") or "reel"
        caps = chunk_text(str(args.get("script") or ""), total)
        out = await media.compose_vertical(work / f"{name}.mp4", background=backgrounds, voiceover=vo, music=music, captions=caps,
                                           seconds=None if vo else total)
        parents = [*backgrounds, *([vo] if vo else []), *([music] if music else [])]
        lib.register_render(out, parents)
        report = await media.check_video(out)
        extra = f" Captions burned in: {len(caps)} chunks." if caps else " No captions."
        return f"Made {out}.{extra} {report.summary()}"

    # ---- drafting ---------------------------------------------------------------------------------
    def sha_of(files: list[Path]) -> str:
        h = hashlib.sha256()
        for f in files:
            h.update(hashlib.sha256(Path(f).read_bytes()).digest())
        return h.hexdigest()

    def facts_of(p) -> PostFacts:
        return PostFacts(p.kind, p.caption, p.problems, p.licensed)

    def judge(p):
        return decide(policy, facts_of(p), store.posted_today("auto"), store.hours_since_last_post())

    def verdict_text(d) -> str:
        return "Your rules let me post this without asking." if d.mode == "auto" else "I will ask you first: " + "; ".join(d.reasons) + "."

    async def draft(args):
        kind = str(args.get("kind") or "reel")
        raw = args.get("files") if isinstance(args.get("files"), list) else [args.get("file")]
        files = [ref(str(f)) if not str(f).startswith("/") else (Path(str(f)) if inside(Path(str(f))) and Path(str(f)).is_file() else None)
                 for f in raw if f]
        if kind not in ("reel", "carousel") or not files or any(f is None for f in files):
            return "Error: give a reel (one video) or a carousel (2 to 20 pictures), as files inside the studio."
        if (kind == "reel") != (len(files) == 1) or (kind == "carousel" and not 2 <= len(files) <= 20):
            return "Error: a reel is one video; a carousel is 2 to 20 pictures."
        problems: list[str] = []
        if kind == "reel":
            problems = (await media.check_video(files[0])).problems
        else:
            for f in files:
                try:
                    info = await media.probe(f)
                    if not info.has_video or info.width < 320:
                        problems.append(f"{f.name} is not a usable picture")
                except media.MediaError as e:
                    problems.append(str(e))
        licence_problems = lib.problems_for_posting(files)
        caption = " ".join(str(args.get("caption") or "").split("\n")) if False else str(args.get("caption") or "").strip()
        credits = lib.credits(files)
        if credits and not all(c in caption for c in credits):
            caption += "\n\nCredits: " + "; ".join(credits)
        candidate = posts_mod.Post(0, 0.0, kind, [str(f) for f in files], caption, posts_mod.DRAFTED, "", "", problems, not licence_problems, "", "", "", 0.0)
        d = judge(candidate)
        if d.mode == "refuse":
            return "Error: I won't queue this: " + "; ".join(d.reasons)
        try:
            p = store.add(kind=kind, files=[str(f) for f in files], caption=caption, content_hash=sha_of(files), problems=problems,
                          licensed=not licence_problems, ai=args.get("ai_generated") is not False)
        except DuplicatePost as e:
            return f"Error: {e}."
        note = f" ({'; '.join(licence_problems)})" if licence_problems else ""
        return f"Draft {p.id} ready: {p.kind}, caption: {p.caption[:200]}{note}\n{verdict_text(d)} Nothing is posted until studio_send {p.id}."

    # ---- sending ----------------------------------------------------------------------------------
    def _post_for(args):
        ident = str(args.get("id") or "")
        p = store.get(int(ident)) if ident.isdigit() else None
        return p, (judge(p) if p is not None else None)

    def send_risk(args) -> int:
        p, d = _post_for(args)
        m = d.mode if d is not None else "ask"
        if p is not None:
            gate_mode[p.id] = m
        return 1 if m == "auto" else 3

    def send_describe(args) -> str:
        p, d = _post_for(args)
        if p is None:
            return f"Post studio item {args.get('id')} to Instagram?"
        why = "" if d.mode == "auto" else f" (asking because {'; '.join(d.reasons)})"
        return f"Post this {p.kind} to Instagram now? Caption: {p.caption[:240]}{why}"

    def not_ready(args) -> str | None:
        if not user_id or not ctx.secret("ig_token"):
            return "set studio.ig_user_id and the key plugin.studio.ig_token (`minty set-key plugin.studio.ig_token`) first"
        return None

    def client():
        factory = ctx.services.get("studio_instagram")
        return factory(user_id, ctx.secret("ig_token"), host) if factory else Instagram(user_id, ctx.secret("ig_token"), host=host)

    async def host_pictures(p) -> list[str]:
        """Copies the pictures into the public folder as JPEG and checks each address answers, before anything is created."""
        if not public_dir or not public_url.startswith("https://"):
            raise ValueError("a carousel needs the settings studio.public_dir (a folder that is served on the internet) and "
                             "studio.public_base_url (its https address): Instagram fetches pictures by address")
        folder = Path(public_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        urls = []
        for i, f in enumerate(p.files):
            name = f"{p.id}-{i}-{hashlib.sha256(Path(f).read_bytes()).hexdigest()[:8]}.jpg"
            await media.to_jpeg(Path(f), folder / name)
            urls.append(f"{public_url}/{name}")
        for url in urls:
            try:
                got = await fetch.safe_fetch("HEAD", url, max_bytes=1000)
            except (FetchFailed, ValueError):
                got = None
            if got is None or got.status != 200 or not got.content_type.lower().startswith("image/"):
                raise ValueError(f"the pictures are not reachable from the internet at {public_url} (is the folder served?)")
        return urls

    async def send(args):
        p, d = _post_for(args)
        if p is None:
            return f"No post {args.get('id')}."
        said = {posts_mod.POSTED: f"Post {p.id} was already posted{(': ' + p.permalink) if p.permalink else ''}; nothing more was sent.",
                posts_mod.DISCARDED: f"Post {p.id} was discarded.",
                posts_mod.UNKNOWN: f"Post {p.id} might already be on Instagram (the connection was lost while posting). Check the account; "
                                   "I won't post it again. Discard it to clear it.",
                posts_mod.PUBLISHING: f"Post {p.id} is being posted right now."}
        if p.status in said:
            return said[p.status]
        if d.mode == "refuse":
            return "Error: I won't post this: " + "; ".join(d.reasons)
        at_gate = gate_mode.pop(p.id, None)
        if at_gate is None:
            return "Nothing was posted: a post has to pass the safety check first, and nothing has asked it about this one."
        auto = at_gate == "auto"
        if auto and d.mode != "auto":
            return "Nothing was posted: your rules changed since I checked them. Ask me again."
        urls: list[str] = []
        try:
            if p.kind == "carousel":
                urls = await host_pictures(p)
        except (ValueError, media.MediaError) as e:
            return f"Error: {e}."
        if not store.claim(p.id, "auto" if auto else "approved"):
            return f"Post {p.id} is already being handled; nothing more was sent."
        try:
            api = client()
            done = await (api.publish_reel(Path(p.files[0]), p.caption, ai_generated=p.ai) if p.kind == "reel"
                          else api.publish_carousel(urls, p.caption, ai_generated=p.ai))
        except InstagramError as e:
            if e.published is None:
                store.finish(p.id, posts_mod.UNKNOWN, error=str(e))
                return f"The post may or may not be on Instagram ({e}). Check the account before posting it again."
            store.finish(p.id, posts_mod.FAILED, error=str(e))
            return f"Error: it was not posted ({e})."
        except Exception as e:  # noqa: BLE001 — before anything reached Instagram: failed, not unknown
            store.finish(p.id, posts_mod.FAILED, error=type(e).__name__)
            return f"Error: it was not posted ({type(e).__name__})."
        store.finish(p.id, posts_mod.POSTED, media_id=done.media_id, permalink=done.permalink)
        how = "automatically, under your posting rules" if auto else "after your yes"
        return f"Posted {how}. {done.permalink or 'It is live (Instagram gave no link yet).'} Post {p.id}."

    async def posts_tool(args):
        action = str(args.get("action") or "list")
        if action == "discard":
            ident = str(args.get("id") or "")
            if not ident.isdigit() or store.get(int(ident)) is None:
                return f"No post {ident}."
            return f"Discarded post {ident}." if store.discard(int(ident)) else f"Post {ident} cannot be discarded now."
        found = store.recent(10)
        if not found:
            return "No posts yet."
        def line(x):
            how = f", {x.how}" if x.status == posts_mod.POSTED and x.how else ""
            err = f" ({x.error})" if x.error and x.status in (posts_mod.FAILED, posts_mod.UNKNOWN) else ""
            return f"#{x.id} {x.status}{how} {x.kind}: {x.caption[:60]}{err}" + (f" {x.permalink}" if x.permalink else "")
        return "\n".join(line(x) for x in found)

    # ---- for the rest of Minty ----------------------------------------------------------------------
    def attention() -> list:
        out, now = [], time.time()
        for p in store.recent(30):
            if p.status == posts_mod.UNKNOWN:
                out.append(Loop(f"studio:{p.id}:unknown", "studio", f"Post {p.id} may or may not be on Instagram. Check the account, then discard it or ask me to try again.", p.updated, "today"))
            elif p.status == posts_mod.FAILED:
                out.append(Loop(f"studio:{p.id}:failed", "studio", f"Post {p.id} was not posted ({p.error}). Say post {p.id} to try again.", p.updated, "today"))
            elif p.status == posts_mod.DRAFTED and now - p.created > DAY_S:
                out.append(Loop(f"studio:{p.id}:waiting", "studio", f"Draft {p.id} is still waiting for you to decide.", p.created, "later"))
        expires = str(settings.get("ig_token_expires") or "").strip()
        if expires:
            try:
                left = (datetime.strptime(expires, "%Y-%m-%d").date() - date.today()).days
            except ValueError:
                left = None
            if left is not None and left <= 7:
                text = ("The Instagram token has expired: posting will fail until you make a new one and run `minty set-key plugin.studio.ig_token`."
                        if left < 0 else f"The Instagram token runs out in {left} days: make a new one and run `minty set-key plugin.studio.ig_token`.")
                out.append(Loop("studio:token", "studio", text, now, "now" if left < 0 else "today"))
        return out

    def lessons() -> list:
        r = store.track_record()
        if mode == "auto" or r["approved_posts"] < EARNED_AFTER or r["bad"]:
            return []
        return [Lesson("studio:autopost", f"You said yes to {r['approved_posts']} posts and none of them went wrong",
                       "To let me post without asking (clean licences, a safe caption, within your daily cap), set studio.post_mode to auto "
                       "yourself. Until you do, every post is still asked.", "confirmed", r["approved_posts"])]

    ctx.services["studio_attention"], ctx.services["studio_lessons"] = attention, lessons

    def make_risk(args) -> int:
        return {"stock": 1, "url": 2, "add": 2, "project": 1}.get(str(args.get("action")), 0)

    s, i = {"type": "string"}, {"type": "integer"}
    return [
        Tool("make", "Make or collect video material. action: stock (Pexels clip, query), url/add (bring a file in, with its license), "
                     "voice (text), music (mood), video (background, voiceover, music, script, name), check (path), library, project.",
             {"type": "object", "properties": {"action": {"type": "string", "enum": list(ACTIONS)}, "query": s, "url": s, "path": s,
                                               "kind": s, "license": s, "source": s, "credit": s, "note": s, "text": s, "voice": s,
                                               "name": s, "mood": s, "seconds": {"type": "number"}, "background": s, "voiceover": s,
                                               "music": s, "script": s}, "required": ["action"]}, make, risk=make_risk),
        Tool("draft", "Queue a finished reel (one video) or carousel (2-20 pictures) for Instagram without posting it. The video is "
                      "checked, licences are checked and credits added. It says whether your rules post it without asking.",
             {"type": "object", "properties": {"kind": s, "files": {"type": "array", "items": s}, "caption": s,
                                               "ai_generated": {"type": "boolean"}}, "required": ["files", "caption"]}, draft),
        Tool("send", "Post a queued item to Instagram. Inside the user's posting rules it goes by itself; otherwise the user is asked "
                     "with the post shown.", {"type": "object", "properties": {"id": i}, "required": ["id"]}, send,
             risk=send_risk, describe=send_describe, blocked_when=not_ready, sensitive=True),
        Tool("posts", "What is queued or posted (action list) or throw away an unposted one (action discard, id).",
             {"type": "object", "properties": {"action": s, "id": i}}, posts_tool, risk=lambda a: 1 if a.get("action") == "discard" else 0),
    ]
