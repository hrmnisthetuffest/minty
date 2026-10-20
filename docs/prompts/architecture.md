# The Minty prompt architecture 

One model call's system prompt is built, in this order (stable parts first so providers' prompt caches keep working):

1. **Personality text** (`personality.DEFAULT_PERSONALITIES`) and the name rule.
2. **Core rules** `personality.BASE_RULES` (spoken replies) or, on a Telegram turn once its gate passed, `promptlib.TELEGRAM_RULES` (text).
3. `SERVICE_RULES` (ego-free service) and the formality / humour / verbosity sliders.
4. **Memory** and the user's standing instructions.
5. **Library modules** (`promptlib.py`), chosen by `channel` (voice, telegram), `tier` (fast, deep = thinking mode) and the tool
   groups the request uses. Only modules listed in `promptlib.GATES` are ever used.
6. **Per-turn extras** (matched skill, brownout note, open page).
7. The history note (older turns as one line each, or the Telegram tiers).

## Rules for a module
* One idea, one to four sentences, written fresh (no vendor text), with a **character budget** a test enforces.
* A `version`; any wording change bumps it and clears its evidence (`GATES[name]` is removed until it passes again).
* It enters `GATES` only after a measured A/B (same model, same items, blind judge where a judgement is needed) shows it improves its target metric without worsening
  safety, honesty or latency; the evidence line (file and numbers) is stored in the GATES value. Failed modules stay out.
* It can be switched off by name: `personality.prompt_modules_off = ["wellbeing"]`; the whole library by
  `personality.prompt_library = false`.
* Every trace line carries `prompt_modules` and `prompt_version` (a hash of module names, versions and base rules), so a bad
  turn is tied to the exact prompt.

## Effort and length are settings, not rewrites
Published model prompts show labs keep one prompt per surface and move effort and length with a header number. In Minty the
**router** chooses model and effort (the Claude ladder, `claude_pick`); the prompt has a length rule in the core and, optionally,
a header line (`Reply length: N of 10`; experiment `length_header`). Nothing in a module depends on the model's size.

## Tools
`minty prompts-show [--channel] [--tier] [--kit]` prints what would be sent, with sizes. Each gated module's evidence line is in
`promptlib.GATES`.
