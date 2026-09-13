"""Manufacturer capability profiles. Data, not code: DRC + studio read this.

Sources (checked Sep 2026, standard tiers — re-verify before ordering):
- JLCPCB rigid capabilities: https://jlcpcb.com/capabilities/Capabilities
- PCBWay standard PCB: https://www.pcbway.com/capabilities.html
- OSH Park: https://oshpark.com (fixed stackup, ENIG only)
- Seeed Fusion: https://www.seeedstudio.com/fusion.html
- Aisler: https://aisler.net (HDSK stackup)

Units: mm. min_drill = finished PTH min. annular = min ring.
"""
from __future__ import annotations

FabProfile = dict[str, object]

PROFILES: dict[str, FabProfile] = {
    "jlc": {
        "name": "JLCPCB standard", "layers": (1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32),
        "min_trace": 0.09, "min_space": 0.09, "min_drill": 0.2,
        "annular": 0.15, "edge": 0.3, "max_w": 400, "max_h": 500,
        "thickness": (0.4, 2.0), "finishes": ("HASL", "HASL-LF", "ENIG", "OSP"),
        "url": "https://jlcpcb.com/capabilities/Capabilities",
    },
    "pcbway": {
        "name": "PCBWay standard", "layers": (1, 2, 4, 6, 8, 10),
        "min_trace": 0.09, "min_space": 0.09, "min_drill": 0.2,
        "annular": 0.15, "edge": 0.3, "max_w": 500, "max_h": 1100,
        "thickness": (0.4, 2.4), "finishes": ("HASL", "HASL-LF", "ENIG", "OSP"),
        "url": "https://www.pcbway.com/capabilities.html",
    },
    "jlc-flex": {
        "name": "JLCPCB flex (FPC)", "layers": (1, 2, 4),
        "min_trace": 0.1, "min_space": 0.1, "min_drill": 0.1,
        "annular": 0.18, "edge": 0.3, "max_w": 234, "max_h": 490,
        "thickness": (0.07, 0.45), "finishes": ("ENIG",),
        "url": "https://jlcpcb.com/capabilities/flex-pcb-capabilities",
    },
    "oshpark": {
        "name": "OSH Park", "layers": (2, 4),
        "min_trace": 0.1524, "min_space": 0.1524, "min_drill": 0.508,
        "annular": 0.18, "edge": 0.5, "max_w": 200, "max_h": 200,
        "thickness": (1.6, 1.6), "finishes": ("ENIG",),
        "url": "https://oshpark.com",
    },
    "seeed": {
        "name": "Seeed Fusion", "layers": (1, 2, 4),
        "min_trace": 0.1, "min_space": 0.1, "min_drill": 0.3,
        "annular": 0.15, "edge": 0.3, "max_w": 400, "max_h": 400,
        "thickness": (0.6, 2.0), "finishes": ("HASL", "HASL-LF", "ENIG"),
        "url": "https://www.seeedstudio.com/fusion.html",
    },
    "aisler": {
        "name": "Aisler HDSK", "layers": (2, 4),
        "min_trace": 0.1, "min_space": 0.1, "min_drill": 0.3,
        "annular": 0.15, "edge": 0.3, "max_w": 300, "max_h": 400,
        "thickness": (1.0, 1.6), "finishes": ("ENIG",),
        "url": "https://aisler.net",
    },
}

DEFAULT = "jlc"


def get(key: str = "jlc") -> FabProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise KeyError(f"unknown fab {key!r} (have {sorted(PROFILES)})")


def list_fabs() -> list[str]:
    return sorted(PROFILES)
