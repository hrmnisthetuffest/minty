---
name: set-up-an-automation
description: Set up something that runs by itself every day or week (a daily reel, a weekly report, a morning check). Use when asked to automate, schedule, repeat or "do this every day".
allowed-tools: recipes_create recipes_list recipes_run_now recipes_manage studio_make attention_review
metadata:
  keywords: automate automation automatic automatically schedule every day daily weekly weekdays regularly repeat recurring routine routines each morning each evening nightly set up
---
# Set up an automation

1. Say back what you will set up in one sentence, then call `recipes_create`: a short name, the goal written as an outcome (what
   should exist at the end, and where it stops: for example "make one reel and queue it as a draft for my approval"), the time
   (HH:MM) and the days. The user is shown the whole plan and says yes or no; nothing is scheduled before that.
2. A recipe is a mission on a timer. It does not get extra rights: anything that sends or posts is still decided by the user's own
   rules and asked otherwise. Say so if they ask for "fully automatic": that is a setting only they can change (for posting,
   `studio.post_mode`; for e-mail, `outbox.auto_reply_to`), and it is better switched on after they have approved a few runs.
3. Try it once with `recipes_run_now` so the first real run holds no surprises.
4. Tell them plainly: it runs only while Minty is running, a run missed while the computer was off is skipped, it pauses itself
   after repeated failures and says why, and each run uses model time (and Claude Code time if it delegates).
5. `recipes_list` shows what is set up and how recent runs went; `recipes_manage` pauses, resumes or deletes. A paused recipe is
   on the attention list.
