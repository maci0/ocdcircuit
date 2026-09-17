# Security

## Supported versions

This project is pre-1.0 (`version = "0.1.2"` in `pyproject.toml`). Security
fixes land on the default development branch when maintainers ship them.
No other release lines are published from this tree.

## Reporting a vulnerability

No dedicated security contact address is published in this repository.
Do not file a public issue that includes exploit details, secrets, or
credentials. Prefer a private channel to a maintainer if you have one;
otherwise wait until a contact appears here rather than disclosing
sensitive details in a public tracker.

## What this software assumes

Studio (`python -m apps.studio`) listens on **127.0.0.1 only** (see
`apps/studio.py`). It is a local multi-user workshop with open signup,
cookie sessions, and filesystem access under `OCD_ROOT` — not a hardened
internet-facing multi-tenant service. The MCP server (`apps/mcp.py`) speaks
JSON-RPC on stdio and trusts its host process. CLI tools run as the invoking
OS user.

Secrets (`OCD_LLM_KEY`, `JLCPCB_*`, and optional `~/.secrets/jlcpcb`) must
never be committed. `.env` / `.ocd-users` are gitignored; `ocd doctor`
redacts known secret values. `OCD_LLM_BASE` may be any http(s) URL with a
host (`ocdcircuit/envcfg.py`); the Bearer key is sent to that base on every
LLM call — treat a hostile or mistyped base as credential disclosure.

Optional live JLC pricing via knoll loads and executes
`knoll/stock.py` from `KNOLL_SRC` (or `~/Desktop/knoll/src` when unset) inside
the process (`ocdcircuit/plugins.py`). Only point those paths at code you
trust.

## Threat model

The living CISO-facing surface map is `docs/THREAT_MODEL.md`. Point
vulnerabilities and code fixes are out of scope for that document.
