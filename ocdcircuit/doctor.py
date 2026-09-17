"""Doctor: is the tooling itself healthy? Python version (≥3.14, pinned
in `.python-version`), optional binaries (ngspice, chromium, …), gate
tools (mypy/ruff — `make setup`), the plugin registry (every kind has an
active), and a redacted dump of the process env knobs (see
ocdcircuit.envcfg / `.env.example`). No board needed — pass None.

`ok` is True when required checks pass (Python + mounted plugins +
well-formed env values). Optional tools still appear as ✗ rows when
missing so a contributor sees the gap up front; they degrade a feature,
they do not fail the gate. One check behind means one degraded feature,
never a mystery traceback later."""
from __future__ import annotations
import os
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board

# Host tools / packages the code imports opportunistically. Missing → feature
# falls back; required gate is Python + plugin registry only. Env rows are
# never optional: a typo'd OCD_LLM_BASE must fail doctor so it is fixed
# before the first chat request.
_OPTIONAL = frozenset({
    "numpy", "rich", "pillow", "ngspice", "kicad-cli", "pdftotext", "chromium",
    # gate tools: needed for `make check`, not for running boards
    "mypy", "ruff",
})


def _find_gate_tool(name: str) -> str | None:
    """PATH first, then project `.venv/bin` (matches Makefile after `make setup`)."""
    p = shutil.which(name)
    if p:
        return p
    local = os.path.join(".venv", "bin", name)
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return os.path.abspath(local)
    return None

# PATH names first; then common install locations when the binary is not
# on PATH (macOS .app bundles, Windows Program Files).
_CHROME_NAMES = (
    "chromium", "chromium-browser", "google-chrome",
    "google-chrome-stable", "chrome", "msedge", "microsoft-edge",
)


def find_chromium() -> str | None:
    """First Chromium/Chrome/Edge binary usable for headless studio gates."""
    for name in _CHROME_NAMES:
        p = shutil.which(name)
        if p:
            return p
    extras = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        extras.append(os.path.join(
            local, "Google", "Chrome", "Application", "chrome.exe"))
        extras.append(os.path.join(
            local, "Microsoft", "Edge", "Application", "msedge.exe"))
    for root_key in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_key)
        if not root:
            continue
        extras.append(os.path.join(
            root, "Google", "Chrome", "Application", "chrome.exe"))
        extras.append(os.path.join(
            root, "Microsoft", "Edge", "Application", "msedge.exe"))
    for p in extras:
        if os.path.isfile(p):
            return p
    return None


def doctor(board: Board | None = None) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python", sys.version_info >= (3, 14), sys.version.split()[0])
    try:
        import numpy
        add("numpy", True, str(numpy.__version__))
    except ImportError:
        add("numpy", False, "missing (SIMD placer falls back to scalar)")
    try:
        import PIL
        add("pillow", True, str(getattr(PIL, "__version__", "ok")))
    except ImportError:
        add("pillow", False, "missing (pcbscan JPEG/HEIC fall back to PNG-only)")
    ng = shutil.which("ngspice")
    add("ngspice", ng is not None,
        ng or "missing (simulate:ngspice unavailable, mna still works)")
    kc = shutil.which("kicad-cli")
    add("kicad-cli", kc is not None,
        kc or "missing (sch ERC + pcb DRC validation skipped in tests)")
    pt = shutil.which("pdftotext")
    add("pdftotext", pt is not None,
        pt or "missing (kb/ datasheet PDFs stay unsearchable; "
              "install poppler-utils)")
    chrom = find_chromium()
    add("chromium", chrom is not None,
        chrom or "missing (studio browser half skipped; "
                 "CI uses runner Google Chrome)")
    mypy_bin = _find_gate_tool("mypy")
    add("mypy", mypy_bin is not None,
        mypy_bin or "missing (run: make setup — pins gate tools in .venv)")
    ruff_bin = _find_gate_tool("ruff")
    add("ruff", ruff_bin is not None,
        ruff_bin or "missing (run: make setup — pins gate tools in .venv)")
    try:
        import rich  # noqa: F401
        add("rich", True, "pretty CLI on")
    except ImportError:
        add("rich", False, "missing (plain-text CLI fallback)")
    if board is not None:
        reg = board.plugins()
        kinds = sorted({k.split(":")[0] for k in reg.list()})
        for kind in kinds:
            try:
                active = reg.get(kind)
                from .core import Plugin
                assert isinstance(active, Plugin)
                add(f"plugin:{kind}", True, f"{kind}:{active.key}")
            except (KeyError, AssertionError) as e:
                add(f"plugin:{kind}", False, str(e))
    from . import envcfg
    for row in envcfg.summary():
        add(str(row["name"]), bool(row["ok"]), str(row["detail"]))
    ok = all(c["ok"] for c in checks if str(c["name"]) not in _OPTIONAL)
    return {"ok": ok, "checks": checks}
