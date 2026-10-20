---
name: youtube-find-and-play
description: Find a video on YouTube and play it in the browser. Use when the user asks to watch, play or put on a video, a tutorial, a trailer or a clip on YouTube.
allowed-tools: browser_task
metadata:
  keywords: youtube video watch trailer tutorial clip channel
---
# YouTube: find and play

Use `browser_task` and say you are on it.

1. Goal template: "Open youtube.com. Search for '<what the user asked for>'. Open the best matching result: prefer
   the official channel and an exact title match, avoid compilations, shorts and ads. If a Skip button appears for an
   ad, press it. Make sure the video is playing and tell me its title and channel."
2. If the user wants audio only or music, still use YouTube here unless they named Spotify (then use the Spotify tools).
3. To pause or stop later, use `browser_task` with "pause the video" (or "close the video tab").
4. Do not sign in, subscribe, like or comment unless the user asked.
