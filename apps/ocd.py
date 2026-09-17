"""ocd CLI: dispatcher for board projects. Stdlib only.

    ocd new <dir>            scaffold board.ocd + README + STATUS
    ocd run <circuit.ocd>    full pipeline: place → route → DRC → fab
    ocd status <circuit.ocd> refresh STATUS.md (score, DRC, ERC, sim)
    ocd diff <a.ocd> <b.ocd> what changed: parts, nets, size, constraints
    ocd xray <board.ocd> <fab.png>  fab x-ray vs design: score + divergences
    ocd scan <photos...>     reverse-engineer a real board from photos
    ocd quote <board.ocd> [qty] [--bare] [--fab F]  fab price comparison
    ocd score <circuit.ocd>  display neatness badge 0-100 + tidy breakdown
                             (no mutation; badge is not for cross-board rank)
    ocd lint <circuit.ocd>   static source lint, no place/route
    ocd kb list|search|read|add|fetch|index|ask  board knowledgebase (`kb/`)
    ocd doctor               tooling self-check (no file needed)

Global flags (run/score): --fab --placer --router --sim. `ocd <file>` = run.
"""
from __future__ import annotations
import os
import re
import sys
from typing import Callable, cast

from ocdcircuit.circuit import Board

USAGE = """usage:
  ocd new <dir>                 scaffold a board project
  ocd run [--fab F] [--placer P] [--router R] [--sim dc|tran] <circuit.ocd>
  ocd status [--fab F] [--placer P] [--router R] <circuit.ocd>
  ocd diff <a.ocd> <b.ocd>       parts/nets/size/constraints delta
  ocd pin <circuit.ocd>          pin use lines to current file hashes
  ocd fp <family> <name> [k=v]   generate a .fp footprint (--out F.fp)
  ocd xray <board.ocd> <fab.png> fab x-ray vs design: score + divergences
  ocd scan [--out DIR] [--mm W] [--no-llm] [--note T] [--doc F]
           [--answer Q=A] <photo|dir|glob>...
                                 photos of a real board -> stitch, enhance,
                                 3D splat, analysis, draft .ocd (--doc reads
                                 a manual/datasheet; the model may ask back)
  ocd quote <board.ocd> [qty] [--bare] [--fab F]  fab price comparison
  ocd score [--fab F] [--placer P] [--router R] <circuit.ocd>
                                 display neatness badge + tidy breakdown
                                 (badge is glance-only — not for ranking)
  ocd lint <circuit.ocd>         static source lint, no place/route
  ocd kb list|search|read|add|fetch|index|ask  kb/: notes + datasheets
  ocd doctor                     tooling self-check (no file needed)
  ocd plugins [kind]            list registry keys (placer/router/…)
  ocd <circuit.ocd>              shorthand for run"""


def _usage(msg: str, *, requested: bool = False) -> int:
    """Print usage. Explicit -h/--help → stdout/0; bad arity → stderr/1."""
    print(msg, file=sys.stdout if requested else sys.stderr)
    return 0 if requested else 1


def _die(msg: str) -> int:
    """Operational CLI error on stderr; always exit 1."""
    print(msg, file=sys.stderr)
    return 1


def _exc_msg(e: BaseException) -> str:
    """Human CLI text for caught errors (no raw Errno noise)."""
    if isinstance(e, FileNotFoundError) and getattr(e, "filename", None):
        return f"no such file: {e.filename}"
    return str(e)


def _one_board(rest: list[str], usage: str) -> tuple[str | None, int | None]:
    """Require exactly one non-flag path; --help → stdout/0, else usage/1."""
    if rest and rest[0] in ("-h", "--help"):
        return None, _usage(usage, requested=True)
    # dangling/unknown flags left in rest by _flags must not be opened as files
    if len(rest) != 1 or rest[0].startswith("-"):
        return None, _usage(usage)
    return rest[0], None


def _boot() -> object:
    from ocdcircuit import agent as _a
    return _a


def _load(agent: object, src: str) -> Board:
    from ocdcircuit.util import read_text
    loads = cast(object, getattr(agent, "loads"))
    fn = cast(Callable[..., Board], loads)
    b = fn(read_text(src), base=os.path.dirname(os.path.abspath(src)))
    b.configure("toml", base=os.path.dirname(os.path.abspath(src)))
    return b


def _proj_list(b: Board, key: str) -> list[str] | None:
    v = b.proj.get(key)
    return list(v) if isinstance(v, list) else None


def _solve(b: Board, placer: str | None, router: str | None
           ) -> tuple[float, int, str, str]:
    """Place+route honoring CLI flags, else board.toml picks, else defaults.
    Returns (cost, segs, placer_used, router_used) — the registry default
    is untouched by one-shot runs, so callers display these, not active."""
    placer = placer or b.proj_str("placer")
    router = router or b.proj_str("router")
    c = b.place(placer) if placer else b.place()
    n = b.route_board(router) if router else b.route_board()
    return (c, n, placer or str(b.plugins().active.get("placer")),
            router or str(b.plugins().active.get("router")))


class _Printer:
    """Rich Console, or a plain-print shim when rich isn't installed."""
    def __init__(self, *, stderr: bool = False) -> None:
        try:
            from rich.console import Console
            self._c: object = Console(stderr=stderr)
        except ImportError:
            self._c = None
        self._stderr = stderr

    def print(self, *a: object) -> None:
        if self._c is not None:
            print_fn = getattr(self._c, "print")
            print_fn(*a)
        else:
            print(re.sub(r"\[(/?[a-z_ ]*|#[0-9a-f]*)\]", "",
                         " ".join(str(x) for x in a)),
                  file=sys.stderr if self._stderr else sys.stdout)


_C: _Printer | None = None  # lazy console (stdout)
_CE: _Printer | None = None  # lazy console (stderr)


def _out() -> _Printer:
    global _C
    if _C is None:
        _C = _Printer()
    return _C


def _err() -> _Printer:
    global _CE
    if _CE is None:
        _CE = _Printer(stderr=True)
    return _CE


def _table(title: str, rows: list[tuple[str, str]]) -> None:
    """Two-column table via rich, or aligned plain text."""
    c = _out()
    if c._c is None:
        w = max(len(r[0]) for r in rows) if rows else 0
        c.print(f"== {title} ==")
        for k, v in rows:
            c.print(f"{k:<{w}}  {v}")
        return
    from rich.table import Table
    t = Table(title=title, show_header=False)
    t.add_column(style="cyan")
    t.add_column()
    for k, v in rows:
        t.add_row(k, v)
    c.print(t)


def cmd_new(args: list[str]) -> int:
    if args and args[0] in ("-h", "--help"):
        return _usage("usage: ocd new <dir>", requested=True)
    if len(args) != 1:
        return _usage("usage: ocd new <dir>")
    d = args[0]
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(os.path.abspath(d)).replace("-", "_")
    board = os.path.join(d, f"{name}.ocd")
    if not os.path.exists(board):
        with open(board, "w", encoding="utf-8") as f:
            f.write(f"board {name} 40x30 2L\n"
                    f"part R1 R0805 10k\npart C1 C0805 100n\n"
                    f"N :: R1.2 <--> C1.2\nGND :: R1.1 <--> C1.1\nfix R1 at 3 5\n")
    readme = os.path.join(d, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n\n`ocd run {name}.ocd` → `out/` fab package.\n"
                    f"`ocd status {name}.ocd` refreshes STATUS.md.\n"
                    f"Notes + datasheets live in `kb/` (`ocd kb search {name}.ocd <term>`,\n"
                    f"`ocd kb fetch {name}.ocd` pulls datasheets for `lcsc=` parts).\n")
    toml = os.path.join(d, "board.toml")
    if not os.path.exists(toml):
        with open(toml, "w", encoding="utf-8") as f:
            f.write('# per-project defaults (CLI flags win). Keys: fab, placer,\n'
                    '# router, drc (list), mask, style. Values are validated:\n'
                    '# `ocd plugins [kind]` lists legal placer/router/drc picks.\n'
                    'fab = "jlc"\nplacer = "diffusion"\nrouter = "maze"\n'
                    'drc = ["fab", "erc"]\nmask = "green"\n')
    os.makedirs(os.path.join(d, "kb", "datasheets"), exist_ok=True)
    notes = os.path.join(d, "kb", "NOTES.md")
    if not os.path.exists(notes):
        with open(notes, "w", encoding="utf-8") as f:
            f.write(f"# {name} — notes\n\n"
                    f"Decisions, errata, pin notes. Datasheets go in `kb/datasheets/`\n"
                    f"(`ocd kb fetch {name}.ocd`, or drop them in by hand).\n"
                    f"Searchable with `ocd kb search {name}.ocd <term>`.\n")
    print(f"new: {board}")
    return 0


def _flags(args: list[str]) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """Leading or trailing --flag value pairs (GNU order either way)."""
    fab: str | None = None
    placer: str | None = None
    router: str | None = None
    simwhat: str | None = None
    rest = list(args)
    for flag in ("--fab", "--placer", "--router", "--sim"):
        while flag in rest:
            i = rest.index(flag)
            if i + 1 >= len(rest):
                break  # dangling flag: leave it, caller reports usage
            val = rest[i + 1]
            del rest[i:i + 2]
            if flag == "--fab":
                fab = val
            elif flag == "--placer":
                placer = val
            elif flag == "--sim":
                simwhat = val
            else:
                router = val
    return fab, placer, router, simwhat, rest


def cmd_run(agent: object, args: list[str]) -> int:  # agent: ocdcircuit.agent
    fab, placer, router, simwhat, rest = _flags(args)
    src, err = _one_board(rest, USAGE)
    if err is not None:
        return err
    assert src is not None
    try:
        b = _load(agent, src)
        if fab is not None:
            b.fab = fab
    except (OSError, ValueError, KeyError, AssertionError) as e:
        _err().print(f"[red]ocd: {_exc_msg(e)}[/red]")
        return 1
    try:
        c, n, pl_used, rt_used = _solve(b, placer, router)
    except KeyError as e:
        _err().print(f"[red]ocd: {_exc_msg(e)}[/red]")
        return 1
    r = b.check("all", keys=_proj_list(b, "drc"))
    out = os.path.join(os.path.dirname(os.path.abspath(src)), "out")
    files = (b.export("jlc", outdir=out) + b.export("kicad", outdir=out)
             + b.export("ocd", outdir=out))
    rendered = b.render_all(out)
    errors = cast(list[object], r["errors"])
    warnings = cast(list[object], r["warnings"])
    ok = not errors
    _out().print(f"[bold]{b.name}[/bold]: cost=[yellow]{c:.1f}[/yellow] "
                 f"segs=[cyan]{n}[/cyan] "
                 f"errors={'[green]0[/green]' if ok else f'[red]{len(errors)}[/red]'} "
                 f"warnings=[yellow]{len(warnings)}[/yellow] "
                 f"({pl_used}/{rt_used})")
    _table("fab output", [(f"{len(files)} files + {len(rendered)} renders", out)])
    if simwhat:
        try:
            res = b.simulate(what=simwhat)
        except (ValueError, KeyError, AssertionError) as e:
            _err().print(f"[red]ocd: sim: {e}[/red]")
            return 1
        if "nets" in res:
            nets = cast(dict[str, object], res["nets"])
            rows = []
            for k, v in sorted(nets.items()):
                assert isinstance(v, (int, float))
                rows.append((k, f"{float(v):.3f}V"))
            _table("sim dc", rows)
            if simwhat == "dc":
                from ocdcircuit import sim as _sim
                _simfails = _sim.expect(b)
                for p in _simfails:
                    _out().print(f"  [red]⚡✗ {p}[/red]")
                if _simfails:
                    errors = [*errors, *["sim: " + p for p in _simfails]]
        else:
            waves = cast(dict[str, list[float]], res["waves"])
            rows = [(k, f"final={v[-1]:.3f}V min={min(v):.3f} max={max(v):.3f} ({len(v)} pts)")
                    for k, v in sorted(waves.items())]
            _table("sim tran", rows)
            from ocdcircuit import sim as _simt
            _tfails = _simt.expect_tran(b)
            for p in _tfails:
                _out().print(f"  [red]⚡✗ {p}[/red]")
            if _tfails:
                errors = [*errors, *["sim: " + p for p in _tfails]]
    if errors:
        for item in errors:
            _out().print(f"  [red]✗ {item}[/red]")
        return 2
    seen: set[str] = set()
    for w in warnings:
        ws = str(w)
        if ws in seen:
            continue
        seen.add(ws)
        if len(seen) > 5:
            break
        _out().print(f"  [yellow]~ {ws}[/yellow]")
    _out().print("[green]✓ DRC clean[/green]")
    return 0


def _pour_line(b: object) -> str:
    """STATUS.md plane row: `planes: GND on 0,3` or empty string."""
    from ocdcircuit.drc import pour_layers
    from ocdcircuit.circuit import Board
    assert isinstance(b, Board)
    poured = pour_layers(b)
    if not poured:
        return ""
    return "planes: " + ", ".join(
        f"{n} on {','.join(str(ll) for ll in lls)}"
        for n, lls in sorted(poured.items())) + "\n"


def cmd_status(agent: object, args: list[str]) -> int:
    fab, placer, router, _sim, rest = _flags(args)
    _st_usage = (
        "usage: ocd status [--fab F] [--placer P] [--router R] <circuit.ocd>")
    src, err = _one_board(rest, _st_usage)
    if err is not None:
        return err
    assert src is not None
    try:
        b = _load(agent, src)
        if fab is not None:
            b.fab = fab
        _, _, pl_used, rt_used = _solve(b, placer, router)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    s = b.score()
    t = b.score(tidy=True)
    _ext = cast(dict[str, object], s["extent"])
    checks = b.check("all", keys=_proj_list(b, "drc"))
    derr = cast(list[object], checks["errors"])
    dwarn = cast(list[object], checks["warnings"])
    simline = ""
    if any(c.get("t") == "sim" for c in b.constraints):
        try:
            nets = b.simulate()["nets"]
            assert isinstance(nets, dict)
            simline = "sim: " + " ".join(
                f"{k}={float(v):.2f}V" for k, v in sorted(nets.items())
                if isinstance(v, (int, float))) + "\n"
            from ocdcircuit import sim as _simexp
            _fails = _simexp.expect(b)
            if _fails:
                simline += "sim FAIL: " + "; ".join(_fails) + "\n"
        except (ValueError, KeyError, AssertionError):
            simline = "sim: error\n"
    quoteline = ""
    try:
        from ocdcircuit import quote as _qq
        qr = _qq.compare(b, qty=5)
        qrows = cast(list[dict[str, object]], qr["rows"])
        priced = [r for r in qrows if "bare_total" in r]
        if priced:
            cheapest = min(priced, key=lambda r: float(cast(float, r["bare_total"])))
            quoteline = (f"quote: cheapest={cheapest['fab']} "
                         f"bare ${cheapest['bare_total']}/bd")
            asm = cheapest.get("asm")
            if isinstance(asm, dict):
                flags = []
                if cast(list[str], asm.get("unpriced", [])):
                    flags.append(f"unpriced={len(cast(list[str], asm['unpriced']))}")
                if cast(list[str], asm.get("low_stock", [])):
                    flags.append(f"low-stock={len(cast(list[str], asm['low_stock']))}")
                if cast(list[str], asm.get("risky", [])):
                    flags.append(f"risky={len(cast(list[str], asm['risky']))}")
                if cast(list[str], asm.get("via_alt", [])):
                    flags.append(f"subs={len(cast(list[str], asm['via_alt']))}")
                if flags:
                    quoteline += " (" + ", ".join(flags) + ")"
            quoteline += "\n"
    except (ValueError, KeyError, AssertionError):
        quoteline = "quote: error\n"
    trows = "\n".join(f"| {k} | {_tidy_md(v)} |" for k, v in t.items()
                        if k not in ("coverage", "routed_segs"))
    ran = checks.get("ran", [])
    doc = (f"# STATUS — {b.name}\n\n"
           f"OCD score: {s['total']}/100 ({s['grade']}) "
           f"(display badge — do not rank boards by this)\n\n"
           f"## tidy ({t['coverage']} metrics defined)\n\n"
           f"| metric | value |\n|---|---|\n{trows}\n\n"
           f"| check | errors | warnings |\n|---|---|---|\n"
           + "".join(f"| {k} | {sum(1 for e in derr if str(e).startswith(k + ':'))} | "
                     f"{sum(1 for w in dwarn if str(w).startswith(k + ':'))} |\n" for k in ran)
           + "\n"
           + ("".join(f"- {e}\n" for e in derr[:10]))
           + ("".join(f"- {w}\n" for w in dwarn[:10]))
           + (f"{simline}\n" if simline else "")
           + (f"{quoteline}\n" if quoteline else "")
           + (f"jumpers: {', '.join(b.jumper_nets())} "
              f"(wire bridges — retry one with alt-click reroute)\n"
              if b.jumper_nets() else "")
           + f"parts: {len(b.parts)}, nets: {len(b.nets)}, "
           + f"traces: {len(b.traces)}, layers: {b.layers}\n"
           + f"solved: {pl_used}/{rt_used} @ {b.fab}\n"
           + _pour_line(b)
           + f"extent: {_ext['w']}x{_ext['h']}mm "
           + f"({float(cast(float, _ext['fill'])) * 100:.0f}% of "
           + f"{b.width:g}x{b.height:g} board, shrink → "
           + f"{cast(list[float], _ext['shrink'])[0]:g}x"
           + f"{cast(list[float], _ext['shrink'])[1]:g})\n")
    proj = os.path.dirname(os.path.abspath(src))
    with open(os.path.join(proj, "STATUS.md"), "w", encoding="utf-8") as f:
        f.write(doc)
    print(doc, end="")
    return 0 if not derr else 2


def cmd_diff(agent: object, args: list[str]) -> int:
    if args and ("-h" in args or "--help" in args):
        return _usage("usage: ocd diff <a.ocd> <b.ocd>", requested=True)
    if len(args) != 2 or any(a.startswith("-") for a in args):
        return _usage("usage: ocd diff <a.ocd> <b.ocd>")
    try:
        a = _load(agent, args[0])
        b = _load(agent, args[1])
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    print(a.diff(b) or "identical")
    return 0


_FPGENS = ("soic", "ssop", "tssop", "msop", "qfp", "qfn", "dfn", "bga",
           "bga_rect", "chip", "pinheader", "pinheader2x", "jst",
           "electrolytic", "fiducial", "mounting_hole", "terminal2")


def cmd_fp(agent: object, args: list[str]) -> int:
    if args and ("-h" in args or "--help" in args):
        return _usage("usage: ocd fp <family> <name> [k=v ...] [--out F.fp]\n"
                      f"  families: {', '.join(_FPGENS)}", requested=True)
    if len(args) < 2 or args[0].startswith("-"):
        return _usage("usage: ocd fp <family> <name> [k=v ...] [--out F.fp]")
    fam, name = args[0], args[1]
    if fam not in _FPGENS:
        return _die(f"ocd: unknown footprint family {fam!r} "
                    f"(have: {', '.join(_FPGENS)})")
    if not name.replace("_", "").replace("-", "").isalnum():
        return _die(f"ocd: bad footprint name {name!r}")
    kw: dict[str, object] = {}
    out: str | None = None
    rest = args[2:]
    i = 0
    while i < len(rest):
        if rest[i] == "--out":
            if i + 1 >= len(rest):
                return _usage("usage: ocd fp <family> <name> [k=v ...] [--out F.fp]")
            out = rest[i + 1]
            i += 2
        elif "=" in rest[i]:
            k, _, v = rest[i].partition("=")
            try:
                kw[k] = int(v)
            except ValueError:
                try:
                    kw[k] = float(v)
                except ValueError:
                    return _die(f"ocd: bad value {rest[i]!r} (want k=number)")
            i += 1
        else:
            return _die(f"ocd: bad arg {rest[i]!r} (want k=v or --out F)")
    from ocdcircuit import parts as _parts
    from ocdcircuit import footprint as _fpmod
    try:
        fp = getattr(_parts, fam)(**kw)
    except TypeError as e:
        return _die(f"ocd: {fam}: {e}")
    text = _fpmod.dumps(name, fp)
    if out is not None:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out}")
    else:
        print(text, end="")
    return 0


def cmd_pin(agent: object, args: list[str]) -> int:
    if args and ("-h" in args or "--help" in args):
        return _usage("usage: ocd pin <circuit.ocd>  "
                      "rewrite use lines with current file hashes", requested=True)
    if len(args) != 1 or args[0].startswith("-"):
        return _usage("usage: ocd pin <circuit.ocd>")
    import hashlib as _hl
    import os as _os
    import re as _re
    path = args[0]
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return _die(f"ocd: {e}")
    base = _os.path.dirname(os.path.abspath(path))
    changed = 0
    for i, ln in enumerate(lines):
        m = _re.match(r"^(\s*use\s+\S+?)(?:@[0-9a-fA-F]{8,64})?"
                      r"((?:\s+as\s+\S+)?(?:\s+join\s+.+)?)$", ln, re.I)
        if not m:
            continue
        pm = _re.match(r"^use\s+(\S+)", ln.strip(), re.I)
        if not pm:
            continue
        fn = _os.path.normpath(_os.path.join(
            base, pm.group(1).split("@", 1)[0]))
        if not _os.path.isfile(fn):
            return _die(f"ocd: no such file: {pm.group(1)!r}")
        with open(fn, "rb") as f:
            digest = _hl.sha256(f.read()).hexdigest()[:12]
        lines[i] = f"{m.group(1)}@{digest}{m.group(2)}"
        changed += 1
    if not changed:
        print("no use lines to pin")
        return 0
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"pinned {changed} include(s) in {path}")
    return 0


def cmd_xray(agent: object, args: list[str]) -> int:
    _xusage = (
        "usage: ocd xray <circuit.ocd> <fab.png> "
        "(dx/dy/scale/thr via env XRAY=dx,dy,scale,thr)")
    if args and ("-h" in args or "--help" in args):
        return _usage(_xusage, requested=True)
    if len(args) != 2 or any(a.startswith("-") for a in args):
        return _usage(_xusage)
    import os as _os
    try:
        b = _load(agent, args[0])
        _solve(b, None, None)
        from ocdcircuit import envcfg as _envcfg
        try:
            kw = _envcfg.xray_overrides()
        except _envcfg.EnvError as e:
            return _die(f"ocd: {e}")
        r = b.xray(None, png=args[1], **kw)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    divs = cast(list[dict[str, object]], r["divs"])
    missing = cast(int, r["missing"])
    extra = cast(int, r["extra"])
    _out().print(f"xray score=[green]{r['score']}[/green] "
                 f"missing=[red]{missing}[/red] extra=[yellow]{extra}[/yellow]")
    for d in divs[:10]:
        _out().print(f"  [red]{d['kind']}[/red] {d['x']} {d['y']} "
                     f"{d['w']}x{d['h']}mm ({d['cells']} cells)")
    out = _os.path.join(_os.path.dirname(_os.path.abspath(args[0])), "out")
    _os.makedirs(out, exist_ok=True)
    for k2 in ("svg", "overlay"):
        fn = _os.path.join(
            out, b.name + (".xray.svg" if k2 == "svg" else ".xray-div.svg"))
        open(fn, "w", encoding="utf-8").write(cast(str, r[k2]))
    _out().print(f"xray: {out}/{b.name}.xray.svg + {b.name}.xray-div.svg")
    return 0


def cmd_quote(agent: object, args: list[str]) -> int:
    _qusage = ("usage: ocd quote <circuit.ocd> [qty] [--bare] [--fab F]  "
               "(parts via live JLC or price= attr)")
    if args and ("-h" in args or "--help" in args):
        return _usage(_qusage, requested=True)
    if not args:
        return _usage(_qusage)
    bare_only = "--bare" in args
    fabs: list[str] = []
    rest: list[str] = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--fab":
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return _die("ocd: --fab needs a fab name")
            fabs.append(args[i + 1])
            skip = True
        elif a == "--bare":
            continue
        elif a.startswith("-"):
            return _die(f"ocd: unknown flag {a}")
        else:
            rest.append(a)
    if not rest or rest[0].startswith("-"):
        return _usage(_qusage)
    if len(rest) > 1 and rest[1].startswith("-"):
        return _usage(_qusage)
    try:
        b = _load(agent, rest[0])
        qty = int(rest[1]) if len(rest) > 1 else 5
        r = b.quote(qty=qty, fabs=fabs or None, no_parts=bare_only)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    rows = cast(list[dict[str, object]], r["rows"])
    _table(f"quote {r['board']} x{r['qty']} ({r['stamp']})",
           [(str(x["fab"]),
             f"bare ${x['bare_total']} (${x['bare_per_board']}/bd)"
             + (f"  asm ${x['asm_total']} (${x['asm_per_board']}/bd)" if "asm_total" in x else "")
             + (f"  ! {x['error']}" if "error" in x else ""))
            for x in rows])
    jlcs = [x for x in rows if x.get("fab") == "jlc" and isinstance(x.get("asm"), dict)]
    if jlcs:
        asm = cast(dict[str, object], jlcs[0]["asm"])
        unp = cast(list[str], asm.get("unpriced", []))
        _out().print(f"jlc assembly: fees ${asm['fees']} + parts "
                     f"${asm['parts_per_board']}/board ({asm['parts']} parts, "
                     f"{asm['joints']} joints, {asm['sources']})")
        if unp:
            _out().print(f"[yellow]unpriced ({len(unp)}): {' '.join(unp[:12])}"
                         f" — add price= attrs or check LCSC codes[/yellow]")
        alt = cast(list[str], asm.get("via_alt", []))
        if alt:
            _out().print(f"substitutes ({len(alt)}): {' '.join(alt[:12])}")
        low = cast(list[str], asm.get("low_stock", []))
        if low:
            _out().print(f"[yellow]low stock ({len(low)}): {' '.join(low[:12])}[/yellow]")
        rsk = cast(list[str], asm.get("risky", []))
        if rsk:
            _out().print(f"[yellow]lifecycle risk ({len(rsk)}): {' '.join(rsk[:12])}[/yellow]")
    _out().print(f"[dim]{r['note']}[/dim]")
    return 0


def cmd_score(agent: object, args: list[str]) -> int:
    fab, placer, router, _sim, rest = _flags(args)
    _sc_usage = (
        "usage: ocd score [--fab F] [--placer P] [--router R] <circuit.ocd>")
    src, err = _one_board(rest, _sc_usage)
    if err is not None:
        return err
    assert src is not None
    try:
        b = _load(agent, src)
        if fab is not None:
            b.fab = fab
        _solve(b, placer, router)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    s = b.score()
    t = b.score(tidy=True)
    sparts = cast(dict[str, float], s["parts"])
    total = cast(float, s["total"])
    color = "green" if total >= 75 else "yellow" if total >= 40 else "red"
    _out().print(f"OCD [{color}]{total}/100 ({s['grade']})[/{color}]"
                 "  [dim](display badge — do not rank boards by this)[/dim]")
    _table("neatness", [(k, str(v)) for k, v in sparts.items()])
    _table("tidy " + str(t["coverage"]), [_tidy_row(k, v) for k, v in t.items()
                                          if k not in ("coverage", "routed_segs")])
    ext = cast(dict[str, object], s["extent"])
    _out().print(f"extent: {ext['w']}x{ext['h']}mm "
                 f"({float(cast(float, ext['fill'])) * 100:.0f}% of "
                 f"{b.width:g}x{b.height:g} board, shrink → "
                 f"{cast(list[float], ext['shrink'])[0]:g}x"
                 f"{cast(list[float], ext['shrink'])[1]:g})")
    return 0


def cmd_lint(agent: object, args: list[str]) -> int:
    src, err = _one_board(args, "usage: ocd lint <circuit.ocd>")
    if err is not None:
        return err
    assert src is not None
    try:
        b = _load(agent, src)
    except (OSError, ValueError, KeyError, AssertionError) as e:
        _err().print(f"[red]ocd: {_exc_msg(e)}[/red]")
        return 1
    r = b.lint()
    errors = cast(list[object], r["errors"])
    warnings = cast(list[object], r["warnings"])
    for item in errors:
        _out().print(f"  [red]✗ {item}[/red]")
    for w in warnings:
        _out().print(f"  [yellow]~ {w}[/yellow]")
    if not errors and not warnings:
        _out().print("[green]✓ lint clean[/green]")
    else:
        _out().print(f"[red]{len(errors)} errors[/red], "
                      f"[yellow]{len(warnings)} warnings[/yellow]")
    return 2 if errors else 0


def cmd_doctor() -> int:
    from ocdcircuit.circuit import Board as _B
    r = _B("doctor").doctor()
    rows = []
    checks = cast(list[dict[str, object]], r["checks"])
    for c in checks:
        mark = "[green]✓[/green]" if c["ok"] else "[red]✗[/red]"
        rows.append((f"{mark} {c['name']}", str(c.get("detail", ""))))
    _table("doctor", rows)
    if not r["ok"]:
        _out().print("[red]broken: required checks failed (Python / plugins)[/red]")
        return 1
    if any(not c["ok"] for c in checks):
        _out().print("[yellow]degraded: see ✗ rows (features fall back, nothing crashes)[/yellow]")
        return 0
    _out().print("[green]✓ all systems[/green]")
    return 0


KB_USAGE = """usage:
  ocd kb list   <board.ocd|dir>            docs in kb/ (+ which parts they cover)
  ocd kb search <board.ocd|dir> <query>    hits as `doc:line: text`
  ocd kb read   <board.ocd|dir> <doc> [start] [lines]
  ocd kb add    <board.ocd|dir> <path|url> [name]
  ocd kb fetch  <board.ocd> [REF ...]      download datasheets (`datasheet=` or `lcsc=`)
  ocd kb index  <board.ocd|dir> [--force]  embed kb/ passages (kb/.cache/vec__*)
  ocd kb ask    <board.ocd|dir> "<question>" [k] [--answer]
                                           passages that answer it (embeddings);
                                           --answer also writes one with the local model
kb/ sits beside the board: drop notes/datasheets in by hand, or add them here."""


def _kb_base(target: str) -> str:
    """The kb lives next to the board: accept the .ocd or the project dir."""
    return target if os.path.isdir(target) else os.path.dirname(os.path.abspath(target))


def cmd_kb(agent: object, args: list[str]) -> int:
    from ocdcircuit import kb as _kb
    from ocdcircuit.util import read_text
    # --help anywhere (e.g. `kb list --help`); never treat it as a path
    if args and ("-h" in args or "--help" in args):
        return _usage(KB_USAGE, requested=True)
    if len(args) < 2:
        return _usage(KB_USAGE)
    op, target, rest = args[0], args[1], args[2:]
    if target.startswith("-"):
        return _usage(KB_USAGE)
    board = None
    parts = None
    try:
        if target.endswith(".ocd") and os.path.isfile(target):
            # `fetch` needs real attrs (datasheet=/lcsc=/value) and runs once;
            # everything else only maps a filename to refs, and building a
            # Board for a 5420-part design costs ~1.5s.
            if op == "fetch":
                board = _load(agent, target)
            else:
                parts = _kb.parts_map(read_text(target))
    except (OSError, ValueError, KeyError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    k = _kb.KB(_kb_base(target), board=board, parts=parts)
    try:
        if op == "list":
            docs = k.docs()
            if not docs:
                print(f"kb empty: {k.dir}\n"
                      f"  drop notes/datasheets in there, or `ocd kb add {target} <url>`")
                return 0
            rel = os.path.relpath(k.dir)
            _table(f"kb {rel if not rel.startswith('..') else k.dir}", [
                (str(d["name"]), " ".join(x for x in [
                    str(d["kind"]), f"{d['bytes']}B",
                    "parts=" + ",".join(cast(list[str], d["parts"])) if d["parts"] else "",
                    str(d.get("title", "")), str(d.get("source", ""))] if x))
                for d in docs])
            return 0
        if op == "search":
            if not rest:
                return _usage(KB_USAGE)
            r = k.search(rest[0], limit=int(rest[1]) if len(rest) > 1 else 20)
            hits = cast(list[dict[str, object]], r["hits"])
            for h in hits:
                print(f"{h['doc']}:{h['line']}: {h['text']}")
            if not hits:
                print(f"no hits for {rest[0]!r} in {r['docs_searched']} docs")
            for s in cast(list[str], r["skipped"]):
                print(f"  (skipped {s})")
            return 0
        if op == "read":
            if not rest:
                return _usage(KB_USAGE)
            r = k.read(rest[0], start=int(rest[1]) if len(rest) > 1 else 1,
                       lines=int(rest[2]) if len(rest) > 2 else 200)
            print(f"{r['name']} lines {r['start']}-{r['end']}/{r['total_lines']}")
            print(r["text"])
            return 0
        if op == "add":
            if not rest:
                return _usage(KB_USAGE)
            out = k.add(rest[0], name=rest[1] if len(rest) > 1 else None)
            print(f"added kb/{out['added']} ({out['bytes']}B)")
            return 0
        if op == "index":
            r = k.index(force=bool(rest and rest[0] == "--force"))
            print(f"indexed {r['indexed']} docs ({r['reused']} fresh, "
                  f"{r['passages']} passages, model {r['model']})")
            for f in cast(list[str], r["failed"]):
                print(f"  skipped {f}")
            return 0
        if op == "ask":
            if not rest:
                return _usage(KB_USAGE)
            want_answer = "--answer" in rest
            args2 = [a for a in rest if a != "--answer"]
            r = k.ask(args2[0], k=int(args2[1]) if len(args2) > 1 else 6,
                      answer=want_answer)
            print(f"# {r['method']}"
                  f"{' · ' + str(r['model']) if r['model'] else ''} · {len(cast(list[object], r['passages']))} passages")
            for p in cast(list[dict[str, object]], r["passages"]):
                print(f"--- {p['doc']}:{p['start']}-{p['end']}  score={p['score']}")
                print(str(p["text"])[:900])
            if "answer" in r:
                print(f"\n== answer ==")
                if r.get("answer_note"):
                    print(f"# {r['answer_note']}")
                print(r["answer"])
            if "answer_error" in r:
                print(f"(no written answer: {r['answer_error']})")
            if r.get("note"):
                print(f"# {r['note']}")
            return 0
        if op == "fetch":
            if board is None:
                return _die("ocd: fetch reads part attrs — pass the board file, not a dir")
            r = k.fetch(board, refs=rest or None)
            for sv in cast(list[dict[str, object]], r["saved"]):
                print(f"saved kb/{sv['name']} ({sv['bytes']}B) for {sv['part']}")
            for sk in cast(list[dict[str, object]], r["skipped"]):
                print(f"skip  {sk['part']}: {sk['why']}")
            for fl in cast(list[dict[str, object]], r["failed"]):
                print(f"FAIL  {fl['part']}: {fl['error']}")
            return 1 if r["failed"] else 0
    except (ValueError, OSError, KeyError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    return _usage(KB_USAGE)


def cmd_scan(agent: object, args: list[str]) -> int:
    """Photos of a real board → stitch/enhance/splat artifacts, and (unless
    --no-llm) a vision-model analysis plus a draft .ocd."""
    import glob as _glob
    _scan_usage = (
        "usage: ocd scan [--out DIR] [--mm WIDTH] [--no-llm] "
        "[--note TEXT] [--doc FILE] [--answer 'Q=A'] "
        "[--zoom N] [--maxdim PX] [--tall MM] "
        "<photo|dir|glob>...\n"
        "  filenames containing 'bot'/'back' are read as the bottom "
        "side, everything else as top\n"
        "  --doc    a manual or datasheet (.pdf/.txt/.md) to read "
        "alongside the photos; repeatable\n"
        "  --answer reply to a question an earlier run asked; "
        "repeatable\n"
        "  --zoom   tile grid for detail views (2 = 2x2 native-res "
        "crops per side, 1 = off)\n"
        "  --maxdim stitch canvas px (1600 default; 2400 resolves "
        "finer traces, ~2x the memory)\n"
        "  --tall   height in mm of the tallest part, scales the 3D "
        "splat (parallax gives relative depth only)")
    if args and args[0] in ("-h", "--help"):
        return _usage(_scan_usage, requested=True)
    if not args:
        return _usage(_scan_usage)
    outdir, mm, use_llm, note = "scan", None, True, ""
    zoom, maxdim, tall = 2, 1600, 5.0
    docs: list[str] = []
    answers: dict[str, str] = {}
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--out", "--mm", "--note", "--tall", "--zoom", "--maxdim",
                 "--doc", "--answer") and i + 1 >= len(args):
            return _die(f"ocd: {a} needs a value")
        if a == "--out":
            outdir, i = args[i + 1], i + 2
        elif a == "--mm":
            try:
                mm = float(args[i + 1])
            except ValueError:
                return _die(f"ocd: --mm wants a number, got {args[i + 1]!r}")
            i += 2
        elif a == "--note":
            note, i = args[i + 1], i + 2
        elif a == "--tall":
            try:
                tall = float(args[i + 1])
            except ValueError:
                return _die(f"ocd: --tall wants a number, got {args[i + 1]!r}")
            i += 2
        elif a in ("--zoom", "--maxdim"):
            try:
                v = int(args[i + 1])
            except ValueError:
                return _die(f"ocd: {a} wants an integer, got {args[i + 1]!r}")
            if a == "--zoom":
                zoom = max(1, v)
            else:
                maxdim = max(400, v)
            i += 2
        elif a == "--doc":
            docs.append(args[i + 1])
            i += 2
        elif a == "--answer":
            if "=" not in args[i + 1]:
                return _die("ocd: --answer wants 'question=answer'")
            q, _, ans = args[i + 1].partition("=")
            answers[q.strip()] = ans.strip()
            i += 2
        elif a == "--no-llm":
            use_llm, i = False, i + 1
        elif a.startswith("-"):
            return _die(f"ocd: unknown flag {a}")
        else:
            paths.append(a)
            i += 1
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(f for f in _glob.glob(os.path.join(p, "*"))
                            if os.path.isfile(f))
        elif any(ch in p for ch in "*?["):
            files += sorted(_glob.glob(p))
        else:
            files.append(p)
    if not files:
        return _die("ocd: no photos matched")
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        return _die(f"ocd: no such file: {missing[0]}")
    from ocdcircuit.circuit import Board as _B
    try:
        r = _B("scan").scan(photos=files, outdir=outdir, board_mm=mm,
                            note=note, docs=docs or None,
                            answers=answers or None, zoom=zoom,
                            maxdim=maxdim, tall_mm=tall, llm=use_llm)
    except (OSError, ValueError, KeyError, RuntimeError, AssertionError) as e:
        return _die(f"ocd: {_exc_msg(e)}")
    for side, s in sorted(cast(dict[str, dict[str, object]],
                               r.get("sides", {})).items()):
        _table(f"{side}: {s['used']}/{s['photos']} photos registered",
               [("reference", str(s["reference"])),
                ("canvas", f"{s['canvas']} px @ {s['mm_per_px']} mm/px"),
                ("coverage", str(s["coverage_mean"])),
                ("relief", str(s["relief"])),
                ("dropped", ", ".join(cast(list[str], s["dropped"])) or "none"),
                ("files", str(len(cast(dict[str, str], s["files"]))))])
    for side, s in sorted(cast(dict[str, dict[str, object]],
                               r.get("sides", {})).items()):
        for name, reason in sorted(cast(dict[str, str],
                                        s.get("dropped_why", {})).items()):
            _out().print(f"  dropped {side}/{name}: {reason}")
    _out().print(f"scan: artifacts in {outdir}/ (manifest.json)")
    if "analysis" in r:
        _out().print(f"scan: analysis {r['analysis']}")
    if "draft" in r:
        _out().print(f"scan: draft {r['draft']} "
                     f"({r.get('draft_parts', '?')} parts, "
                     f"{r.get('draft_nets', '?')} nets)")
    if "draft_error" in r:
        _out().print(f"scan: no usable draft ({r['draft_error']})")
    if "draft_wired" in r:
        floating = cast(list[str], r.get("draft_floating", []))
        drc = r.get("draft_drc_errors", 0)
        _out().print(
            f"scan: draft buildability — {r['draft_wired']}/"
            f"{r.get('draft_parts', '?')} parts wired, "
            f"{drc} DRC error(s) after place+route")
        for line in cast(list[str], r.get("draft_drc", []))[:4]:
            _out().print(f"  ! {line}")
        if floating:
            _out().print(
                f"  {len(floating)} parts have no nets ("
                + ", ".join(floating[:6])
                + (", …" if len(floating) > 6 else "") + ")")
            _out().print("  photos cannot show nets: wire these from the "
                         "datasheet, then the placer can separate them")
    if "draft_solve_error" in r:
        _out().print(f"scan: draft did not solve ({r['draft_solve_error']})")
    asked = cast(list[str], r.get("questions", []))
    if asked:
        _out().print("\nscan: the model asked — answer with "
                     "--answer 'question=your answer' and re-run:")
        for q in asked:
            _out().print(f"  ? {q}")
    return 0


def cmd_plugins(agent: object, args: list[str]) -> int:
    if args and args[0] in ("-h", "--help"):
        return _usage("usage: ocd plugins [kind]", requested=True)
    from ocdcircuit.circuit import Board as _B
    reg = _B("plugins").plugins()
    kinds = list(args) if args else ["placer", "router", "layers", "drc",
                                      "exporter", "renderer", "silk", "calc",
                                      "simulate", "importer"]
    for kind in kinds:
        keys = reg.list(kind)
        if not keys:
            return _die(f"unknown plugin kind {kind!r}")
        _table(kind, [(k, "") for k in keys])
    return 0


def _tidy_md(v: object) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, dict):
        return ", ".join(f"{a}={c}" for a, c in sorted(v.items()))
    return str(v)


def _tidy_row(k: str, v: object) -> tuple[str, str]:
    if v is None:
        return (k, "n/a")
    if isinstance(v, float):
        return (k, f"{v:.3f}")
    if isinstance(v, dict):
        return (k, ", ".join(f"{a}={c}" for a, c in sorted(v.items())))
    return (k, str(v))


def main(argv: list[str]) -> int:
    agent = _boot()
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        return _usage(USAGE, requested=bool(args))
    if args[0] == "new":
        return cmd_new(args[1:])
    if args[0] == "run":
        return cmd_run(agent, args[1:])
    if args[0] == "status":
        return cmd_status(agent, args[1:])
    if args[0] == "diff":
        return cmd_diff(agent, args[1:])
    if args[0] == "pin":
        return cmd_pin(agent, args[1:])
    if args[0] == "fp":
        return cmd_fp(agent, args[1:])
    if args[0] == "xray":
        return cmd_xray(agent, args[1:])
    if args[0] == "scan":
        return cmd_scan(agent, args[1:])
    if args[0] == "quote":
        return cmd_quote(agent, args[1:])
    if args[0] == "score":
        return cmd_score(agent, args[1:])
    if args[0] == "lint":
        return cmd_lint(agent, args[1:])
    if args[0] == "doctor":
        if args[1:] and args[1] in ("-h", "--help"):
            return _usage("usage: ocd doctor", requested=True)
        return cmd_doctor()
    if args[0] == "plugins":
        return cmd_plugins(agent, args[1:])
    if args[0] == "kb":
        return cmd_kb(agent, args[1:])
    if args[0].startswith("-"):
        return _usage(USAGE)
    return cmd_run(agent, args)  # `ocd <file>` = run


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
