"""Shared scalar coercion and the lazy optional-dependency handle.

These were 23 near-identical private helpers copied per module (`_f` x10,
`_i` x5, `s` x2, `_num` x2, `_numpy` x2 — 112 lines). One implementation
each, aliased back to the short local names at the import site, so no call
site changed.

None handling is the one thing the copies disagreed on, so it is explicit:
without a `default`, a missing value is an error (what six of the `_f` copies
did, and the repo's fixable-input convention wants); pass one where absence
legitimately means zero.
"""
from __future__ import annotations
from typing import Any


def as_float(v: object, default: float | None = None) -> float:
    """Number from a parsed value. `as_float(x)` rejects None; `as_float(x,
    0.0)` reads it as zero."""
    if v is None:
        assert default is not None, "expected a number, got None"
        return default
    assert isinstance(v, (int, float, str)), f"expected a number, got {v!r}"
    return float(v)


def as_int(v: object, default: int = 0) -> int:
    """Integer from a parsed value; None reads as `default`."""
    if v is None:
        return default
    assert isinstance(v, (int, str)), f"expected an integer, got {v!r}"
    return int(v)


def as_str(v: object, default: str | None = None) -> str:
    """String from a parsed value. `as_str(x)` rejects None; `as_str(x, "")`
    reads it as empty."""
    if v is None:
        assert default is not None, "expected a string, got None"
        return default
    assert isinstance(v, str), f"expected a string, got {v!r}"
    return v


_np = None  # lazy: imported on first vectorized call, not at package load


def numpy() -> Any:
    """numpy handle, imported once on first use (~70ms). None when absent:
    callers keep a scalar path (solver's diffusion loops, kb's ranking)."""
    global _np
    if _np is None:
        try:
            import numpy as _m
            _np = _m
        except ImportError:
            return None
    return _np
