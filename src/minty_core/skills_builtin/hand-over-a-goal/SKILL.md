---
name: hand-over-a-goal
description: Take a goal that needs several steps or some time and work on it in the background, then report what was done, what is stuck and what needs the user. Use for research, comparing, organising, checking or doing several things in a row.
allowed-tools: missions_start missions_answer missions_resume missions_report missions_review attention_review jobs_status
metadata:
  keywords: goal mission background overnight unattended research compare organise several steps finished stuck resume
---
# Hand over a goal

1. If the request is one quick action, just do it. If it needs several steps, tools or time, start a mission with
   `missions_start` and give the outcome wanted, not the steps. Add what makes it finished ("compare three options and tell me
   which to pick", "tidy the notes and list what you could not decide").
2. Say that it is running and that you will say when it is done, stuck, or needs them. Do not say it is finished.
3. When it needs an answer it stops with one question; pass the user's answer on with `missions_answer`. A mission that failed or
   was interrupted can be picked up with `missions_resume`; it keeps its plan and does not repeat what it already did.
4. `missions_report` is the proof: the plan, each action and what came back. Use it before telling the user what happened.
5. "What needs me?" is `attention_review`. "How is this going?" is `jobs_status`. "What could you do better?" is `missions_review`.
