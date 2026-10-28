# The studio: videos, posts and automations

Minty can make short videos (a voiceover over a background clip, with music and captions), get Claude Code to make motion graphics
for it, queue posts for Instagram, and run all of that on a schedule. This page says what it does, what you set up yourself, what it
will not do, and what has and has not been tested.

## Make a video

Say what you want: "make a 30 second Minecraft parkour video with a fun fact, voiceover and captions". Minty writes the script
and uses `studio_make`:

- **voice**: the script spoken by the local Kokoro voice (offline; run `scripts/install_voice.sh` once if it is not installed).
- **music**: a plain generated bed in a mood (calm, upbeat, tense). It is a soft backing sound, not a song. For a real track, add
  one you have the right to use (see licences below).
- **video**: a 9:16 video from a background clip (looped or cut to the voiceover), the voiceover over quiet music, and the script
  burned in as captions two or three words at a time. The timing is by length of text, not by listening, so it is close but not
  frame-exact.
- **check**: what is really in a file: shape, codecs, length, whether the sound is audible, whether the picture is black.

**Minecraft parkour and other gameplay.** Minty cannot play Minecraft. Record your own parkour footage (Mojang's guidelines allow
gameplay videos) and add it with `studio_make add` (licence `owned`, kind `gameplay`). From then on "make a parkour video" picks
a clip from your library and a random stretch of it.

**Stock footage.** `studio_make stock` fetches a free portrait clip from Pexels (you get a free key at pexels.com/api and run
`minty set-key plugin.studio.pexels_key`). The credit Pexels asks for is added to the caption automatically.

**Not built, on purpose.** No downloading songs or clips from YouTube and similar sites: it breaks their terms, and posts built on
other people's music get muted or taken down and put your account at risk. A link you give is accepted only with a licence you
state (`owned`, `cc0`, `cc-by` with a credit, `permission`).

## Background styles: a different look on different days
The background of a video comes from a catalogue of footage, each clip filed under a style: `parkour` and `gameplay` (your own
recordings, never fetched), and `nature`, `city`, `satisfying`, `abstract` and `space` (fetched from providers whose terms allow it).
`studio_make action video` with `background: auto` picks the style of the day, then a clip in it:

- A day keeps one style; the next day is a different one (never the same two days running while there is a choice) and every style comes
  round before one repeats. The order starts from a private starting point, so it is not the same as anyone else's. It is plain
  arithmetic on what was shown before, kept in the library's database, so a restart changes nothing. No language model is involved.
- Within a style a clip that was not just shown is picked (the last few are skipped while others are left), favourites more often.
- A style with fewer clips than `studio.min_clips_per_style` (3) is skipped for one with enough; if every style is thin they take turns anyway.
- `prefer` turns a style off, on, more or less (`category` and `choice`), favours or skips one clip (`asset` and `choice`), or sets the mode
  (`daily`, the default, or `each_video`). `shuffle` changes the style now. `catalog` shows what there is.
- Nothing in the catalogue: a plain animated colour background is made (once) and the result says so and how to get real footage.

Where footage comes from (`studio_make action discover`: a change to your library, so it is a class 1 action, asked in the careful
mode and run without asking in the default one; it is bounded as described below):

| Provider | Key | Terms (read 2026-10-28) | Credit |
|---|---|---|---|
| NASA Image and Video Library | none | generally not copyrighted in the US; items with third-party rights are marked, and any hit that looks like someone else's work is skipped; never imply NASA endorses anything | "Video: NASA" |
| Pexels | yours, free (`plugin.studio.pexels_key`) | free to use, commercial too; no redistribution of unaltered copies; its API asks for a prominent link to Pexels where content is shown | "Video by NAME on Pexels" |
| Pixabay | yours, free (`plugin.studio.pixabay_key`) | Pixabay Content Licence: free, no credit needed; no standalone redistribution; the API asks that results are cached 24 hours and forbids systematic mass downloads | none |
| Mixkit, game videos from video sites | | no API; its licence page could not be read; creators' gameplay is theirs | not used |

Each provider is asked at most once a day for each style (a failure waits six hours), at most `limit` clips are kept per run and a run
stops at 200 MB. Every answer is untrusted: an address must be on the provider's own hosts and goes through the same guarded fetcher as
everything else (public addresses only, redirects checked, size capped), the bytes must be real media, and the clip is cut to at most a
minute, made portrait and stripped of sound (a 55 MB NASA download is kept as about 4.5 MB). Fetched clips beyond
`studio.library_max_mb` (1500) are deleted, least recently shown first; your own are never deleted, and a deleted clip's licence record stays.
Set `studio.auto_discover` to let this happen by itself once a day in the quiet window (off by default: it downloads).

## Motion graphics with Claude Code

`studio_make project` sets up a folder (default `~/Documents/project/minty-studio`) with a brief for Claude Code, the rules
(1080x1920, with sound, never post anything, only material that is yours to use), and a check script it has to pass. Then ask:
"have Claude Code make a kinetic-text intro about X". Minty runs it through the coding-agent jobs: it is asked first (unless you set the agents
setting `auto_start`, which a daily unattended recipe needs), and the job counts as done only when `check_video.py` passes on what it
made, not when Claude says so. The check is the one the job started with: a job that edits `check_video.py` or `.minty/check` is
reported as failed, because passing a check it rewrote proves nothing. When it is done, `studio_draft` takes the file straight from the
project's `out/` folder and treats it as yours. Claude Code can run ffmpeg, ffprobe and
the other tools in its default list; for more (for example `Bash(npx:*)` for Remotion) add them in the agents setting
`claude_extra_tools` (a blanket allow, `rm`, `sudo`, `curl` and `wget` are never added).

## Licences

Every file in the library has a licence on record from a closed list: owned, generated (made here), cc0, pexels, pixabay, cc-by
(with a credit), permission. A video made from files with a clean record is clean; one made from anything else is not. A post made
from anything unclean is never automatic: it is shown to you. Bringing a file in with a licence is a statement, so it is asked each
time.

## Posting to Instagram

Minty publishes through Meta's official Graph API, never by pretending to be you in the app. You set this up once yourself:

1. An Instagram **professional** account (Business or Creator) connected to a Facebook Page.
2. A Meta developer app with the Instagram publishing permission, and an access token for your account. Put the token in with
   `minty set-key plugin.studio.ig_token`, the account id in `studio.ig_user_id`, and the date it runs out in
   `studio.ig_token_expires` so Minty warns you a week before. A parent may need to own the Meta app and token while you are under
   the age Meta allows for developer accounts; check Meta's terms (Instagram itself needs you to be 13).
3. For **carousels** only, Instagram fetches each picture from a public web address, so you also need a folder that is served on
   the internet: set `studio.public_dir` and `studio.public_base_url`. Minty converts the pictures to JPEG, puts them there and checks
   that each address answers before it creates anything. Reels need none of this: the video is uploaded directly.

`studio_draft` queues a reel or carousel (checks the video, the licences and the caption, adds required credits) and says what your
rules will do with it. `studio_send` posts it. **By default every post is shown to you first.** Automatic posting is a setting only
you can switch on (`studio.post_mode = auto`) and then it only covers a clean post: the video passed its checks, every file has a
clean licence, the caption has no link, no @mention and no sensitive word, under your daily cap (default 1) and a minimum gap
(default 6 hours). After you have approved five posts with nothing going wrong, `missions_review` offers to switch it on; it never
does it for you. Posts are labelled as AI-generated unless you say they are real footage.

**The token.** A long-lived Instagram token lasts 60 days. `studio_make refresh_token` (asked, because a credential changes) swaps
it for a fresh 60-day one, never showing it; Minty warns you a week before it would run out (from the date you give, or from the
last refresh). A token under 24 hours old cannot be refreshed yet, and a Facebook-login token is renewed in Meta's dashboard.

**What works.** `studio_posts insights` asks Instagram how posted ones did (once they are about a day old: reach, likes,
comments, shares, saves). Tag each draft (`tags`, for example `parkour,facts`) and, once there are three or more posts on each
side, `missions_review` says which kind reaches at least 1.5 times as many people, as evidence and a suggestion. The metric
names follow Meta's current documentation, which I could not read in full, so a metric Instagram does not offer is simply left out.

**Seeing what you approve.** The approval question shows the caption and why it is being asked. To also see the video, set
`studio.preview_to_phone` (default off): each draft is then sent to your paired Telegram chat (only from the studio folder, only a
real video or picture, not on a private day). **Unattended approvals.** A mission waiting for your yes (for example a daily recipe
that reached `studio_send`) waits `missions.approval_wait_s`, six hours by default, instead of the usual five minutes; if nobody
answers, the step is recorded as "no answer came in time", not as a no. During a lockdown no recipe starts, and a run missed to it
says so.

Every post is recorded and never goes out twice: drafted, posted (with its link, and whether automatically or after your yes),
failed, or **unknown** if the connection dropped after the post was requested (check the account; it is not repeated).

There is no following, liking or commenting on other accounts, and no buying followers: that breaks Instagram's rules and gets
accounts banned. Followers come from posting good things regularly. Meta limits API posting to 100 posts a day, far above this.

## Automations ("do this every day")

`recipes_create` sets up a goal on a schedule: "every day at 18:00 make a reel and queue it for my approval". Each run is a mission
with all its checks. It runs once a day, a run missed because the computer was off is skipped (not made at 3 am), and three failures
in a row pause it with the reason. Creating one is always asked with the whole plan shown. A recipe holds no permissions: whether
a post goes out without asking is the posting setting above, not something a recipe can grant itself. Setting up another automation
(a weekly report, a morning check) is the same conversation, no new code.

**What it costs.** Each run spends model time, and Claude Code time if it delegates. It runs only while Minty is running. Cap the
spending with the agents setting `claude_max_budget_usd` and `max_runs_per_day` on the recipe.

## What has and has not been tested

- Tested for real: the ffmpeg tools (on small generated clips), the checks, the offline voice, the licence ledger, the queue, the
  policy, the standalone check script, the recipes clock, and the plugin wiring.
- Tested only against pretend servers: Pexels and the Instagram Graph API (the steps follow Meta's documentation, version v26.0 by
  default and a setting; some details,
  such as the reel size limits and how long tokens last, are not stated on the pages I read, so the check uses common Instagram
  limits). **Nothing has been published to a real account.** Expect the first real post to need a fix or two; keep posting set to ask.
- Tested for real, once: a Claude Code job in a studio folder made a 4-second vertical video with animated text, ran the check, and
  Minty's verdict was "verified" (it cost a few cents). A recipe was run through the real app with a scripted model: it made a draft
  and posted nothing.
- Not tested: a recipe over several days with a real model, and anything against a real Instagram or Pexels account.
