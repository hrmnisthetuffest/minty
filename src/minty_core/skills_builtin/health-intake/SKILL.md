---
name: health-intake
description: Set up the user's health tracking: what to collect, their age, goals, equipment and limits. Use when they want to start tracking workouts, sleep, food or body data, or to change what is tracked.
allowed-tools: health_status health_set_profile health_consent
metadata:
  keywords: health tracking track workouts sleep food nutrition weight fitness gym profile goals set up setup start collecting consent
---
# Health tracking set-up

All of this stays on the laptop. Say that once, plainly.

1. Call `health_status` to see what is already on. Do not repeat what is set.
2. Ask which of these they want kept: workouts, sleep, food, body measures (weight), other activity (runs, rides).
   For each one they clearly say yes to, call `health_consent` with that domain. A maybe is a no; ask again later.
3. Ask, one question at a time, and save each answer with `health_set_profile`: `age`, `goals` (one short line),
   `equipment` (what they can train with), `constraints` (anything to avoid; their words, not a diagnosis).
4. Say how data gets in: Hevy (their API key with `minty-core set-key hevy`, then the Hevy switch in the panel),
   or export files sent to the Telegram bot (Cronometer daily summary, Strava archive `activities.csv`, a Health
   Connect export). Nothing is written to Hevy or any other service.
5. Say they can stop it any time with "stop collecting my health data", and that the data stays until they say
   "delete my <topic> data".

Do not give training or diet advice in this set-up, and do not comment on their weight or age.
