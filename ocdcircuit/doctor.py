"""Doctor: is the tooling itself healthy? Python version, optional
binaries (ngspice), and the plugin registry (every kind has an active).
No board needed — pass None. One check behind means one degraded feature,
never a mystery traceback later."""
from __future__ import annotations
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit import Board


def doctor(board: Board | None = None) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        import numpy  # noqa: F401
        add("numpy", True, str(numpy.__version__))
    except ImportError:
        add("numpy", False, "missing (SIMD placer falls back to scalar)")
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
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}
