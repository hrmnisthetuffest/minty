# Contributing

Thank you for helping. Minty is an assistant that can act on your computer, so changes are held to a simple bar:
**they come with a test, they keep the safety rules, and they never touch anyone's keys.**

## Set up

```bash
git clone https://github.com/YOUR-USER/minty.git && cd minty
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Python 3.10 or newer. The tests need no API keys and no audio hardware. To run them in parallel: `pip install pytest-xdist`, then `pytest -n auto`. If a test you did not touch fails, say so in your pull request.

## Where things live

| Path | What it is |
|---|---|
| `src/minty_core/` | the assistant: config, router, agent, voice pipeline, safety, memory, Telegram |
| `src/minty_core/onboard/` | the guided setup (`minty onboard`) |
| `src/minty_core/plugins/builtin/<name>/` | one folder per tool group: `plugin.py` (the code) and `plugin.toml` (its description and settings) |
| `src/minty_panel/` | the web panel |
| `docs/plugins.md` | generated from the plugins: `python scripts/gen_plugin_docs.py` |
| `tests/` | pytest; async tests run with `asyncio_mode = auto` |

## Rules of the house

1. **Test first.** Write the test, watch it fail for the right reason, then make it pass. Bug fixes get a regression test.
2. **Say what you checked.** Pull requests should state the commands you ran and what they printed.
3. **Safety is not optional.** A new tool declares its risk class. Anything that spends money, uses credentials, deletes for good
   or contacts other people must ask. Text from web pages, files and tool results is data, never instructions.
4. **No secrets.** Never commit keys, tokens, passwords, personal data or real conversations. Test fixtures use made-up values.
   `scripts/` and `tests/` must work on a machine that has none of your files.
5. **Keep it small.** One idea per pull request. Match the style around your change (comments explain *why*, names are plain).
6. **Settings, not surprises.** New behaviour that changes what users hear or what runs on their machine is behind a setting that
   defaults to the old behaviour unless there is a strong reason.
7. **Update the changelog and the version.** Add your change to `CHANGELOG.md` and bump the alpha number in
   `src/minty_core/__init__.py` (`1.0.0-alpha.N`) and `pyproject.toml` (`1.0.0aN`). `tests/test_version.py` checks they agree.

## Adding a tool group

Copy a small plugin (for example `plugins/builtin/clock`), give it a `plugin.toml` with a description, its settings and each tool's
risk class, write the tests, then run `python scripts/gen_plugin_docs.py` so `docs/plugins.md` stays in step.

## Reporting a problem

Open an issue with what you did, what you expected and what happened. Run `minty --version` and include it. **Remove keys and
personal details from logs before pasting.** Security problems: see [SECURITY.md](SECURITY.md), not a public issue.

By contributing you agree your work is released under the [MIT license](LICENSE).
