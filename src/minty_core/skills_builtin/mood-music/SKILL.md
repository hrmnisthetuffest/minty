---
name: mood-music
description: Turn a mood or activity ("something calm to study", "pump me up for the gym") into the right thing to ask Spotify for. Use when the music request names a feeling or an activity instead of a song or artist.
allowed-tools: spotify_play spotify_volume spotify_shuffle spotify_now_playing
metadata:
  keywords: mood feeling feel calm relaxing relax chill study focus concentrate workout gym energetic upbeat pump happy sad tired sleepy sleep cheer vibe something
---
# Music for a mood

The request names a mood or activity, not a song. Pick ONE search phrase from the table, say in one short sentence what you
chose, and call `spotify_play` with that phrase. Their own playlists are searched first, so a phrase like "focus" may land on
a playlist they already made. That is what they want.

| They want | Ask Spotify for |
|---|---|
| calm, relax, chill, unwind | lofi chill |
| study, focus, concentrate, homework | deep focus instrumental |
| gym, workout, run, pump up, energetic | workout hits |
| happy, cheer up, party, upbeat | feel good hits |
| sad, miss someone, rainy day | sad songs |
| sleep, tired, bedtime, wind down | sleep piano |
| morning, wake up | good morning playlist |
| driving, road trip | road trip songs |

Rules:
* If the mood is not in the table, use their own words as the phrase ("music for a rainy evening").
* Keep the volume where it is; do not change it unless they ask.
* If they say "sleep timer" or "stop in 30 minutes", that is a separate tool (`integrations_sleep`); start it after the music.
* If nothing plays, say so and offer one different phrase from the table, not a long list.
