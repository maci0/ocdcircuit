"""Emit a CycloneDX 1.5 JSON SBOM from the pinned manifests (stdlib only).

Reads `requirements-dev.txt` and `[project.optional-dependencies]` /
`[build-system].requires` in `pyproject.toml`. No network — inventory
only, for scanners and release attestation.

Usage: python -m tools.sbom > sbom.cdx.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s#]+)")
_TOML_PIN = re.compile(r'"([A-Za-z0-9_.-]+)==([^"]+)"')


def _pins_from_requirements(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _PIN.match(s)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _pins_from_pyproject(text: str) -> list[tuple[str, str, str]]:
    """Pull == pins; scope is optional (extras) or excluded (build-system)."""
    out: list[tuple[str, str, str]] = []
    # build-system.requires block first
    bm = re.search(
        r"\[build-system\](.*?)(?=\n\[|\Z)", text, re.S)
    build_blob = bm.group(1) if bm else ""
    build_names = {_norm(m.group(1)) for m in _TOML_PIN.finditer(build_blob)}
    for m in _TOML_PIN.finditer(text):
        name, ver = m.group(1), m.group(2)
        scope = "excluded" if _norm(name) in build_names else "optional"
        out.append((name, ver, scope))
    return out


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def inventory() -> list[dict[str, object]]:
    rows: list[tuple[str, str, str]] = []  # name, version, scope
    for name, ver in _pins_from_requirements(
            (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")):
        rows.append((_norm(name), ver, "required"))
    for name, ver, scope in _pins_from_pyproject(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")):
        rows.append((_norm(name), ver, scope))
    seen: set[str] = set()
    comps: list[dict[str, object]] = []
    for name, ver, scope in rows:
        if name in seen:
            continue
        seen.add(name)
        purl = f"pkg:pypi/{name}@{ver}"
        comps.append({
            "type": "library",
            "name": name,
            "version": ver,
            "bom-ref": purl,
            "purl": purl,
            "scope": scope,
        })
    return comps


def main() -> int:
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("usage: python -m tools.sbom > sbom.cdx.json\n"
              "  Emit a CycloneDX 1.5 JSON SBOM of the pinned dependencies to\n"
              "  stdout (no network). Reads requirements-dev.txt and pyproject.toml.")
        return 0
    raw = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    nm = re.search(r'^name\s*=\s*"([^"]+)"', raw, re.M)
    vm = re.search(r'^version\s*=\s*"([^"]+)"', raw, re.M)
    name = nm.group(1) if nm else "ocdcircuit"
    version = vm.group(1) if vm else "0.0.0"
    doc: dict[str, object] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": name,
                "version": version,
                "bom-ref": f"pkg:pypi/{name}@{version}",
            },
        },
        "components": inventory(),
    }
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
