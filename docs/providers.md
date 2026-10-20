# Model providers

Minty talks to several providers through one router. It spreads requests over the ones you have keys for, respects each one's
limits, falls back when one fails, and sends private conversations only to providers marked safe for them.

| Provider | Get a free key | Notes |
|---|---|---|
| **Groq** | <https://console.groq.com/keys> | Fast. Also used for speech recognition. Minty treats it as not training on prompts (check Groq's current terms). Recommended first key. |
| **Google Gemini** | <https://aistudio.google.com/apikey> | Needed for Gemini Live voice. Free-tier prompts may be used for training; not used for private turns. |
| **OpenRouter** | <https://openrouter.ai/keys> | Many `:free` models, low daily limit. May train on prompts; not for private turns. |
| **Cerebras** | <https://cloud.cerebras.ai> | Very fast. Data terms not verified; not used for private turns. |
| **Mistral** | <https://console.mistral.ai/api-keys> | Free experiment tier. May train on prompts; not for private turns. |
| **NVIDIA** | <https://build.nvidia.com/settings/api-keys> | Free credits for large models. Data terms not verified; not used for private turns. |
| **Anthropic API** | <https://console.anthropic.com> | Paid, off by default, with a monthly cap you set. |
| **Claude Code login** | `curl -fsSL https://claude.ai/install.sh \| bash`, then run `claude` once | No key: uses your Claude subscription. See below. |

Free tiers change their limits and terms. Check each provider's current terms yourself; Minty marks what it knows
(in the web panel) but cannot vouch for the future. Edit `[[providers]]` in `config.toml` to change a model or add
any OpenAI-compatible service.

## Claude as the brain (optional)

`minty claude-brain on` (or the setup's brain step) runs the `claude` program you already signed in to, with no tools, no
settings and no saved session, as a ladder:

| Turn | Model and effort |
|---|---|
| Most turns | Haiku, low |
| Medium | Haiku, high |
| Hard | Sonnet, low |
| Thinking mode | Sonnet, high |
| Only when you say "use opus ..." | Opus, at the effort you name |

When your Claude usage window is nearly used up (90 %), the free providers take over until it resets. Each reply starts a Claude
Code process, so replies take a few seconds longer than Groq. Check Anthropic's current terms for using your plan this way.
`minty claude-brain off` puts everything back.
