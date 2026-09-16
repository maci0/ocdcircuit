"""Process-level environment knobs: read, validate, summarize.

board.toml (config:toml) is project-level and separate — CLI flags win over
it. These are the OCD_*/JLCPCB_*/SOURCE_DATE_EPOCH knobs deployers and
operators set in the shell. Malformed values raise EnvError (or, for the
studio port/root, fall back with a stderr note — a bad port must not kill
an interactive session).

summary() is what `ocd doctor` prints: every known knob, secrets as
set/unset only. See .env.example for the operator-facing list.
"""
from __future__ import annotations

import os
import sys
from typing import Callable
from urllib.parse import urlparse


class EnvError(ValueError):
    """Malformed or out-of-range environment variable."""


# Names whose values must never appear in logs or doctor output.
_SECRETS = frozenset({
    "OCD_LLM_KEY", "JLCPCB_API_KEY", "JLCPCB_API_SECRET",
})


def env_str(name: str, default: str = "") -> str:
    """Raw string; unset and empty both yield `default` (empty is not a
    distinct value for these knobs)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def env_int(name: str, default: int, *, lo: int | None = None,
            hi: int | None = None) -> int:
    """Integer from the environment. Unset/empty → default; bad or
    out-of-range → EnvError with the knob name in the message."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        n = int(raw.strip())
    except ValueError as e:
        raise EnvError(f"{name}={raw!r} is not an int") from e
    if lo is not None and n < lo:
        raise EnvError(f"{name} must be >= {lo} (got {n})")
    if hi is not None and n > hi:
        raise EnvError(f"{name} must be <= {hi} (got {n})")
    return n


def env_float(name: str, default: float, *, lo: float | None = None,
              hi: float | None = None) -> float:
    """Float from the environment. Unset/empty → default; bad or
    out-of-range → EnvError."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        n = float(raw.strip())
    except ValueError as e:
        raise EnvError(f"{name}={raw!r} is not a number") from e
    if lo is not None and n < lo:
        raise EnvError(f"{name} must be >= {lo} (got {n})")
    if hi is not None and n > hi:
        raise EnvError(f"{name} must be <= {hi} (got {n})")
    return n


def llm_base() -> str:
    """OpenAI-compatible API root. Must be http(s) with a host."""
    base = env_str("OCD_LLM_BASE", "http://127.0.0.1:11434/v1").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise EnvError(
            f"OCD_LLM_BASE={base!r} must be an http(s) URL with a host")
    return base


def llm_key() -> str:
    """Bearer token; empty is allowed (local Ollama / LM Studio)."""
    return os.environ.get("OCD_LLM_KEY", "")


def llm_model() -> str:
    return env_str("OCD_LLM_MODEL", "qwen3.5:latest")


def llm_embed() -> str:
    return env_str("OCD_LLM_EMBED", "nomic-embed-text")


def llm_max_tokens(default: int = 8192) -> int | None:
    """Completion cap. Unset → `default`; `0` omits the field; else int ≥ 1."""
    raw = os.environ.get("OCD_LLM_MAX_TOKENS", "").strip()
    if raw == "0":
        return None
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError as e:
        raise EnvError(f"OCD_LLM_MAX_TOKENS={raw!r} is not an int") from e
    if n < 1:
        raise EnvError(f"OCD_LLM_MAX_TOKENS must be >= 1 (got {n})")
    return n


def studio_port(default: int = 8077) -> int:
    """TCP port for the studio. Bad / out-of-range falls back to `default`
    with a stderr note (interactive boot must not die on a typo)."""
    raw = os.environ.get("OCD_PORT", str(default))
    try:
        port = int(raw)
        if not (1 <= port <= 65535):
            raise ValueError("out of range")
        return port
    except ValueError:
        print(f"studio: bad OCD_PORT {raw!r}, using {default}",
              file=sys.stderr)
        return default


def studio_root(fallback: str) -> str:
    """Project browser root. Bad OCD_ROOT falls back to `fallback`."""
    raw = os.environ.get("OCD_ROOT")
    root = os.path.abspath(raw or fallback)
    if not os.path.isdir(root):
        print(f"studio: OCD_ROOT {raw!r} is not a directory, "
              f"using {fallback}", file=sys.stderr)
        return fallback
    return root


def scan_max_mb(default: float = 24.0) -> float:
    """Vision request budget in MB; must be positive."""
    return env_float("OCD_SCAN_MAX_MB", default, lo=0.1)


def source_date_epoch(default: int = 0) -> int:
    """Unix epoch for fab.zip entry mtimes. Negatives rejected; the exporter
    clamps to the ZIP DOS-date window (1980-01-01 .. 2107-12-31)."""
    return env_int("SOURCE_DATE_EPOCH", default, lo=0)


def scanbench_reps(default: int = 1) -> int:
    return env_int("SCANBENCH_REPS", default, lo=1, hi=100)


def knoll_src() -> str | None:
    """Optional knoll checkout path for live JLC pricing."""
    raw = os.environ.get("KNOLL_SRC")
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _kicad_3d_candidates() -> list[str]:
    """Likely KiCad 3dmodels trees across Linux / macOS / Windows installs."""
    cands: list[str] = [
        "/usr/share/kicad/3dmodels",
        "/usr/local/share/kicad/3dmodels",
        os.path.expanduser("~/.local/share/kicad/3dmodels"),
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels",
        "/Applications/KiCad.app/Contents/SharedSupport/3dmodels",
    ]
    for root_key in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_key)
        if root:
            cands.append(os.path.join(root, "KiCad", "share", "kicad", "3dmodels"))
    return cands


def kicad_3d_dir(default: str = "/usr/share/kicad/3dmodels") -> str:
    """Local STEP library for export probes. Env wins; else first existing
    candidate; else `default` (Linux FHS — also what .env.example shows)."""
    override = os.environ.get("KICAD10_3DMODEL_DIR")
    if override is not None and override.strip() != "":
        return override.strip()
    for cand in _kicad_3d_candidates():
        if os.path.isdir(cand):
            return cand
    return default


def kicad_3d_viewer_cfg() -> str | None:
    """Path to an existing KiCad 3d_viewer.json, or None.

    Probes XDG_CONFIG_HOME / ~/.config (Linux), ~/Library/Preferences
    (macOS), and %APPDATA% (Windows) for common KiCad version dirs."""
    bases: list[str] = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        bases.append(xdg)
    bases.append(os.path.expanduser("~/.config"))
    bases.append(os.path.expanduser("~/Library/Preferences"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        bases.append(appdata)
    for base in bases:
        for ver in ("10.0", "9.0", "8.0", "7.0"):
            p = os.path.join(base, "kicad", ver, "3d_viewer.json")
            if os.path.isfile(p):
                return p
    return None


def jlc_offline_db() -> str | None:
    """First existing kicad-mcp JLC SQLite path, or None if none present."""
    cands: list[str] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        cands.append(os.path.join(xdg, "kicad-mcp", "jlcpcb_parts.db"))
    cands.append(os.path.expanduser("~/.local/share/kicad-mcp/jlcpcb_parts.db"))
    cands.append(os.path.expanduser(
        "~/Library/Application Support/kicad-mcp/jlcpcb_parts.db"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(os.path.join(local, "kicad-mcp", "jlcpcb_parts.db"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def jlc_env_creds() -> tuple[str, str, str]:
    """(app_id, access_key, secret) from env only — empty strings when unset.
    Caller merges ~/.secrets/jlcpcb; this never logs the values."""
    return (
        os.environ.get("JLCPCB_APP_ID", ""),
        os.environ.get("JLCPCB_API_KEY", ""),
        os.environ.get("JLCPCB_API_SECRET", ""),
    )


def _probe(name: str, fn: Callable[[], object]) -> tuple[bool, str]:
    """Run a getter; return (ok, detail). Secrets never include the value."""
    try:
        val = fn()
    except EnvError as e:
        return False, str(e)
    if name in _SECRETS:
        return True, "set" if val else "unset"
    return True, repr(val)


def summary() -> list[dict[str, object]]:
    """Redacted active configuration for doctor / operators."""
    rows: list[tuple[str, Callable[[], object]]] = [
        ("OCD_LLM_BASE", llm_base),
        ("OCD_LLM_MODEL", llm_model),
        ("OCD_LLM_EMBED", llm_embed),
        ("OCD_LLM_KEY", llm_key),
        ("OCD_LLM_MAX_TOKENS", llm_max_tokens),
        ("OCD_PORT", lambda: env_int("OCD_PORT", 8077, lo=1, hi=65535)),
        ("OCD_ROOT", lambda: os.environ.get("OCD_ROOT") or "(unset → board dir)"),
        ("OCD_SCAN_MAX_MB", scan_max_mb),
        ("SOURCE_DATE_EPOCH", source_date_epoch),
        ("KICAD10_3DMODEL_DIR", kicad_3d_dir),
        ("KNOLL_SRC", lambda: knoll_src() or "(unset)"),
        ("JLCPCB_APP_ID", lambda: os.environ.get("JLCPCB_APP_ID", "")),
        ("JLCPCB_API_KEY", lambda: os.environ.get("JLCPCB_API_KEY", "")),
        ("JLCPCB_API_SECRET", lambda: os.environ.get("JLCPCB_API_SECRET", "")),
        ("SCANBENCH_REPS", scanbench_reps),
        ("XRAY", lambda: os.environ.get("XRAY") or "(unset)"),
    ]
    # APP_ID is not a secret by itself but is useless without keys; treat as
    # non-secret so operators can confirm which app is wired.
    out: list[dict[str, object]] = []
    for name, fn in rows:
        ok, detail = _probe(name, fn)
        if name == "JLCPCB_APP_ID":
            detail = "set" if os.environ.get("JLCPCB_APP_ID") else "unset"
            ok = True
        out.append({"name": f"env:{name}", "ok": ok, "detail": detail})
    return out


def _selfcheck() -> None:
    import tempfile
    saved = {k: os.environ.pop(k, None) for k in (
        "OCD_LLM_BASE", "OCD_LLM_MAX_TOKENS", "OCD_SCAN_MAX_MB",
        "SOURCE_DATE_EPOCH", "OCD_PORT", "SCANBENCH_REPS",
        "KICAD10_3DMODEL_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
    try:
        assert llm_base().startswith("http")
        assert llm_max_tokens() == 8192
        os.environ["OCD_LLM_MAX_TOKENS"] = "0"
        assert llm_max_tokens() is None
        os.environ["OCD_LLM_MAX_TOKENS"] = "bogus"
        try:
            llm_max_tokens()
            raise AssertionError("expected EnvError")
        except EnvError:
            pass
        del os.environ["OCD_LLM_MAX_TOKENS"]
        assert scan_max_mb() == 24.0
        os.environ["OCD_SCAN_MAX_MB"] = "0"
        try:
            scan_max_mb()
            raise AssertionError("expected EnvError")
        except EnvError:
            pass
        del os.environ["OCD_SCAN_MAX_MB"]
        assert source_date_epoch() == 0
        os.environ["SOURCE_DATE_EPOCH"] = ""
        assert source_date_epoch() == 0  # empty ≡ unset
        os.environ["SOURCE_DATE_EPOCH"] = "nope"
        try:
            source_date_epoch()
            raise AssertionError("expected EnvError")
        except EnvError:
            pass
        del os.environ["SOURCE_DATE_EPOCH"]
        os.environ["OCD_PORT"] = "99999"
        assert studio_port() == 8077  # out of range → fallback
        del os.environ["OCD_PORT"]
        os.environ["OCD_LLM_BASE"] = "not-a-url"
        try:
            llm_base()
            raise AssertionError("expected EnvError")
        except EnvError:
            pass
        del os.environ["OCD_LLM_BASE"]
        with tempfile.TemporaryDirectory() as d:
            os.environ["OCD_ROOT"] = os.path.join(d, "missing")
            assert studio_root(d) == d
            del os.environ["OCD_ROOT"]
            fake = os.path.join(d, "3dmodels")
            os.makedirs(fake)
            os.environ["KICAD10_3DMODEL_DIR"] = fake
            assert kicad_3d_dir() == fake
            del os.environ["KICAD10_3DMODEL_DIR"]
            # probe finds a real tree when present; else documented default
            got = kicad_3d_dir()
            assert os.path.isdir(got) or got.endswith("3dmodels"), got
            cfg_dir = os.path.join(d, "kicad", "9.0")
            os.makedirs(cfg_dir)
            cfg = os.path.join(cfg_dir, "3d_viewer.json")
            open(cfg, "w", encoding="utf-8").write("{}")
            os.environ["XDG_CONFIG_HOME"] = d
            assert kicad_3d_viewer_cfg() == cfg
            del os.environ["XDG_CONFIG_HOME"]
            db_dir = os.path.join(d, "kicad-mcp")
            os.makedirs(db_dir)
            db = os.path.join(db_dir, "jlcpcb_parts.db")
            open(db, "wb").write(b"")
            os.environ["XDG_DATA_HOME"] = d
            assert jlc_offline_db() == db
            del os.environ["XDG_DATA_HOME"]
            assert jlc_offline_db() is None or os.path.isfile(
                jlc_offline_db() or "")
        rows = summary()
        assert all(str(r["name"]).startswith("env:") for r in rows)
        assert any(r["name"] == "env:OCD_LLM_KEY" and r["detail"] in ("set", "unset")
                   for r in rows)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    _selfcheck()
    print("envcfg self-check ok")
