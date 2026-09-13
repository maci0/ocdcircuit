"""ocdcircuit — circuits as code, agents first-class, every edit reversible.

Language: plain Python (Board API) or JSON (agent.ir / from_json — same
schema). Everything else (placer/router/layers/drc/exporter/parts/renderer)
is a hot-swappable Plugin resolved through the board's Registry.
"""
from .core import Context, Component, Loader, Plugin, Registry
from .circuit import Board, Module, Part, Net
from . import solver, drc, export, agent, plugins

__all__ = ["Context", "Component", "Loader", "Plugin", "Registry",
           "Board", "Module", "Part", "Net",
           "solver", "drc", "export", "agent", "plugins"]
