# Security

Minty can read files, browse, run tools and talk to other services on your behalf, so security reports matter.

## Report privately

Please **do not open a public issue** for a vulnerability. Use GitHub's "Report a vulnerability" button on the repository's
Security tab (private advisories). Include what you found, how to reproduce it and what you think the impact is. You will get an
answer within a few days; this is a volunteer project, so fixes are best effort.

## What counts

- A way to make Minty act without the approval its risk class requires.
- A way for web pages, files, messages or tool output to give Minty instructions that it follows.
- Keys, tokens or personal data leaking into logs, traces, prompts, error messages or network requests.
- Anything that lets another person on the network, or another Telegram chat, control Minty.
- The web panel or the local API listening where they should not, or accepting requests without authentication.

## Good to know

- The local API and the web panel listen on `127.0.0.1` only and need a token or password.
- Only Telegram chats paired with a one-time code are answered.
- A coding agent (Claude Code) gets a cleaned environment (no provider keys or tokens) and, when bubblewrap is available, runs in a
  sandbox where your home folder is out of reach; the project's tests it wrote run offline. The result says whether it was sandboxed.
- Refused logins, lockouts, foreign hosts and refused sockets are written to the `minty.security` log (`/logs?name=minty.security`),
  with the address and the reason, never a PIN, cookie or token.
- What was audited, fixed and left open: [docs/security-audit.md](docs/security-audit.md). `python scripts/security_scan.py` repeats the scan.
- Keys are stored in `~/.config/minty/secrets.toml` with mode 0600. If you pasted a key anywhere public, rotate it at the provider.

Supported versions: the latest release and `main`.
