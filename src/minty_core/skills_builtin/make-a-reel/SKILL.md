---
name: make-a-reel
description: Make a short vertical video (a voiceover over a background clip, with music and captions, or motion graphics made by Claude Code) and queue it for Instagram. Use for reels, shorts, Minecraft parkour videos, facts videos and carousels.
allowed-tools: studio_make studio_draft studio_send studio_posts agents_start_task agents_task_status
metadata:
  keywords: reel reels video videos short shorts instagram insta post minecraft parkour voiceover captions carousel clip clips gameplay motion graphics tiktok followers content
---
# Make a reel

1. **Pick the idea and write the script.** Short, spoken, about 60 to 120 words for 30 to 45 seconds. Write it yourself; do not copy
   other people's posts or videos. Open with the hook in the first line.
2. **Get a background.** For Minecraft parkour or any gameplay look: the user's own footage (their recordings in the library
   `studio_make action library`, kind gameplay). If there are none, say so and ask them to record or add some with
   `studio_make action add` (license owned). Otherwise stock footage: `studio_make action stock` (free Pexels clips, with the
   credit added for you). Do not rip clips or songs from video sites: they get posts muted or removed and put the account at risk.
3. **Voice and music.** `studio_make action voice` with the script, then `studio_make action video` with that voiceover, the
   background (`gameplay` or an id), `music` as a mood (calm, upbeat, tense) and the script so the captions are burned in. The
   music is a plain generated bed. For a real song, the user adds a track they have the right to use, with its licence.
4. **Motion graphics.** For animated text, charts or title cards, do it with Claude Code: `studio_make action project` once to set up
   the folder, then `agents_start_task` there with the brief (the look, the length, the words). Its job is checked with the
   folder's own `check_video.py`, not by what it says.
5. **Check and queue.** `studio_draft` checks the video (shape, audio, not black, length), the licences and the caption, adds required
   credits, and says whether the user's rules post it without asking. Write a caption with a few relevant hashtags and no links,
   no @mentions, no "follow for follow". It is labelled as AI-made unless it is real footage with a human voice.
6. **Post.** `studio_send` posts it: by itself only if the user switched automatic posting on and everything is clean; otherwise
   they are shown the post and decide. Report what really happened (posted, asked, failed, unknown), never more.
7. No growth tricks: no following, liking or commenting on other accounts, no buying followers. Followers come from posting good
   things regularly; later, `missions_review` can say what is working.
