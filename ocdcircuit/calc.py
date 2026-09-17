"""Embedded calculators (flux-style, no tab-switching): IPC-2221 trace
width (and its inverse: capacity of an existing width), via current
capacity, voltage divider, microstrip impedance. Pure functions, stdlib only."""
from __future__ import annotations
import math


def trace_width(amps: float, temp_rise: float = 10.0, copper_oz: float = 1.0,
                external: bool = True) -> float:
    """IPC-2221 width in mm for given current. k/b/c per standard."""
    k, b, c = (0.048, 0.44, 0.725) if external else (0.024, 0.44, 0.725)
    area_mils2 = (amps / (k * temp_rise ** b)) ** (1.0 / c)
    width_mils: float = area_mils2 / (copper_oz * 1.378)
    return width_mils * 0.0254


def trace_amps(width_mm: float, temp_rise: float = 10.0, copper_oz: float = 1.0,
                 external: bool = True) -> float:
    """IPC-2221 current capacity in A of an existing trace width in mm.
    Inverse of trace_width (same k/b/c): "is this 0.3mm trace enough for 2A?"
    """
    k, b, c = (0.048, 0.44, 0.725) if external else (0.024, 0.44, 0.725)
    area_mils2: float = (width_mm / 0.0254) * (copper_oz * 1.378)
    out: float = k * temp_rise ** b * area_mils2 ** c
    return out


def via_amps(drill: float, temp_rise: float = 10.0) -> float:
    """Conservative via current: ~ drill circumference rule (A per mm)."""
    out: float = drill * 3.0 * (temp_rise / 10.0) ** 0.5
    return out


def divider(vin: float, r_top: float, r_bot: float) -> float:
    """Vout of a resistive divider."""
    den = r_top + r_bot
    if den == 0:
        raise ValueError("divider resistors sum to zero")
    return vin * r_bot / den


def divider_pick(vin: float, vout: float, r_bot: float = 10000.0) -> float:
    """Top resistor for target Vout given bottom resistor."""
    if vout == 0:
        raise ValueError("vout must be nonzero")
    return r_bot * (vin / vout - 1.0)


def microstrip_z0(w_mm: float, h_mm: float = 0.2, t_mm: float = 0.035,
                  er: float = 4.4) -> float:
    """Single-ended microstrip Z0 (Hammerstad-Jensen, closed form).
    w = trace width, h = dielectric height, t = copper thickness,
    er = relative permittivity (FR4 4.4). Estimate, not a field solver."""
    if min(w_mm, h_mm, t_mm, er) <= 0:
        raise ValueError("microstrip geometry must be positive")
    u = w_mm / h_mm
    er_eff = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 / u)
    if u <= 1:
        return 60 / math.sqrt(er_eff) * math.log(8 / u + u / 4)
    return (120 * math.pi / (math.sqrt(er_eff) *
                             (u + 1.393 + 0.667 * math.log(u + 1.444))))


def microstrip_diff(w_mm: float, s_mm: float, h_mm: float = 0.2,
                    t_mm: float = 0.035, er: float = 4.4) -> float:
    """Edge-coupled differential Zdiff from Z0 + coupling term
    (Wadell approximation). s = edge-to-edge spacing."""
    if min(w_mm, s_mm, h_mm) <= 0:
        raise ValueError("diff-pair geometry must be positive")
    z0 = microstrip_z0(w_mm, h_mm, t_mm, er)
    return 2 * z0 * (1 - 0.48 * math.exp(-0.96 * s_mm / h_mm))
