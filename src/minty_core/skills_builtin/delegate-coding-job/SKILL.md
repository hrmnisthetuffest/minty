---
name: delegate-coding-job
description: Hand a coding task to Claude Code (or another coding agent) on one of the user's projects and report what the files and tests show. Use when asked to have an agent build, fix, refactor or look into code.
allowed-tools: agents_list_projects agents_list_agents agents_start_task agents_start_project agents_task_status agents_stop_task jobs_status
metadata:
  keywords: claude code coding agent project repo repository implement build fix refactor tests failing bug feature delegate develop
---
# Delegate a coding job

1. Know which project: `agents_list_projects` if the user did not say. A new idea is `agents_start_project`; existing code is
   `agents_start_task`. Which agents exist: `agents_list_agents`.
2. A question about the code ("why is this slow?", "where is X handled?") is `mode: ask`: it only reads. A change is the default
   `mode: work`.
3. Write the request as an outcome with its test: what should be true afterwards, which files or behaviour matter, and how to
   know it works (the command that runs the tests). A vague request makes a vague result.
4. It asks the user before it starts. Then it runs in the background: `agents_task_status` or `jobs_status` shows what is changing on
   disk, and the user can stop it.
5. When it finishes you are told what the files and the project's own tests show, not what the agent claims. Report that verdict
   as it is: **verified**, not confirmed (nothing to test), tests failing, tests already failing, or nothing changed. If the tests
   fail, say so plainly and offer to send it back with the failure. Never say a job worked unless the verdict says so.
6. Inside a mission, pass `wait: true` to get the verdict as the result of the step.
