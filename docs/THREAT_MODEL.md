# Threat model (living)

Last reviewed: 2026-09-17 (from code). Owner / review cadence: unset
(organizational — do not invent). This document models attack surface and
gaps; point fixes belong to sec-review, not here.

## Risk-ranked summary

| Rank | Risk | Boundary | Why it matters | Existing control | Gap |
|------|------|----------|----------------|------------------|-----|
| 1 | Localhost studio is a full project workstation once any account exists | Browser → studio (B1) | Authed callers reach board mutate, `/fs/*`, `/vcs/commit`, `/kb/*`, `/chat`, `/scan`, `/solve`, `/fs/import` under `OCD_ROOT` | Bind `127.0.0.1` only (`apps/studio.py`); cookie sessions + shelf checks | Open signup; no multi-tenant isolation for shared paths under `ROOT`; any local peer process can hit the port |
| 2 | Secrets ride outbound LLM/JLC calls; `OCD_LLM_BASE` is any http(s) host | Secrets → vendors (B2) | Bearer `OCD_LLM_KEY` is sent to `envcfg.llm_base()` (`ocdcircuit/llm.py` `_post_json`); JLC creds authorize spend | Redaction in `envcfg.summary()`; `.gitignore` for `.env` / `.ocd-users`; base must be http(s) with a host | No host allowlist on LLM base; poisoned env exfiltrates the key; no rotation/expiry in-app |
| 3 | Optional knoll checkout is `exec_module` of local Python | Build/path → runtime (B4) | `plugins._knoll_price` loads `KNOLL_SRC/.../knoll/stock.py` (or `~/Desktop/knoll/src` when unset) via `importlib` | Opt-in / degrade to unpriced on failure; set `KNOLL_SRC` does not fall through to Desktop | No integrity check; a planted `stock.py` runs as the studio/CLI/MCP OS user |
| 4 | MCP stdio tools inherit the host agent's trust | Host agent → MCP (B3) | `load_board`, `export`, `kb` add/fetch, `import_footprint`, `solve` act as the OS user (`apps/mcp.py` `TOOLS`) | Stdio-only (no network listener) | No authn between host and tools; path/URL inputs are host-trusted |
| 5 | Authenticated DoS / cost amplification | B1 / B2 | `/solve`, `/candidates`, `/scan`, `/chat`, `/kb/fetch`, foreign import parse are CPU-, disk-, and token-heavy | POST `_MAX_BODY` 20MB; `/fs/import` 2MB + ext allowlist; `OCD_SCAN_MAX_MB`; LLM `max_tokens` | No per-user quotas on solve/scan/chat; login rate limit only |
| 6 | Outbound HTTPS fetch from board/kb URLs | App → network (B2) | `kb._download` / LCSC / JLC leave the host | HTTPS-only + size cap (`kb.MAX_BYTES`); LCSC host suffix for `lcsc=` | `datasheet=` / `kb add` URL has no host allowlist (SSRF-class to other HTTPS targets) |

## Attack surface inventory

### Network / HTTP (studio)

Listener: `http.server.ThreadingHTTPServer(("127.0.0.1", port), H)` in
`apps/studio.py` (port from `ocdcircuit/envcfg.studio_port`, default 8077).
Not bound to `0.0.0.0`.

| Entry | Auth | Notes | Location |
|-------|------|-------|----------|
| `GET /` landing + login HTML | Public | Gate for workshop UI | `apps/studio.py` `do_GET` |
| `GET /fab-logo/<key>` | Public | Key charset-restricted `[a-z0-9-]+` | `do_GET` |
| `GET /slots` | Public | Plugin slot inventory probe | `do_GET` |
| `POST /auth/signup\|login\|logout\|me\|profile` | Public keyhole | Signup/login rate-limited (`_auth_rate_ok`); whole `/auth/*` prefix bypasses the “log in first” gate | `do_POST` |
| `GET /poll`, `/fs`, `/collab/events` | Session | Shelf / open-board guards | `do_GET` |
| `POST /shelf*`, `/init`, `/build`, `/solve`, `/candidates`, `/pick`, `/export`, `/render`, `/xray`, `/quote`, `/simulate`, `/undo`, `/redo`, `/scan`, `/doctor`, `/kb/*`, `/chat*`, `/fs/*`, `/vcs*`, `/collab/*`, `/load`, `/reload`, `/diff_prev` | Session | Board + project ops; `/fs/import` is base64 upload → `fp/`/`sym/` | `do_POST`; route list also in `docs/STUDIO.md` |

Client-supplied paths for `/fs/*` and related routes go through `_rel` /
`_abs` (`apps/studio.py`): must realpath inside `ROOT`; `.ocd-users` and
other users' `.users/<name>/` shelves refused.

### MCP (stdio JSON-RPC)

`apps/mcp.py`: Content-Length-framed JSON-RPC on stdin/stdout. Methods
`initialize`, `tools/list`, `tools/call`, `ping`. Thirty tools in `TOOLS`
(including `load_board`, mutations, `export`, `kb`, `import_footprint`,
`solve`, `xray`, `simulate`). No TLS, no cookies — trust is the parent
process that spawns the server.

### CLI

`apps/ocd.py`: local argv → `run|status|diff|xray|scan|quote|score|lint|kb|doctor|plugins|new`.
Reads/writes board trees; `scan` / `kb` / `quote` may call network helpers.

### File / upload parsers (untrusted bytes)

| Entry | Inputs | Location |
|-------|--------|----------|
| Studio `/fs/import` | Base64 body, basename + `IMPORT_EXTS` | `apps/studio.py` `_import_upload` |
| MCP `import_footprint` / board load | Path on disk | `apps/mcp.py` `t_import`, `t_load` |
| Foreign footprint/board formats | KiCad / Eagle / Altium / tscircuit / … XML & text | `ocdcircuit/foreign.py` (`ET.fromstring`) |
| Photo scan / x-ray | PNG path or base64 | `ocdcircuit/pcbscan.py`, studio `/scan` `/xray` |

### Contributor tools (not studio listeners)

| Tool | Surface | Location |
|------|---------|----------|
| `tools/easyeda_live.py` | Outbound CDP WebSocket to a local browser debug port | `CDP.__init__` `socket.create_connection` |
| `tools/scanbench.py` | Outbound HTTPS fetch of benchmark assets | `urllib.request.urlopen` |
| `tools/atopile.py`, `mitox.py`, `tscircuit.py` | Local file convert CLIs | argv → board trees |

### Environment / secrets inputs

Enumerated in `.env.example` and parsed by `ocdcircuit/envcfg.py`:
`OCD_LLM_*` (including `OCD_LLM_BASE` → any http(s) host), `OCD_PORT`,
`OCD_ROOT`, `OCD_SCAN_MAX_MB`, `SOURCE_DATE_EPOCH`, `JLCPCB_*`, `KNOLL_SRC`,
`KICAD10_3DMODEL_DIR`, `XRAY`, `SCANBENCH_*`. JLC creds also from
`~/.secrets/jlcpcb` (`ocdcircuit/plugins.py`).

### Outbound clients (dependency / deployment surface)

| Client | Purpose | Location |
|--------|---------|----------|
| OpenAI-compatible HTTP | chat / embed / models; Bearer key attached | `ocdcircuit/llm.py` |
| HTTPS GET | kb datasheets, LCSC PDF URL | `ocdcircuit/kb.py` |
| JLC signed HTTP | parts pricing | `ocdcircuit/plugins.py` |
| Knoll dynamic import | live JLC price via optional checkout | `plugins._knoll_price` |
| Subprocess | ngspice, kicad-cli, pdftotext, git | `ocdcircuit/spice.py`, `plugins.py`, `kb.py`, studio `/vcs*` |

No separate admin/debug HTTP port beyond studio. CI:
`.github/workflows/check.yml` (lint/test only).

## Trust boundaries

### B1 — Untrusted browser ↔ studio (localhost)

- **Crossing data:** cookies, JSON bodies, query `board` / `dir`, uploaded
  scan images and `/fs/import` bytes, collab text/ops, kb URLs/paths.
- **Authn point:** `_authed` session cookie `ocd_user` (HttpOnly,
  SameSite=Lax) in `apps/studio.py`; scrypt password file `.ocd-users`.
- **Authz point:** `_ensure_shelf_rel` / `_ensure_open_board` / `_abs` for
  per-user shelves; shared/public trees under `ROOT` are readable/writable
  by any logged-in user who can name the path.
- **Privilege transition:** unauthenticated → session after signup/login;
  session → filesystem and subprocess (`/vcs/commit`, engines, import parse).

### B2 — Studio/CLI/MCP process ↔ secrets & vendors

- **Crossing data:** Bearer `OCD_LLM_KEY` to whatever `OCD_LLM_BASE` is;
  JLC app id / key / secret; HTTPS bodies from kb/LCSC.
- **Entry:** environment and `~/.secrets/jlcpcb` (`envcfg.jlc_env_creds`,
  `plugins.py`).
- **Mitigation present:** doctor/summary redacts secret *values*
  (`envcfg._SECRETS`); `llm_base()` rejects non-http(s) / hostless URLs.
- **Gap:** no vault; no LLM-base host allowlist; process or host-user
  compromise equals key use.

### B3 — Host agent ↔ MCP tools

- **Crossing data:** tool arguments (paths, board text, URLs, import keys).
- **Authn:** none inside MCP; OS user identity of the MCP process.
- **Gap:** a compromised or confused agent gets full `TOOLS` power.

### B4 — Build / optional path → runtime

- Pure Python apps; no signed release artifact in-tree. Runtime trust is
  the checkout + interpreter + optional deps from `pyproject.toml`.
- **Privilege transition:** `plugins._knoll_price` `exec_module` on
  `KNOLL_SRC` or default Desktop path — local file becomes process code.

### B5 — Tenant ↔ tenant (same studio process)

- Accounts share one `ThreadingHTTPServer`, one `OCD_ROOT`, and collab
  rooms (`ocdcircuit/collab.py`). Shelves under `.users/<name>/` are
  owner-checked; everything else under `ROOT` is a shared workspace.
- **Gap:** no stronger isolation than path checks; open signup adds peers
  on the same host.

## Assets

| Asset | Where | Impact if stolen / corrupted / denied |
|-------|-------|----------------------------------------|
| Board sources (`.ocd`), fab exports, `kb/` notes & datasheets | under `OCD_ROOT` / board dirs | IP loss; bad Gerbers to fab; poisoned docs in agent context |
| Account password hashes | `<ROOT>/.ocd-users` | Local account takeover (scrypt; still sensitive) |
| Session tokens | in-memory `_SESSIONS` | Workshop access until TTL/restart |
| `OCD_LLM_KEY` / JLC secrets | env / `~/.secrets/jlcpcb` | Billable API abuse; vendor account misuse; key theft via hostile LLM base |
| Host CPU / disk | solve, scan, fetch, foreign parse | Workstation unavailability |
| Collab room state | `ocdcircuit/collab.py` | Tampered shared board text for co-editors |
| Process code integrity | knoll `stock.py` path | Full OS-user code execution if that file is hostile |

## Threats per boundary (concrete)

### B1 (browser ↔ studio)

- **Spoofing:** cookie theft on the same host; open signup creates peer
  accounts (`/auth/signup`). Mitigation: localhost bind; rate limit 30/60s
  per client key (`_auth_rate_ok`). Gap: no invite-only mode.
- **Tampering:** authed `/build`/`/collab/push`/`/fs`/`/fs/import` rewrite
  project files; path traversal attempts. Mitigation: `_abs` root jail +
  shelf checks; import basename ASCII filter + ext allowlist + 2MB cap.
- **Information disclosure:** `/slots` and `/fab-logo/*` unauthenticated;
  shared `ROOT` boards visible to all accounts; REQ lines scrub `.users/<name>`
  via `_path_for_log`. Gap: public project files are intentionally shared.
- **DoS:** large POST (capped at 20MB), heavy `/solve`/`/scan`/`/candidates`,
  crafted foreign files through `/fs/import`. Gap: no compute quota after auth.
- **Elevation:** riding another user's open shelf board via process-global
  `SRC`. Mitigation: `_ensure_open_board` on board routes (auth gate in
  `do_POST`).

### B2 / outbound

- **Information disclosure:** Bearer key posted to attacker-chosen
  `OCD_LLM_BASE` (`llm._post_json`).
- **SSRF-class:** `kb._download` requires `https` and size limit; does not
  restrict destination host for arbitrary URLs (`ocdcircuit/kb.py`). LCSC
  path checks `*.lcsc.com`.
- **Tampering:** LLM/tool replies applied only after `/build` validation in
  the chat path (studio `/chat/apply`).

### B3 (MCP)

- **Elevation of privilege relative to the human:** tools can read/write
  paths the OS user can access (`t_load` `open(path)`, `t_export`, `t_kb`,
  `t_import`). Treat MCP host compromise as full local tool
  RCE-equivalent for this tree — record for sec-review; not demonstrated.

### B4 (optional code load)

- **Elevation:** hostile or substituted `knoll/stock.py` under `KNOLL_SRC`
  or `~/Desktop/knoll/src` executes inside the quoting path
  (`plugins._knoll_price`). No hash/signature gate.

### B5 (tenant ↔ tenant)

- **Tampering / disclosure:** any authed user can mutate shared `ROOT`
  boards and join collab on the same open board URL (see abuse cases).

## Mitigations mapping (present in code)

| Control | Threats covered | Reference |
|---------|-----------------|-----------|
| Bind `127.0.0.1` | Remote internet drive-by on studio | `apps/studio.py` server boot |
| Session cookie HttpOnly + SameSite=Lax | XSS token exfil (partial) | `_write_bytes` Set-Cookie |
| scrypt password hashes + hmac compare | Offline / timing on password verify | `_write_user`, `_check_user` |
| Auth attempt rate limit + bounded maps | Brute force / memory spray | `_auth_rate_ok`, `_SESSIONS_MAX` |
| Path jail `_abs` + shelf owner check | Traversal / cross-shelf read | `_abs`, `_ensure_shelf_rel` |
| POST `_MAX_BODY` | Oversized body DoS | `do_POST` |
| Import ext allowlist + 2MB + ASCII basename | Path smuggling / huge upload | `_import_upload`, `IMPORT_EXTS` |
| Browser headers nosniff / DENY frame / CSP frame-ancestors | Clickjacking / MIME sniff | `_secure_headers` |
| HTTPS-only kb download + MAX_BYTES + PDF magic | Cleartext fetch / huge file | `kb._download` |
| `llm_base()` http(s)+host check | Non-URL / empty base | `envcfg.llm_base` |
| Env secret redaction in doctor | Accidental secret log in health output | `envcfg.summary` |
| Atomic writes for users + kb | Truncated credential/doc files | `_atomic_write`, `kb._download` |
| Git hash charset check on `/vcs/diff` | Arg injection via rev | `_GIT_HASH_RE` |

**Single points of failure:** (1) studio session cookie authorizes nearly
all high-impact routes; (2) localhost bind is the only control against
remote exposure; (3) process env + optional knoll path carry secrets and
code trust with no second factor.

**Doc vs code:** `docs/STUDIO.md` and `.env.example` claim `127.0.0.1` only —
matches `ThreadingHTTPServer(("127.0.0.1", port), H)`. `DESIGN.md` session
cookie attributes match Set-Cookie construction. STUDIO “invite link” is a
board URL share helper, not invite-gated auth — matches open signup. No
false *mitigation* claims found in those files this pass; prior gap was
omission of knoll/`OCD_LLM_BASE` surfaces (now listed above).

## Abuse cases (authenticated, hostile)

1. **Shared-tree rewrite:** User A and user B both authed; B opens/writes a
   board under public `ROOT` (not under `.users/B/`) via `/fs` or shelf-adjacent
   paths — enabling path in `_abs` when `_shelf_owner` is `None`.
2. **Compute burn:** Repeated `/solve` or `/scan` with large inputs — enabled
   by authed `do_POST` branches; only body size gated.
3. **Signup spray then shelf fill:** `/auth/signup` creates `.users/<name>/`
   (rate-limited, not invite-gated).
4. **Import parser burn:** Authed `/fs/import` of crafted Eagle/KiCad/Altium
   XML — path `foreign.py` `ET.fromstring`; size capped at 2MB but parse cost
   unbounded relative to that cap.
5. **Client-side only expectations:** UI may hide controls; server enforces
   login on POST (except `/auth/*`) — do not treat browser validation as a
   control.

Operator / env abuse (not requiring a studio session): point `OCD_LLM_BASE`
at an attacker HTTPS endpoint with `OCD_LLM_KEY` set; or plant
`knoll/stock.py` on the knoll search path before `/quote`.

## Response readiness (note only)

- Studio prints scrubbed `REQ` lines to stderr; no durable audit log of
  authz denials or file writes for incident reconstruction (o11y-review).
- No in-repo path from “vulnerability reported” → “fix shipped” beyond
  normal contribution (`CONTRIBUTING.md`). See root `SECURITY.md` for
  disclosure status.
