# Threat model (living)

Last reviewed: 2026-09-18 (Studio HTTP/authentication, proposal validation,
and KB download controls checked against code). Other surfaces retain their
2026-09-17 assessment. Owner and review cadence: unset. This document models
attack surface and gaps; point fixes belong to sec-review, not here.

## Risk-ranked summary

| Rank | Risk | Boundary | Why it matters | Existing control | Gap |
|------|------|----------|----------------|------------------|-----|
| 1 | Localhost studio is a full project workstation once any account exists | Browser → studio (B1) | Authed callers reach board mutate, `/fs/*`, `/vcs/commit`, `/kb/*`, `/chat`, `/scan`, `/solve`, `/fs/import` under `OCD_ROOT` | Bind `127.0.0.1` only (`apps/studio.py`); cookie sessions + shelf checks | Open signup; no multi-tenant isolation for shared paths under `ROOT`; any local peer process can hit the port |
| 2 | Secrets ride outbound LLM/JLC calls; `OCD_LLM_BASE` is any http(s) host | Secrets → vendors (B2) | Bearer `OCD_LLM_KEY` is sent to `envcfg.llm_base()` (`ocdcircuit/llm.py` `_post_json`); JLC creds authorize spend | Redaction in `envcfg.summary()`; `.gitignore` for `.env` / `.ocd-users`; base must be http(s) with a host | No host allowlist on LLM base; poisoned env exfiltrates the key; no rotation/expiry in-app |
| 3 | Optional knoll checkout is `exec_module` of local Python | Build/path → runtime (B4) | `plugins._knoll_price` loads `KNOLL_SRC/.../knoll/stock.py` (or `~/Desktop/knoll/src` when unset) via `importlib` | Opt-in / degrade to unpriced on failure; set `KNOLL_SRC` does not fall through to Desktop | No integrity check; a planted `stock.py` runs as the studio/CLI/MCP OS user |
| 4 | MCP stdio tools inherit the host agent's trust | Host agent → MCP (B3) | `load_board`, `export`, `kb` add/fetch, `import_footprint`, `solve` act as the OS user (`apps/mcp.py` `TOOLS`) | Stdio-only (no network listener) | No authn between host and tools; path/URL inputs are host-trusted |
| 5 | Authenticated DoS / cost amplification | B1 / B2 | `/solve`, `/candidates`, `/reroute`, `/scan`, `/chat`, `/kb/fetch`, foreign import parse are CPU-, disk-, and token-heavy | POST 20MB cap (`apps/studio.py:1630`); import 2MB cap (`apps/studio.py:768`); chat single-flight (`apps/studio.py:1116`); scan single-flight, 40-photo cap, temporary-tree cleanup (`apps/studio.py:2197`) | These bounds do not impose per-user compute or spending quotas; serial calls can still monopolize resources |
| 6 | Outbound HTTPS fetch from board/kb URLs | App → network (B2) | User-supplied datasheet URLs cause host network requests | `_assert_public_https` rejects non-global IPs and DNS answers, local hostnames, and URL credentials; redirects are rechecked; downloads are capped at 64MiB (`ocdcircuit/kb.py:172`, `ocdcircuit/kb.py:213`, `ocdcircuit/kb.py:223`) | No destination allowlist or aggregate download quota; DNS validation and connection are separate, not address-pinned (`ocdcircuit/kb.py:201`, `ocdcircuit/kb.py:233`) |

## Attack surface inventory

### Network / HTTP (studio)

Listener: `http.server.ThreadingHTTPServer(("127.0.0.1", port), H)` in
`apps/studio.py` (port from `ocdcircuit/envcfg.studio_port`, default 8077).
Not bound to `0.0.0.0`.

| Entry | Auth | Notes | Location |
|-------|------|-------|----------|
| `GET /` landing + login HTML | Public | Gate for workshop UI | `apps/studio.py` `do_GET` |
| `GET /fab-logo/<key>` | Public | Key charset-restricted `[a-z0-9-]+` | `do_GET` |
| `GET /slots` | Public | Plugin slot inventory probe | `apps/studio.py:1470` |
| `GET /web/*` | Public | Frontend assets under `apps/web`; extension/path-pattern checks and rejection of `..`; deployment must keep this tree trusted, including symlinks (no realpath containment check) | `apps/studio.py:1352`, `apps/studio.py:1456` |
| `GET /fabs` | Public | Fab profile names, keys, and URLs, not account or board data | `apps/studio.py:1461` |
| `POST /reroute` | Session + open-board guard | Maze rerouting of a selected net or jumpers; commits and saves the resulting board | `apps/studio.py:1653`, `apps/studio.py:2015` |
| `POST /auth/signup\|login\|logout\|me\|profile` | Public keyhole | Signup/login rate-limited (`_auth_rate_ok`); whole `/auth/*` prefix bypasses the “log in first” gate | `do_POST` |
| `GET /poll`, `/fs`, `/collab/events` | Session | Shelf / open-board guards | `do_GET` |
| `POST /shelf*`, `/init`, `/build`, `/solve`, `/candidates`, `/pick`, `/export`, `/render`, `/xray`, `/quote`, `/simulate`, `/undo`, `/redo`, `/scan`, `/doctor`, `/kb/*`, `/chat*`, `/fs/*`, `/vcs*`, `/collab/*`, `/load`, `/reload`, `/diff_prev` | Session | Board + project ops; `/fs/import` is base64 upload → `fp/`/`sym/` | `do_POST`; route list also in `docs/STUDIO.md` |

Explicit `/fs/open` and `/fs/read` paths use `_abs` through `open_file` /
`_read`: realpath must be inside `ROOT`; `.ocd-users` and other users'
shelves are refused (`apps/studio.py:656`, `apps/studio.py:729`,
`apps/studio.py:1037`). This is not a universal filesystem sandbox:
`/fs/import` constructs its destination under `BASE/fp` or `BASE/sym`
without `_abs`, so it relies on trusted directory/symlink layout in addition
to the open-board check (`apps/studio.py:748`, `apps/studio.py:1653`).

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
- **SSRF-class:** KB downloads reject non-public destinations and recheck
  redirects (`ocdcircuit/kb.py:172`, `ocdcircuit/kb.py:213`). The connection
  is not pinned to the validated DNS answer (`ocdcircuit/kb.py:201`,
  `ocdcircuit/kb.py:233`), so this is not a complete egress boundary.
  LCSC discovery uses a textual `endswith("lcsc.com")` check, not a
  domain-label-aware allowlist (`ocdcircuit/kb.py:257`).
- **Tampering:** Chat proposals pass `_abs` and a writable-extension check.
  The open board is built before persistence, sibling `.ocd` files are
  parsed, and other writable files are saved as text (`apps/studio.py:1060`).
  Manual `/chat/apply` can persist a board with DRC errors and report them
  afterward (`apps/studio.py:2424`). Build/DRC checks are not a guarantee
  that vendor or model output is safe or electrically correct.

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
| Public HTTPS destination checks, redirect revalidation, 64MiB limit, PDF magic for PDF destinations | Private-address requests (partial), cleartext fetch, oversized downloads; not PDF parser safety | `ocdcircuit/kb.py:172`, `ocdcircuit/kb.py:213`, `ocdcircuit/kb.py:223` |
| Single-flight chat and scan; 40-photo scan cap and temporary-tree cleanup | Concurrent spend / scan disk accumulation, not cumulative quotas | `apps/studio.py:1116`, `apps/studio.py:2197`, `apps/studio.py:2322` |
| `llm_base()` http(s)+host check | Non-URL / empty base | `envcfg.llm_base` |
| Env secret redaction in doctor | Accidental secret log in health output | `envcfg.summary` |
| Atomic writes for users + kb | Truncated credential/doc files | `_atomic_write`, `kb._download` |
| Git hash charset check on `/vcs/diff` | Arg injection via rev | `_GIT_HASH_RE` |

**Single points of failure:** (1) studio session cookie authorizes nearly
all high-impact routes; (2) localhost bind is the only control against
remote exposure; (3) process env + optional knoll path carry secrets and
code trust with no second factor.

**Security policy cross-check:** `SECURITY.md` correctly describes a loopback
listener (`apps/studio.py:2659`), open signup (`apps/studio.py:1663`), and
cookie sessions (`apps/studio.py:501`, `apps/studio.py:1411`). Neither the
listener nor these cookies turn Studio into an internet-facing multi-tenant
service. Cookie HttpOnly prevents script access to the token, not script use
of an authenticated session; the CSP has no script restriction
(`apps/studio.py:1326`).

## Abuse cases (authenticated, hostile)

1. **Shared-tree rewrite:** User A and user B both authed; B opens/writes a
   board under public `ROOT` (not under `.users/B/`) via `/fs` or shelf-adjacent
   paths — enabling path in `_abs` when `_shelf_owner` is `None`.
2. **Compute burn:** Repeated authenticated `/solve`, `/reroute`, `/scan`, or
   `/chat` requests consume shared compute or vendor credits. Scan and chat
   reject overlapping calls, but have no per-user cumulative budget
   (`apps/studio.py:1942`, `apps/studio.py:2015`, `apps/studio.py:2197`,
   `apps/studio.py:1116`).
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
