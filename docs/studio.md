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

## Motion graphics with Claude Code

`studio_make project` sets up a folder (default `~/Documents/project/minty-studio`) with a brief for Claude Code, the rules
(1080x1920, with sound, never post anything, only material that is yours to use), and a check script it has to pass. Then ask:
"have Claude Code make a kinetic-text intro about X". Minty runs it through the coding-agent jobs: it is asked first, and the job
counts as done only when `check_video.py` passes on what it made, not when Claude says so. Claude Code can run ffmpeg, ffprobe and
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
- Tested only against pretend servers: Pexels and the Instagram Graph API (the steps follow Meta's documentation; some details,
  such as the reel size limits and how long tokens last, are not stated on the pages I read, so the check uses common Instagram
  limits). **Nothing has been published to a real account.** Expect the first real post to need a fix or two; keep posting set to ask.
- Not tested: Claude Code making motion graphics on this machine, and a full daily recipe over several days.
