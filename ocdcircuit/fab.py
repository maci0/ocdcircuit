"""Manufacturer capability profiles. Data, not code: DRC + studio read this.

Sources (checked Sep 2026, standard tiers — re-verify before ordering):
- JLCPCB rigid capabilities: https://jlcpcb.com/capabilities/Capabilities
- PCBWay standard PCB: https://www.pcbway.com/capabilities.html
- OSH Park: https://oshpark.com (fixed stackup, ENIG only)
- Seeed Fusion: https://www.seeedstudio.com/fusion.html
- Aisler: https://aisler.net (HDSK stackup)
- Eurocircuits pattern/drill classes: https://www.eurocircuits.com/technical-guidelines/pcb-design-guidelines/classification/
- NextPCB capabilities: https://www.nextpcb.com/capabilities
- ALLPCB prototype: https://www.allpcb.com
- Sierra Circuits proto: https://www.protoexpress.com/products/
- Advanced Circuits: https://www.4pcb.com/pcb-capabilities.html

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
    "eurocircuits": {
        "name": "Eurocircuits standard pool", "layers": (2, 4, 6, 8),
        "min_trace": 0.15, "min_space": 0.15, "min_drill": 0.3,
        "annular": 0.15, "edge": 0.5, "max_w": 400, "max_h": 500,
        "thickness": (0.5, 2.4), "finishes": ("ENIG", "HASL-LF"),
        "url": "https://www.eurocircuits.com/technical-guidelines/pcb-design-guidelines/classification/",
    },
    "nextpcb": {
        "name": "NextPCB standard", "layers": (1, 2, 4, 6, 8),
        "min_trace": 0.09, "min_space": 0.09, "min_drill": 0.2,
        "annular": 0.15, "edge": 0.3, "max_w": 500, "max_h": 1100,
        "thickness": (0.4, 3.0), "finishes": ("HASL", "HASL-LF", "ENIG", "OSP"),
        "url": "https://www.nextpcb.com/capabilities",
    },
    "allpcb": {
        "name": "ALLPCB prototype", "layers": (1, 2, 4, 6, 8),
        "min_trace": 0.1, "min_space": 0.1, "min_drill": 0.2,
        "annular": 0.15, "edge": 0.3, "max_w": 500, "max_h": 1100,
        "thickness": (0.4, 3.0), "finishes": ("HASL", "HASL-LF", "ENIG"),
        "url": "https://www.allpcb.com",
    },
    "sierra": {
        "name": "Sierra Circuits proto", "layers": (2, 4, 6, 8),
        "min_trace": 0.09, "min_space": 0.09, "min_drill": 0.15,
        "annular": 0.1, "edge": 0.25, "max_w": 457, "max_h": 610,
        "thickness": (0.5, 3.2), "finishes": ("ENIG", "HASL-LF"),
        "url": "https://www.protoexpress.com/products/",
    },
    "advanced": {
        "name": "Advanced Circuits standard", "layers": (1, 2, 4, 6, 8),
        "min_trace": 0.09, "min_space": 0.09, "min_drill": 0.25,
        "annular": 0.15, "edge": 0.25, "max_w": 533, "max_h": 914,
        "thickness": (0.8, 2.4), "finishes": ("HASL", "HASL-LF", "ENIG"),
        "url": "https://www.4pcb.com/pcb-capabilities.html",
    },
}

DEFAULT = "jlc"  # the fallback profile when a board names none

# Monogram badges: (initials, bg, fg) per fab. Fallback identity when no
# vendor tile exists — logo() prefers the scraped tile in assets/fabs/.
MARKS: dict[str, tuple[str, str, str]] = {
    "jlc": ("JLC", "#0b5cab", "#ffffff"),
    "pcbway": ("PW", "#0e9f6e", "#ffffff"),
    "jlc-flex": ("FPC", "#073a6b", "#ffd8a0"),
    "oshpark": ("OSH", "#5e2b97", "#ffffff"),
    "seeed": ("SEE", "#00875a", "#ffffff"),
    "aisler": ("AIS", "#14b8a6", "#06281f"),
    "eurocircuits": ("EC", "#d9480f", "#ffffff"),
    "nextpcb": ("NPC", "#1f2937", "#fbbf24"),
    "allpcb": ("ACB", "#0891b2", "#ffffff"),
    "sierra": ("SC", "#7c2d12", "#ffd8a0"),
    "advanced": ("ADV", "#b91c1c", "#ffffff"),
}

# Vendor tiles scraped from the fabs' own sites (ocdcircuit/assets/fabs/),
# normalized to 192×64 transparent PNGs. SOURCES below records where each
# came from; logos are their owners' trademarks, used nominatively to name
# the fab a quote row / strip cell points at.
SOURCES: dict[str, str] = {
    "jlc": "https://rs.jlcpcb.com/static/image/homepage/jlcpcb-logo.webp",
    "jlc-flex": "same JLCPCB wordmark (flex is a JLC process, not a brand)",
    "pcbway": "https://www.pcbway.com/img/images/iconspirit.png (logo sprite @0,0 141x41)",
    "oshpark": "https://oshpark.com apple-touch-icon (gear mark)",
    "seeed": "https://media-cdn.seeedstudio.com/.../logo_2018_horizontal.png",
    "aisler": "https://cdn.aisler.net/packs/static/images/logo_medium-....png",
    "eurocircuits": "https://www.eurocircuits.com/.../build/img/logo.png",
    "nextpcb": "https://static.nextpcb.com/images/newNavIcon/logo2025.svg",
    "allpcb": "https://www.allpcb.com/img/img/logo.webp",
    "sierra": "https://fbfa5ace.delivery.rocketcdn.me/.../logo.svg (sierra-circuits theme)",
    "advanced": "https://www.advancedpcb.com/getattachment/.../Advanced-PCB-logo.svg",
}


def logo_bytes(key: str = DEFAULT) -> tuple[bytes, str]:
    """Raw logo bytes + Content-Type. Vendor PNG from assets/fabs/ when
    present, else the MARKS monogram as SVG. KeyError on unknown fab."""
    if key not in PROFILES:
        raise KeyError(f"unknown fab {key!r} (have {sorted(PROFILES)})")
    try:
        import importlib.resources as _res
        raw = _res.files("ocdcircuit.assets.fabs").joinpath(f"{key}.png").read_bytes()
        return raw, "image/png"
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass
    initials, bg, fg = MARKS[key]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="28">'
           f'<rect width="48" height="28" rx="6" fill="{bg}"/>'
           f'<text x="24" y="19" font-family="Arial,sans-serif" font-size="12"'
           f' font-weight="bold" text-anchor="middle" fill="{fg}">{initials}</text></svg>')
    return svg.encode(), "image/svg+xml"


def logo(key: str = DEFAULT) -> str:
    """Fab logo as a data URI (quote rows embed inline). Landing strip uses
    /fab-logo/<key> instead so the first HTML byte is not ~100 KB of tiles.
    KeyError on unknown fab, like get()."""
    from urllib.parse import quote as _q
    import base64 as _b64
    raw, ctype = logo_bytes(key)
    if ctype == "image/png":
        return "data:image/png;base64," + _b64.b64encode(raw).decode()
    return "data:image/svg+xml," + _q(raw.decode(), safe="")


def get(key: str = DEFAULT) -> FabProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise KeyError(f"unknown fab {key!r} (have {sorted(PROFILES)})")


def list_fabs() -> list[str]:
    return sorted(PROFILES)
