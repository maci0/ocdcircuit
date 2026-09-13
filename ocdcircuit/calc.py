"""Embedded calculators (flux-style, no tab-switching): IPC-2221 trace
width, via current capacity, voltage divider. Pure functions, stdlib only."""
from __future__ import annotations


def trace_width(amps: float, temp_rise: float = 10.0, copper_oz: float = 1.0,
                external: bool = True) -> float:
    """IPC-2221 width in mm for given current. k/b/c per standard."""
    k, b, c = (0.048, 0.44, 0.725) if external else (0.024, 0.44, 0.725)
    area_mils2 = (amps / (k * temp_rise ** b)) ** (1.0 / c)
    width_mils: float = area_mils2 / (copper_oz * 1.378)
    return width_mils * 0.0254


def via_amps(drill: float, temp_rise: float = 10.0) -> float:
    """Conservative via current: ~ drill circumference rule (A per mm)."""
    out: float = drill * 3.0 * (temp_rise / 10.0) ** 0.5
    return out


def divider(vin: float, r_top: float, r_bot: float) -> float:
    """Vout of a resistive divider."""
    return vin * r_bot / (r_top + r_bot)


def divider_pick(vin: float, vout: float, r_bot: float = 10000.0) -> float:
    """Top resistor for target Vout given bottom resistor."""
    return r_bot * (vin / vout - 1.0)
