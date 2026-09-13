"""OCD MCP server (stdio, JSON-RPC 2.0). Agents drive boards as tools.

Protocol: Content-Length-framed JSON-RPC over stdio (MCP stdio transport).
Methods: initialize, tools/list, tools/call, ping. Notifications ignored.

Tools: load_board, get_state, apply_patch, parse_constraint, place, route,
check, export, render, use_plugin, list_plugins, solve.
State: one board in memory; load_board replaces it (old one undoable? no —
load is a fresh Board; agents snapshot via get_state if needed).
"""
from __future__ import annotations
import json
import os
import sys
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ocdcircuit import agent  # noqa: E402
from ocdcircuit.circuit import Board  # noqa: E402

BASE = os.getcwd()
BOARD: Board | None = None
SRC = "<memory>"


def _board() -> Board:
    assert BOARD is not None, "no board loaded (call load_board first)"
    return BOARD


def t_load(a: dict[str, object]) -> dict[str, object]:
    global BOARD, SRC
    if "text" in a:
        text = str(a["text"])
        base = str(a.get("base", BASE))
    else:
        SRC = str(a["path"])
        text = open(SRC).read()
        base = os.path.dirname(os.path.abspath(SRC))
    BOARD = agent.loads(text, base=base)
    if isinstance(a.get("fab"), str):
        BOARD.fab = str(a["fab"])
    return {"board": BOARD.name, "parts": len(BOARD.parts),
            "nets": len(BOARD.nets)}


def t_state(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    return {"ir": agent.ir(b), "text": agent.dumps(b)}


def t_patch(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    ops = cast(list[dict[str, object]], a["ops"])
    snap = b.ctx.snapshot()
    try:
        n = agent.apply_patch(b, ops)
    except (ValueError, KeyError) as e:
        b.ctx.rollback(snap)
        return {"applied": 0, "error": str(e)}
    return {"applied": n}


def t_state_set(a: dict[str, object]) -> dict[str, object]:
    """Declarative desired-state: {parts, nets, constraints, board}.
    Reconciles (add/drop/update), idempotent, order-independent, atomic
    (rollback on error). Prefer over apply_patch for agents."""
    b = _board()
    snap = b.ctx.snapshot()
    try:
        counts = b.declare(a)
    except (ValueError, KeyError, AssertionError) as e:
        b.ctx.rollback(snap)
        return {"applied": {}, "error": str(e)}
    return {"applied": counts}


def t_parse(a: dict[str, object]) -> dict[str, object]:
    c = agent.parse_constraint(str(a["text"]))
    return {"constraint": c}


def _i(v: object, default: int) -> int:
    if v is None:
        return default
    assert isinstance(v, (int, str))
    return int(v)


def t_place(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    frames: list[dict[str, object]] = []
    cost = b.place(key, seeds=_i(a.get("seeds"), 4), iters=_i(a.get("iters"), 400),
                   frames=frames if a.get("frames") else None)
    out: dict[str, object] = {"cost": cost}
    if a.get("frames"):
        out["frames"] = frames
    return out


def t_route(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    frames: list[dict[str, object]] = []
    n = b.route_board(key, frames=frames if a.get("frames") else None)
    out: dict[str, object] = {"segments": n}
    if a.get("frames"):
        out["frames"] = frames
    return out


def t_check(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    return b.check(key)


def t_export(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    outdir = str(a.get("outdir", "out"))
    return {"files": b.export(key, outdir=outdir)}


def t_render(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    return {"svg": b.render(key)}


def t_import(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    path = str(a.get("path", ""))
    return b.import_fp(key, path=path)


def t_calc(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    args = {k: v for k, v in a.items() if k != "key"}
    return b.calc(key, **args)


def t_sim(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    key = a.get("key")
    assert key is None or isinstance(key, str)
    args = {k: v for k, v in a.items() if k != "key"}
    return b.simulate(key, **args)


def t_use(a: dict[str, object]) -> dict[str, object]:
    _board().use(str(a["kind"]), str(a["key"]))
    return {"active": str(a["key"])}


def t_plugins(a: dict[str, object]) -> dict[str, object]:
    reg = _board().plugins()
    return {"plugins": reg.list()}


def t_solve(a: dict[str, object]) -> dict[str, object]:
    b = _board()
    pk = a.get("placer")
    rk = a.get("router")
    assert pk is None or isinstance(pk, str)
    assert rk is None or isinstance(rk, str)
    cost = b.place(pk)
    n = b.route_board(rk)
    r = b.check()
    return {"cost": cost, "segments": n, "errors": r["errors"],
            "warnings": r["warnings"]}


TOOLS: dict[str, object] = {
    "load_board": (t_load, {"text": "ocd source (or path)", "fab": "fab key"}),
    "get_state": (t_state, {}),
    "apply_patch": (t_patch, {"ops": "patch op list (undoable, atomic)"}),
    "set_state": (t_state_set, {"parts": "{ref: {fp, value?}}",
                                "nets": "{net: [REF.PIN]}",
                                "constraints": "[...] (declarative, idempotent)"}),
    "parse_constraint": (t_parse, {"text": "NL constraint"}),
    "place": (t_place, {"key": "placer?", "seeds": 4, "iters": 400, "frames?": True}),
    "route": (t_route, {"key": "router?", "frames?": True}),
    "check": (t_check, {"key": "drc?"}),
    "export": (t_export, {"key": "exporter?", "outdir": "out"}),
    "render": (t_render, {"key": "renderer?"}),
    "import_footprint": (t_import, {"key": "fp|kicad|eagle|tscircuit|pcb", "path": "file"}),
    "calc": (t_calc, {"what": "trace|via|divider", "amps": 1.0}),
    "simulate": (t_sim, {"what": "dc|tran"}),
    "use_plugin": (t_use, {"kind": "kind", "key": "key"}),
    "list_plugins": (t_plugins, {}),
    "solve": (t_solve, {"placer?": "key", "router?": "key"}),
}


def handle(msg: dict[str, object]) -> dict[str, object] | None:
    mid = msg.get("id")
    method = str(msg.get("method", ""))
    params = msg.get("params", {})
    assert isinstance(params, dict)
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "ocd-circuit", "version": "0.2"}}}
    if method == "tools/list":
        tools = [{"name": n, "inputSchema": {"type": "object"}}
                 for n in TOOLS]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        name = str(params.get("name", ""))
        args = params.get("arguments", {})
        assert isinstance(args, dict)
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"unknown tool {name}"}}
        fn = TOOLS[name]
        assert isinstance(fn, tuple) and callable(fn[0])
        try:
            out = fn[0](args)
        except (ValueError, KeyError, AssertionError, OSError) as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32000, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(out)}]}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method.startswith("notifications/"):
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            chunk = stdin.read(1)
            if not chunk:
                return
            header += chunk
        length = 0
        for line in header.decode().split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":")[1].strip())
        body = stdin.read(length)
        if not body:
            return
        try:
            resp = handle(json.loads(body))
        except (ValueError, AssertionError) as e:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": str(e)}}
        if resp is None:
            continue
        out = json.dumps(resp).encode()
        stdout.write(f"Content-Length: {len(out)}\r\n\r\n".encode() + out)
        stdout.flush()


if __name__ == "__main__":
    main()
