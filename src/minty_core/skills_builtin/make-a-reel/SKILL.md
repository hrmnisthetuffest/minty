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
2. **Get a background.** Use `background: auto`: a style is chosen for the day from the catalogue (the user's own Minecraft parkour and
   other gameplay, plus nature, city, satisfying, abstract and NASA space footage), a different style on different days and a clip that
   was not just shown. `studio_make action catalog` shows what there is; `shuffle` changes it now; `prefer` turns a style off or on,
   favours a clip or sets the mode. Ask for one style with `background: nature` (or space, city, satisfying, abstract, parkour).
   Parkour and other gameplay are only ever the user's own footage (the licence is `owned`): add it with `studio_make action add`
   (category parkour). To get more of the rest: `studio_make action discover` (NASA needs no key; Pexels and Pixabay use the user's free keys),
   and the credit is added for you. If the catalogue is empty a plain generated background is used and you say so. Do not rip clips or
   songs from video sites: they get posts muted or removed and put the account at risk.
3. **Voice and music.** `studio_make action voice` with the script, then `studio_make action video` with that voiceover, the
   background (`auto`, a style or an id), `music` as a mood (calm, upbeat, tense) and the script so the captions are burned in. The
   music is a plain generated bed. For a real song, the user adds a track they have the right to use, with its licence.
4. **Motion graphics.** For animated text, charts or title cards, do it with Claude Code: `studio_make action project` once to set up
   the folder, then `agents_start_task` there with the brief (the look, the length, the words). Its job is checked with the
   folder's own `check_video.py`, not by what it says. Then queue what it made: `studio_draft` takes the file straight from the
   project's `out/` folder (no need to bring it in by hand), and treats it as the user's own.
5. **Check and queue.** `studio_draft` checks the video (shape, audio, not black, length), the licences and the caption, adds required
   credits, and says whether the user's rules post it without asking. Write a caption with a few relevant hashtags and no links,
   no @mentions, no "follow for follow". It is labelled as AI-made unless it is real footage with a human voice.
6. **Post.** `studio_send` posts it: by itself only if the user switched automatic posting on and everything is clean; otherwise
   they are shown the post and decide. Report what really happened (posted, asked, failed, unknown), never more.
7. No growth tricks: no following, liking or commenting on other accounts, no buying followers. Followers come from posting good
   things regularly; later, `missions_review` can say what is working.
