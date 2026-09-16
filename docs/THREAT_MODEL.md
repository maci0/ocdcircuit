# Threat model (living)

Last reviewed: 2026-09-17 (from code). Owner / review cadence: unset
(organizational — do not invent). This document models attack surface and
gaps; point fixes belong to sec-review, not here.

## Risk-ranked summary

| Rank | Risk | Boundary | Why it matters | Existing control | Gap |
|------|------|----------|----------------|------------------|-----|
| 1 | Localhost studio is a full project workstation once any account exists | Browser → studio | Authed callers reach board mutate, `/fs/*`, `/vcs/commit`, `/kb/*`, `/chat`, `/scan`, `/solve` under `OCD_ROOT` | Bind `127.0.0.1` only (`apps/studio.py`); cookie sessions + shelf checks | Open signup; no multi-tenant isolation for shared/public paths under `ROOT`; any local peer process can hit the port |
| 2 | Secrets in process env / home files ride every LLM and JLC call | Secrets → code | `OCD_LLM_KEY`, `JLCPCB_*` authorize spend and vendor APIs | Redaction in `ocdcircuit/envcfg.py` `summary()`; `.gitignore` for `.env` / `.ocd-users` | Key lives in the studio/CLI/MCP process; no rotation/expiry in-app |
| 3 | MCP stdio tools inherit the host agent's trust | Host agent → MCP | `load_board` path/text, `export`, `kb` add/fetch, `import_footprint` act as the OS user | Stdio-only (no network listener) in `apps/mcp.py` | No authn between host and tools; path/URL inputs are host-trusted |
| 4 | Authenticated DoS / cost amplification | Browser → studio / LLM | `/solve`, `/candidates`, `/scan`, `/chat`, `/kb/fetch` are CPU-, disk-, and token-heavy | POST body cap `_MAX_BODY` (`apps/studio.py`); `OCD_SCAN_MAX_MB`; LLM `max_tokens` | No per-user quotas on solve/scan/chat; login rate limit only |
| 5 | Outbound HTTPS fetch from board/kb URLs | App → network | `kb._download` / LCSC / LLM / JLC leave the host | HTTPS-only + size cap in `ocdcircuit/kb.py`; LCSC host suffix check for `lcsc=` | `datasheet=` / `kb add` URL has no host allowlist (SSRF to other HTTPS targets) |

## Attack surface inventory

### Network / HTTP (studio)

Listener: `http.server.ThreadingHTTPServer(("127.0.0.1", port), H)` in
`apps/studio.py` (port from `ocdcircuit/envcfg.studio_port`, default 8077).
Not bound to `0.0.0.0`.

| Entry | Auth | Notes | Location |
|-------|------|-------|----------|
| `GET /` landing + login HTML | Public | Gate for workshop UI | `apps/studio.py` `do_GET` |
| `GET /fab-logo/<key>` | Public | Key charset-restricted | `do_GET` |
| `GET /slots` | Public | Plugin slot inventory probe | `do_GET` |
| `POST /auth/signup\|login\|logout\|me\|profile` | Public keyhole | Signup/login rate-limited | `do_POST` |
| `GET /poll`, `/fs`, `/collab/events` | Session | Shelf / open-board guards | `do_GET` |
| `POST /shelf*`, `/init`, `/build`, `/solve`, `/candidates`, `/pick`, `/export`, `/render`, `/xray`, `/quote`, `/simulate`, `/undo`, `/redo`, `/scan`, `/doctor`, `/kb/*`, `/chat*`, `/fs/*`, `/vcs*`, `/collab/*`, `/load`, `/reload`, `/diff_prev` | Session | Board + project ops | `do_POST`; inventory also in `docs/STUDIO.md` |

Client-supplied paths for `/fs/*` and related routes go through `_rel` /
`_abs` (`apps/studio.py`): must realpath inside `ROOT`; `.ocd-users` and
other users' `.users/<name>/` shelves refused.

### MCP (stdio JSON-RPC)

`apps/mcp.py`: Content-Length-framed JSON-RPC on stdin/stdout. Methods
`initialize`, `tools/list`, `tools/call`, `ping`. Tools in `TOOLS` include
`load_board`, mutations, `export`, `kb` (list/search/read/add/fetch/ask),
`import_footprint`, `solve`, etc. No TLS, no cookies — trust is the parent
process that spawns the server.

### CLI

`apps/ocd.py`: local argv → `run|status|diff|xray|scan|quote|score|lint|kb|doctor|plugins|new`.
Reads/writes board trees; `scan` / `kb` / `quote` may call network helpers.

### Environment / secrets inputs

Enumerated in `.env.example` and parsed by `ocdcircuit/envcfg.py`:
`OCD_LLM_*`, `OCD_PORT`, `OCD_ROOT`, `OCD_SCAN_MAX_MB`, `SOURCE_DATE_EPOCH`,
`JLCPCB_*`, `KNOLL_SRC`, `KICAD10_3DMODEL_DIR`, `XRAY`, `SCANBENCH_*`.
JLC creds also from `~/.secrets/jlcpcb` (`ocdcircuit/plugins.py`).

### Outbound clients (dependency / deployment surface)

| Client | Purpose | Location |
|--------|---------|----------|
| OpenAI-compatible HTTP | chat / embed / models | `ocdcircuit/llm.py` |
| HTTPS GET | kb datasheets, LCSC PDF URL | `ocdcircuit/kb.py` |
| JLC signed HTTP | parts pricing | `ocdcircuit/plugins.py` |
| Subprocess | ngspice, kicad-cli, pdftotext, git | `ocdcircuit/spice.py`, `plugins.py`, `kb.py`, studio `/vcs*` |

No separate admin/debug HTTP port beyond studio. CI:
`.github/workflows/check.yml` (lint/test only).

## Trust boundaries (priority this pass)

### B1 — Untrusted browser ↔ studio (localhost)

- **Crossing data:** cookies, JSON bodies, query `board` / `dir`, uploaded
  scan images, collab text/ops, kb URLs/paths.
- **Authn point:** `_authed` session cookie `ocd_user` (HttpOnly,
  SameSite=Lax) in `apps/studio.py`; scrypt password file `.ocd-users`.
- **Authz point:** `_ensure_shelf_rel` / `_ensure_open_board` / `_abs` for
  per-user shelves; shared/public trees under `ROOT` are readable/writable
  by any logged-in user who can name the path.
- **Privilege transition:** unauthenticated → session after signup/login;
  session → filesystem and subprocess (`/vcs/commit`, engines).

### B2 — Studio/CLI/MCP process ↔ secrets & vendors

- **Crossing data:** Bearer `OCD_LLM_KEY`; JLC app id / key / secret.
- **Entry:** environment and `~/.secrets/jlcpcb` (`envcfg.jlc_env_creds`,
  `plugins.py`).
- **Mitigation present:** doctor/summary redacts secret *values*
  (`envcfg._SECRETS`).
- **Gap:** no vault; compromise of the process or host user equals key use.

### B3 — Host agent ↔ MCP tools

- **Crossing data:** tool arguments (paths, board text, URLs).
- **Authn:** none inside MCP; OS user identity of the MCP process.
- **Gap:** a compromised or confused agent gets full `TOOLS` power.

### B4 — Build → runtime

- Pure Python apps; no signed release artifact in-tree. Runtime trust is
  the checkout + interpreter + optional deps from `pyproject.toml`.

## Assets

| Asset | Where | Impact if stolen / corrupted / denied |
|-------|-------|----------------------------------------|
| Board sources (`.ocd`), fab exports, `kb/` notes & datasheets | under `OCD_ROOT` / board dirs | IP loss; bad Gerbers to fab; poisoned docs in agent context |
| Account password hashes | `<ROOT>/.ocd-users` | Local account takeover (scrypt; still sensitive) |
| Session tokens | in-memory `_SESSIONS` | Workshop access until TTL/restart |
| `OCD_LLM_KEY` / JLC secrets | env / `~/.secrets/jlcpcb` | Billable API abuse; vendor account misuse |
| Host CPU / disk | solve, scan, fetch | Workstation unavailability |
| Collab room state | `ocdcircuit/collab.py` | Tampered shared board text for co-editors |

## Threats per boundary (concrete)

### B1 (browser ↔ studio)

- **Spoofing:** cookie theft on the same host; open signup creates peer
  accounts (`/auth/signup`). Mitigation: localhost bind; rate limit 30/60s
  per client key (`_auth_rate_ok`). Gap: no invite-only mode.
- **Tampering:** authed `/build`/`/collab/push`/`/fs` rewrite project files;
  path traversal attempts. Mitigation: `_abs` root jail + shelf checks.
- **Information disclosure:** `/slots` and `/fab-logo/*` unauthenticated;
  shared `ROOT` boards visible to all accounts; REQ lines scrub `.users/<name>`
  via `_path_for_log`. Gap: public project files are intentionally shared.
- **DoS:** large POST (capped at 20MB), heavy `/solve`/`/scan`/`/candidates`.
  Gap: no compute quota after auth.
- **Elevation:** riding another user's open shelf board via process-global
  `SRC`. Mitigation: `_ensure_open_board` on board routes (see comments at
  auth gate in `do_POST`).

### B2 / outbound

- **Information disclosure / SSRF-class:** `kb._download` requires `https`
  and size limit; does not restrict destination host for arbitrary URLs
  (`ocdcircuit/kb.py`). LCSC path checks `*.lcsc.com`.
- **Tampering:** LLM/tool replies applied only after `/build` validation in
  the chat path (`ocdcircuit/llm.py` module doc; studio `/chat/apply`).

### B3 (MCP)

- **Elevation of privilege relative to the human:** tools can read/write
  paths the OS user can access (`t_load` `open(path)`, `t_export`, `t_kb`).
  Treat MCP host compromise as full local tool RCE-equivalent for this
  tree — record for sec-review; not demonstrated here.

## Mitigations mapping (present in code)

| Control | Threats covered | Reference |
|---------|-----------------|-----------|
| Bind `127.0.0.1` | Remote internet drive-by on studio | `apps/studio.py` server boot |
| Session cookie HttpOnly + SameSite=Lax | XSS token exfil (partial) | `_write_bytes` Set-Cookie |
| scrypt password hashes + hmac compare | Offline / timing on password verify | `_write_user`, `_check_user` |
| Auth attempt rate limit + bounded maps | Brute force / memory spray | `_auth_rate_ok`, `_SESSIONS_MAX` |
| Path jail `_abs` + shelf owner check | Traversal / cross-shelf read | `_abs`, `_ensure_shelf_rel` |
| POST `_MAX_BODY` | Oversized body DoS | `do_POST` |
| Browser headers nosniff / DENY frame / CSP frame-ancestors | Clickjacking / MIME sniff | `_secure_headers` |
| HTTPS-only kb download + MAX_BYTES + PDF magic | Cleartext fetch / huge file | `kb._download` |
| Env secret redaction in doctor | Accidental secret log in health output | `envcfg.summary` |
| Atomic writes for users + kb | Truncated credential/doc files | `_atomic_write`, `kb._download` |

**Single point of failure:** the studio session cookie authorizes nearly all
high-impact routes; loss of cookie secrecy on the host equals loss of the
workshop. Localhost bind is the other control carrying remote exposure.

**Doc vs code:** `docs/STUDIO.md` and `.env.example` claim `127.0.0.1` only —
matches `ThreadingHTTPServer(("127.0.0.1", port), H)`. `DESIGN.md` session
cookie attributes match Set-Cookie construction. No false mitigation claims
found in those files this pass.

## Abuse cases (authenticated, hostile)

1. **Shared-tree rewrite:** User A and user B both authed; B opens/writes a
   board under public `ROOT` (not under `.users/B/`) via `/fs` or shelf-adjacent
   paths — enabling path in `_abs` when `_shelf_owner` is `None`.
2. **Compute burn:** Repeated `/solve` or `/scan` with large inputs — enabled
   by authed `do_POST` branches; only body size gated.
3. **Signup spray then shelf fill:** `/auth/signup` creates `.users/<name>/`
   (rate-limited, not invite-gated).
4. **Client-side only expectations:** UI may hide controls; server enforces
   login on POST (except `/auth/*`) — do not treat browser validation as a
   control.

## Response readiness (note only)

- Studio prints scrubbed `REQ` lines to stderr; no durable audit log of
  authz denials or file writes for incident reconstruction (o11y-review).
- No in-repo path from “vulnerability reported” → “fix shipped” beyond
  normal contribution (`CONTRIBUTING.md`). See root `SECURITY.md` for
  disclosure status.
