"""OCD Studio: visual editor. Stdlib only (http.server + inline JS, no deps).

Layout (tmog cockpit: whole-system state at a glance, no tabs hiding answers):
  .ocd editor (highlighted) | PCB canvas | SCH canvas | 3D preview | DRC panel

Interactions:
- edit .ocd → debounce 400ms → rebuild → PCB/SCH/3D/DRC update live
- drag part on PCB → drops `fix REF at x y`, re-solves around it, editor updates
- placer/router/fab/silk/theme dropdowns → re-run with animation frames;
  parts glide (ease-out cubic tween), traces grow net-by-net
- 🎲 → N candidate layouts in a filmstrip; click picks (positions restored,
  routed), drag nudges+fixes, re-run same/different engine (chain via fixes)
- every build carries a routing-feasibility badge per layer count (maze
  probe on current placement; theory, not proof)
- light/dark toggle (themes change skin, never structure)

Run: python studio.py [file.ocd]  → http://localhost:8077
"""
from __future__ import annotations
from ocdcircuit.util import as_float as _f, as_int as _i, path_for_log
# Explicit bind so mypy strict re-exports the name (tests import apps.studio._path_for_log).
_path_for_log = path_for_log
import contextvars
import gzip
import hashlib
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from typing import cast

# Compress HTML/JSON only when the body pays for the CPU. Level 4 matches
# per-request studio pages (slots injected each hit); higher effort barely wins.
_MIN_GZIP = 512
_GZIP_LEVEL = 4
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = ROOT

from ocdcircuit import agent  # noqa: E402
from ocdcircuit import envcfg as _envcfg  # noqa: E402
from ocdcircuit import fab as _fab  # noqa: E402
from ocdcircuit.circuit import Board  # noqa: E402
from ocdcircuit.types import DrcReport  # noqa: E402
from ocdcircuit.recommend import recommend  # noqa: E402
from ocdcircuit.core import UiSlots  # noqa: E402

SLOTS = UiSlots()
# Built-in views (harness-slot shape: shell declares, entries contribute).
# A UI plugin = _slot(slot, id, fn) + optional /api route. register() returns
# a disposer and this module used to drop it, which made every row permanent
# import-time state. _UI_DISPOSERS holds them, so unload_ui() is the inverse
# of this module's UI registration and a host can embed or reset the studio.
# The registrations below still run at import: this file IS the composition
# root. Every control carries a visible word: a glyph alone is not a label.
_UI_DISPOSERS: list[Callable[[], None]] = []


def _slot(slot: str, id: str, render: object, order: float = 0.0) -> None:
    """Contribute into a slot and keep the disposer register() hands back."""
    _UI_DISPOSERS.append(SLOTS.register(slot, id, render, order=order))


def unload_ui() -> None:
    """Undo every slot contribution, LIFO and once (each disposer is
    idempotent): the inverse of importing the studio's UI."""
    while _UI_DISPOSERS:
        _UI_DISPOSERS.pop()()


def fab_strip() -> str:
    """Supported-fabs logo strip for the landing page, built from fab.py
    (tiles + profile urls) — one source of truth, never a stale copy.
    Logos load from /fab-logo/<key> (lazy) so ~100 KB of PNGs stay off the
    first HTML response; the hero can paint before the strip asks for them."""
    from ocdcircuit.fab import PROFILES
    cells = []
    for key in sorted(PROFILES):
        name = str(PROFILES[key].get("name", key))
        url = str(PROFILES[key].get("url", ""))
        cells.append(
            f'<a class=fabcell href="{url}" title="{name} — capabilities" '
            f'rel="noopener noreferrer">'
            f'<img src="/fab-logo/{key}" alt="{name} logo" width=96 height=32 '
            f'loading=lazy decoding=async>'
            f'</a>')
    return ('<div class=fabstrip role=group aria-label="supported fabs">'
            '<span class=fabkicker>ships to</span>' + "".join(cells)
            + '<span class=fabfine>logos belong to their owners</span></div>')
MENUS = (
    '<nav class=menubar aria-label="board menus">'
    '<button id=solve class=primary title="full solve, 5 seeds x 500 iters (Ctrl+Enter)">solve</button>'
    '<details class=menu id=m-board><summary title="board outputs and layouts">Board</summary><div class=mpop>'
    '<div class=mrow><button id=dice title="generate N candidate layouts side by side">candidates</button>'
    '<input id=ncand value=4 size=1 aria-label="candidate count" title="candidate count"></div>'
    '<button id=stamp title="stamp another copy of the hovered instance">stamp instance</button>'
    '<button id=fab_dl title="download the fab bundle as one zip">fab zip</button>'
    '<button id=dl title="download a render (cycles svg, sch, png, xray; shift-click backwards)">download render</button>'
    '</div></details>'
    '<details class=menu id=m-edit><summary title="undo history and revisions">Edit</summary><div class=mpop>'
    '<button id=undo title="undo (Ctrl+Z)">undo<span class=kbd>Ctrl+Z</span></button>'
    '<button id=redo title="redo (Ctrl+Y)">redo<span class=kbd>Ctrl+Y</span></button>'
    '<button id=diffprev title="what changed since the previous revision">diff</button>'
    '<button id=commit title="commit the open file to git (Ctrl+S)">commit<span class=kbd>Ctrl+S</span></button>'
    '</div></details>'
    '<details class=menu id=m-engines><summary title="placement, routing, fab and silk engines">Engines</summary><div class=mpop>'
    '<label>placer<select id=placer title="placement engine"></select></label>'
    '<label>router<select id=router title="routing engine"></select></label>'
    '<label>fab<select id=fab title="fab rules (edge, clearance, min trace)"></select></label>'
    '<label>silk<select id=silk title="silkscreen density"></select></label>'
    '</div></details>'
    '<details class=menu id=m-sim><summary title="simulate the current board">Simulate</summary><div class=mpop>'
    '<button id=simbtn title="simulate the current board (shift-click: tran)">sim dc</button>'
    '<div class=mnote>shift-click toggles dc/tran · needs sim lines</div>'
    '</div></details>'
    '<details class=menu id=m-tools><summary title="agent, calculators, prices, health">Tools</summary><div class=mpop>'
    '<button id=chatbtn type=button aria-pressed=false title="show or hide the agent chat panel">chat</button>'
    '<label class=auto title="apply a proposal without asking, but only when '
    'it builds DRC-clean"><input type=checkbox id=chatauto>auto-apply clean proposals</label>'
    '<details id=calc title="trace width and divider calculators"><summary>calc</summary>'
    '<label>A <input id=ca size=4 value=1 aria-label="trace current A"></label>'
    '<label>dT <input id=cdt size=3 value=10 aria-label="temperature rise C"></label>'
    '<div id=cout></div>'
    '<label>V <input id=dv size=4 value=5 aria-label="divider input V"></label>'
    '<label>Rt <input id=drt size=5 value=10k aria-label="divider top R"></label>'
    '<label>Rb <input id=drb size=5 value=10k aria-label="divider bottom R"></label>'
    '<div id=dout></div></details>'
    '<details id=doc title="tooling health: python, ngspice, plugins"><summary>health</summary>'
    '<div id=docout>click to check</div></details>'
    '<details id=quote title="fab price comparison: bare per fab, JLC assembled"><summary>quote</summary>'
    '<label>qty <input id=qqty value=5 size=3 aria-label="boards ordered"></label>'
    '<label><input type=checkbox id=qbare> bare only</label>'
    '<button id=qgo type=button class=primary title="compare fab prices for the open board">compare</button>'
    '<div id=qout role=status aria-live=polite></div></details>'
    '</div></details>'
    '<div class=mastat><span id=cost class=pill title="total wirelength">cost</span>'
    '<span id=ocdscore class=pill title="OCD neatness, 0-100"></span>'
    '<span id=feas class=pill title="routing feasibility per layer count"></span>'
    '<span id=stat role=status aria-live=polite></span></div>'
    '</nav>')
_slot("toolbar", "menus",
               lambda s: MENUS,
               order=1.0)
_slot("toolbar", "collab",
               lambda s: '<details class=menu id=m-live><summary title="who is on this board and how to invite">Share</summary><div class=mpop>'
                         '<div class=mnote id=roomnote>live on this board</div>'
                         '<button id=sharebtn title="copy a link to this board">copy invite link</button></div></details>',
               order=0.5)
_slot("view", "gallery",
               lambda s: '<section id=galwrap style="display:none">'
                         '<header class=panel-head><span class=panel-title>candidates</span>'
                         '<span class=panel-note>click one to adopt it, then drag it on the PCB to nudge and pin</span>'
                         '<span class=panel-note>job file 1F-04 &middot; placer diffusion &middot; 1 seed &times; 400 iters</span></header>'
                         '<div id=gal></div></section>',
               order=4.0)
_slot("view", "editor",
               lambda s: '<section id=edwrap>'
                         '<header class=panel-head><span class=panel-title>job file</span>'
                         '<span class=panel-note id=srcnote>board.ocd &middot; saved on every good build</span>'
                         '<span class=panel-note>edit here or drag on the PCB &middot; rebuilds in 0.4s</span></header>'
                         '<div id=ed contenteditable spellcheck=false role=textbox aria-multiline=true '
                         'aria-label=".ocd source, edits rebuild the board"></div>'
                         '<div id=srcpanels>'
                         + SLOTS.render("panel-left", None)
                         + '</div></section>',
               order=0.0)
_slot("panel-left", "filetree",
               lambda s: '<section id=filetree class=side>'
                         '<header class=panel-head><span class=panel-title>project</span>'
                         '<span class=panel-note id=treenote></span>'
                         '<label id=importlbl title="import a footprint, symbol, or board (kicad, eagle, tscircuit, altium, easyeda)">import<input id=importfile type=file hidden></label></header>'
                         '<div id=tree></div><div id=importstat role=status aria-live=polite></div></section>',
               order=0.0)
_slot("panel-left", "chat",
               lambda s: '<section id=chat class=side>'
                         '<header class=panel-head><span class=panel-title>agent</span>'
                         '<span class=panel-note id=chatwhere></span>'
                         '<button id=chatclear title="forget this conversation">clear</button></header>'
                         '<div id=msgs role=log aria-live=polite aria-label="agent conversation"></div>'
                         '<form id=composer><textarea id=ask rows=2 aria-label="message to the agent" '
                         'placeholder="ask about this board, or say what to change (Ctrl+Enter)"></textarea>'
                         '<button id=send class=primary type=submit>send</button></form>'
                         '</section>',
               order=1.0)
_slot("view", "vcs",
               lambda s: '<section id=vcswrap>'
                         '<header class=panel-head><span class=panel-title>revisions</span>'
                         '<span class=panel-note id=vcsnote></span>'
                         '<span class=panel-note>git history of the board directory</span></header>'
                         '<div id=vcs></div></section>',
               order=5.0)
_slot("view", "pcb",
               lambda s: '<section id=pcbwrap>'
                         '<header class=panel-head><span class=panel-title>PCB</span>'
                         '<span class=panel-note>drag a part to pin it &middot; double-click unpins &middot; right-click rotates</span>'
                         '<details id=layerbox title="show or hide layers and marks on this canvas">'
                         '<summary>layers</summary>'
                         '<div id=layers role=group aria-label="visible layers">'
                         '<span class=lbl>copper</span><div id=cu class=row></div>'
                         '<span class=lbl>marks</span><div id=marks class=row></div>'
                         '<button id=layersall type=button>show all</button></div></details>'
                         '<details id=partbox title="show or hide individual parts on this canvas">'
                         '<summary>parts</summary>'
                         '<div id=partpanel role=group aria-label="visible parts">'
                         '<div id=partbar><input id=partfilter type=search '
                         'placeholder="filter ref, value, footprint" aria-label="filter parts">'
                         '<button id=parthide type=button title="hide every part">none</button>'
                         '<button id=partshow type=button title="show every part">all</button>'
                         '<span id=partnote class=panel-note></span></div>'
                         '<div id=partlist></div></div></details></header>'
                         '<div class=platewrap><canvas id=pcb role=img aria-label="PCB layout"></canvas>'
                         '<div id=drc role=status aria-live=polite></div></div></section>',
               order=1.0)
_slot("view", "sch",
               lambda s: '<section id=schwrap>'
                         '<header class=panel-head><span class=panel-title>schematic</span>'
                         '<span class=panel-note>click a pin then a net to rewire &middot; alt-click drops a pin &middot; double-click a label renames it</span></header>'
                         '<canvas id=sch role=img aria-label="schematic"></canvas></section>',
               order=2.0)
_slot("view", "inspector",
               lambda s: '<section id=wrap3d>'
                         '<header class=panel-head><span class=panel-title>3D</span>'
                         '<span class=panel-note>click to spin</span></header>'
                         '<canvas id=t3d role=img aria-label="3D board preview"></canvas>'
                         '<header class=panel-head><span class=panel-title>x-ray</span>'
                         '<span class=panel-note>reference + fab scan check</span></header>'
                         '<div id=xraybar><input id=xrayfile type=file accept="image/png,.png" '
                         'aria-label="fab x-ray PNG to compare against the design">'
                         '<button id=xraysvg type=button title="the reference x-ray view">reference</button>'
                         '<button id=xraygo type=button class=primary title="compare the chosen scan against the design">compare</button>'
                         '<label>dx <input id=xraydx value=0 size=3 aria-label="scan x offset mm"></label>'
                         '<label>dy <input id=xraydy value=0 size=3 aria-label="scan y offset mm"></label>'
                         '<label>sc <input id=xraysc value=1 size=4 aria-label="scan scale"></label>'
                         '<label>cu <input id=xraythr value=100 size=3 aria-label="copper brightness cutoff"></label></div>'
                         '<div id=xraystat role=status aria-live=polite></div>'
                         '<div id=xraydivs></div>'
                         '<header class=panel-head><span class=panel-title>tidy</span>'
                         '<span class=panel-note id=tidycov></span></header>'
                         '<div id=tidy></div></section>',
               order=3.0)

_slot("view", "scan",
               lambda s: '<section id=scanwrap>'
                         '<header class=panel-head><span class=panel-title>photo scan</span>'
                         '<span class=panel-note>photos of a real board &rarr; draft design</span></header>'
                         '<div id=scanbar>'
                         '<input id=scanfiles type=file multiple accept="image/*" '
                         'aria-label="photos of the board, both sides">'
                         '<label>mm <input id=scanmm size=4 value="" '
                         'aria-label="board width in mm, if known"></label>'
                         '<button id=scango type=button class=primary '
                         'title="stitch, enhance, and reverse-engineer">analyse</button></div>'
                         '<div id=scanbar2>'
                         '<input id=scannote type=search aria-label="what this board is" '
                         'placeholder="what is it? e.g. scope PSU pulled from a dead unit">'
                         '<input id=scandocs type=file multiple accept=".pdf,.txt,.md" '
                         'aria-label="manual or datasheet"></div>'
                         '<div id=scanstat role=status aria-live=polite>'
                         'name files with &ldquo;top&rdquo; / &ldquo;bottom&rdquo; so the sides are split &middot; '
                         'shoot whole-board frames plus mid-range ones; very tight close-ups often fail to line up</div>'
                         '<div id=scanq></div>'
                         '<div id=scanview hidden>'
                         '<div id=scanviewbar>'
                         '<label>side <select id=scanside aria-label="board side to view">'
                         '<option value=top>top</option><option value=bottom>bottom</option>'
                         '</select></label>'
                         '<label>view <select id=scanviewkind aria-label="which enhancement to show">'
                         '<option value=stitch>photo</option><option value=contrast>markings</option>'
                         '</select></label>'
                         '<label class=scancheck><input id=scanlabels type=checkbox checked> labels</label>'
                         '<label class=scancheck><input id=scanboxes type=checkbox checked> outlines</label>'
                         '<span id=scanhover role=status aria-live=polite></span></div>'
                         '<div class=scanstage><img id=scanimg alt="stitched board photo">'
                         '<svg id=scansvg aria-hidden=true></svg></div>'
                         '<div id=scanparts></div></div>'
                         '<pre id=scanout></pre>'
                         '</section>',
               order=5.5)

_slot("view", "kb",
               lambda s: '<section id=kbwrap>'
                         '<header class=panel-head><span class=panel-title>knowledgebase</span>'
                         '<span class=panel-note id=kbnote>kb/ beside the board</span>'
                         '<span class=panel-note>notes + datasheets &middot; the agent reads the same files</span></header>'
                         '<div id=kbbar>'
                         '<input id=kbq type=search aria-label="ask the knowledgebase" '
                         'placeholder="ask: what is the input voltage range?  (or a search term)">'
                         '<button id=kbask class=primary type=button title="passages that answer the question (embeddings)">ask</button>'
                         '<button id=kbgrep type=button title="exact term match, one line per hit">search</button>'
                         '<button id=kbans type=button title="also write an answer with the local model">answer</button></div>'
                         '<div id=kbadd>'
                         '<input id=kburl type=search aria-label="datasheet url or file path" '
                         'placeholder="https://…/datasheet.pdf  or  path/to/note.md">'
                         '<button id=kbaddbtn type=button title="copy or download it into kb/">add</button>'
                         '<button id=kbfetch type=button title="download the datasheet for every datasheet= / lcsc= part">fetch datasheets</button>'
                          '<button id=kbprefsbtn type=button title="preferences the agent follows without being asked">preferences</button></div>'
                          '<div id=kbprefs style="display:none"><div id=kbprefslist></div>'
                          '<div id=kbprefsadd><input id=kbwhen aria-label="when this applies" placeholder="when placing connectors">'
                          '<input id=kbwhat aria-label="what to prefer" placeholder="put them on the board edge">'
                          '<button id=kbprefsgo type=button title="save as a new preference">remember</button></div></div>'
                         '<div id=kbstat role=status aria-live=polite>click a document to read it</div>'
                         '<div id=kblist></div>'
                         '<pre id=kbview></pre>'
                         '</section>',
               order=6.0)

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "boards", "blinky_555.ocd")
SRC = os.path.abspath(SRC)
BASE = os.path.dirname(SRC)

# THESIS: the logged-out screen is a launchpad, not a wall — Flux's hero
# (dark cosmos, glowing prompt, honest flow strip) earns the signup; the form
# waits one click behind. OWN-WORLD: login-gate terminal tokens; the visual is
# the product (live board canvas, traces + glow dots, no stock photo, no
# invented stats). STORY: visitor gets the offer in one viewport, starts
# designing via the account form, lands on the shelf. FIRST VIEWPORT: nav,
# hook, glowing prompt card over the board visual, single CTA; collab strip
# + AI engine story sit below. FORM: Persuade surface in the established
# world, no seed roll (brief-pinned). FINISH: DESIGN.md is the authority.
LOGIN_PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8><title>OCD Studio — two engineers, one board</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=description content="Open a board, send the link, co-edit it live. Two cursors, one schematic, zero merge conflicts — with an AI engine that drafts, places and routes beside you.">
<link rel=icon href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Crect width='20' height='20' rx='4' fill='%23101418'/%3E%3Crect x='2' y='2' width='16' height='16' rx='4' fill='none' stroke='%23d8e2dc' stroke-width='1.8'/%3E%3Cpath d='M6.5 7.2 9.3 10l-2.8 2.8' fill='none' stroke='%235fd894' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3Cline x1='11' y1='12.8' x2='14' y2='12.8' stroke='%235fd894' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E">
<style>
:root{
--term:#101418;--term-2:#1a2129;--term-line:#2a333d;--term-text:#d8e2dc;
--term-faint:#7f8b94;--term-bad:#ff7364;--term-ok:#5fd894;--term-key:#ffd8a0;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--term);color:var(--term-text);font:1rem/1.55 var(--sans)}
/* hero: nav, hook, glowing prompt over the live board, one CTA. The prompt
sits over the visual (like Flux's card over its PCB render), so the canvas
is atmosphere behind real content — never decoration. */
.hero{position:relative;overflow:hidden;text-align:center;
padding:1rem 1.5rem 3.5rem;min-height:92vh;display:flex;flex-direction:column}
.hero canvas{position:absolute;inset:0;width:100%;height:100%}
.hero>*:not(canvas){position:relative}
.nav{display:flex;align-items:center;gap:.6rem 1rem;flex-wrap:wrap;
max-width:70rem;margin:0 auto;width:100%}
.brand{display:flex;align-items:center;gap:.5rem;font-weight:700;font-size:1.1rem;
letter-spacing:-.02em;color:#fff;text-decoration:none}
.brand em{font-style:normal;color:var(--term-ok)}
.nav .sp{flex:1}
.nav button{font:600 .9rem var(--sans);padding:.5rem 1rem;border-radius:8px;cursor:pointer}
#loginbtn{background:transparent;color:var(--term-text);border:1px solid var(--term-line)}
#loginbtn:hover{border-color:var(--term-ok)}
#topcta{background:var(--term-ok);border:1px solid var(--term-ok);color:#06130d}
#topcta:hover{filter:brightness(1.07)}
/* one centred stack instead of a 12vh margin that pushed the hero past the
fold on short viewports; auto margins centre it and the content sets the height. */
.herobody{flex:1;display:flex;flex-direction:column;justify-content:center;
max-width:70rem;margin:0 auto;width:100%;padding:3rem 0 0}

h1{font-size:clamp(2.2rem,5vw,3.4rem);line-height:1.08;letter-spacing:-.03em;
margin:0 0 .6rem;color:#fff;text-wrap:balance}
.dek{color:var(--term-faint);font-size:clamp(1rem,1.6vw,1.15rem);margin:0 auto 2rem;max-width:34rem}
/* the hero's one authored glow — signal green, the product's own colour,
never a category purple that belongs to no token here. */
.prompt{max-width:34rem;margin:0 auto;width:100%;background:rgba(16,20,24,.92);
border:1px solid var(--term-ok);border-radius:14px;padding:1.1rem 1.2rem;text-align:left;
box-shadow:0 0 0 1px rgba(95,216,148,.22),0 18px 60px -12px rgba(95,216,148,.45)}
.prompt p{margin:0 0 .9rem;font-size:1.02rem;line-height:1.7;color:var(--term-text)}
.prompt button{width:100%;font:600 1rem var(--sans);padding:.8rem;border-radius:9px;
border:1px solid var(--term-ok);background:var(--term-ok);color:#06130d;cursor:pointer}
.prompt button:hover{filter:brightness(1.07)}
/* honest strip: the flow, never invented counts (no fake builders stat).
An ordered list, because that is what four numbered steps are; the arrow is
CSS so screen readers hear the steps, not "right arrow" four times. */
.flowline{display:flex;gap:.6rem;justify-content:center;flex-wrap:wrap;
margin:2.2rem 0 0;padding:0;list-style:none;font:.82rem var(--mono);color:var(--term-faint)}
.flowline li{display:flex;align-items:center;gap:.6rem}
.flowline li:not(:last-child)::after{content:"→";color:var(--term-line)}
.flowline b{color:var(--term-ok);font-weight:600}
/* gate: the account form, one click behind the hero */
.gate{display:none;min-height:100dvh;grid-template-columns:minmax(0,34rem) 1fr}
body.authed .hero,body.gating .hero{display:none}
body.gating .gate{display:grid}
/* min-width:0 — as a grid item it defaults to min-content, so a long board
name in a shelf card stretched the whole column past a phone viewport. */
.form{padding:clamp(2rem,6vh,4.5rem) clamp(1.5rem,4vw,3.5rem);display:flex;flex-direction:column;
justify-content:center;gap:1rem;max-width:30rem;width:100%;margin:0 auto;min-width:0}
.form h2{font-size:1.6rem;letter-spacing:-.02em;margin:.5rem 0 0;color:#fff}
.sub{color:var(--term-faint);font-size:.92rem;margin:0 0 .5rem}
label{display:grid;gap:.3rem;font-size:.85rem;font-weight:600}
input{font:1rem var(--sans);padding:.7rem .9rem;border-radius:9px;border:1px solid var(--term-line);
background:var(--term-2);color:var(--term-text)}
input:focus{outline:2px solid var(--term-ok);outline-offset:1px;border-color:var(--term-ok)}
.form button{font:600 1rem var(--sans);padding:.75rem;border-radius:9px;border:1px solid var(--term-ok);
background:var(--term-ok);color:#06130d;cursor:pointer}
.form button:hover{filter:brightness(1.07)}
button.ghost{background:transparent;color:var(--term-text);border-color:var(--term-line)}
button.ghost:hover{border-color:var(--term-ok);background:rgba(95,216,148,.06)}
/* the credential form is a stack: full-width submit, then the mode swap.
Inline-flow buttons sized themselves to their label and read as a mismatched pair. */
#f{display:grid;gap:.85rem}
/* shelf controls belong to a logged-in shelf, never to the signup form */
#promptbox,#newprojbtn,.shelfonly{display:none}
body.shelf #promptbox{display:flex}
body.shelf #newprojbtn,body.shelf .shelfonly{display:block}
:focus-visible{outline:2px solid var(--term-ok);outline-offset:2px}
/* the gate was a one-way door: entering it hid the hero with no way back */
.backlink{align-self:start;background:none;border:0;padding:.3rem 0;cursor:pointer;
color:var(--term-faint);font:.85rem var(--sans)}
.backlink:hover{color:var(--term-ok)}
body.shelf .backlink{display:none}
#err{color:var(--term-bad);font:.85rem var(--mono);min-height:1.4em;margin:0}
#err:empty{min-height:0}
#err.ok{color:var(--term-ok)}
#shelf{display:none;gap:.6rem}
#shelf.has{display:grid}
.scard{text-align:left;background:var(--term-2);color:var(--term-text);
border:1px solid var(--term-line);border-radius:10px;padding:.7rem .9rem;cursor:pointer}
.scard:hover{border-color:var(--term-ok)}
.scard b{display:block;font-size:.95rem}
.scard span{display:block;font-size:.8rem;color:var(--term-faint)}
.scard small{font:.75rem var(--mono);color:var(--term-faint)}
/* board names and paths are user data: wrap them, never widen the layout */
.scard b,.scard span,.scard small{overflow-wrap:anywhere}
#newboard{display:none;gap:.5rem}
body.shelf #newboard.has{display:grid}
#profrow{gap:.5rem}
body.shelf #profrow{display:flex}
#profrow input{flex:1;min-width:0}
#profrow button{white-space:nowrap}
.fine{color:var(--term-faint);font-size:.78rem;margin:.2rem 0 0}
.tsec{font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--term-faint);margin:.6rem 0 0}
#promptbox{gap:.5rem;margin-bottom:.4rem}  /* display owned by body.shelf above */
#promptbox input{flex:1;min-width:0}
#promptbox button{white-space:nowrap}
/* new-project modal: search + grid + blank CTA over the dimmed shelf.
Native <dialog>: focus trap, Esc, backdrop — no library, no state. */
#newproj{border:1px solid var(--term-line);border-radius:14px;background:var(--term);
color:var(--term-text);padding:1.2rem;max-width:40rem;width:calc(100vw - 3rem)}
#newproj::backdrop{background:rgba(0,0,0,.6)}
#newproj h3{margin:0 0 .6rem;font-size:1.1rem}
#npsearch{width:100%;margin-bottom:.8rem}
#npgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:.6rem;
max-height:50vh;overflow:auto}
#npblank{margin-top:.8rem;width:100%}
.visual{position:relative;min-height:100dvh;overflow:hidden;background:#0a0f14;margin:0}
.visual canvas{position:absolute;inset:0;width:100%;height:100%}
/* the caption sat on bare canvas; a scrim keeps it legible over any frame */
.visual figcaption{position:absolute;left:0;right:0;bottom:0;color:#fff;
font:.85rem var(--mono);padding:3rem 1.5rem 1.2rem;
background:linear-gradient(transparent,rgba(6,10,14,.85))}
/* collab strip: a live crowd on one board, the realtime story in one row */
/* min-width:0 all the way down: the .mini blocks are white-space:pre, so their
min-content width (563px) would otherwise blow the grid past a phone viewport. */
.collab{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(17rem,100%),1fr));
gap:1.2rem;margin:2.6rem auto 0;max-width:62rem;width:100%;min-width:0;text-align:left}
.person{background:rgba(16,20,24,.92);border:1px solid var(--term-line);
border-radius:14px;padding:1rem 1.1rem;position:relative;min-width:0}
.person b.who{display:block;margin:0 0 .2rem;font-size:.95rem;color:#fff;font-weight:700}
.person b.who i{display:inline-block;width:.65rem;height:.65rem;border-radius:50%;margin-right:.45rem}
.person p{margin:.15rem 0 .7rem;font-size:.85rem;color:var(--term-faint)}
.mini{border:1px solid var(--term-line);border-radius:8px;background:#0a0f14;
font:.72rem/1.7 var(--mono);color:var(--term-text);padding:.6rem .7rem;
white-space:pre;overflow-x:auto}  /* pre would overflow the card on narrow screens */
.person small{display:block;margin-top:.6rem;font:.75rem var(--mono);color:var(--term-faint)}
.person small b{color:var(--term-ok);font-weight:600}
/* below the fold: AI engine story — never inside the hero CTA card */
.aistory{max-width:34rem;margin:2.4rem auto 0;color:var(--term-faint);
font-size:.95rem;line-height:1.65;text-wrap:balance}
.aistory b{color:var(--term-text);font-weight:600}
a{color:var(--term-ok)}
/* supported-fabs strip: real vendor tiles, links out to capabilities */
.fabstrip{display:flex;gap:.4rem .8rem;justify-content:center;align-items:center;flex-wrap:wrap;
margin:2.6rem auto 0;max-width:62rem}
.fabkicker{font:.72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--term-faint)}
.fabfine{font:.72rem var(--mono);color:var(--term-faint)}
.fabcell{display:inline-flex;align-items:center;text-decoration:none;
border:1px solid var(--term-line);border-radius:9px;padding:.3rem}
.fabcell:hover{border-color:var(--term-ok)}
.fabcell img{display:block;border-radius:5px;width:96px;height:32px;object-fit:contain}
@media(max-width:760px){.gate{grid-template-columns:1fr}.visual{display:none}
.hero{padding:1rem 1.1rem 2.5rem}.herobody{padding-top:2rem}
/* offer + CTA first; collab proof and AI story follow below the fold */
.collab{order:2;margin-top:1.8rem}.aistory{order:3}.fabstrip{order:4}
.mini{white-space:pre-wrap}}
/* reduced motion keeps the authored frame — paint() already stops after one
pass, so hiding the canvas threw away the visual instead of the animation. */
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<!-- landing: hero first, account form one click behind, shelf after login -->
<header class=hero><canvas id=art aria-hidden=true></canvas>
<nav class=nav><span class=brand><svg width=22 height=22 viewBox="0 0 20 20" aria-hidden=true><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#5fd894 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#5fd894 stroke-width=1.8 stroke-linecap=round></line></svg>OCD <em>Studio</em></span>
<span class=sp></span><button id=loginbtn type=button>Log in</button>
<button id=topcta type=button>Start a board together</button></nav>
<div class=herobody>
<h1>Your whole team. One board. Zero merge conflicts.</h1>
<p class=dek>Open a board, send the link, you're co-editing — every cursor, every part move, every net, in real time.</p>
<div class=prompt><p>Open a board, send the link — same schematic, two cursors, zero merge conflicts.</p>
<button id=herogo type=button>Start a board together</button></div>
<ol class=flowline><li><b>1</b> idea</li><li><b>2</b> schematic</li><li><b>3</b> layout</li><li><b>4</b> make</li></ol>
<div class=collab role=group aria-label="three engineers editing one board live">
<div class=person><b class=who><i style="background:#5fd894"></i>maya</b>
<p>dragging the regulator into place</p>
<div class=mini>fix U1 at 12.4 18.1
part C3 C0805 100n
GND :: U1.1 &lt;--&gt; C3.1</div>
<small><b>● live</b> · rev 42 · pushing</small></div>
<div class=person><b class=who><i style="background:#3a7bd5"></i>leo</b>
<p>wiring the sensor net</p>
<div class=mini>net N_SDA :: U2.5 &lt;--&gt; J1.3
net N_SCL :: U2.6 &lt;--&gt; J1.4
route N_SDA on 0</div>
<small><b>● live</b> · rev 42 · pushing</small></div>
<div class=person><b class=who><i style="background:#8a2318"></i>priya</b>
<p>pouring the ground plane</p>
<div class=mini>pour GND on 0
keep U1 near C1 3
power VCC GND</div>
<small><b>● live</b> · rev 42 · pushing</small></div>
</div>
<p class=aistory><b>AI beside you</b> — drafts the schematic, places parts, routes traces. You stay the lead engineer.</p>
/*__FABS__*/
</div>
</header>
<main class=gate><div class=form>
<button id=back type=button class=backlink><span aria-hidden=true>←</span> Back to the overview</button>
<div class=brand><svg width=24 height=24 viewBox="0 0 20 20" aria-hidden=true><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#5fd894 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#5fd894 stroke-width=1.8 stroke-linecap=round></line></svg>OCD <em>Studio</em></div>
<h2 id=title>Create your Studio account</h2>
<p class=sub id=sub>Local account beside your boards — then open one and send the link.</p>
<p id=err role=alert aria-live=polite tabindex=-1></p>
<form id=f><label>Username<input id=u autocomplete=username maxlength=32 required aria-describedby=err></label>
<label>Password<input id=p type=password autocomplete=current-password minlength=8 required aria-describedby=err></label>
<button id=go type=submit>Create account</button>
<button id=swap type=button class=ghost>Have an account? Log in</button></form>
<div id=shelf role=group aria-label="your boards"></div>
<form id=promptbox><input id=promptq aria-label="describe a board to start" placeholder="e.g. 555 blinky, USB-C breakout, 2-layer sensor"><button type=submit>Start</button></form>
<button id=newprojbtn type=button class=ghost>New project…</button>
<dialog id=newproj aria-label="new project"><h3>New project</h3>
<input id=npsearch type=search aria-label="search boards and templates" placeholder="Search boards and templates">
<div id=npgrid></div>
<button id=npblank type=button>New blank project</button></dialog>
<div id=profrow class=shelfonly><input id=profin aria-label="display name" maxlength=40 placeholder="Display name"><button id=profgo type=button title="save how your name reads on boards and in rooms">Save display name</button></div>
<form id=newboard><label>New board<input id=nbname placeholder=blinky maxlength=32></label>
<button type=submit>New board</button></form>
<p class=fine>Local-first: accounts live in this studio only (.ocd-users beside the boards).</p>
</div>
<figure class=visual><canvas id=art2 aria-hidden=true></canvas>
<figcaption>idea → schematic → layout → make · drag parts · solve · fab zip</figcaption></figure>
</main>
<script>
const $=id=>document.getElementById(id);
let mode='signup', me=null;
function paint(c){const x=c.getContext('2d');let t=Math.random()*10; // one authored visual, two canvases
function frame(){if(!c.isConnected)return;
const r=c.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);
c.width=Math.max(1,r.width*d);c.height=Math.max(1,r.height*d);
x.setTransform(d,0,0,d,0,0);const W=r.width,H=r.height;t+=0.004;
const g=x.createLinearGradient(0,0,W,H);
g.addColorStop(0,'#0a1410');g.addColorStop(.55,'#0c1a30');g.addColorStop(1,'#0a0f14');
x.fillStyle=g;x.fillRect(0,0,W,H);
x.fillStyle='rgba(255,255,255,.5)';
for(let i=0;i<90;i++){x.globalAlpha=.12+((i*13)%10)/60;
x.fillRect((i*97.3)%W,(i*57.7)%H,1.2,1.2);}
x.globalAlpha=1;
const bw=Math.max(200,W*.62),bh=Math.max(140,H*.42),ox=(W-bw)/2,oy=(H-bh)/2+20;
x.strokeStyle='rgba(95,216,148,.35)';x.lineWidth=1.5;x.strokeRect(ox,oy,bw,bh);
const cols=['#c0392b','#3a7bd5','#5fd894','#ffd8a0']; // red/blue/signal/key — no category purple
for(let i=0;i<4;i++){const y0=oy+20+i*(bh-40)/3;
x.strokeStyle=cols[i];x.lineWidth=3;x.globalAlpha=.8;
x.beginPath();x.moveTo(ox,y0);
x.bezierCurveTo(ox+bw*.3,y0-40,ox+bw*.6,y0+40,ox+bw,y0-10+((i*29)%30));x.stroke();
const tt=(t+i*.25)%1;
x.fillStyle='#ffd8a0';x.beginPath();
x.arc(ox+bw*tt,y0+Math.sin(tt*6.28+i)*14,3.5,0,7);x.fill();}
x.globalAlpha=1;x.fillStyle='#d9a821';
for(let i=0;i<8;i++){const px=ox+20+i*(bw-40)/7;
x.fillRect(px-3,oy-5,6,4);x.fillRect(px-3,oy+bh+1,6,4);}
if(!matchMedia('(prefers-reduced-motion: reduce)').matches)requestAnimationFrame(frame);}
frame();}
// paint after first frame so hero text is not blocked by canvas setup
requestAnimationFrame(()=>{paint($('art'));paint($('art2'));});
function gate(){document.body.classList.add('gating');
const f=$('u');if(f&&!f.value)f.focus();}
$('herogo').onclick=()=>setMode('signup');$('topcta').onclick=()=>setMode('signup');
$('back').onclick=()=>{document.body.classList.remove('gating');$('err').textContent='';$('err').classList.remove('ok');};
$('loginbtn').onclick=()=>{gate();setMode('login');};
async function api(p,b){const r=await fetch(p,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json();}
async function boot(){const r=await api('/auth/me',{});
// the hero is the landing page: only an existing session skips it. A logged-out
// visitor keeps the hero and picks the mode the form will open in.
if(r.user){me=r.user;gate();showShelf();}else setMode(r.needs_setup?'signup':'login',false);}
function setMode(m,show=true){mode=m;if(show)gate();
$('title').textContent=m==='signup'?'Create your Studio account':'Welcome back';
$('sub').textContent=m==='signup'?'Local account beside your boards — then open one and send the link.'
  :'Log in to open your shelf and pick up where you left off.';
$('go').textContent=m==='signup'?'Create account':'Log in';
$('p').setAttribute('autocomplete',m==='signup'?'new-password':'current-password');
$('swap').textContent=m==='signup'?'Have an account? Log in':'New here? Create an account';}
$('swap').onclick=()=>setMode(mode==='signup'?'login':'signup');
function authErr(msg,field){ // announce + move focus so keyboard/AT users hear it
$('err').textContent=msg;$('err').classList.remove('ok');
['u','p'].forEach(id=>$(id).removeAttribute('aria-invalid'));
if(field){field.setAttribute('aria-invalid','true');field.focus();}
else{$('err').focus();}}
$('f').onsubmit=async e=>{e.preventDefault();$('err').textContent='';$('err').classList.remove('ok');
['u','p'].forEach(id=>$(id).removeAttribute('aria-invalid'));
const u=$('u').value.trim(),p=$('p').value;
if(!u){authErr("Username can't be blank!",$('u'));return;}
const go=$('go');if(go.disabled)return;go.disabled=true;
try{
const r=await api(mode==='signup'?'/auth/signup':'/auth/login',{user:u,password:p});
if(r.error){authErr(r.error);return;}
me=r.user||u;showShelf();
}finally{go.disabled=false;}};
function openShelfBoard(name){ // every create path lands in the workshop
  if(!name)return;location.href='/?board='+encodeURIComponent(String(name).replace(/\.ocd$/,''));}
async function fromTemplate(name){ // one in-flight copy: double-click must not mint -2/-3
  if(fromTemplate._busy)return;fromTemplate._busy=true;
  try{const x=await api('/shelf/from_template',{name});
    if(x.error){authErr(x.error);return;}
    if($('newproj').open)$('newproj').close();
    openShelfBoard(x.name);}
  finally{fromTemplate._busy=false;}}
async function showShelf(){$('f').style.display='none';
document.body.classList.add('shelf');  // one class reveals every shelf-only control
const me0=await api('/auth/me',{});
const who=(me0&&me0.display)||me;
if(me0&&me0.display)$('profin').value=me0.display;
$('title').textContent='Welcome, '+who;
$('sub').textContent='Pick a board to open the workshop, or start a new one.';
const r=await api('/shelf',{});
const box=$('shelf');box.innerHTML='';box.classList.add('has');
$('newboard').classList.add('has');
if(!r.boards.length){box.innerHTML='<span class=fine>no boards yet — describe one above, or New project</span>';}
cardSec(box,'Your boards',r.boards.map(b=>({t:b.name.replace(/\.ocd$/,''),
  s:b.blurb||`${b.parts} parts · ${b.nets} nets`,
  m:`${b.name} · ${b.mtime}`,
  go:()=>openShelfBoard(b.name)})));
cardSec(box,'Templates',(r.templates||[]).map(t=>({t:t.name.replace(/\.ocd$/,''),
  s:t.blurb||'starter board',
  m:'template — opens a copy in the workshop',
  go:()=>fromTemplate(t.name)})));
_npcache={boards:r.boards.map(b=>({t:b.name.replace(/\.ocd$/,''),
  s:b.blurb||`${b.parts} parts · ${b.nets} nets`,
  m:`${b.name} · ${b.mtime}`,
  go:()=>openShelfBoard(b.name)})),
  templates:(r.templates||[]).map(t=>({t:t.name.replace(/\.ocd$/,''),
  s:t.blurb||'starter board',m:'template — opens a copy in the workshop',
  go:()=>fromTemplate(t.name)}))};
}
function cardSec(box,h,cards){
  if(!cards.length)return;
  const sec=document.createElement('div');sec.className='tsec';sec.textContent=h;
  box.appendChild(sec);
  cards.forEach(c=>{const b=document.createElement('button');b.className='scard';
    b.innerHTML='';const t=document.createElement('b');t.textContent=c.t;
    const s=document.createElement('span');s.textContent=c.s;
    const m=document.createElement('small');m.textContent=c.m;
    b.append(t,s,m);b.onclick=c.go;box.appendChild(b);});
}
$('promptbox').onsubmit=async e=>{e.preventDefault();
  const q=$('promptq').value.trim();if(!q)return;
  const go=$('promptbox').querySelector('button');if(go&&go.disabled)return;
  if(go)go.disabled=true;
  try{const r=await api('/shelf/new',{name:q});
    if(r.error){authErr(r.error+' — try a shorter name');return;}
    openShelfBoard(r.name);}
  finally{if(go)go.disabled=false;}};
$('profgo').onclick=async()=>{
  if($('profgo').disabled)return;$('profgo').disabled=true;
  try{const r=await api('/auth/profile',{display:$('profin').value});
    if(r.error){authErr(r.error);return;}
    $('title').textContent='Welcome, '+r.display;
    $('err').textContent='display name saved';$('err').classList.add('ok');}
  finally{$('profgo').disabled=false;}};
$('newboard').onsubmit=async e=>{e.preventDefault();
  const go=$('newboard').querySelector('button[type=submit]');if(go&&go.disabled)return;
  if(go)go.disabled=true;
  try{const r=await api('/shelf/new',{name:$('nbname').value});
    if(r.error){authErr(r.error);return;}
    openShelfBoard(r.name);}
  finally{if(go)go.disabled=false;}};
// new-project modal: the shelf's own lists, filtered client-side. Reuses
// cardSec cards; blank reuses /shelf/new with the search text as the name.
let _npcache={boards:[],templates:[]};
$('newprojbtn').onclick=()=>{_npcache._last=($('promptq').value||'');
  $('npsearch').value=_npcache._last;npRender();$('newproj').showModal();};
$('npsearch').oninput=npRender;
$('npblank').onclick=async()=>{
  if($('npblank').disabled)return;$('npblank').disabled=true;
  try{const q=$('npsearch').value.trim()||'untitled';
    const r=await api('/shelf/new',{name:q});
    if(r.error){authErr(r.error);return;}
    $('newproj').close();openShelfBoard(r.name);}
  finally{$('npblank').disabled=false;}};
function npRender(){
  const q=$('npsearch').value.trim().toLowerCase();
  const box=$('npgrid');box.innerHTML='';
  const hit=c=>(c.t+' '+c.s).toLowerCase().includes(q);
  cardSec(box,'Your boards',_npcache.boards.filter(hit));
  cardSec(box,'Templates',_npcache.templates.filter(hit));
  if(!box.children.length)box.innerHTML='<span class=fine>no matches — try blank below</span>';
}
boot();
</script></body></html>
"""

PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8><title>OCD Studio</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel=icon href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Crect width='20' height='20' rx='4' fill='%23f7f5f0'/%3E%3Crect x='2' y='2' width='16' height='16' rx='4' fill='none' stroke='%231a1d21' stroke-width='1.8'/%3E%3Cpath d='M6.5 7.2 9.3 10l-2.8 2.8' fill='none' stroke='%230f5c37' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3Cline x1='11' y1='12.8' x2='14' y2='12.8' stroke='%230f5c37' stroke-width='1.8' stroke-linecap='round'/%3E%3C/svg%3E">
<style>
/* Paper spec sheet, one terminal. Tokens follow the recompile.online design
   system: warm paper ground, white cards, one signal green, one dark surface
   (the job-file editor). Sans carries what a person reads; mono is the
   machine's voice (source, readouts, listings). State is a word in a pill. */
:root{
--paper:#f7f5f0;--paper-2:#efece4;--card:#fffdf8;--line:#e2ddd0;--line-2:#cfc8b6;
--ink:#1a1d21;--ink-2:#4d545c;--ink-3:#5c646c;
--signal:#0f5c37;--signal-ink:#0c4a2d;--signal-wash:#dcefe1;--ok-border:#9cc6aa;
--bad:#8a2318;--bad-wash:#f5c9c2;--danger-border:#d59f96;
--warn:#6b4a00;--warn-wash:#f2dbaa;--warn-border:#cbab72;
--term:#101418;--term-2:#1a2129;--term-line:#2a333d;--term-text:#d8e2dc;
--term-faint:#7f8b94;--term-key:#ffd8a0;--term-ok:#5fd894;--term-bad:#ff7364;
--r:10px;--r-control:9px;--shadow:0 1px 2px rgba(26,29,33,.06),0 8px 24px -12px rgba(26,29,33,.18);
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:1rem/1.55 var(--sans);height:100vh;display:flex;flex-direction:column;overflow:hidden}
.skip{position:absolute;left:-9999px}.skip:focus{left:8px;top:8px;z-index:9;background:var(--card);padding:8px 12px;border:1px solid var(--line-2);border-radius:var(--r-control)}
/* --- header: brand left, labelled control groups, status pills right --- */
header.top{background:var(--card);border-bottom:1px solid var(--line)}
header.top .inner{display:flex;align-items:center;gap:20px;padding:10px 20px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:1.02rem;letter-spacing:-.02em;color:var(--ink)}
.brand svg{color:var(--ink)}
.brand i{font-style:normal;font-weight:400;color:var(--ink-3)}
.menubar{display:flex;align-items:center;gap:4px;flex-wrap:wrap;flex:1}
.menubar>details.menu{position:relative;display:inline-block}
.menubar>details.menu>summary{padding:9px 13px;list-style:none;display:inline-block;cursor:pointer;
font:600 .9rem/1.3 var(--sans);color:var(--ink);background:var(--card);border:1px solid transparent;border-radius:var(--r-control)}
.menubar>details.menu>summary::-webkit-details-marker{display:none}
.menubar>details.menu>summary:hover{background:var(--paper-2)}
.menubar>details.menu[open]>summary{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink)}
.menubar .mpop{position:absolute;left:0;top:calc(100% + 6px);z-index:5;display:flex;flex-direction:column;
gap:2px;align-items:stretch;background:var(--card);border:1px solid var(--line);border-radius:var(--r);
box-shadow:var(--shadow);padding:6px;min-width:15rem;max-width:22rem}
.menubar .mpop>button{text-align:left;background:none;border:0;border-radius:6px;padding:8px 10px;
display:flex;justify-content:space-between;align-items:center;gap:1rem}
.menubar .mpop>button:hover{background:var(--paper-2)}
.menubar .mpop>label{display:flex;align-items:center;justify-content:space-between;gap:8px;
font-size:.85rem;color:var(--ink-2);padding:6px 10px;border-radius:6px}
.menubar .mpop>label:hover{background:var(--paper-2)}
.menubar .mpop select{max-width:9rem}
.menubar .mpop details{margin:0}
.menubar .mpop details>summary{width:100%;text-align:left;border:0;background:none;padding:8px 10px;border-radius:6px}
.menubar .mpop details>summary:hover{background:var(--paper-2)}
.menubar .mpop details[open]>:not(summary){position:static;box-shadow:none;border:0;border-top:1px solid var(--line);
border-radius:0;min-width:0;padding:10px}
.mrow{display:flex;gap:6px;align-items:center;padding:2px 4px}
.mrow button{flex:1}
.mnote{font-size:.78rem;color:var(--ink-3);padding:4px 10px}
.kbd{font:.72rem var(--mono);color:var(--ink-3);margin-left:auto;padding-left:1rem}
.mastat{display:flex;align-items:center;gap:6px;margin-left:auto;flex-wrap:wrap}
.mlive{display:flex;align-items:center;gap:6px;margin-left:8px}
button,select,input,summary{font:600 .9rem/1.3 var(--sans);color:var(--ink);background:var(--card);border:1px solid var(--line-2);border-radius:var(--r-control);padding:9px 13px;cursor:pointer}
button:hover,select:hover,summary:hover{background:var(--paper-2)}
button:active{transform:translateY(1px)}
button.primary{background:var(--signal);border-color:var(--signal-ink);color:#fff;box-shadow:0 1px 2px rgba(12,74,45,.3)}
button.primary:hover{background:var(--signal-ink)}
/* dark signal green is light: white label fails 1.4.3; ink on green keeps ≥4.5:1 */
body.dark button.primary{color:#06130d;box-shadow:none}
body.dark button.primary:hover{filter:brightness(1.06);background:var(--signal)}
button[disabled]{opacity:.45;cursor:not-allowed;transform:none}
input{cursor:text;font-weight:400;font-variant-numeric:tabular-nums;padding:8px 10px}
#ncand{width:3.2rem;text-align:center}
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible,[contenteditable]:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
details{position:relative;display:inline-block}
summary{padding:9px 13px;list-style:none;display:inline-block}
summary::-webkit-details-marker{display:none}
details[open]>summary{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink)}
details[open]>:not(summary){position:absolute;left:0;top:calc(100% + 6px);z-index:5;display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:12px 14px;min-width:max-content}
details label{display:flex;align-items:center;gap:5px;font-size:.85rem;color:var(--ink-2)}
details input{width:4.4rem}
/* layer/part visibility: a column panel, not the one-row calculator dropdown */
.panel-head{overflow:visible} /* the dropdown panels must escape the head */
#layerbox>#layers,#partbox>#partpanel{display:block;flex-direction:column;width:17rem;min-width:0;gap:0;left:auto;right:0}
#pcbwrap>header{flex-wrap:wrap;row-gap:6px}
#layerbox,#partbox{margin-left:auto}
#layerbox+details{margin-left:0}
#layers .lbl{display:block;font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);margin:6px 0 4px}
#layers .row{display:flex;flex-wrap:wrap;gap:4px}
#layers label{display:inline-flex;align-items:center;gap:4px;font:.75rem var(--mono);background:var(--paper-2);border:1px solid var(--line);border-radius:999px;padding:6px 10px;min-height:24px;cursor:pointer}
#layers label.on{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink);font-weight:600}
#layers label.off{color:var(--ink-3);text-decoration:line-through}
#layers input{width:auto;margin:0;accent-color:var(--signal)}
#layersall{margin-top:8px;font-size:.78rem;padding:6px 10px;min-height:24px}
#partbar{display:flex;gap:6px;align-items:center;padding-bottom:6px;border-bottom:1px solid var(--line)}
#partfilter{width:100%;font-size:.82rem;padding:5px 8px}
#parthide,#partshow{font-size:.75rem;padding:6px 10px;min-height:24px}
#partlist{max-height:15rem;overflow:auto;margin-top:4px}
#partlist label{display:flex;align-items:center;gap:6px;font:.8rem var(--mono);padding:6px 4px;min-height:24px;border-radius:4px;cursor:pointer;white-space:nowrap}
#partlist label:hover{background:var(--paper-2)}
#partlist label.hidden{color:var(--ink-3);text-decoration:line-through}
#partlist input{width:auto;margin:0;accent-color:var(--signal)}
#partlist .pv{color:var(--ink-3);overflow:hidden;text-overflow:ellipsis}
#partnote{font-size:.72rem;color:var(--ink-3);margin-left:auto;white-space:nowrap}
.selpart{font-weight:600;color:var(--signal-ink)}
#cout,#dout{font:.82rem var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;min-width:8rem}
/* --- pills: state is a word, never a bare colour --- */
.pill{font-size:.75rem;font-weight:600;letter-spacing:.02em;padding:5px 10px;border-radius:999px;border:1px solid var(--line-2);background:var(--paper-2);color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.pill:empty{display:none}
.pill.ok{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink)}
.pill.bad{background:var(--bad-wash);border-color:var(--danger-border);color:var(--bad)}
.feasline{font-size:.72rem;font-weight:600;padding:3px 8px;border-radius:999px;border:1px solid var(--line-2);background:var(--paper-2);color:var(--ink-2);font-variant-numeric:tabular-nums}
.feasline.fok{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink)}
.feasline.fbad{background:var(--warn-wash);border-color:var(--warn-border);color:var(--warn)}
.feasline.here{outline:2px solid var(--signal);outline-offset:1px}
#stat:empty{display:none}
#stat{font-size:.85rem;font-weight:600;padding:5px 10px;border-radius:var(--r-control);max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#stat.ok{background:var(--signal-wash);color:var(--signal-ink);border:1px solid var(--ok-border)}
#stat.err{background:var(--bad-wash);color:var(--bad);border:1px solid var(--danger-border)}
/* --- grid of panel cards on the paper ground --- */
main{flex:1 1 0;height:0;display:grid;grid-template-columns:minmax(320px,400px) 1fr 1fr;grid-template-rows:minmax(0,1.5fr) minmax(0,1fr);gap:16px;padding:16px 20px 20px;min-height:0;overflow:auto}
section{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);display:flex;flex-direction:column;min-height:0;min-width:0;overflow:hidden}
.panel-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;background:var(--paper-2);border-bottom:1px solid var(--line);padding:8px 14px}
.panel-title{font:.78rem/1.4 var(--mono);font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--ink)}
.panel-note{font-size:.8rem;color:var(--ink-3)}
.panel-note:last-child{margin-left:auto;text-align:right}
/* job file left, tall; schematic + 3D/tidy right; PCB centre */
#edwrap{grid-column:1;grid-row:1/3;min-height:34rem}
#pcbwrap{grid-column:2;grid-row:1/3;position:relative}
#schwrap{grid-column:3;grid-row:1}
#wrap3d{grid-column:3;grid-row:2;overflow:auto}
#galwrap{grid-column:1/4}
#vcswrap{grid-column:1/4}
#kbwrap{grid-column:1/4;min-height:16rem}
#scanbar,#scanbar2{display:flex;gap:8px;align-items:center;padding:10px 14px 0;flex-wrap:wrap}
#scanbar2 input[type=search]{flex:1;min-width:0}
#scanstat{padding:8px 14px;font-size:13px}
#scanq{padding:0 14px}
.scanqa{display:flex;gap:8px;align-items:center;margin:6px 0;flex-wrap:wrap}
.scanqa label{flex:1;min-width:220px;font-size:13px}
.scanqa input{flex:1;min-width:0}
#scanout{max-height:340px;overflow:auto;white-space:pre-wrap;padding:0 14px}
#scanview{padding:0 14px 12px}
#scanviewbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:8px 0;font-size:13px}
.scancheck{display:flex;gap:4px;align-items:center}
#scanhover{margin-left:auto;color:var(--ink-2);min-height:1.2em}
.scanstage{position:relative;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;background:#000}
.scanstage img{display:block;width:100%;height:auto}
.scanstage svg{position:absolute;inset:0;width:100%;height:100%}
.scanstage .bx{fill:rgba(46,204,113,.10);stroke:#2ecc71;stroke-width:1.5}
.scanstage .bx.unc{fill:rgba(231,76,60,.14);stroke:#e74c3c}
.scanstage text{font:11px var(--mono);paint-order:stroke;stroke:#000;stroke-width:3px}
#scanparts{display:flex;flex-direction:column;gap:2px;margin-top:8px;max-height:220px;overflow:auto}
.scanprow{display:flex;gap:8px;align-items:center;font:.8rem var(--mono);padding:6px 4px;min-height:28px;border-radius:4px}
.scanprow.unc{background:rgba(231,76,60,.10)}
.scanprow input{width:9ch}
.scanprow select{max-width:12ch}
#kbbar,#kbadd{display:flex;gap:8px;align-items:center;padding:10px 14px 0}
#kbbar input,#kbadd input{flex:1;min-width:0}
#kbstat{padding:8px 14px 0;font:.8rem var(--mono);color:var(--ink-2);min-height:1.3em}
#kblist{flex:1;min-height:3rem;overflow:auto;padding:8px 14px}
#kblist .kbrow{display:flex;gap:8px;align-items:baseline;padding:6px 4px;min-height:28px;border-radius:4px}
#kblist .kbrow:hover{background:var(--paper-2)}
/* quote rows wear the fab logo next to the name */
#qout .qrow{display:flex;gap:8px;align-items:center;padding:2px 0}
#qout .qlogo{border-radius:4px;flex:none;width:64px;height:21px;object-fit:contain;background:#fff}
#kblist button.kbname{flex:1;min-width:0;background:none;border:0;padding:4px 0;min-height:24px;font:inherit;color:var(--signal-ink);
  cursor:pointer;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#kblist .kbkind{color:var(--ink-3);font:.72rem var(--mono);font-variant-numeric:tabular-nums}
#kblist .kbparts{margin-left:auto;color:var(--signal-ink);font:.72rem var(--mono)}
#kbview{margin:0;border-top:1px solid var(--line);padding:10px 14px;overflow:auto;max-height:44%;
  background:var(--card);font:.78rem/1.6 var(--mono);white-space:pre-wrap;word-break:break-word}
/* job file column: editor, project files, then the agent chat */
#edwrap{display:grid;grid-template-rows:auto minmax(12rem,min(52%,34rem)) minmax(0,1fr);overflow:hidden;min-height:0}
#srcpanels{display:grid;grid-template-rows:minmax(6rem,11rem) minmax(0,1fr);gap:12px;padding:12px;border-top:1px solid var(--line);min-height:0;overflow:hidden}
#srcpanels section{box-shadow:none;overflow:hidden}
#filetree{min-height:0;max-height:11rem}
.side{min-height:0}
#tree{flex:1;min-height:0;overflow:auto;padding:8px 10px;font:.85rem/1.5 var(--mono)}
#tree .trow{display:flex;gap:6px;align-items:baseline;width:100%;text-align:left;
font:inherit;color:inherit;background:none;border:0;padding:6px 8px;min-height:28px;
border-radius:4px;cursor:pointer;white-space:nowrap}
#tree .trow:hover{background:var(--paper-2)}
#tree .trow.active{background:var(--signal-wash);color:var(--signal-ink);font-weight:600}
#tree .trow.tdir{color:var(--ink-3);cursor:pointer}
#tree .trow:disabled,#tree .trow.tdir:disabled{cursor:default;opacity:1}
#tree .tsize{color:var(--ink-3);font-size:.72rem;margin-left:auto;font-variant-numeric:tabular-nums}
#msgs{overflow:auto;padding:10px 12px;flex:1;min-height:4rem}
.msg{margin:0 0 10px;font-size:.9rem;line-height:1.55}
.msg .who{font-size:.7rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);display:block}
.msg.me{color:var(--ink-2)}
.msg.bot{color:var(--ink)}
.msg.err{color:var(--bad);background:var(--bad-wash);border:1px solid var(--danger-border);border-radius:var(--r-control);padding:8px 10px}
.msg.bot pre{background:var(--paper-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px;overflow:auto;font:.8rem/1.6 var(--mono);margin:6px 0 0}
.msg .tools{color:var(--ink-3);font:.75rem var(--mono);display:block;margin-top:4px}
/* agent timeline: a turn reads as thought → tools → reply, not a bare log.
Flux shows its work; the log lines already exist, they just needed a shape. */
.thought{margin:0 0 4px;font-size:.8rem;color:var(--ink-3)}
.thought summary{padding:2px 0;border:0;background:none;font:.78rem var(--mono);cursor:pointer}
.thought summary:hover{background:none;text-decoration:underline}
.thought ul{margin:4px 0 6px;padding-left:1.1rem;font:.78rem/1.7 var(--mono)}
.thought li.ok{color:var(--ink-2)}
.thought li.bad{color:var(--bad)}
#followups{display:flex;gap:6px;flex-wrap:wrap;padding:0 12px 8px}
#followups:empty{display:none}
#followups button{font-size:.78rem;padding:4px 10px}
#composer{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--line);background:var(--paper-2)}
#composer textarea{flex:1;resize:none;font:.9rem/1.5 var(--sans);padding:9px 11px;border:1.5px solid var(--line-2);border-radius:var(--r-control);background:var(--card);color:var(--ink)}
#composer textarea:focus-visible{outline:2px solid var(--signal);outline-offset:1px}
#composer button{align-self:flex-end}
label.auto{display:inline-flex;align-items:center;gap:4px;font-size:.75rem;font-weight:600;color:var(--ink-2)}
label.auto input{width:auto;padding:0}
/* proposals: a diff you accept or refuse, never a silent write */
.prop{border:1px solid var(--line-2);border-radius:var(--r-control);background:var(--card);margin:8px 0 0;overflow:hidden}
.prop .phead{display:flex;align-items:center;gap:8px;background:var(--paper-2);border-bottom:1px solid var(--line);padding:6px 10px;font-size:.78rem}
.prop .phead b{font-family:var(--mono)}
.prop .phead .grow{flex:1}
.prop .prow{display:flex;gap:8px;padding:8px 10px;border-top:1px solid var(--line)}
.prop pre{margin:0;max-height:16rem;overflow:auto;padding:8px 10px;font:.78rem/1.55 var(--mono);background:var(--term);color:var(--term-text)}
.dl-add{color:var(--term-ok)}.dl-del{color:var(--term-bad)}.dl-at{color:var(--term-faint)}
#vcs{overflow:auto;padding:6px 12px 12px;font:.85rem/1.7 var(--mono);max-height:16rem}
#vcs .rev{display:flex;gap:10px;align-items:baseline;width:100%;text-align:left;
font:inherit;color:inherit;background:none;border:0;padding:6px 4px;min-height:28px;
cursor:pointer;white-space:nowrap}
#vcs .rev:hover{color:var(--signal-ink)}
#vcs .rh{color:var(--signal-ink);font-weight:600}
#vcs .rd{color:var(--ink-3)}
#vcs .rs{overflow:hidden;text-overflow:ellipsis}
#vcs pre{margin:8px 0 0;background:var(--term);color:var(--term-text);border-radius:6px;padding:10px 12px;overflow:auto;font:.78rem/1.6 var(--mono);max-height:22rem}
#vcs .dirty{color:var(--warn)}
/* the agent panel is opt-in: off by default, so the first thing the studio
   shows is the board, not a chat box */
#chat{display:none}  /* its 1fr row collapses with it: the tree grows */
body.chatty #chat{display:flex}
.toast{position:fixed;right:18px;bottom:18px;z-index:9;background:var(--term);color:var(--term-text);
  border:1px solid var(--term-line);border-radius:var(--r-control);padding:9px 13px;font:.85rem var(--mono);
  box-shadow:var(--shadow);max-width:40ch;overflow:hidden;text-overflow:ellipsis}
#gal{display:flex;gap:14px;overflow-x:auto;padding:14px}
#gal canvas{width:190px;height:140px;border:1px solid var(--line-2);border-radius:6px;background:var(--card);pointer-events:none}
#gal button.galpick{margin:0;padding:0;border:0;background:none;cursor:pointer;text-align:center;
font:inherit;color:inherit;font-size:.8rem;border-radius:6px}
#gal button.galpick:hover canvas{border-color:var(--signal)}
#gal button.galpick:focus-visible{outline:2px solid var(--signal);outline-offset:2px}
#gal .galcap{display:block;color:var(--ink-3);font-variant-numeric:tabular-nums;padding-top:4px}
#ed{overflow-y:auto;overflow-x:hidden;white-space:pre-wrap;word-break:break-all;padding:14px 16px;outline:none;flex:1;min-height:0;background:var(--term);color:var(--term-text);font:.82rem/1.7 var(--mono);tab-size:2}
#ed:focus-visible{outline:2px solid var(--signal);outline-offset:-2px}
#pcbwrap .platewrap{position:relative;flex:1;min-height:0;background:var(--paper-2);border-radius:0 0 var(--r) var(--r);overflow:hidden}
canvas{width:100%;height:100%;display:block}
#pcb{cursor:grab}
#wrap3d canvas{flex:1;min-height:0}
#tidy{padding:4px 14px 12px;font:.82rem/1.9 var(--mono);color:var(--ink-2);overflow:auto;max-height:32%;font-variant-numeric:tabular-nums}
#tidy div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* DRC reads as a listing: dark terminal strip over the PCB plate */
#drc{position:absolute;left:0;right:0;bottom:0;max-height:44%;overflow:auto;background:var(--term);color:var(--term-text);border-top:1px solid var(--term-line);padding:10px 14px;font:.82rem/1.75 var(--mono);font-variant-numeric:tabular-nums}
#drc:empty{display:none}
.err{color:var(--term-bad)}.warn{color:var(--term-key)}.ok{color:var(--term-ok)}
#tidy .dim,#drc .dim{color:var(--term-faint)}
#tidy .err{color:var(--bad)}#tidy .warn{color:var(--warn)}#tidy .ok{color:var(--signal-ink)}#tidy .dim{color:var(--ink-3)}
.tok-k{color:#7fb4ff}.tok-c{color:var(--term-faint)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media(max-width:1100px){main{grid-template-columns:minmax(0,1fr) 1fr;grid-template-rows:none}
#edwrap,#pcbwrap{grid-column:auto;grid-row:auto;min-height:70vh}
#galwrap,#schwrap,#wrap3d,#vcswrap{grid-column:1/3}}
@media(max-width:760px){main{grid-template-columns:minmax(0,1fr);padding:12px}
#galwrap,#schwrap,#wrap3d,#vcswrap{grid-column:1}#edwrap,#pcbwrap{min-height:60vh}
#srcpanels{grid-template-rows:minmax(0,1fr) minmax(0,1fr)}
.mastat,.mlive{margin-left:0}}
@media(min-width:1500px){#srcpanels{grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);grid-template-rows:minmax(0,1fr)}}
/* Flux-dark: the same tokens, re-pointed. One class, no second stylesheet. */
body.dark{--paper:#101418;--paper-2:#1a2129;--card:#161c22;--line:#2a333d;--line-2:#3a4550;
--ink:#d8e2dc;--ink-2:#aeb8c0;--ink-3:#7f8b94;
--signal:#5fd894;--signal-ink:#5fd894;--signal-wash:#123526;--ok-border:#2a5a3d;
--bad:#ff7364;--bad-wash:#3a1a16;--danger-border:#6a2a22;
--warn:#ffd8a0;--warn-wash:#3a2c14;--warn-border:#6a522a;}
body.dark #ed{background:#0a0d11}
body.dark #composer{background:var(--paper-2)}
/* view tabs: Flux's Docs/Schematic/Layout/3D row, our panels underneath */
#viewtabs{display:flex;gap:4px;margin-left:8px}
#viewtabs button{font-size:.8rem;padding:8px 12px;min-height:32px}
#viewtabs button.on{background:var(--signal);border-color:var(--signal-ink);color:#fff}
body.dark #viewtabs button.on{color:#06130d}
body.tabs #pcbwrap,body.tabs #schwrap,body.tabs #wrap3d,body.tabs #kbwrap{display:none}
body.tabs[data-view=pcb] #pcbwrap{display:flex}
body.tabs[data-view=sch] #schwrap{display:flex}
body.tabs[data-view=t3d] #wrap3d{display:flex}
body.tabs[data-view=docs] #kbwrap{display:flex}
body.tabs #pcbwrap,body.tabs #schwrap,body.tabs #wrap3d,body.tabs #kbwrap{grid-column:2/4;grid-row:1/3}
</style></head><body>
<a class=skip href=#ed>skip to the job file</a>
<header class=top><div class=inner>
<span class=brand><svg width=20 height=20 viewBox="0 0 20 20" aria-hidden=true focusable=false><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round></line></svg>OCD Studio <i>board &amp; PCB workshop</i></span>
<span id=room role=status aria-live=polite class=pill title="who else is on this board right now">solo</span>
<span id=me class=pill title="logged in as"></span>
<button id=logoutbtn title="log out of the studio">log out</button>
<button id=themebtn type=button aria-pressed=false title="toggle Flux-dark theme (paper ↔ dark)">dark</button>
<nav id=viewtabs role=tablist aria-label="views" title="pick a view, or All for every panel at once"><button data-v=all role=tab aria-selected=true tabindex=0 class=on title="every panel at once (the cockpit)">All</button><button data-v=pcb role=tab aria-selected=false tabindex=-1 title="PCB layout">Layout</button><button data-v=sch role=tab aria-selected=false tabindex=-1 title="schematic">Schematic</button><button data-v=t3d role=tab aria-selected=false tabindex=-1 title="3D preview">3D</button><button data-v=docs role=tab aria-selected=false tabindex=-1 title="notes and datasheets">Docs</button></nav>
/*__TOOLBAR__*/
</div></header>
<main>
/*__VIEWS__*/
</main>
<script>
const $=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
const j=await r.json();
if(j&&j.login){location.href='/';throw new Error('login');} // session died mid-work
return j;}
let S=null, anim=null;
const ease=t=>1-Math.pow(1-t,3);
function fit(cv){ // size canvas once per real resize; dpr capped (4x pixels buy nothing)
  const R=cv.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);
  const w=Math.max(1,Math.round(R.width*dpr)),h=Math.max(1,Math.round(R.height*dpr));
  if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return [ctx,R];
}
const TRACECOLS=['#8a2318','#1d5fa8','#0f5c37','#6b3fa0']; // net hues: red/blue/green/violet
// canvas palette: paper ground, ink marks, one signal green (matches the sheet)
// dark theme re-points these at paint time (readTheme), never at draw time.
const C={paper:'#f7f5f0',paper2:'#efece4',card:'#fffdf8',line:'#e2ddd0',line2:'#cfc8b6',ink:'#1a1d21',ink2:'#4d545c',ink3:'#5c646c',
  signal:'#0f5c37',wash:'#dcefe1',bad:'#8a2318',warn:'#6b4a00',copper:'#9a7134',
  pad:'#d9a821',padline:'#8a6d00'}; // pad copper: same pair the SVG/PNG renders use
const CDARK={paper:'#101418',paper2:'#1a2129',card:'#161c22',line:'#2a333d',line2:'#3a4550',ink:'#d8e2dc',ink2:'#aeb8c0',ink3:'#7f8b94',
  signal:'#5fd894',wash:'#123526',bad:'#ff7364',warn:'#ffd8a0',copper:'#c9962e',
  pad:'#d9a821',padline:'#8a6d00'};
const CLIGHT={...C};
function readTheme(){ // Flux-dark: one class on body re-points the palette
  const dark=document.body.classList.contains('dark');
  Object.assign(C,dark?CDARK:CLIGHT);
  return dark;}
const bgCol=C.paper; // PCB ground; getComputedStyle per frame forces a style flush
// copper pour: a spec-sheet hatch with the thermal gaps punched out of it, so
// the routing underneath still reads through the flood.
let pourCv=null;
function pourTile(px,py,w,h,cuts){
  if(!pourCv){pourCv=document.createElement('canvas');}
  if(pourCv.width!==px||pourCv.height!==py){pourCv.width=px;pourCv.height=py;}
  const g=pourCv.getContext('2d');
  g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,px,py);
  g.strokeStyle=C.copper;g.globalAlpha=0.5;g.lineWidth=1.3; // 45 deg hatch, screen scale
  for(let d=-py;d<px;d+=7){g.beginPath();g.moveTo(d,0);g.lineTo(d+py,py);g.stroke();}
  g.globalAlpha=1;
  g.globalCompositeOperation='destination-out'; // cutouts become holes, not slabs
  for(const r of cuts)g.fillRect(r[0],r[1],r[2]-r[0],r[3]-r[1]);
  g.globalCompositeOperation='source-over';
  return {cv:pourCv,ox:w,oy:h};
}
// --- visibility: what the canvas shows (a view, never a document edit) ---
// Off-list marks: ref, value, pads, pour, grid. Layers are keyed by the same
// index traces carry (0 = F.Cu, 1 = B.Cu, then In1.Cu...), so the toggles name
// exactly the layers the fab export does. Persisted per browser.
const MARKLABEL={ref:"designators",value:"values",pads:"pads",pour:"copper pour",grid:"grid"};
let VIS={layers:{},parts:{},marks:{},filter:""};
try{const s=JSON.parse(localStorage.getItem("ocd-studio-vis")||"{}");
  VIS.layers=s.layers||{};VIS.parts=s.parts||{};VIS.marks=s.marks||{};}catch(e){}
function visSave(){try{localStorage.setItem("ocd-studio-vis",JSON.stringify(
  {layers:VIS.layers,parts:VIS.parts,marks:VIS.marks}));}catch(e){}}
function layerNames(st){ // same stack the KiCad export writes
  const n=Math.max(1,+st.layers||2);
  if(n===1)return["F.Cu"];
  if(n===2)return["F.Cu","B.Cu"];
  return["F.Cu"].concat(Array.from({length:n-2},(_,i)=>`In${i+1}.Cu`),["B.Cu"]);
}
const SILKMARKS=["silk","mask"];
function visLayer(name,st){
  const v=VIS.layers[name];
  if(v===undefined){
    const s=st.silk||"full";
    if(name==="silk")return s!=="none"&&s!=="off";
    if(name==="mask")return s==="full";
    return true;
  }
  return !!v;
}
const isCu=n=>/\.Cu$/.test(n);
function visMark(k,st){ // explicit choice wins; otherwise silk/mask decide
  const v=VIS.marks[k];
  if(v!==undefined)return !!v;
  if(!st)return true;
  if(k==="pads")return visLayer("mask",st); // pads are the mask openings
  return visLayer("silk",st);               // ref/value/pour marks
}
function partShown(r,st){ // visibility + the parts filter
  if(VIS.parts[r]===false)return false;
  const f=VIS.filter.trim().toLowerCase();
  if(!f)return true;
  const p=(st&&st.parts&&st.parts[r])||{};
  return (r+" "+(p.value||"")+" "+(p.fp||"")).toLowerCase().includes(f);
}
function setAllParts(on,st){
  Object.keys((st&&st.parts)||{}).forEach(r=>{VIS.parts[r]=on;});
  visSave();renderParts();markDirty();
}
function drawPCB(st, t){ // t: 0..1 trace reveal + part blend handled by caller
  view.t=t;
  const [ctx,R]=fit($('pcb'));
  const s0=Math.min(R.width/st.bw,R.height/st.bh),s=Math.max(1,s0);
  const ox=(R.width-st.bw*s)/2,oy=(R.height-st.bh*s)/2;
  const X=x=>ox+x*s,Y=y=>oy+(st.bh-y)*s;
  ctx.fillStyle=bgCol;ctx.fillRect(0,0,R.width,R.height);
  const cuts=(st.cuts&&st.cuts['0'])||[];
  if(visMark('grid',st)){
    ctx.strokeStyle=C.line; // board grid sits under the copper, not on top of it
    for(let gx=0;gx<=st.bw;gx+=5){ctx.beginPath();ctx.moveTo(X(gx),Y(0));ctx.lineTo(X(gx),Y(st.bh));ctx.stroke();}
    for(let gy=0;gy<=st.bh;gy+=5){ctx.beginPath();ctx.moveTo(X(0),Y(gy));ctx.lineTo(X(st.bw),Y(gy));ctx.stroke();}
  }
  if(visMark('pour',st)&&st.pours&&Object.values(st.pours).some(lls=>lls.includes(0))){
    const e=st.edge||0.3;
    const px=Math.ceil((st.bw-2*e)*s),py=Math.ceil((st.bh-2*e)*s);
    const tile=pourTile(px,py,(st.bw-2*e)*s,(st.bh-2*e)*s,cuts);
    ctx.drawImage(tile.cv,0,0,px,py,X(e),Y(st.bh-e),tile.ox,tile.oy);
  }
  ctx.strokeStyle=C.line2;ctx.strokeRect(X(0),Y(st.bh),st.bw*s,st.bh*s);
  const cols=TRACECOLS;
  const names=layerNames(st);
  const n=Math.min(Math.ceil(st.traces.length*t),MAX_SEGS);
  // Label every part on a normal board; on a dense one the refs overlap into
  // noise, so draw a readable sample (every 6th) and always the selected ones.
  const labelEvery=st.compact?6:1;
  let labelSeq=0;
  // Batch traces: one path per (layer, width) instead of a stroke() per
  // segment. On a dense board this is the whole frame — 9.6k strokes -> a
  // handful — and it is the same pixels (same colour, same line width, each
  // segment still an independent pair of points).
  const buckets=new Map();
  for(let i=0;i<n;i++){const g=st.traces[i];
    const nm=names[g.layer];
    if(nm&&!visLayer(nm,st))continue; // layer hidden: not drawn, not batched
    const lw=Math.max(1,g.w*s);
    const key=g.layer+'|'+lw;
    let b=buckets.get(key);
    if(!b){b={color:cols[g.layer%4],lw,segs:[]};buckets.set(key,b);}
    b.segs.push(g);}
  for(const b of buckets.values()){
    ctx.strokeStyle=b.color;ctx.lineWidth=b.lw;ctx.beginPath();
    for(const g of b.segs){
      // a fresh moveTo per segment: segments stay separate (no spurious joins)
      ctx.moveTo(X(g.x1),Y(g.y1));ctx.lineTo(X(g.x2),Y(g.y2));
      ctx.moveTo(X(g.x2),Y(g.y2));
    }
    ctx.stroke();}
  for(const r in st.parts){
    if(!partShown(r,st))continue;
    const p=st.parts[r];
    ctx.fillStyle=st.fixed&&st.fixed[r]?C.wash:C.ink2; // pinned parts wear the signal wash
    ctx.fillRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    ctx.strokeStyle=st.fixed&&st.fixed[r]?C.signal:C.ink;
    ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    // the footprint itself, not its box: real pads + drills + pin-1, so an 0805
    // and a SOIC8 stop looking alike (same geometry the SVG/PNG renders use)
    if(visMark('pads',st))for(const pd of (p.pads||[])){
      const cx=X(p.x+pd.x),cy=Y(p.y+pd.y),pw=Math.max(1,pd.w*s),ph=Math.max(1,pd.h*s);
      ctx.fillStyle=C.pad;ctx.strokeStyle=C.padline;
      ctx.fillRect(cx-pw/2,cy-ph/2,pw,ph);ctx.strokeRect(cx-pw/2,cy-ph/2,pw,ph);
      if(pd.d>0){ctx.fillStyle=bgCol;ctx.beginPath();ctx.arc(cx,cy,Math.max(0.8,pd.d*s/2),0,7);ctx.fill();}
      if(pd.p1){ctx.fillStyle=C.ink3;ctx.beginPath();ctx.arc(cx,cy,Math.max(0.8,0.22*s),0,7);ctx.fill();}}
    // On a dense board every label overlaps its neighbours anyway (5,420 refs
    // in one canvas): draw a readable sample instead of 5,420 glyph runs.
    const drawRef=labelEvery===1||(labelSeq%labelEvery===0)||edHl.has(r);
    labelSeq++;
    const fs=Math.min(12,Math.max(7,p.h*s*0.32)); // never wider than the box
    if(drawRef){
      ctx.font=`${fs}px ui-monospace,Menlo,monospace`;ctx.textAlign='center';ctx.textBaseline='middle';
      if(visMark('ref',st)&&visLayer('silk',st)){
        ctx.fillStyle=st.fixed&&st.fixed[r]?C.signal:'#f7f5f0';
        const name=r.length*fs*0.62>p.w*s?r.slice(0,Math.max(1,Math.floor(p.w*s/(fs*0.62))))+'…':r;
        ctx.fillText(name,X(p.x),Y(p.y));
      }
      ctx.textBaseline='alphabetic';
      if(visMark('value',st)&&visLayer('silk',st)&&st.silk!=='ref'&&p.value&&p.h*s>18){
        ctx.fillStyle=C.ink3;ctx.font=`${Math.min(9,fs*0.8)}px ui-monospace,Menlo,monospace`;
        ctx.fillText(p.value,X(p.x),Y(p.y-p.h/2)+10);}
    }
    if(edHl.has(r)){ctx.strokeStyle=C.signal;ctx.lineWidth=3; // editor text selection → ring
      ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);ctx.lineWidth=1;}
    // collaborators: whoever has this part selected rings it in their color.
    // Several cursors can share one part — stack the name tags so N users
    // stay readable instead of overprinting.
    let stack=0;
    for(const u of collabUsers){if(u.ref!==r||u.name===collabMe)continue;
      const pad=3+stack*2;
      ctx.strokeStyle=u.color||'#1d5fa8';ctx.lineWidth=2;ctx.setLineDash([4,3]);
      ctx.strokeRect(X(p.x-p.w/2)-pad,Y(p.y+p.h/2)-pad,p.w*s+pad*2,p.h*s+pad*2);
      ctx.setLineDash([]);ctx.fillStyle=u.color||'#1d5fa8';
      ctx.font='10px ui-monospace,Menlo,monospace';ctx.textAlign='left';
      ctx.fillText(u.name,X(p.x-p.w/2)-pad,Y(p.y+p.h/2)-pad-3-stack*11);ctx.lineWidth=1;
      stack++;}}
  // instance groups (block stamping): shared dashed outline + tag, one hue per owner
  const groups={};
  for(const r in st.parts){if(!partShown(r,st))continue;
    const p=st.parts[r];if(!p.owner)continue;
    const g=groups[p.owner]||(groups[p.owner]=[1e9,1e9,-1e9,-1e9]);
    g[0]=Math.min(g[0],p.x-p.w/2);g[1]=Math.min(g[1],p.y-p.h/2);
    g[2]=Math.max(g[2],p.x+p.w/2);g[3]=Math.max(g[3],p.y+p.h/2);}
  const hues=Object.keys(groups);
  hues.forEach((o,i)=>{const g=groups[o],c=`hsl(${(i*137)%360},55%,35%)`;
    ctx.strokeStyle=c;ctx.setLineDash([5,3]);
    ctx.strokeRect(X(g[0]-1),Y(g[3]+1),(g[2]-g[0]+2)*s,(g[3]-g[1]+2)*s);
    ctx.setLineDash([]);ctx.fillStyle=c;ctx.font='10px ui-monospace,Menlo,monospace';ctx.textAlign='left';
    ctx.fillText(o.replace(/_$/,''),X(g[0]-1),Y(g[3]+1)-3);});
  return {s,ox,oy};
}
let view={s:1,ox:0,oy:0,t:1};
const MAX_SEGS=25000; // dense boards route 25k+: drawing all of them every frame is a slideshow
let schSel=null, schDirty=true; // selected "REF.PIN"
let edHl=new Set(), edPin=new Set(); // refs/pins named by the editor's text selection
function drawSCH(st){
  if(schDirty){ // static until nets/selection change — not 20fps
    const [ctx,R]=fit($('sch'));
    ctx.fillStyle=C.card||C.paper;ctx.fillRect(0,0,R.width,R.height);
    st._schmap={pins:[],nets:[]};
    const sch=st.sch||{order:[],px:{},rail_y:{},top:70,W:0},cols=TRACECOLS;
    const zw=sch.W||Math.max(...Object.values(sch.px),1)+60; // world px → fit
    const zx=Math.min(1,R.width/Math.max(1,zw)); // shrink-to-fit only, never upscale
    ctx.save();ctx.scale(zx,zx);
  sch.order.forEach(r=>{const hi=edHl.has(r); // editor selection lights the same box
    ctx.fillStyle=hi?C.wash:C.paper2;ctx.fillRect(sch.px[r]-50,sch.top-34,100,30);
    ctx.strokeStyle=hi?C.signal:C.ink;ctx.lineWidth=hi?2:1;
    ctx.strokeRect(sch.px[r]-50,sch.top-34,100,30);ctx.lineWidth=1;
    ctx.fillStyle=hi?C.signal:C.ink;ctx.textAlign='center';ctx.font='12px ui-monospace,Menlo,monospace';ctx.fillText(r,sch.px[r],sch.top-20);});
  Object.keys(st.nets).forEach((n,i)=>{const y=sch.rail_y[n];if(y===undefined)return;
    const xs=st.nets[n].map(pp=>sch.px[pp.split('.')[0]]).filter(x=>x!==undefined);
    if(!xs.length)return;
    ctx.strokeStyle=cols[i%4];ctx.lineWidth=2;ctx.beginPath();
    ctx.moveTo(Math.min(...xs),y);ctx.lineTo(Math.max(...xs),y);ctx.stroke();ctx.lineWidth=1;
    ctx.fillStyle=C.ink2;ctx.font='11px ui-monospace,Menlo,monospace';ctx.fillText(n,Math.min(...xs)-8,y+4);
    st._schmap.nets.push({n,x:((Math.min(...xs)+Math.max(...xs))/2)*zx,y:y*zx});
    st.nets[n].forEach(pp=>{const x=sch.px[pp.split('.')[0]];if(x===undefined)return;
      ctx.strokeStyle=cols[i%4];ctx.beginPath();ctx.moveTo(x,sch.top-4);ctx.lineTo(x,y);ctx.stroke();
      const sel=schSel===pp||edPin.has(pp);
      ctx.fillStyle=sel?C.ink:cols[i%4];ctx.beginPath();ctx.arc(x,y,4,0,7);ctx.fill();
      if(sel){ctx.strokeStyle=C.ink;ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,7);ctx.stroke();ctx.lineWidth=1;}
      st._schmap.pins.push({pp,net:n,x:x*zx,y:y*zx});});});
    ctx.restore();
    schDirty=false;
  }
}
// --- schematic edits → .ocd text (two-way binding) ---
function schLines(){return $('ed').innerText.split('\n');}
const schEsc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
// parse net lines: {name, idx, pins:[{tok, li}]} (li = line index)
function schNets(){
  const lines=schLines(),out=[];
  lines.forEach((l,li)=>{let m=l.match(/^(\S+?)((?:\s+[LWlw][\d.]+)*)\s*::\s*(.*)$/);
    if(!m){const m2=l.match(/^net\s+(\S+?)(?:\s+[LWlw][\d.]+)*\s*:\s*(.*)$/);if(m2)m=[m2[0],m2[1],"",m2[2]];}
    if(m)out.push({name:m[1],li,pins:m[3].split(/\s*<-->\s*|\s+/).filter(Boolean).filter(t=>t.includes('.'))});});
  return out;
}
function schCommit(lines){$('ed').innerText=lines.join('\n');push();}
function schMovePin(pp,dst){
  const [ref,pin]=pp.split('.'),lines=schLines(),nets=schNets();
  let changed=false;
  for(const n of nets){
    const i=n.pins.findIndex(t=>{const [r,p]=t.split('.');return r===ref&&p===pin;});
    if(i>=0&&n.name!==dst){n.pins.splice(i,1);changed=true;
      lines[n.li]=schSplitPins(lines[n.li],n.pins);}
  }
  for(const n of nets)if(n.name===dst){n.pins.push(`${ref}.${pin}`);
    lines[n.li]=schSplitPins(lines[n.li],n.pins);changed=true;}
  if(changed)schCommit(lines);
}
function schSplitPins(line,pins){ // rebuild one net line: join with space-<-->, never ':'
  const i=line.indexOf('::')>=0?line.indexOf('::'):line.indexOf(':');
  const head=line.slice(0,i+ (line[i+1]===':'?2:1) ).replace(/\s+$/,'');
  return pins.length?head+' '+pins.join(' <--> '):head;
}
function schDropPin(pp){
  const [ref,pin]=pp.split('.'),lines=schLines(),nets=schNets();
  for(const n of nets){
    const i=n.pins.findIndex(t=>{const [r,p]=t.split('.');return r===ref&&p===pin;});
    if(i>=0){lines[n.li]=schSplitPins(lines[n.li],n.pins.filter((_,j)=>j!==i));
      schCommit(lines);return;}
  }
}
function schRename(net){
  const to=prompt(`rename net ${net} to:`,net);
  if(!to||to===net||!/^\w+$/.test(to))return;
  const esc=schEsc(net);
  const lines=schLines().map(l=>{
    if(/^(net\s+|route\s+|trace\s+|power\s+|match\s+)/.test(l))
      return l.replace(new RegExp(`^((?:net|route|trace|power|match)\\s+)${esc}\\b`,'$1'+to));
    if(/^\S+(\s+[LWlw][\d.]+)*\s*::/.test(l))
      return l.replace(new RegExp(`^${esc}(?=\\s|::)`),to);
    return l;});
  // join lists: word-boundary replace on those lines only
  for(let i=0;i<lines.length;i++){
    if(/\bjoin\b/.test(lines[i]))
      lines[i]=lines[i].replace(new RegExp(`\\b${esc}\\b`, 'g'),to);
  }
  schCommit(lines);
}
(()=>{const c=$('sch');
c.addEventListener('mousedown',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const p of S._schmap.pins){
    if(Math.abs(mx-p.x)<7&&Math.abs(my-p.y)<7){
      if(e.altKey){schDropPin(p.pp);schSel=null;schDirty=true;return;}
      schSel=(schSel===p.pp)?null:p.pp;schDirty=true;return;}}
  for(const n of S._schmap.nets){
    if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){
      if(schSel){schMovePin(schSel,n.n);schSel=null;}schDirty=true;return;}}
  schSel=null;schDirty=true;});
c.addEventListener('dblclick',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const n of S._schmap.nets)if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){schRename(n.n);return;}});
})();
function draw3D(st,rot){
  const [ctx,R]=fit($('t3d'));
  ctx.fillStyle=C.card||C.paper;ctx.fillRect(0,0,R.width,R.height);
  const cx=R.width/2,cy=R.height/2+20,s=Math.min(R.width/(st.bw+20),R.height/(st.bh+14));
  const P=(x,y,z)=>{const a=rot,dx=x-st.bw/2,dy=y-st.bh/2;
    const rx=dx*Math.cos(a)-dy*Math.sin(a),ry=(dx*Math.sin(a)+dy*Math.cos(a))*0.5-z*0.9;
    return [cx+rx*s,cy+ry*s];};
  const faces=[];
  // lambert-ish: top faces full color, sides shaded by facing
  function shade(hex,k){const n=parseInt(hex.slice(1),16);
    const r=Math.min(255,((n>>16)&255)*k)|0,g=Math.min(255,((n>>8)&255)*k)|0,b=Math.min(255,(n&255)*k)|0;
    return `rgb(${r},${g},${b})`;}
  function box(x0,y0,z0,x1,y1,z1,cols){const c000=P(x0,y0,z0),c100=P(x1,y0,z0),c110=P(x1,y1,z0),c010=P(x0,y1,z0),c001=P(x0,y0,z1),c101=P(x1,y0,z1),c111=P(x1,y1,z1),c011=P(x0,y1,z1);
    // cols: {top, front, side} — top brightest (tmog: brightest = live data)
    faces.push({z:z1,p:[c001,c101,c111,c011],c:cols.top});
    faces.push({z:z0,p:[c000,c100,c110,c010],c:shade(cols.top,0.35)});
    faces.push({z:(z0+z1)/2,p:[c000,c100,c101,c001],c:cols.front});
    faces.push({z:(z0+z1)/2,p:[c100,c110,c111,c101],c:cols.side});
    faces.push({z:(z0+z1)/2,p:[c110,c010,c011,c111],c:shade(cols.front,0.8)});
    faces.push({z:(z0+z1)/2,p:[c010,c000,c001,c011],c:shade(cols.side,0.8)});}
  function cyl(cxx,cyy,rad,z0,z1,cols){ // round bodies (electrolytics, headers)
    const N=12,top=[],bot=[];
    for(let i=0;i<N;i++){const a=i/N*2*Math.PI;
      top.push(P(cxx+rad*Math.cos(a),cyy+rad*Math.sin(a),z1));
      bot.push(P(cxx+rad*Math.cos(a),cyy+rad*Math.sin(a),z0));}
    for(let i=0;i<N;i++){const j=(i+1)%N;
      faces.push({z:(z0+z1)/2,p:[bot[i],bot[j],top[j],top[i]],c:cols.side});}
    faces.push({z:z1,p:top,c:cols.top});
    faces.push({z:z0,p:bot,c:shade(cols.top,0.35)});}
  const MASK={top:'#0f5c37',front:'#0a3d25',side:'#0c4a2d'};
  box(0,0,0,st.bw,st.bh,1.6,MASK);
  // copper traces on top layer shimmer gold (hidden with that layer)
  if(visLayer(layerNames(st)[0],st))
    for(const g of st.traces.slice(0,400)){if(g.layer!==0)continue;
      const w=Math.max(0.15,g.w/2);
      faces.push({z:1.75,p:[P(g.x1-w,g.y1-w,1.7),P(g.x2+w,g.y1-w,1.7),P(g.x2+w,g.y2+w,1.7),P(g.x1-w,g.y2+w,1.7)],c:'#c9962e'});}
  const MATS={chip:{top:'#3a3f45',front:'#22262b',side:'#2c3136'},tant:{top:'#d9a419',front:'#8a6a0a',side:'#b8890f'},
    elec:{top:'#9aa3b5',front:'#5a6270',side:'#767f92'},led:{top:'#c0392b',front:'#7a1a12',side:'#96261a'},
    steel:{top:'#c8ccd2',front:'#7a7e85',side:'#9ea3ab'},plastic:{top:'#2b2f34',front:'#16191d',side:'#212528'},
    copper:{top:'#d9a832',front:'#8a6a1a',side:'#b8891f'}};
  for(const r in st.parts){
    if(!partShown(r,st))continue; // the 3D view hides what the PCB view hides
    const p=st.parts[r];
    const cols=MATS[p.mat]||MATS.chip;
    for(const bd of (p.bodies||[{w:p.w-0.6,h:p.h-0.6,z:1.6,hgt:p.h3d||1,dx:0,dy:0}])){
      if(bd.cyl){cyl(p.x+bd.dx,p.y+bd.dy,bd.w/2,bd.z,bd.z+bd.hgt,cols);continue;}
      box(p.x+bd.dx-bd.w/2,p.y+bd.dy-bd.h/2,bd.z,p.x+bd.dx+bd.w/2,p.y+bd.dy+bd.h/2,bd.z+bd.hgt,cols);}}
  faces.sort((a,b)=>a.z-b.z);
  for(const f of faces){ctx.fillStyle=f.c;ctx.beginPath();ctx.moveTo(f.p[0][0],f.p[0][1]);for(let i=1;i<f.p.length;i++)ctx.lineTo(f.p[i][0],f.p[i][1]);ctx.closePath();ctx.fill();ctx.strokeStyle='rgba(0,0,0,.28)';ctx.stroke();}
}
function renderAll(){if(!S||!S.cur)return;readTheme();view=drawPCB(S.cur,1);drawSCH(S);if(spinOn){rot+=0.003;draw3D(S.cur,rot);dirty=true;}}
let rot=0.6,spinOn=true,spinT=null,dirty=true; // render-on-demand: static board costs zero frames
function loop(){if(dirty){dirty=false;renderAll();}requestAnimationFrame(loop);}
requestAnimationFrame(loop);
function markDirty(){dirty=true;schDirty=true;}
function spinBriefly(ms=4000){spinOn=true;dirty=true;clearTimeout(spinT);spinT=setTimeout(()=>spinOn=false,ms);}
$('t3d').addEventListener('pointerdown',()=>spinBriefly(8000));
// animation: tween parts from->to, reveal traces
function animate(frames,traces,done){
  cancelAnimationFrame(anim);
  const from=JSON.parse(JSON.stringify(S.cur.parts));let i=0;
  function step(){
    if(i>=frames.length){S.cur.traces=[];let n=0;dirty=true;
      (function grow(){n+=3;S.cur.traces=traces.slice(0,n);dirty=true;if(n<traces.length)anim=requestAnimationFrame(grow);else{S.cur.traces=traces;done&&done();}})();return;}
    const f=frames[i],to={};
    for(const r in f.pos){  // hidden parts snap, they do not glide
      const p=S.cur.parts[r];
      if(!p||!partShown(r,S.cur)){if(p){p.x=f.pos[r][0];p.y=f.pos[r][1];}}
      else to[r]=f.pos[r];
    }
    let k=0;const N=14;
    (function tw(){k++;const e=ease(k/N);
      for(const r in to){const a=from[r]||to[r],b=to[r];
        S.cur.parts[r]={...S.cur.parts[r],x:a[0]+(b[0]-a[0])*e,y:a[1]+(b[1]-a[1])*e};}
      dirty=true;
      $('cost').textContent=`cost ${f.cost}`;
      if(k<N)anim=requestAnimationFrame(tw);else{for(const r in to)S.cur.parts[r]={...S.cur.parts[r],x:to[r][0],y:to[r][1]};i++;step();}})();}
  step();
}
function statMsg(txt,ok){const el=$('stat');el.textContent=txt||'';el.title=txt||'';el.className=!txt?'':ok?'ok':'err';}
async function withBusy(btn,label,fn){ // long actions: disable + say what is happening
  if(!btn||btn.disabled)return;
  const was=btn.textContent;btn.disabled=true;
  if(label){btn.textContent=label;statMsg(label,true);}
  try{return await fn();}
  finally{btn.disabled=false;btn.textContent=was;}}
let deb=null, pulseq=0; // monotonic: a slow build must not land on a newer board
function cancelPush(){clearTimeout(deb);deb=null;pulseq++;} // switching boards
$('ed').addEventListener('input',()=>{clearTimeout(deb);deb=setTimeout(push,400);});
async function push(){
  const text=$('ed').innerText, seq=++pulseq;
  const r=await api('/collab/push',{text,rev:collabRev,src:SRCREL,thash:heldThash,
    placer:$('placer').value,router:$('router').value,
    fab:$('fab').value,silk:$('silk').value});
  if(seq!==pulseq)return;   // the editor moved on (or another board opened)
  if(r.stale){ // someone else edited first: adopt their text (undo keeps ours)
    cancelPush();
    if(r.text!==undefined)setEditor(r.text);
    if(r.rev!==undefined)collabRev=+r.rev;
    statMsg(r.error||'reloaded a collaborator edit (yours is in undo)',true);
    push();return;
  }
  if(r.error){statMsg(r.error);S=null;return;}
  statMsg('');applyState(r,false);
}

let heldThash='', heldTraces=[];
let collabRev=-1, collabOn=false, collabUsers=[], collabTimer=null, collabMe='';
function applyState(r,live){
  if(r.thash){ // server skipped the trace list: keep the one we already have
    if(r.thash!==heldThash){heldTraces=r.traces||[];}
    r.traces=(r.traces&&r.traces.length)?r.traces:heldTraces;
    heldThash=r.thash;
  }
  if(r.rev!==undefined&&r.rev!==null)collabRev=+r.rev; // the room's rev rides every build
  S=r;S.cur=r;markDirty();spinBriefly(); // render live on the state itself (bw/bh/pours/fixed ride along)
  notePlacement(r);
  if(live&&r.frames&&r.frames.length)animate(r.frames,r.traces,()=>{drawDRC(r);});
  else{S.cur.traces=r.traces;$('cost').textContent=`cost ${r.cost}`;drawDRC(r);}
  drawFeas(r);renderLayers(r);renderParts(r);
  if(document.activeElement!==$('ed'))setEditor(r.text);
}
// --- realtime collab: one SSE stream per board, rev-guarded pushes --------
// Same banner pattern as the file-watch: a rev mismatch means someone else
// edited first, so reload their text (never auto-merge, never clobber).
function collabPaint(users){
  collabUsers=users||[];
  const el=$('room');if(!el)return;
  const others=collabUsers.filter(u=>u.name!==collabMe);
  // the pill never grows past three names no matter the room size —
  // the full roster lives in the tooltip.
  const head=collabUsers.slice(0,3).map(u=>u.name).join(', ')
    +(collabUsers.length>3?` +${collabUsers.length-3}`:'');
  el.textContent=others.length?`${others.length+1} here: ${head}`
    :((collabUsers.length?'solo · '+head:'solo'));
  el.className='pill'+(others.length?' ok':'');
  el.title=collabUsers.map(u=>`${u.name}${u.ref?' on '+u.ref:''}`).join('\n')||'no one else here yet';
  const note=$('roomnote');
  if(note)note.textContent=others.length?`live now: ${head}`:'just you here — copy the link to co-edit';
  markDirty();
}
let collabSyncSeq=0; // monotonic: a slow sync must not land on a newer room
async function collabSync(){ // pull the room's text (first connect + on rev bump)
  const seq=++collabSyncSeq;
  const r=await api('/collab/sync',{});
  if(seq!==collabSyncSeq)return; // a newer sync is already in flight
  if(r.error||r.text===undefined)return;
  collabMe=r.hello||collabMe;
  collabPaint(r.users);
  if(r.rev!==undefined)collabRev=+r.rev;
  if(r.text!==$('ed').innerText&&document.activeElement!==$('ed')){
    cancelPush();setEditor(r.text);push(); // parse + render their text
  }
}
let collabES=null; // one stream per board: rehomed on openFile (old room dies)
function collabStart(){
  if(collabOn)return;collabOn=true;
  collabSync();
  if(collabES){try{collabES.close();}catch(err){}}
  const es=collabES=new EventSource('/collab/events');
  es.onmessage=e=>{
    let m;try{m=JSON.parse(e.data);}catch(err){return;}
    if(m.hello!==undefined)collabMe=m.hello;
    if(m.users)collabPaint(m.users);
    if(m.rev!==undefined&&+m.rev!==collabRev&&(m.by||'')!==collabMe
       &&(m.by!==undefined||m.rev>collabRev)){ // somebody's push landed
      collabRev=+m.rev;collabSync();
    }
  };
  es.onerror=()=>{ // the stream drops (sleep, proxy): re-sync, the next rev heals
    if(collabES!==es)return; // rehomed already — this stream is dead, stay dead
    try{es.close();}catch(err){}
    collabES=null;collabOn=false;setTimeout(collabStart,3000);
  };
  // presence: cursor + selected ref, every 5s (the server prunes at 15s)
  clearInterval(collabTimer);
  collabTimer=setInterval(async()=>{
    try{
      const r=await api('/collab/cursor',{x:view.t||0,y:0,
        ref:((S&&S.cur&&S.cur.hover)||(edHl.size?[...edHl][0]:''))});
      if(r.users)collabPaint(r.users);
      if(r.rev!==undefined)collabRev=+r.rev;
    }catch(err){}
  },5000);
  const sh=$('sharebtn');
  if(sh)sh.onclick=async()=>{
    try{await navigator.clipboard.writeText(location.href);
      toast('link copied — send it to your collaborator');}
    catch(err){toast('copy failed — select the URL from the address bar');}
  };
}
function drawFeas(r){
  const f=r.feasible||{},el=$('feas');if(!el)return;
  const ks=Object.keys(f).sort();
  if(!ks.length)return;
  // state is a word: the current layer count is marked, every count reads ok/unroutable
  el.innerHTML='routing feasibility per layer count: '+ks.map(L=>{
    const v=f[L],here=+L===r.layers;
    return `<span class="feasline ${v.ok?'fok':'fbad'}${here?' here':''}" title="${v.segs} segments, ${v.wirelength}mm of wire at ${L} layer${L==='1'?'':'s'}">${L}L ${v.ok?'routable':'unroutable'}${here?' (this board)':''}</span>`;}).join(' ');
}
// --- layer and part visibility controls (view state, never a board edit) --
function renderLayers(st){
  const cu=$('cu'),marks=$('marks');
  if(!cu)return;
  st=st||(S&&S.cur)||{layers:2,silk:'full'};
  cu.innerHTML='';marks.innerHTML='';
  layerNames(st).forEach(nm=>{
    const on=visLayer(nm,st);
    const id='lay_'+nm.replace(/[^\w]/g,'_');
    cu.appendChild(layerRow(nm,nm,on,id));
  });
  SILKMARKS.forEach(nm=>{
    const on=visLayer(nm,st);
    marks.appendChild(layerRow(nm==='silk'?'silkscreen':'mask',nm,on,'mark_'+nm));
  });
  Object.keys(MARKLABEL).forEach(k=>{ // ref/value ride the silkscreen toggle
    if(k==='ref'||k==='value')return;
    marks.appendChild(layerRow(MARKLABEL[k],k,visMark(k,st),'mark_'+k));
  });
}
function layerRow(label,key,on,id){
  const l=document.createElement('label');
  l.className=on?'on':'off';
  const i=document.createElement('input');
  i.type='checkbox';i.checked=on;i.id=id;
  i.onchange=()=>{
    if(isCu(key))VIS.layers[key]=i.checked; else if(SILKMARKS.includes(key))VIS.layers[key]=i.checked;
    else VIS.marks[key]=i.checked;
    visSave();
    const st=(S&&S.cur)||null;
    renderLayers(st);renderParts(st);markDirty();
  };
  l.append(i,document.createTextNode(label));
  l.title=isCu(key)?`copper layer ${key}`:`${label} on the PCB canvas`;
  return l;
}
const MAX_ROWS=400; // DOM rows, not parts: 5400 checkboxes brick the page
function renderParts(st){
  const box=$('partlist');
  if(!box)return;
  st=st||(S&&S.cur)||{parts:{}};
  const all=Object.keys(st.parts||{}).sort();
  // a filter searches every part (the list is capped, the search is not)
  const refs=(VIS.filter?all.filter(r=>partShown(r,st)):all).slice(0,MAX_ROWS);
  if(!VIS.filter&&all.length>MAX_ROWS){
    const shown=new Set(refs);
    edHl.forEach(r=>{if(!shown.has(r))refs.push(r);}); // keep the selection reachable
    refs.sort();
  }
  const same=box.children.length===refs.length
    && refs.every((r,i)=>box.children[i]&&box.children[i].dataset.ref===r);
  if(!same)box.innerHTML=''; // first draw, or a different board
  refs.forEach((r,i)=>{
    let l=box.children[i];
    if(!l||l.dataset.ref!==r){
      l=document.createElement('label');
      l.dataset.ref=r;
      const cb=document.createElement('input');
      cb.type='checkbox';
      cb.onchange=()=>{VIS.parts[r]=cb.checked;visSave();paintParts();markDirty();};
      const nm=document.createElement('span');nm.textContent=r;
      const v=document.createElement('span');v.className='pv';
      l.append(cb,nm,v);
      if(box.children[i])box.replaceChild(l,box.children[i]);
      else box.appendChild(l);
    }
    const p=st.parts[r]||{};
    const v=l.querySelector('.pv');
    v.textContent=[p.value||'',p.fp||''].filter(Boolean).join(' '); // filterable
    v.title=v.textContent;
  });
  paintParts();
}
function paintParts(){ // the rows are the source of truth for the note
  const box=$('partlist');
  if(!box)return;
  const total=Object.keys((S&&S.cur&&S.cur.parts)||{}).length;
  const refs=Array.from(box.children).map(l=>l.dataset.ref).filter(Boolean);
  let hidden=0;
  refs.forEach((r,i)=>{
    const l=box.children[i];
    if(!l)return;
    const on=VIS.parts[r]!==false;
    if(!on)hidden++;
    l.dataset.hidden=on?'':'1';
    l.className=(on?'':'hidden ')+(edHl.has(r)?'selpart':'');
    l.querySelector('input').checked=on;
    // the filter greys the rest, it does not detach the rows (no stale nodes)
    l.style.opacity='1'; // a filter selects rows; it cannot dim rows that are not here
  });
  $('partnote').textContent=!total?'no parts'
    :(total>refs.length?`${refs.length-hidden}/${refs.length} of ${total} (capped)`
                       :`${refs.length-hidden}/${refs.length} shown`);
}
$('partfilter').addEventListener('input',()=>{VIS.filter=$('partfilter').value;renderParts();markDirty();});
$('parthide').onclick=()=>setAllParts(false,S&&S.cur);
$('partshow').onclick=()=>setAllParts(true,S&&S.cur);
$('layersall').onclick=()=>{
  VIS.layers={};VIS.marks={};visSave();
  const st=(S&&S.cur)||null;renderLayers(st);markDirty();
};
function onlyBox(open){ // the two panels overlap: never show both
  [['layerbox','partbox'],['partbox','layerbox']].forEach(([a,b])=>{
    if($(a)===open&&$(a).open)$(b).open=false;
  });
}
$('layerbox').addEventListener('toggle',()=>onlyBox($('layerbox')));
$('partbox').addEventListener('toggle',()=>onlyBox($('partbox')));
document.addEventListener('keydown',e=>{
  if(e.target===$('ed')||e.target===$('ask'))return; // typing, not a shortcut
  if(e.key==='l'||e.key==='L'){$('layerbox').open=!$('layerbox').open;}
  else if(e.key==='p'||e.key==='P'){$('partbox').open=!$('partbox').open;}
});
// --- menubar: one menu open at a time, Esc closes, Alt+letter jumps -----
// Native <details> for the popups (no library, same as calc/health/quote
// before them): JS only enforces exclusivity + mnemonics. Shortcuts fire
// the same handlers the buttons always had — ids unchanged.
document.querySelectorAll('.menubar>details.menu').forEach(d=>{
  d.addEventListener('toggle',()=>{
    if(!d.open)return;
    document.querySelectorAll('.menubar>details.menu').forEach(o=>{if(o!==d)o.open=false;});
  });
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){document.querySelectorAll('.menubar>details.menu').forEach(d=>{d.open=false;});return;}
  if(!e.altKey||e.ctrlKey||e.metaKey)return;
  if(e.target===$('ed')||e.target===$('ask'))return;
  const k=e.key.toLowerCase();
  const map={b:'m-board',e:'m-edit',g:'m-engines',s:'m-sim',t:'m-tools'};
  if(map[k]){e.preventDefault();const d=$(map[k]);d.open=!d.open;}
});
// --- candidate gallery: N layouts, pick → nudge (drag=fix) → re-run ---
let galSeed=0;
function thumb(cand,i){
  const fig=document.createElement('button');fig.type='button';fig.className='galpick';
  fig.setAttribute('aria-label',`adopt candidate ${i}, cost ${cand.cost}`);
  const cv=document.createElement('canvas');cv.width=300;cv.height=220;fig.appendChild(cv);
  const cap=document.createElement('span');cap.className='galcap';cap.textContent=`#${i} cost ${cand.cost}`;fig.appendChild(cap);
  fig.onclick=()=>pickCand(i);
  const ctx=cv.getContext('2d'),W=300,H=220,s=Math.min(W/S.bw,H/S.bh),ox=(W-S.bw*s)/2,oy=(H-S.bh*s)/2;
  ctx.fillStyle=C.paper;ctx.fillRect(0,0,W,H);
  ctx.strokeStyle=C.line2;ctx.strokeRect(ox,oy+S.bh*s,S.bw*s,-S.bh*s);
  for(const r in cand.pos){const p=S.parts[r];if(!p)continue;
    const [x,y]=cand.pos[r];
    const fixed=S.fixed&&S.fixed[r];
    ctx.fillStyle=fixed?C.wash:C.ink2;ctx.fillRect(ox+(x-p.w/2)*s,oy+(S.bh-y-p.h/2)*s,p.w*s,p.h*s);
    ctx.strokeStyle=fixed?C.signal:C.ink;ctx.strokeRect(ox+(x-p.w/2)*s,oy+(S.bh-y-p.h/2)*s,p.w*s,p.h*s);}
  return fig;
}
async function genCands(){
  if(!S)return;
  const n=Math.max(1,Math.min(8,parseInt($('ncand').value||'4',10)));
  galSeed=(galSeed+1)%1000;
  await withBusy($('dice'),`generating ${n}…`,async()=>{
    const r=await api('/candidates',{placer:$('placer').value,n,seed:galSeed,iters:400});
    if(r.error){statMsg(r.error);return;}
    galMeta={n,seed:galSeed};
    const g=$('gal');g.innerHTML='';r.candidates.forEach((c,i)=>g.appendChild(thumb(c,i)));
    $('galwrap').style.display='';
    drawFeas({feasible:r.feasible,layers:r.layers});
    statMsg(`${n} candidates — click one to pick`,true);
  });
}
let galMeta={n:4,seed:0};
async function pickCand(i){
  await withBusy($('dice'),`picking #${i}…`,async()=>{
    const r=await api('/pick',{placer:$('placer').value,router:$('router').value,
      index:i,n:galMeta.n,seed:galMeta.seed,iters:400,silk:$('silk').value});
    if(r.error){statMsg(r.error);return;}
    statMsg('');$('galwrap').style.display='none';applyState(r,true);
  });
}
function drawDRC(r){
  const d=$('drc');let h='';
  const li=r.lint||{errors:[],warnings:[]}; // static source lint, no place/route
  if(li.errors.length)h+=li.errors.map(e=>`<div class=err>✗ lint: ${e}</div>`).join('');
  if(r.errors.length)h+=r.errors.map(e=>`<div class=err>✗ ${e}</div>`).join('');
  else if(!li.errors.length)h+='<div class=ok>✓ DRC clean ('+r.fab+')</div>';
  h+=r.warnings.slice(0,5).map(w=>`<div class=warn>~ ${w}</div>`).join('');
  h+=(li.warnings||[]).slice(0,3).map(w=>`<div class=warn>~ lint: ${w}</div>`).join('');
  const rec=(r.recommend&&r.recommend.items)||[];
  h+=rec.slice(0,6).map(it=>`<div class=warn>+ ${it.kind}: ${it.msg}</div>`).join('');
  if(r.sim&&Object.keys(r.sim).length)h+='<div class=ok>⚡ '+Object.entries(r.sim).map(([n,v])=>`${n}=${v}V`).join(' ')+'</div>';
  if(r.sim_problems&&r.sim_problems.length)h+=r.sim_problems.map(p=>`<div class=err>⚡✗ ${p}</div>`).join('');
  if(r.tran&&Object.keys(r.tran).length)h+='<div class=ok>⚡tran '+Object.entries(r.tran).map(([n,w])=>`${n} ${w[w.length-1].toFixed(2)}V [${Math.min(...w).toFixed(2)},${Math.max(...w).toFixed(2)}] (${w.length}pts)`).join(' · ')+'</div>';
  d.innerHTML=h;
  drawTidy(r);
}
function tidyVal(v){
  if(v===null||v===undefined)return '<span class=dim>n/a</span>';
  if(typeof v==='number')return Number.isInteger(v)?String(v):v.toFixed(3);
  if(typeof v==='object'){const ks=Object.keys(v);
    if(v.total!==undefined&&v.per_net!==undefined)return `total=${v.total}`; // nested detail lives in STATUS.md
    return ks.map(a=>`${a}=${v[a]}`).join(', ');}
  return String(v);
}
function drawTidy(r){
  const t=r.tidy||{};
  if(r.dense){ // metrics and the routability probe are skipped at this size
    $('tidycov').textContent='';
    $('tidy').innerHTML='<div class=dim>tidy metrics skipped on a dense board '
      +`(${Object.keys(r.parts||{}).length} parts) — run the CLI for the full report</div>`;
    $('ocdscore').textContent='OCD n/a (dense)';
    return;
  }
  $('tidycov').textContent=t.coverage?`(${t.coverage})`:'';
  const rows=Object.entries(t).filter(([k])=>k!=='coverage'&&k!=='routed_segs')
    .map(([k,v])=>`<div><span class=dim>${k}</span> ${tidyVal(v)}</div>`).join('');
  $('tidy').innerHTML=rows;
  const sc=r.score; // OCD neatness 0-100 next to cost
  if(sc&&!sc.dense)$('ocdscore').textContent=`OCD ${sc.total}/100 (${sc.grade})`;
}
// A dense board says so: it is loaded from the file's own positions, its
// metrics are skipped, and its parts list is capped. Never let the page look
// broken when the board is simply large.
function notePlacement(r){
  const n=Object.keys(r.parts||{}).length;
  const skip=(r.skipped||[]).join(' + ');
  // A dense board usually carries no layout in its file (only pinned parts
  // have positions), so say what it needs and where to get it rather than
  // letting the canvas look broken. `solve` works but is minutes at this size.
  if(r.dense)statMsg(`${n} parts, loaded as saved`
    +(skip?` — ${skip} skipped at this size (run the CLI for those)`:'')
    +'. "solve" places it here, but takes minutes.',true);
  else if(r.placed===false)statMsg('loaded as saved — solve to re-place',true);
}
function setEditor(t){$('ed').innerText=t;}
// import: file picker in the project panel → base64 to /fs/import →
// import_fp/import_sym by extension (key sniffed server-side). Same
// FileReader pattern as the x-ray upload.
if($('importfile'))$('importfile').onchange=()=>{const f=$('importfile').files[0];if(!f)return;
  const rd=new FileReader();rd.onload=async()=>{
    const data=String(rd.result).split(',',1)[1]||'';
    $('importstat').textContent='importing '+f.name+'…';
    const r=await api('/fs/import',{name:f.name,data});
    $('importstat').textContent=r.error||r.note||('imported '+f.name);
    if(!r.error){loadTree(DIR);if(r.text){setEditor(r.text);push();}}};
  rd.readAsDataURL(f);$('importfile').value='';};
function unpinRefs(gone){ // drop fix lines for refs; true when something left
  const lines=$('ed').innerText.split('\n')
    .filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  if(lines.length===$('ed').innerText.split('\n').length)return false;
  $('ed').innerText=lines.join('\n');push();return true;}
function rotRefs(refs){ // rotate 90°: bump rot= on the part line (add or +90)
  const lines=$('ed').innerText.split('\n');
  const hit=new Set();
  const out=lines.map(l=>{const m=l.match(/^part\s+(\S+)\s+(\S+)(.*)$/);
    if(!m||!refs.has(m[1]))return l;hit.add(m[1]);
    const cur=(m[3].match(/rot=(\d+)/)||[])[1];
    const nx=cur?((+cur+90)%360):90;
    const rest=m[3].replace(/\s*rot=\d+/,'');
    return `part ${m[1]} ${m[2]} rot=${nx}${rest}`;});
  const miss=[...refs].filter(r=>!hit.has(r));
  if(miss.length){statMsg('no part line for '+miss.join(', '));return;}
  $('ed').innerText=out.join('\n');push();}
// drag parts on pcb
(()=>{const c=$('pcb');let drag=null,dragGroup=null;
function hit(mx,my){for(const r in S.cur.parts){
  if(!S.cur.parts[r]||!partShown(r,S.cur))continue; // hidden parts are not targets
  const p=S.cur.parts[r];
  const x=view.ox+p.x*view.s,y=view.oy+(S.bh-p.y)*view.s;
  if(Math.abs(mx-x)<p.w*view.s/2+4&&Math.abs(my-y)<p.h*view.s/2+4)return r;}return null;}
c.addEventListener('mousedown',e=>{if(!S)return;const R=c.getBoundingClientRect();
  drag=hit(e.clientX-R.left,e.clientY-R.top);
  // rigid group: an instanced part drags its whole owner-group (offsets kept)
  dragGroup=null;
  if(drag){const o=S.cur.parts[drag].owner;
    if(o)dragGroup=Object.keys(S.cur.parts).filter(r=>S.cur.parts[r].owner===o);}});
c.addEventListener('mousemove',e=>{const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  if(drag){const p=S.cur.parts[drag];
    const nx=Math.round(((mx-view.ox)/view.s)*10)/10,ny=Math.round((S.bh-(my-view.oy)/view.s)*10)/10;
    const dx=nx-p.x,dy=ny-p.y;p.x=nx;p.y=ny;
    if(dragGroup)for(const r of dragGroup){if(r===drag)continue;
      const q=S.cur.parts[r];q.x=Math.round((q.x+dx)*10)/10;q.y=Math.round((q.y+dy)*10)/10;}
    dirty=true;}
  else if(S)S.cur.hover=hit(mx,my);});
c.addEventListener('mouseup',async()=>{if(!drag)return;const moved=dragGroup||[drag];dragGroup=null;const r=drag;drag=null;
  const gone=new Set(moved);
  const lines=$('ed').innerText.split('\n').filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  // drop fix lines right after board/use block (group order kept)
  let idx=lines.findIndex(l=>/^(part|net|fix|keep|route|trace|power|silk)\b/.test(l));if(idx<0)idx=lines.length;
  moved.forEach((rr,i)=>{const p=S.cur.parts[rr];lines.splice(idx+i,0,`fix ${rr} at ${p.x} ${p.y}`);});
  // The pin is written either way (it is the truth about where the part is),
  // but re-placing thousands of parts is minutes: ask before hanging the page.
  $('ed').innerText=lines.join('\n');
  if(S&&S.cur&&S.cur.dense&&!confirm(
      `Re-place ${Object.keys(S.cur.parts).length} parts around the pin?\n\n`
      +'This runs the placer over a dense board and can take minutes.\n'
      +'Cancel keeps the pin and re-loads from the file instead.')){
    loadBoard();return;}
  push();});
c.addEventListener('dblclick',()=>{ // unpin: remove fix (whole group if instanced)
  if(!S||!S.cur||!S.cur.hover)return;
  const r=S.cur.hover,o=S.cur.parts[r].owner;
  unpinRefs(new Set(o?Object.keys(S.cur.parts).filter(k=>S.cur.parts[k].owner===o):[r]));});
c.addEventListener('contextmenu',e=>{ // right-click: rotate here, unpin there
  e.preventDefault();if(!S||!S.cur)return;const R=c.getBoundingClientRect();
  const r=hit(e.clientX-R.left,e.clientY-R.top);
  if(!r)return; // empty board: browser menu stays suppressed, nothing to do
  const o=S.cur.parts[r].owner;
  rotRefs(new Set(o?Object.keys(S.cur.parts).filter(k=>S.cur.parts[k].owner===o):[r]));});
})();
$('solve').onclick=async()=>{
  await withBusy($('solve'),'solving…',async()=>{
    const r=await api('/solve',{placer:$('placer').value,router:$('router').value,full:true});
    if(r.error){statMsg(r.error);return;}
    statMsg('');applyState(r,true);
  });
};
$('dice').onclick=genCands;
$('fab_dl').onclick=async()=>{
  await withBusy($('fab_dl'),'building zip…',async()=>{
    const r=await api('/export',{});
    if(r.error){statMsg(r.error);return;}
    const a=document.createElement('a');
    a.href='data:application/zip;base64,'+r.zip;a.download=r.name;a.click();
    statMsg(`${r.name} (${(r.bytes/1024).toFixed(0)}KB)`,true);
  });
};
$('dl').onclick=async()=>{ // cycle svg → sch → png → xray (shift-click backwards)
  const keys=['svg','sch','png','xray'];
  dlIdx=(dlIdx+((window.event&&window.event.shiftKey)?-1:1)+keys.length)%keys.length;
  const key=keys[dlIdx];
  $('dl').textContent=`download ${key}`; // keep a word label (glyph alone is not a label)
  await withBusy($('dl'),`download ${key}…`,async()=>{
    const r=await api('/render',{key});
    if(r.error){statMsg(r.error);return;}
    const a=document.createElement('a');
    a.href=r.bin?`data:application/octet-stream;base64,${r.data}`
      :`data:image/svg+xml,${encodeURIComponent(r.data)}`;
    if(key==='png'&&!r.bin)a.href=`data:image/png;base64,${r.data}`;
    a.download=r.name;a.click();statMsg(r.name,true);
  });
};
let dlIdx=0;
let simWhat='dc';
$('simbtn').onclick=async()=>{ // dc ⇄ tran on shift-click
  if(window.event&&window.event.shiftKey)simWhat=simWhat==='dc'?'tran':'dc';
  $('simbtn').textContent=`sim ${simWhat}`;
  await withBusy($('simbtn'),`sim ${simWhat}…`,async()=>{
    const r=await api('/simulate',{what:simWhat});
    if(r.error){statMsg(r.error);return;}
    if(r.sim&&Object.keys(r.sim).length)S.sim=r.sim;
    if(r.tran&&Object.keys(r.tran).length)S.tran=r.tran;
    statMsg('',true);drawDRC(S);
  });
};
// Ω calculators: same math as ocdcircuit/calc.py, instant, no round-trip
function calcLive(){
  const A=parseFloat($('ca').value)||0,dT=parseFloat($('cdt').value)||10;
  const area=Math.pow(A/(0.048*Math.pow(dT,0.44)),1/0.725); // IPC-2221 ext 1oz
  $('cout').textContent=`${(area/1.378*0.0254).toFixed(2)}mm ext, via ${(A/(3*Math.sqrt(dT/10))).toFixed(2)}mm drill`;
  const pv=v=>{const m=String(v).match(/^([\d.]+)(k|M)?$/i);return m?parseFloat(m[1])*(m[2]?({k:1e3,M:1e6})[m[2].toLowerCase()]||1:1):NaN;};
  const V=pv($('dv').value),Rt=pv($('drt').value),Rb=pv($('drb').value);
  $('dout').textContent=(V>=0&&Rt>0&&Rb>0)?`Vout ${(V*Rb/(Rt+Rb)).toFixed(2)}V`:'';
}
['ca','cdt','dv','drt','drb'].forEach(id=>$(id).addEventListener('input',calcLive));
if($('qgo'))$('qgo').onclick=async()=>{ // fab price comparison for the open board
  await withBusy($('qgo'),'comparing…',async()=>{
    const q=Math.max(1,parseInt($('qqty').value)||5);
    const r=await api('/quote',{qty:q,no_parts:$('qbare').checked});
    if(r.error){$('qout').textContent=r.error;statMsg(r.error);return;}
    $('qout').innerHTML=(r.rows||[]).map(x=>
      `<div class=qrow>${x.logo?`<img class=qlogo src="${x.logo}" alt="${x.fab} logo" width=64 height=21>`:''}<span class=dim>${x.fab}</span> bare $${x.bare_total}${x.asm_total?` asm $${x.asm_total} ($${x.asm_per_board}/bd)`:''}</div>`).join('')
      +`<div class=dim>${r.stamp} estimates — re-verify before ordering</div>`;
    statMsg('');
  });
};
$('doc').addEventListener('toggle',async()=>{ // lazy: check on first open
  if(!$('doc').open||$('docout').dataset.done)return;
  const r=await api('/doctor',{});
  if(r.error){$('docout').textContent=r.error;return;}
  $('docout').innerHTML=(r.ok?'<div class=ok>✓ all systems</div>':'<div class=warn>degraded: features fall back, nothing crashes</div>')
    +r.checks.map(c=>`<div class=${c.ok?'ok':'err'}>${c.ok?'✓':'✗'} ${c.name}${c.detail?' <span class=dim>'+c.detail+'</span>':''}</div>`).join('');
  $('docout').dataset.done='1';
});
// undo/redo: server keeps text history (git-style log); undo restores + rebuilds
// photo scan: upload shots of a physical board, get a draft design back
const scanAsk=[];
const b64=f=>new Promise(r=>{const d=new FileReader();
  d.onload=()=>r({name:f.name,data:String(d.result).split(',',1).length>1?String(d.result).slice(String(d.result).indexOf(',')+1):''});
  d.readAsDataURL(f);});
if($('scango'))$('scango').onclick=async()=>{
  const fs=[...($('scanfiles').files||[])];
  if(!fs.length){$('scanstat').textContent='choose some photos first';return;}
  $('scanstat').textContent=`stitching ${fs.length} photos — this takes a minute…`;
  $('scango').disabled=true;$('scanout').textContent='';$('scanq').innerHTML='';
  try{
    const photos=await Promise.all(fs.map(b64));
    const docs=await Promise.all([...($('scandocs').files||[])].map(b64));
    const body={photos,docs,note:$('scannote').value||'',
                answers:scanAsk.filter(x=>x.a).map(x=>({q:x.q,a:x.a}))};
    if($('scanmm').value)body.mm=Number($('scanmm').value);
    const r=await api('/scan',body);
    if(r.error){$('scanstat').textContent=r.error;return;}
    const sides=Object.entries(r.sides||{}).map(([k,v])=>
      `${k}: ${v.used}/${v.photos} registered, coverage ${v.coverage_mean}`).join(' · ');
    $('scanstat').textContent=sides||'scan done';
    Object.entries(r.sides||{}).forEach(([k,v])=>{
      Object.entries(v.dropped_why||{}).forEach(([nm,why])=>{
        const d=document.createElement('div');d.className='panel-note';
        d.textContent=`dropped ${k}/${nm}: ${why}`;$('scanq').appendChild(d);});});
    $('scanout').textContent=r.analysis||r.draft_error||'(no analysis)';
    scanRev = r.review||null; scanViews = r.views||{};
    scanDraft = r.draft||'';
    scanShowReview();
    if(r.draft){const b=document.createElement('button');b.type='button';
      b.className='primary';b.textContent='open this draft in the editor';
      b.onclick=()=>{setEditor(r.draft);push();};
      $('scanq').appendChild(b);}
    if(r.draft_error){const w=document.createElement('div');
      w.className='panel-note';w.textContent='draft did not parse: '+r.draft_error;
      $('scanq').appendChild(w);}
    if(r.draft&&!r.draft_error){const w=document.createElement('div');
      w.className='panel-note';
      const fl=(r.floating||[]).length;
      w.textContent=`buildability: ${r.wired} parts wired, ${r.drc} DRC error(s)`
        +(fl?` · ${fl} parts have no nets (photos cannot show them) — wire from the datasheet`:'');
      $('scanq').appendChild(w);}
    (r.questions||[]).forEach(q=>{
      const row=document.createElement('div');row.className='scanqa';
      const lab=document.createElement('label');lab.textContent=q;
      const inp=document.createElement('input');inp.placeholder='your answer — then analyse again';
      inp.setAttribute('aria-label',q);
      const rec={q,a:''};scanAsk.push(rec);
      inp.oninput=()=>{rec.a=inp.value;};
      row.appendChild(lab);row.appendChild(inp);$('scanq').appendChild(row);});
  }catch(e){$('scanstat').textContent='scan failed: '+e;}
  finally{$('scango').disabled=false;}
};

// scan review: stitch image with detected-part boxes, labels, tooltips.
// Boxes come from the draft's own fix constraints + live footprint sizes
// (the review payload), so an outline cannot drift from the positions the
// edits below act on. mm -> canvas px uses the canvas + mm_per_px from the
// same response; canvas y grows downward, board y grows up.
let scanRev=null, scanViews={}, scanDraft='';
function scanSide(){
  const want=($('scanside')||{}).value||'top';
  if(scanViews[want])return want;
  const ks=Object.keys(scanViews);
  return ks.length?ks[0]:'top';
}
function scanShowReview(){
  const has=(scanRev&&scanRev.parts&&scanRev.parts.length)||Object.keys(scanViews).length;
  $('scanview').hidden=!has;
  if(!has)return;
  const side=scanSide();
  if($('scanside').value!==side)$('scanside').value=side;
  scanDraw();
  scanRows();
}
function scanScale(){
  const cv=((scanRev||{}).canvas||{})[scanSide()]||[0,0];
  const mm=(((scanRev||{}).mm_per_px||{})[scanSide()])||0;
  return {cw:cv[0]||0, ch:cv[1]||0, mm:mm||0};
}
function scanDraw(){
  const img=$('scanimg'), svg=$('scansvg');
  const side=scanSide();
  const views=scanViews[side]||{};
  img.src=views[($('scanviewkind')||{}).value||'stitch']||views.stitch||'';
  const {cw,ch,mm}=scanScale();
  const W=cw||img.naturalWidth||1, H=ch||img.naturalHeight||1;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const showB=$('scanboxes').checked, showL=$('scanlabels').checked;
  const parts=((scanRev||{}).parts||[]);
  let h='';
  // draft x/y grow DOWN the stitch image (the model reads positions off the
  // photo: U4 at y=82 sits low in the frame, matching "bottom-left/centre"
  // in its own inventory). canvas rows grow the same way, so no flip.
  for(const p of parts){
    if(!p.w||!p.h||!mm)continue;
    const x0=(p.x-p.w/2)/mm, x1=(p.x+p.w/2)/mm;
    const y0=(p.y-p.h/2)/mm, y1=(p.y+p.h/2)/mm;
    if(showB)h+=`<rect class="bx${p.uncertain?' unc':''}" x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1-x0).toFixed(1)}" height="${(y1-y0).toFixed(1)}" data-ref="${p.ref}"/>`;
    if(showL)h+=`<text x="${x0.toFixed(1)}" y="${(y0-3).toFixed(1)}" data-ref="${p.ref}">${p.ref}</text>`;
  }
  svg.innerHTML=h;
}
function scanTip(p){
  const bits=[`${p.ref} · ${p.fp}${p.value&&p.value!=='?'?` · ${p.value}`:''}`,
    `${p.w}×${p.h}mm at (${p.x}, ${p.y})`];
  if(p.value==='?')bits.push('value unreadable — confirm it below');
  else if(p.uncertain)bits.push('reading uncertain');
  if(p.note)bits.push(p.note);
  return bits.join(' — ');
}
function scanRows(){
  const box=$('scanparts');box.innerHTML='';
  for(const p of ((scanRev||{}).parts||[])){
    const row=document.createElement('div');
    row.className='scanprow'+(p.uncertain?' unc':'');
    row.dataset.ref=p.ref;
    row.title=scanTip(p);
    const lbl=document.createElement('b');lbl.textContent=p.ref;row.appendChild(lbl);
    const dim=document.createElement('span');dim.className='dim';
    dim.textContent=`${p.fp} · ${p.value||'?'}`;row.appendChild(dim);
    if(p.uncertain){
      const fix=document.createElement('button');fix.type='button';
      fix.textContent='confirm';fix.title=`accept ${p.ref} as ${p.fp} ${p.value||'?'}`;
      fix.onclick=()=>scanConfirm(p.ref);
      row.appendChild(fix);
      const ed=document.createElement('button');ed.type='button';
      ed.textContent='edit';ed.title=`correct ${p.ref} (value / footprint)`;
      ed.onclick=()=>scanEdit(p.ref);
      row.appendChild(ed);
    }
    const del=document.createElement('button');del.type='button';
    del.textContent='remove';del.title=`drop ${p.ref} from the draft`;
    del.onclick=()=>scanRemove(p.ref);
    row.appendChild(del);
    box.appendChild(row);
  }
}
// review edits rewrite the DRAFT text, never Board state: the draft is the
// review's working copy and /build re-parses it, so the editor, solver and
// undo history all see the same change they would from a hand edit.
function scanDraftLines(){return (scanDraft||'').split('\n');}
function scanSetDraft(lines){
  scanDraft=lines.join('\n');
  scanRev.parts=scanRev.parts||[];
}
function scanPartLine(ref){
  const lines=scanDraftLines();
  const i=lines.findIndex(l=>l.startsWith('part '+ref+' ')||l==='part '+ref);
  return {lines,i};
}
function scanCommit(msg){
  setEditor(scanDraft);push();
  $('scanstat').textContent=msg;
}
function scanConfirm(ref){
  // '?'/hedged value -> keep fp+position, mark certain by writing the value
  // the row already shows (no-op textually) only when a real value exists;
  // otherwise ask: a confirm must resolve the '?', not bless it.
  const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
  if(!p)return;
  if(!p.value||p.value==='?'){
    scanEdit(ref, true);
    return;
  }
  // Confirming a NAMED value only clears the hedge flag: the draft line
  // already carries this value, so there is nothing to rewrite — but the
  // trailing model note ("marking likely 45DB041B") described doubt that no
  // longer applies, and leaving it would re-flag the part on reload.
  // Strip a trailing `# ...` note only; x/y/attrs are untouched.
  const {lines,i}=scanPartLine(ref);
  if(i>=0&&/#/.test(lines[i])){
    lines[i]=lines[i].split('#')[0].trimEnd();
    scanSetDraft(lines);
  }
  p.uncertain=false;p.note='';
  scanDraw();scanRows();
  $('scanstat').textContent=`${ref} confirmed as ${p.fp} ${p.value}`;
}
function scanEdit(ref, mustName){
  const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
  if(!p)return;
  const val=prompt(`value for ${ref} (footprint ${p.fp})${p.note?'\nmodel note: '+p.note:''}`, p.value==='?'?'':p.value);
  if(val===null)return;
  const fp=prompt(`footprint for ${ref} (Enter keeps ${p.fp})`, p.fp) || p.fp;
  const {lines,i}=scanPartLine(ref);
  if(i<0){$('scanstat').textContent=`${ref} not found in draft text`;return;}
  // splice tokens, keep the rest of the line: the draft may carry attrs the
  // review row does not model (rot=, mpn=...), and a blind rewrite would eat
  // them. x=/y= keep model position unless the line already disagrees.
  // Values with spaces are shlex-quoted the way dumps() quotes them.
  const qv=v=>(/\s|["']/.test(v)?`"${v.replace(/"/g,'\\"')}"`:v);
  const toks=lines[i].split(/\s+/);
  // part REF FP [VALUE] [k=v ...] [# note]
  let j=3;
  if(j<toks.length&&!toks[j].includes('=')&&!toks[j].startsWith('#'))j++;
  toks.splice(2, j-2, fp, ...(val?[qv(val)]:[]));
  lines[i]=toks.join(' ');
  p.value=val;p.fp=fp;p.uncertain=false;p.note='';
  scanSetDraft(lines);scanDraw();scanRows();
  scanCommit(`${ref} corrected — rebuilding`);
}
function scanRemove(ref){
  const {lines,i}=scanPartLine(ref);
  if(i<0){$('scanstat').textContent=`${ref} not found in draft text`;return;}
  lines.splice(i,1);
  // a removed part must not leave dangling pins: a net naming a part that
  // no longer exists fails the whole draft ("net GND: unknown part 'U5'").
  // drop its pins, and drop nets left with fewer than two pins.
  const pinre=new RegExp(`\\b${ref}\\.\\S+`,'g');
  for(let k=lines.length-1;k>=0;k--){
    const ln=lines[k];
    if(!ln.startsWith('net '))continue;
    const cut=ln.replace(pinre,'').replace(/<-->\s*<-->/g,'').trim();
    const pins=(cut.match(/[A-Za-z0-9_]+\.[A-Za-z0-9_]+/g)||[]).length;
    if(pins<2)lines.splice(k,1);
    else lines[k]=cut;
  }
  scanRev.parts=(scanRev.parts||[]).filter(x=>x.ref!==ref);
  scanSetDraft(lines);scanDraw();scanRows();
  scanCommit(`${ref} removed — rebuilding`);
}
if($('scanside'))$('scanside').onchange=scanShowReview;
if($('scanviewkind'))$('scanviewkind').onchange=scanShowReview;
if($('scanlabels'))$('scanlabels').onchange=scanShowReview;
if($('scanboxes'))$('scanboxes').onchange=scanShowReview;
if($('scansvg')){
  $('scansvg').addEventListener('mousemove',e=>{
    const t=e.target;
    const ref=t&&t.dataset?t.dataset.ref:null;
    const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
    $('scanhover').textContent=p?scanTip(p):'';
  });
  $('scansvg').addEventListener('mouseleave',()=>{$('scanhover').textContent='';});
}

// x-ray: reference download + fab-scan upload vs the design (score + boxes)
let xrayRaw='';
if($('xrayfile'))$('xrayfile').onchange=()=>{const f=$('xrayfile').files[0];if(!f)return;
  const rd=new FileReader();rd.onload=()=>{xrayRaw=String(rd.result).split(',',1)[1]||'';
    $('xraystat').textContent=`${f.name} ready — compare to check it`;};
  rd.readAsDataURL(f);};
if($('xraysvg'))$('xraysvg').onclick=async()=>{
  const r=await api('/render',{key:'xray'});if(r.error){$('xraystat').textContent=r.error;return;}
  const a=document.createElement('a');
  a.href=`data:image/svg+xml,${encodeURIComponent(r.data)}`;
  a.download=r.name;a.click();$('xraystat').textContent=r.name;};
if($('xraygo'))$('xraygo').onclick=async()=>{
  if(!xrayRaw){$('xraystat').textContent='pick a fab PNG first';return;}
  const pv=(id,fb)=>{const v=parseFloat($(id).value);return Number.isFinite(v)?v:fb;};
  const r=await api('/xray',{png:xrayRaw,dx:pv('xraydx',0),dy:pv('xraydy',0),
    scale:pv('xraysc',1),thr:Math.round(pv('xraythr',100))});
  if(r.error){$('xraystat').textContent=r.error;return;}
  $('xraystat').textContent=`score ${r.score} — missing ${r.missing}px extra ${r.extra}px`;
  $('xraydivs').innerHTML=(r.divs||[]).map(d=>
    `<div><span class=dim>${d.kind}</span> ${d.x} ${d.y} ${d.w}x${d.h}mm</div>`).join('')
    ||'<div class=dim>no divergences</div>';
  if(r.overlay){const w=open('','_blank');if(w)w.document.write(r.overlay);}};
async function hist(op){
  const r=await api(op,{});
  if(r.error){statMsg(r.error);return;}
  statMsg('');setEditor(r.text);applyState(r,false);
}
$('undo').onclick=()=>hist('/undo');
$('redo').onclick=()=>hist('/redo');
$('diffprev').onclick=async()=>{
  const r=await api('/diff_prev',{});
  statMsg(r.error||r.diff, !r.error);
};
$('stamp').onclick=()=>{ // repeat-layout: stamp another copy of hovered instance
  if(!S||!S.cur||!S.cur.hover){statMsg('hover an instanced part, then stamp');return;}
  const o=S.cur.parts[S.cur.hover].owner;
  if(!o){statMsg('that part is not in an instance');return;}
  const pre=o.replace(/_$/,'');
  const lines=$('ed').innerText.split('\n');
  const inst=lines.map((l,i)=>({m:l.match(/^instance\s+(\S+)\s+as\s+(\S+?)(?:\s+join\s+(.*))?$/),i}))
    .filter(x=>x.m);
  const src=inst.find(x=>x.m[2]===pre);
  if(!src){statMsg(`no instance line for ${pre}`);return;}
  const stem=pre.replace(/\d+$/,'')||pre;
  const nums=inst.map(x=>{const m=x.m[2].match(/(\d+)$/);return m?parseInt(m[1]):0;});
  const next=stem+(Math.max(0,...nums)+1);
  const join=src.m[3]?` join ${src.m[3]}`:'';
  const at=inst[inst.length-1].i;
  lines.splice(at+1,0,`instance ${src.m[1]} as ${next}${join}`);
  $('ed').innerText=lines.join('\n');push();
};
$('ed').addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('solve').click();}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();commitBoard();}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey){e.preventDefault();hist('/undo');}
  else if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='y'||(e.key.toLowerCase()==='z'&&e.shiftKey))){e.preventDefault();hist('/redo');}
});
// --- project files: browse, open, and say which file is the board -------
let TREE=[],SRCREL='',VC={},DIR='.',ROOTREL='.';
function treeRow(e,depth){
  const d=document.createElement('button');d.type='button';
  d.className='trow '+(e.kind==='dir'?'tdir':'tfile')+(e.path===SRCREL?' active':'');
  d.style.paddingLeft=(8+depth*13)+'px';
  d.textContent=e.kind==='dir'?e.name+'/':e.name;
  d.title=e.kind==='dir'?'folder — click to open it':e.path;
  d.setAttribute('aria-label',e.kind==='dir'
    ?('open folder '+e.name):('open '+e.name));
  if(e.kind==='file'){
    if(e.bytes){const s=document.createElement('span');s.className='tsize';
      s.textContent=(+e.bytes/1024).toFixed(1)+'k';d.appendChild(s);}
    d.onclick=()=>e.name.endsWith('.ocd')?openFile(e.path):previewFile(e.path);
  }else{
    d.onclick=()=>loadTree(e.path);
  }
  return d;
}
function renderTree(){
  const t=$('tree');t.innerHTML='';
  // one level at a time: this directory, then a way back up while inside root
  if(DIR!=='.'){const up=DIR.includes('/')?DIR.replace(/\/[^/]*$/,''):'.';
    t.appendChild(treeRow({name:'.. ('+(up==='.'?ROOTREL:up)+')',path:up,kind:'dir'},0));}
  if(!TREE.length){const e=document.createElement('div');e.className='trow tdir';
    e.setAttribute('role','status');e.textContent='(no text files)';t.appendChild(e);}
  TREE.forEach(e=>t.appendChild(treeRow(e,1)));
}
async function loadTree(dir){
  const f=await fetch('/fs?dir='+encodeURIComponent(dir||'.')).then(x=>x.json());
  if(f.error){statMsg(f.error);return;}
  DIR=f.dir||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';VC=f.vcs||{};
  TREE=f.tree||[];
  $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
  renderTree();
}
async function previewFile(path){
  const r=await api('/fs/read',{path});
  if(r.error){statMsg(r.error);return;}
  msg('bot','preview of '+path+' (read-only here; open a .ocd in the editor to edit it).\n'
      +r.text.split('\n').slice(0,40).join('\n'));
}
async function openFile(path){
  cancelPush();  // a queued rebuild of the old board must not follow us here
  collabSyncSeq++; // a sync for the old board must not land on the new one
  if(collabES){try{collabES.close();}catch(err){}collabES=null;}
  collabOn=false; // collabStart re-opens the stream on the new board's room
  const r=await api('/fs/open',{path});
  if(r.error){statMsg(r.error);return;}
  statMsg('');$('msgs').innerHTML='';
  heldThash='';heldTraces=[];  // a different board: its traces are not ours
  setQueue([]);  // the server dropped the old board's proposals with it
  collabRev=(r.rev!==undefined)?+r.rev:-1; // re-home the room to the new board
  collabPaint([]);
  applyState(r,false);
  collabStart(); // join the new board's room: sync + fresh SSE stream
  toast('opened '+path);
  const f=await fetch('/fs').then(x=>x.json());
  if(!f.error){DIR=f.base||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';TREE=f.tree||[];
    $('srcnote').textContent=`${SRCREL} · ${f.base||'.'} · saved on every good build`;
    $('chatwhere').textContent=SRCREL;
    $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
    renderTree();}
  loadVCS();
}
// --- agent chat --------------------------------------------------------
function msg(who,text){
  const el=document.createElement('p');
  el.className='msg '+(who==='you'?'me':who);
  const w=document.createElement('span');w.className='who';
  w.textContent=who==='bot'?'flux':who; // the agent has a name, like its counterpart
  el.appendChild(w);
  el.appendChild(document.createTextNode(text)); // textContent: never innerHTML
  $('msgs').appendChild(el);
  $('msgs').scrollTop=$('msgs').scrollHeight;
  return el;
}
function renderDiff(p,text){
  p.textContent='';
  String(text).split('\n').forEach(ln=>{
    const d=document.createElement('div');
    d.className=ln.startsWith('@@')||/^(\+\+\+|---)/.test(ln)?'dl-at'
      :ln.startsWith('+')?'dl-add':ln.startsWith('-')?'dl-del':'';
    d.textContent=ln;
    p.appendChild(d);
  });
}
function propose(pr,onQueue){
  const box=document.createElement('div');box.className='prop';
  const head=document.createElement('div');head.className='phead';
  const b=document.createElement('b');b.textContent=pr.path;
  const g=document.createElement('span');g.className='grow';
  const apply=document.createElement('button');apply.textContent='apply';
  const drop=document.createElement('button');drop.textContent='reject';
  head.append('proposed edit to ',b,g,apply,drop);
  const pre=document.createElement('pre');renderDiff(pre,pr.diff||'');
  box.append(head,pre);
  apply.onclick=async()=>{
    apply.disabled=drop.disabled=true;
    const r=await api('/chat/apply',{id:pr.id});
    if(r.error){msg('err',r.error);apply.disabled=drop.disabled=false;return;}
    if(r.state)applyState(r.state,false);
    else loadVCS();
    box.remove();
    msg('bot','applied '+pr.path+(r.state?'':' (not the open board)')
        +(r.note?' — '+r.note:''));
    if(onQueue)onQueue(r.proposals||[]);
    loadVCS();
  };
  drop.onclick=async()=>{
    apply.disabled=drop.disabled=true;
    const r=await api('/chat/reject',{id:pr.id});
    box.remove();
    msg('bot','rejected '+pr.path+' — nothing was written');
    if(onQueue)onQueue(r.proposals||[]);
  };
  $('msgs').appendChild(box);
  $('msgs').scrollTop=$('msgs').scrollHeight;
  return box;
}
// a turn may propose several files: every card lives in one queue, and each
// apply/reject removes its own card without disturbing the others
function setQueue(list){
  $('msgs').querySelectorAll('.prop').forEach(e=>e.remove());
  (list||[]).forEach(p=>propose(p,setQueue));
}
async function chat(text,auto){
  const go=$('composer').querySelector('button[type=submit]');
  if(go&&go.disabled)return; // one turn at a time — a double Enter doubles LLM spend
  if(go)go.disabled=true;
  msg('you',text);
  const wait=msg('bot','thinking…');
  let r;
  try{r=await api('/chat',{text,auto:!!auto});}
  finally{if(go)go.disabled=false;wait.remove();}
  if(r.error){msg('err',r.error);setQueue(r.proposals);return;}
  const tn=(r.proposals||[]).filter(p=>/\.ocd$/.test(p.path||''));
  if(tn.length){ // plan checklist: the turn's file edits as checkable steps
    const det=document.createElement('details');det.className='thought';det.open=true;
    const sum=document.createElement('summary');
    sum.textContent=`plan: ${tn.length} file${tn.length===1?'':'s'} proposed`;
    const ul=document.createElement('ul');
    tn.forEach(p=>{const li=document.createElement('li');li.className='ok';
      li.textContent='◻ '+p.path;ul.appendChild(li);});
    det.append(sum,ul);$('msgs').appendChild(det);
    $('msgs').scrollTop=$('msgs').scrollHeight;
  }
  if(r.log&&r.log.length){ // thought trace: what the agent actually did
    const det=document.createElement('details');det.className='thought';
    const sum=document.createElement('summary');
    sum.textContent=`thought for ${r.log.length} step${r.log.length===1?'':'s'}`;
    const ul=document.createElement('ul');
    r.log.forEach(ln=>{const li=document.createElement('li');
      li.className=/failed|error/i.test(ln)?'bad':'ok';li.textContent=ln;
      ul.appendChild(li);});
    det.append(sum,ul);$('msgs').appendChild(det);
    $('msgs').scrollTop=$('msgs').scrollHeight;
  }
  msg('bot',r.reply||'(no reply)');
  followups(r.reply||'');
  if(r.note)msg('bot',r.note);
  if(r.proposals&&r.proposals.length)setQueue(r.proposals);
  if(r.state)applyState(r.state,false);
  if(r.applied)loadVCS();
}
// follow-up chips: Flux's "Route and verify / Add thermal copper" row.
// Mined from the reply's own next-steps, else the three generic moves.
function followups(reply){
  let box=$('followups');
  if(!box){box=document.createElement('div');box.id='followups';
    $('composer').before(box);}
  box.innerHTML='';
  const picks=[];
  reply.split('\n').forEach(ln=>{
    const m=ln.match(/^(?:\d+[.)]\s*|[-*]\s+)(.{12,80})$/);
    if(m&&/rout|check|valid|verif|test|place|thermal|copper|drc|fix/i.test(m[1])
       &&picks.length<3)picks.push(m[1].trim());});
  if(!picks.length)picks.push('Route and verify','Check DRC','Explain this board');
  picks.slice(0,3).forEach(q=>{const b=document.createElement('button');
    b.textContent=q.length>34?q.slice(0,33)+'…':q;b.title=q;
    b.onclick=()=>chat(q,$('chatauto').checked);
    box.appendChild(b);});
}
$('composer').addEventListener('submit',e=>{
  e.preventDefault();
  const t=$('ask').value.trim();if(!t)return;
  $('ask').value='';
  chat(t,$('chatauto').checked);
});
$('ask').addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('composer').requestSubmit();}
});
$('chatclear').onclick=async()=>{await api('/chat/reset',{});$('msgs').innerHTML='';};
// --- revisions: the board directory's git log --------------------------
async function loadVCS(){
  const r=await api('/vcs',{path:SRCREL||null});
  if(r.error){$('vcsnote').textContent=r.error;return;}
  VC=r.status||{};
  $('vcsnote').textContent=VC.repo
    ? `${VC.branch} · `+(VC.dirty?'uncommitted changes in '+VC.board:'clean')
    : 'not a git repository';
  const box=$('vcs');box.innerHTML='';
  if(!VC.repo){box.textContent='commit from the toolbar once this directory is a repo';return;}
  (r.log||[]).forEach(c=>{
    const d=document.createElement('button');d.type='button';d.className='rev';
    d.setAttribute('aria-label','show diff for '+c.hash+' '+c.subject);
    const h=document.createElement('span');h.className='rh';h.textContent=c.hash;
    const dt=document.createElement('span');dt.className='rd';dt.textContent=c.date;
    const s=document.createElement('span');s.className='rs';s.textContent=c.subject;
    d.append(h,dt,s);
    d.onclick=async()=>{
      const x=await api('/vcs/diff',{hash:c.hash});
      const pre=document.createElement('pre');pre.textContent=x.error||x.diff;
      const old=box.querySelector('pre');if(old)old.remove();
      d.after(pre);
    };
    box.appendChild(d);
  });
}
async function commitBoard(){
  if(!VC.repo){statMsg('not a git repository');return;}
  const m=prompt('commit message',(SRCREL||'board')+': ');
  if(!m)return;
  const r=await api('/vcs/commit',{message:m});
  if(r.error){statMsg(r.error);return;}
  toast(r.commit||'committed');
  loadVCS();
}
function toast(t){
  const b=document.createElement('div');b.className='toast';
  b.setAttribute('role','status');b.setAttribute('aria-live','polite');
  b.textContent=t;
  document.body.appendChild(b);
  setTimeout(()=>b.remove(),4200);
}
async function loadBoard(){ // parse + route what is on disk; never re-place
  const r=await api('/load',{});
  if(r.error){statMsg(r.error);return;}
  setEditor(r.text);applyState(r,false);
}
async function boot(){
  // whoami: a stale cookie lands here sessionless — bounce to the gate.
  try{const me=await api('/auth/me',{});
    if(me&&me.user){$('me').textContent=me.user;}
    else{location.href='/';return;}}catch(e){location.href='/';return;}
  // /load parses the file and routes what is there. It does not re-place:
  // a 5k-part board takes minutes to place, and the file already says where
  // the parts go. `solve` is the explicit ask for a fresh placement.
  // /load and /fs are independent — fetch in parallel (was a waterfall).
  const [r,f]=await Promise.all([api('/load',{}),fetch('/fs').then(x=>x.json())]);
  $('placer').innerHTML=r.placers.map(p=>`<option>${p}</option>`).join('');
  $('router').innerHTML=r.routers.map(p=>`<option>${p}</option>`).join('');
  $('silk').innerHTML=r.silks.map(p=>`<option ${p===r.silk?'selected':''}>${p}</option>`).join('');
  $('fab').innerHTML=r.fabs.map(p=>`<option>${p}</option>`).join('');
  setEditor(r.text);applyState(r,false);
  if(r.rev!==undefined)collabRev=+r.rev; // the room's rev from the first load
  collabStart(); // realtime: SSE fan-out + presence from here on
  if(f.error){$('treenote').textContent=f.error;return;}
  DIR=f.base||'.';ROOTREL=f.root||'.';TREE=f.tree||[];SRCREL=f.src||'';VC=f.vcs||{};
  $('srcnote').textContent=`${SRCREL} · ${f.base||'.'} · saved on every good build`;
  $('chatwhere').textContent=SRCREL;
  $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
  renderTree();
  loadVCS();
  $('logoutbtn').onclick=async()=>{await api('/auth/logout',{});location.href='/';};
  try{if(JSON.parse(localStorage.getItem('ocd-studio-dark')||'0'))setDark(true);}catch(e){}
  $('themebtn').onclick=()=>setDark(!document.body.classList.contains('dark'));
  document.querySelectorAll('#viewtabs button').forEach(b=>b.onclick=()=>setView(b.dataset.v));
  try{const v=localStorage.getItem('ocd-studio-view');if(v&&v!=='all')setView(v);}catch(e){}
  // cockpit ↔ tabs: All (or double-click any tab) returns to every panel
  document.querySelectorAll('#viewtabs button').forEach(b=>b.ondblclick=()=>showCockpit());
  // tablist: arrow keys move selection (APG pattern); Home/End jump ends
  $('viewtabs').addEventListener('keydown',e=>{
    const tabs=[...$('viewtabs').querySelectorAll('[role=tab]')];
    const i=tabs.indexOf(document.activeElement);
    if(i<0)return;
    let n=-1;
    if(e.key==='ArrowRight'||e.key==='ArrowDown')n=(i+1)%tabs.length;
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp')n=(i-1+tabs.length)%tabs.length;
    else if(e.key==='Home')n=0;
    else if(e.key==='End')n=tabs.length-1;
    else if(e.key==='Enter'||e.key===' '){e.preventDefault();setView(tabs[i].dataset.v);return;}
    if(n<0)return;
    e.preventDefault();
    if(document.body.classList.contains('tabs')){setView(tabs[n].dataset.v);}
    else{tabs.forEach((t,j)=>t.tabIndex=j===n?0:-1);}
    tabs[n].focus();
  });
}
function setDark(on){
  document.body.classList.toggle('dark',on);
  $('themebtn').textContent=on?'paper':'dark';
  $('themebtn').setAttribute('aria-pressed',on?'true':'false');
  try{localStorage.setItem('ocd-studio-dark',on?'1':'0');}catch(e){}
  markDirty();
}
function showCockpit(){ // every panel at once — the default, and a real control (All)
  document.body.classList.remove('tabs');
  delete document.body.dataset.view;
  document.querySelectorAll('#viewtabs button').forEach(b=>{
    const all=b.dataset.v==='all';
    b.classList.toggle('on',all);
    b.setAttribute('aria-selected',all?'true':'false');
    b.tabIndex=all?0:-1;});
  try{localStorage.setItem('ocd-studio-view','all');}catch(e){}
  renderAll();markDirty();
}
function setView(v){
  if(v==='all'){showCockpit();return;}
  let tabs=document.body.classList.contains('tabs');
  if(!tabs){ // first pick enables tab mode (cockpit is the default)
    document.body.classList.add('tabs');tabs=true;}
  document.body.dataset.view=v;
  document.querySelectorAll('#viewtabs button').forEach(b=>{
    const on=b.dataset.v===v;b.classList.toggle('on',tabs&&on);
    b.setAttribute('aria-selected',tabs&&on?'true':'false');
    b.tabIndex=tabs&&on?0:-1;});
  try{localStorage.setItem('ocd-studio-view',v);}catch(e){}
  renderAll();markDirty();
}
// selecting text in the editor highlights every ref it names, on PCB and SCH
function edHighlight(){
  if(!S||!S.cur)return;
  const sel=window.getSelection();
  const txt=(sel&&!sel.isCollapsed&&sel.anchorNode&&$('ed').contains(sel.anchorNode))?String(sel):'';
  const refs=new Set(),pins=new Set();
  txt.split(/[^\w.]+/).forEach(t=>{const r=t.split('.')[0];
    if(!S.cur.parts[r])return;
    refs.add(r);
    if(t!==r)pins.add(t);}); // "U1.7" also rings that pin in the schematic
  if(refs.size===edHl.size&&pins.size===edPin.size
     &&[...refs].every(r=>edHl.has(r))&&[...pins].every(p=>edPin.has(p)))return;
  edHl=refs;edPin=pins;markDirty();
}
document.addEventListener('selectionchange',edHighlight);
$('placer').onchange=$('router').onchange=$('fab').onchange=$('silk').onchange=push;
$('chatbtn').onclick=()=>{
  document.body.classList.toggle('chatty');
  const on=document.body.classList.contains('chatty');
  $('chatbtn').classList.toggle('primary',on);
  $('chatbtn').setAttribute('aria-pressed',on?'true':'false');
  if(on)$('ask').focus();
};
(async()=>{await boot();})();
if(location.search.includes('perf')){
setTimeout(()=>{
  const st=S&&S.cur||{};
  const P=CanvasRenderingContext2D.prototype;
  const cnt={stroke:0,fill:0,fillRect:0,beginPath:0,fillText:0};
  const orig={};
  Object.keys(cnt).forEach(k=>{orig[k]=P[k];
    P[k]=function(){cnt[k]++;return orig[k].apply(this,arguments);};});
  drawPCB(st,1);
  Object.keys(cnt).forEach(k=>{P[k]=orig[k];});
  const parts=Object.keys(st.parts||{}).length;
  document.title='DRAW parts='+parts+' traces='+(st.traces||[]).length
    +' strokes='+cnt.stroke+' begins='+cnt.beginPath+' fills='+cnt.fill+' texts='+cnt.fillText;
},2500);}


// file-watch: poll SRC hash; an external edit banners with one-click
// reload (never auto: a keystroke debounce may be in flight, and
// auto-reload would clobber it — the user picks the moment).
let lastHash=null;
async function watch(){try{
  const p=await api('/poll',{});
  if(lastHash===null){lastHash=p.hash;return;}
  if(p.hash===lastHash||$('extbanner'))return;
  if(p.clean)return;  // our own save (or untouched) — nothing external
  lastHash=p.hash;
  const b=document.createElement('button');b.type='button';
  b.id='extbanner';
  b.style.cssText='width:100%;border:0;border-radius:0;background:var(--signal);color:#fff;'
    +'padding:10px 14px;min-height:40px;cursor:pointer;font:600 .9rem var(--sans);text-align:left';
  if(document.body.classList.contains('dark'))b.style.color='#06130d';
  b.textContent='file changed on disk — click to reload (your edits stay in undo)';
  b.onclick=async()=>{const r=await api('/reload',{});setEditor(r.text);applyState(r,false);b.remove();lastHash=null;};
  document.body.prepend(b);
}catch(e){}}
// --- knowledgebase panel: notes + datasheets, the agent's own files -----
let KBDOCS=[];
function kbRow(name,kind,tail){
  const r=document.createElement('div');r.className='kbrow';
  const b=document.createElement('button');b.className='kbname';b.textContent=name;
  const k=document.createElement('span');k.className='kbkind';k.textContent=kind;
  const t=document.createElement('span');t.className='kbparts';t.textContent=tail||'';
  r.append(b,k,t);return {row:r,btn:b};
}
async function kbLoad(){
  const r=await api('/kb/list',{});
  if(r.error){$('kbnote').textContent=r.error;return;}
  KBDOCS=r.docs||[];
  const parts=KBDOCS.reduce((n,d)=>n+(d.parts||[]).length,0);
  $('kbnote').textContent=`${KBDOCS.length} document${KBDOCS.length===1?'':'s'} · `
    +`${parts} part link${parts===1?'':'s'} · `+(r.dir||'kb/');
  const box=$('kblist');box.innerHTML='';
  KBDOCS.forEach(d=>{
    const {row,btn}=kbRow(d.name,d.kind+(d.source?' · from url':''),
                          (d.parts||[]).join(' '));
    btn.title=d.source?('source: '+d.source):d.name;
    btn.onclick=()=>kbOpen(d.name,1);
    box.appendChild(row);
  });
  if(!KBDOCS.length)box.textContent='nothing yet — add a url, or fetch datasheets';
  else if(r.total&&r.total>KBDOCS.length)
    box.appendChild(document.createTextNode('… showing '+KBDOCS.length+' of '+r.total
      +' documents — ask or search to reach the rest'));
  if(r.busy)$('kbstat').textContent='fetching datasheets…';
  else if((r.log||[]).length)$('kbstat').textContent=(r.log||[]).slice(-3).join(' · ');
}
async function kbOpen(doc,start){
  const r=await api('/kb/read',{doc,start:start||1,lines:120});
  if(r.error){$('kbstat').textContent=r.error;return;}
  $('kbview').textContent=`${doc}  lines ${r.start}-${r.end} of ${r.total_lines}\n\n${r.text}`;
}
async function kbGo(semantic,answer){
  const q=$('kbq').value.trim();if(!q){$('kbstat').textContent='type a question first';return;}
  $('kbstat').textContent=answer?'asking the local model…':'searching kb/…';
  const r=await api(semantic?'/kb/ask':'/kb/search',{q,limit:8,answer:!!answer});
  if(r.error){$('kbstat').textContent=r.error;return;}
  const hits=r.passages||r.hits||[];
  $('kbstat').textContent=`${hits.length} hit${hits.length===1?'':'s'}`
    +(r.method?' · '+r.method+(r.model?' · '+r.model:''):'')
    +((r.note&&!r.answer)?' · '+r.note:'');
  const box=$('kblist');box.innerHTML='';
  hits.forEach(h=>{
    const line=h.line!==undefined?h.line:h.start;
    const {row,btn}=kbRow(h.doc||'(no doc)',line!==undefined?'line '+line:'passage',
                          h.score!==undefined?String(h.score):'');
    btn.onclick=()=>kbOpen(h.doc,Math.max(1,(line||1)-3));
    row.title=String(h.text||'').slice(0,400);
    box.appendChild(row);
  });
  if(r.answer)$('kbview').textContent='answer (from kb/ only)\n\n'+r.answer;
  else if(r.answer_error)$('kbview').textContent='(no written answer: '+r.answer_error+')';
}
$('kbask').onclick=()=>kbGo(true,false);
$('kbgrep').onclick=()=>kbGo(false,false);
$('kbans').onclick=()=>kbGo(true,true);
$('kbaddbtn').onclick=async()=>{
  const src=$('kburl').value.trim();if(!src)return;
  $('kbstat').textContent='adding '+src+'…';
  const r=await api('/kb/add',{src});
  if(r.error){$('kbstat').textContent=r.error;return;}
  $('kbstat').textContent=`added ${r.added} (${r.bytes} bytes)`;
  $('kburl').value='';kbLoad();
};
$('kbfetch').onclick=async()=>{
  const r=await api('/kb/fetch',{});
  $('kbstat').textContent=r.error||r.note||'fetch started';
  kbLoad();
};
// preferences: Flux's Knowledge approvals. The file is the store; the
// buttons flip the `# ok` suffix and the agent reads what is approved.
$('kbprefsbtn').onclick=async()=>{
  const box=$('kbprefs'),open=box.style.display!=='none';
  box.style.display=open?'none':'';
  if(!open)kbPrefs();
};
async function kbPrefs(){
  const r=await api('/kb/prefs',{});
  const box=$('kbprefslist');box.innerHTML='';
  if(r.error){$('kbstat').textContent=r.error;return;}
  (r.prefs||[]).forEach(p=>{
    const d=document.createElement('div');d.className='kbrow';
    const b=document.createElement('span');b.className='kbname';
    b.textContent=`when ${p.when} :: ${p.text}`;
    const k=document.createElement('span');k.className='kbkind';
    k.textContent=p.approved?'approved':'pending';
    const t=document.createElement('button');t.textContent=p.approved?'reject':'approve';
    t.title=p.approved?'stop following this':'follow this from now on';
    t.onclick=async()=>{
      const x=await api('/kb/prefs/set',{id:p.id,approved:!p.approved});
      if(x.error)$('kbstat').textContent=x.error;else kbPrefs();};
    d.append(b,k,t);box.appendChild(d);});
  if(!(r.prefs||[]).length)box.textContent='no preferences yet — teach one below';
}
$('kbprefsgo').onclick=async()=>{
  if($('kbprefsgo').disabled)return;$('kbprefsgo').disabled=true;
  try{const r=await api('/kb/prefs/add',{when:$('kbwhen').value,text:$('kbwhat').value});
    if(r.error){$('kbstat').textContent=r.error;return;}
    $('kbwhen').value='';$('kbwhat').value='';kbPrefs();}
  finally{$('kbprefsgo').disabled=false;}
};
$('kbq').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();kbGo(true,false);}});
$('kburl').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();$('kbaddbtn').click();}});
kbLoad();
setInterval(()=>{if($('kbstat').textContent.startsWith('fetching'))kbLoad();},3000);
setInterval(watch,2000);
</script></body></html>
"""


def _sch_state(b: Board) -> dict[str, object]:
    """Schematic geometry for the canvas: same sch_layout() the SVG
    renderer uses, so both pictures always agree. On a dense board the layout
    is ~17s of work to draw a picture nobody can read (5400 boxes across the
    canvas), so it is skipped and the canvas stays empty."""
    if len(b.parts) >= DENSE_PARTS:
        return {"order": [], "px": {}, "rail_y": {}, "top": 70, "W": 0,
                "skipped": "board is dense — the schematic is not laid out"}
    from ocdcircuit.sch import sch_layout
    lay = sch_layout(b)
    return {"order": lay.order, "px": lay.px, "rail_y": lay.rail_y,
            "top": lay.top, "W": lay.W}


def board_state(b: Board, text: str, frames: list[dict[str, object]],
                traces: list[dict[str, object]], cost: float,
                drc: dict[str, object] | DrcReport) -> dict[str, object]:
    from ocdcircuit.geom3d import body_material
    from ocdcircuit.parts import bodies_of, hole_drill, pad_size, pads_of
    from typing import cast
    parts: dict[str, dict[str, object]] = {}
    lib = b._lib()
    # Per-part footprint geometry is ~190s across 5,420 parts, and at that
    # density a pad is a sub-pixel dot: a dense board ships boxes only, so the
    # page opens in seconds. The compact state is flagged for the client.
    compact = len(b.parts) >= DENSE_PARTS
    for ref, p in b.parts.items():
        h3d = 1.0
        mats: list[str] = []
        bds: list[dict[str, object]] = []
        if compact:
            rot = p.rot
            pw, ph = p.wh()
            parts[ref] = {"x": p.x, "y": p.y, "w": pw, "h": ph,
                          "value": p.value, "fp": p.fp, "h3d": h3d,
                          "owner": p.owner or "", "mat": "chip"}
            # `pads`/`bodies` are omitted: empty arrays cost bytes per part
            # and the canvas/3D default them. 5,420 × '[]' was ~100KB.
            continue
        for body in bodies_of(p.fp, lib):
            mat = body_material(p.fp, body)
            mats.append(mat)
            if "box" in body:
                box3 = body["box"]
                assert isinstance(box3, (list, tuple))
                w2, h2, bh = _f(box3[0]), _f(box3[1]), _f(box3[2])
                h3d = max(h3d, bh)
                ats = body.get("at", [(0.0, 0.0)])
                assert isinstance(ats, list)
                for at in ats:
                    assert isinstance(at, (list, tuple))
                    bds.append({"w": w2, "h": h2, "z": 1.6 + _f(body.get("z", 0)),
                                "hgt": bh, "dx": _f(at[0]), "dy": _f(at[1])})
            elif "cyl" in body:
                cyl2 = body["cyl"]
                assert isinstance(cyl2, (list, tuple))
                r, bh = _f(cyl2[0]), _f(cyl2[1])
                h3d = max(h3d, bh)
                bds.append({"w": r * 2, "h": r * 2, "z": 1.6 + _f(body.get("z", 0)),
                            "hgt": bh, "dx": 0.0, "dy": 0.0, "cyl": True})
        # dominant material = tallest body (what you actually see).
        # bodies pre-rotated into board frame (mirrors geom3d.build).
        rot = p.rot
        pw, ph = p.wh()
        # real pads in board frame: center + size (+ axle-swap on 90/270), drill,
        # pin-1 flag — the footprint, not its bounding box.
        pds: list[dict[str, object]] = []
        for pin, (dx, dy) in pads_of(p.fp, lib).items():
            rx, ry = p.rot_xy(dx, dy)
            pwid, phei = pad_size(p.fp, pin, lib)
            if rot in (90, 270):
                pwid, phei = phei, pwid
            pds.append({"x": round(rx, 3), "y": round(ry, 3),
                        "w": pwid, "h": phei, "d": hole_drill(p.fp, pin, lib),
                        "p1": str(pin) == "1"})
        if rot in (90, 270):
            for bd in bds:
                bd["w"], bd["h"] = bd["h"], bd["w"]
                bd["dx"], bd["dy"] = p.rot_xy(cast(float, bd["dx"]),
                                              cast(float, bd["dy"]))
        parts[ref] = {"x": p.x, "y": p.y, "w": pw, "h": ph,
                      "value": p.value, "fp": p.fp, "h3d": h3d,
                      "owner": p.owner or "",
                      "mat": mats[-1] if mats else "chip", "bodies": bds,
                      "pads": pds}
    nets = {n: [f"{r}.{pin}" for r, pin in net.pins] for n, net in b.nets.items()}
    fixed = {str(c["ref"]): True for c in b.constraints if c.get("t") == "fixed"}
    sim_nets: dict[str, float] = {}
    sim_problems: list[str] = []
    if any(c.get("t") == "sim" for c in b.constraints):
        try:
            res = b.simulate()
            raw = res.get("nets", {})
            assert isinstance(raw, dict)
            sim_nets = {str(k): round(float(v), 3) for k, v in raw.items()
                        if isinstance(v, (int, float))}
            from ocdcircuit import sim as _sim
            sim_problems = [str(p) for p in _sim.expect(b)]
        except (ValueError, KeyError, AssertionError):
            sim_nets = {}
    from ocdcircuit.drc import pour_layers as _pours
    from ocdcircuit.export import plane_plots as _plots
    from ocdcircuit.fab import get as _fab_get
    pours = {n: lls for n, lls in _pours(b).items()}
    cuts = {str(ll): [[round(v, 2) for v in r] for r in _plots(b).get(ll, [])]
            for lls in pours.values() for ll in lls}
    edge = float(cast(float, _fab_get(b.fab).get("edge", 0.3)))
    return {"text": text, "parts": parts, "nets": nets, "fixed": fixed,
            "compact": compact,
            "bw": b.width, "bh": b.height, "layers": b.layers,
            "frames": frames,
            "traces": traces, "cost": round(cost, 1), "sim": sim_nets,
            "sim_problems": sim_problems, "pours": pours, "cuts": cuts,
            "edge": edge,
            "errors": _brief(drc["errors"]), "warnings": _brief(drc["warnings"]),
            "fab": drc.get("fab", "jlc"), "silk": 1, "sch": _sch_state(b)}


# --- project files -------------------------------------------------------
# Everything the browser and the agent touch is relative to the project ROOT
# (the directory of the board that is open) and guarded: no absolute paths,
# no .., no symlink escape. Text files only, writes only to .ocd/.toml/.md.
TEXT_EXT = {".ocd", ".toml", ".md", ".txt", ".fp", ".json", ".py", ".csv", ".kicad_mod"}
WRITE_EXT = {".ocd", ".toml", ".md"}
SKIP_DIR = {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache",
            "node_modules", ".venv", "venv", "out", "outputs", ".scratch",
            ".users"}
GITIGNORE_AUTH = ".ocd-users\n.users/\n"
ROOT = _envcfg.studio_root(BASE)
START_DIR = BASE  # the board directory as launched, before any /fs/open


# --- accounts: local users with salted passwords, cookie sessions -----------
# stdlib only (hashlib scrypt + secrets): no new deps. Users live one per line
# in <ROOT>/.ocd-users (name:salt_hex:hash_hex[:display]). Sessions are bearer
# tokens in memory with a TTL — a restart also clears them. Each signup gets
# its own shelf under .users/<name>/; paths under another user's shelf are
# refused (see _abs).
_AUTH_COOKIE = "ocd_user"
_USERS_FILE = ".ocd-users"
_SESSION_TTL = 86400.0  # 24h elapsed (monotonic) from login
_SESSIONS: dict[str, tuple[str, float]] = {}  # token -> (username, expires_mono)
_SESSIONS_MAX = 64  # in-memory cap; restart clears all
_AUTH_HITS: dict[str, list[float]] = {}  # client key -> recent attempt times
_AUTH_HITS_MAX = 1024  # bound spray: one idle key per IP must not grow forever
# ThreadingHTTPServer: every request is its own thread. Sessions, rate-limit
# buckets, and H.* board state are shared — one lock each, not the GIL.
_AUTH_MU = threading.Lock()
_MAX_BODY = 20_000_000  # POST body cap (base64 photo scans need headroom)
_GIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_REQ_USER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ocd_req_user", default=None)


def _atomic_write(path: str, data: str) -> None:
    """Write-to-temp, fsync, rename so a crash mid-write cannot leave a
    half-written users file or board source."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ocd-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _users_path() -> str:
    return os.path.join(ROOT, _USERS_FILE)


def _exc_for_log(exc: BaseException) -> str:
    """`Type: message` with account names scrubbed — stderr and API errors."""
    return f"{type(exc).__name__}: {_path_for_log(exc)}"


def _client_key(handler: object) -> str:
    """Rate-limit key: peer host when available, else a shared bucket."""
    addr = getattr(handler, "client_address", None)
    if isinstance(addr, tuple) and addr:
        return str(addr[0])
    return "local"


def _auth_rate_ok(key: str, limit: int = 30, window: float = 60.0) -> bool:
    """Sliding-window gate for login/signup (brute-force / spray).

    The map is capped so a many-IP spray cannot grow `_AUTH_HITS` forever;
    FIFO-evicts other keys, never the caller.
    """
    now = time.monotonic()
    with _AUTH_MU:
        hits = _AUTH_HITS.setdefault(key, [])
        hits[:] = [t for t in hits if now - t < window]
        if len(hits) >= limit:
            return False
        hits.append(now)
        while len(_AUTH_HITS) > _AUTH_HITS_MAX:
            victim = next(iter(_AUTH_HITS))
            if victim == key:
                _AUTH_HITS[victim] = _AUTH_HITS.pop(victim)  # rotate to end
                if next(iter(_AUTH_HITS)) == key:
                    break
                continue
            _AUTH_HITS.pop(victim, None)
        return True


def _purge_sessions(now: float | None = None) -> None:
    """Drop expired tokens so they cannot steal slots from live sessions.
    Caller must hold `_AUTH_MU`."""
    t = time.monotonic() if now is None else now
    for tok in [k for k, (_n, exp) in _SESSIONS.items() if t > exp]:
        _SESSIONS.pop(tok, None)


def _norm_display(disp: str) -> str:
    """NFC so macOS NFD paste and NFC typing land as one spelling in the
    users file (e.g. caf\u00e9 vs cafe\\u0301)."""
    import unicodedata
    return unicodedata.normalize("NFC", disp)


def _ok_display(disp: str) -> bool:
    """Display names must not break the colon-separated users file or hide
    identity with format/control chars (ZWSP, bidi marks, etc.).
    Caller passes NFC text (see `_norm_display`)."""
    import unicodedata
    if not disp or len(disp) > 40:
        return False
    if ":" in disp:
        return False
    for c in disp:
        if ord(c) < 32 or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp"):
            return False
    return True


def _ok_username(name: str) -> bool:
    """Account ids are ASCII [A-Za-z0-9_-] only — matches the signup error
    text and keeps `.users/<name>/` free of NFC/NFD and non-ASCII alnum traps."""
    if not name or len(name) > 32:
        return False
    return all(c.isascii() and (c.isalnum() or c in "_-") for c in name)


def _read_users() -> dict[str, tuple[str, str, str]]:
    """name -> (salt_hex, hash_hex, display). Missing file = no accounts yet.
    display is a 4th colon field; old 3-field lines read as display=name.
    Other OSError (permission, EISDIR, I/O) propagates: treating those as
    empty used to flip needs_setup and let signup wipe the real roster."""
    out: dict[str, tuple[str, str, str]] = {}
    try:
        with open(_users_path(), encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) >= 3 and parts[0]:
                    disp = parts[3] if len(parts) > 3 and parts[3] else parts[0]
                    out[parts[0]] = (parts[1], parts[2], _norm_display(disp))
    except FileNotFoundError:
        return out
    return out


def _set_display(name: str, display: str) -> None:
    """Rewrite the user's line with a new display name (validated by caller)."""
    display = _norm_display(display)
    if not _ok_display(display):
        raise ValueError("display name can't contain control chars or ':'")
    with _AUTH_MU:
        try:
            lines = open(_users_path(), encoding="utf-8").read().splitlines()
        except FileNotFoundError:
            raise ValueError("no accounts yet") from None
        out = []
        found = False
        for ln in lines:
            parts = ln.split(":")
            if parts and parts[0] == name and len(parts) >= 3:
                out.append(":".join([parts[0], parts[1], parts[2], display]))
                found = True
            elif ln.strip():
                out.append(ln)
        if not found:
            raise ValueError("no such account")
        _atomic_write(_users_path(), "\n".join(out) + "\n")


def _write_user(name: str, password: str) -> None:
    import hashlib
    import secrets
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    line = f"{name}:{salt.hex()}:{digest.hex()}\n"
    with _AUTH_MU:
        path = _users_path()
        try:
            cur = open(path, encoding="utf-8").read()
        except FileNotFoundError:
            cur = ""
        # Refuse a duplicate under the same lock that writes — two concurrent
        # signups for the same name must not both land. Other OSError
        # (unreadable file) must not fall through to an empty cur + replace:
        # that wiped every existing account.
        for ln in cur.splitlines():
            if ln.split(":", 1)[0] == name:
                raise ValueError(f"{name} exists — log in instead")
        if cur and not cur.endswith("\n"):
            cur += "\n"
        _atomic_write(path, cur + line)
    # secrets must never be committed: keep them out of git on first signup
    try:
        gi = os.path.join(ROOT, ".gitignore")
        have = open(gi, encoding="utf-8").read() if os.path.isfile(gi) else ""
        if _USERS_FILE not in have:
            with open(gi, "a", encoding="utf-8") as f:
                if have and not have.endswith("\n"):
                    f.write("\n")
                f.write(GITIGNORE_AUTH)
    except OSError as e:
        print(f"studio: could not append {_USERS_FILE} to .gitignore: {e}",
              file=sys.stderr)


def _check_user(name: str, password: str) -> bool:
    import hashlib
    import hmac
    users = _read_users()
    if name not in users:
        return False
    salt_hex, want, _disp = users[name]
    try:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=16384, r=8, p=1)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), want)


def _new_session(name: str) -> str:
    import secrets
    tok = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _AUTH_MU:
        _purge_sessions(now)  # expired must not crowd out live logins
        _SESSIONS[tok] = (name, now + _SESSION_TTL)
        while len(_SESSIONS) > _SESSIONS_MAX:
            _SESSIONS.pop(next(iter(_SESSIONS)))
    return tok


def _authed(headers: object) -> str | None:
    """Username for a valid, unexpired session cookie, else None."""
    get = getattr(headers, "get", None)
    cookie = get("Cookie", "") if get else ""
    now = time.monotonic()
    with _AUTH_MU:
        for chunk in str(cookie).split(";"):
            k, _, v = chunk.strip().partition("=")
            tok = v.strip()
            if k.strip() != _AUTH_COOKIE or tok not in _SESSIONS:
                continue
            name, exp = _SESSIONS[tok]
            if now > exp:
                _SESSIONS.pop(tok, None)
                return None
            return name
    return None


def _user_dir(name: str) -> str:
    """Per-user shelf: <ROOT>/.users/<name>/ for boards (hidden from _tree)."""
    d = os.path.join(ROOT, ".users", name)
    os.makedirs(d, exist_ok=True)
    return d


def _shelf_owner(as_rel: str) -> str | None:
    """If `as_rel` is under `.users/<name>/…`, return that name; else None."""
    rel = as_rel.replace("\\", "/")
    if rel == ".users" or rel.startswith(".users/"):
        parts = rel.split("/")
        return parts[1] if len(parts) > 1 and parts[1] else ""
    return None


def _ensure_shelf_rel(as_rel: str, label: str | None = None) -> None:
    """Raise if `as_rel` (ROOT-relative) is another account's private shelf."""
    owner = _shelf_owner(as_rel)
    if owner is None:
        return
    who = _REQ_USER.get()
    if not who or owner != who:
        # scrub the other account's name — the peer only needs "not yours"
        shown = label if label is not None else _path_for_log(as_rel)
        raise ValueError(f"{shown}: outside your shelf")


def _ensure_open_board() -> None:
    """Refuse using the process-global SRC when it sits on another shelf.

    Studio keeps one open board (SRC/BASE) for the process: collab on a
    launch/template board is shared on purpose, but a `/?board=` shelf open
    must not let the next authed peer /init, /export, or /collab/* that file.
    """
    root = os.path.realpath(ROOT)
    rp = os.path.realpath(SRC)
    if rp != root and not rp.startswith(root + os.sep):
        return
    as_rel = os.path.relpath(rp, root).replace(os.sep, "/")
    _ensure_shelf_rel(as_rel)


def _shelf_meta_path(path: str) -> bool:
    """Auth + shelf listing/create: do not require access to the open board."""
    return path.startswith("/auth/") or path == "/shelf" or path.startswith("/shelf/")


STARTER_OCD = """board {name} 40x30
part R1 R0805 10k
part C1 C0805 100n
net N: R1.2 C1.1
net GND: R1.1 C1.2
"""


def _shelf(name: str) -> list[dict[str, str]]:
    """The user's boards: name, size, modified, blurb, parts, nets.

    Blurb = first `#` comment in the file (the author's own one-liner);
    counts come from a text scan (no Board build — Context journals every
    part and net, ~1.5s on a dense board, and the shelf lists on every
    login). Missing file facts stay empty, never errors."""
    import time
    d = _user_dir(name)
    rows: list[dict[str, str]] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".ocd"):
            continue
        full = os.path.join(d, fn)
        try:
            st = os.stat(full)
            blurb, np_, nn = "", "0", "0"
            with open(full, encoding="utf-8", errors="replace") as f:
                seen_p: set[str] = set()
                seen_n: set[str] = set()
                for i, line in enumerate(f):
                    if i > 400:
                        break  # header facts live up top; don't read megabytes
                    s = line.strip()
                    if s.startswith("#") and not blurb:
                        blurb = s.lstrip("# ").strip()[:140]
                    elif s.startswith("part "):
                        seen_p.add(s.split(None, 2)[1] if len(s.split()) > 1 else "")
                        np_ = str(len(seen_p))
                    elif s.startswith(("net ", "net:", "VCC ", "GND ")) or " :: " in s:
                        nn = str(int(nn) + 1)
            rows.append({"name": fn, "bytes": str(st.st_size),
                         # UTC instant: host TZ (or make's TZ=UTC) must not
                         # shift the shelf label by hours across machines.
                         "mtime": time.strftime("%Y-%m-%d %H:%M UTC",
                                                time.gmtime(st.st_mtime)),
                         "blurb": blurb, "parts": np_, "nets": nn})
        except OSError:
            continue
    return rows


def _templates() -> list[dict[str, str]]:
    """Starter cards from boards/*.ocd (read-only; opening one copies it)."""
    out: list[dict[str, str]] = []
    bdir = os.path.join(HERE, "boards")
    try:
        names = sorted(os.listdir(bdir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".ocd"):
            continue
        blurb = ""
        try:
            with open(os.path.join(bdir, fn), encoding="utf-8",
                      errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 30:
                        break
                    s = line.strip()
                    if s.startswith("#"):
                        blurb = s.lstrip("# ").strip()[:140]
                        break
        except OSError:
            continue
        out.append({"name": fn, "blurb": blurb})
    return out


def _rel(path: object) -> str:
    """Normalise a client-supplied relative path. `..` is allowed *here* and
    resolved against the project root by _abs, which then re-checks the
    result: the browser may walk up inside the project, never out of it."""
    p = str(path or ".").strip().replace("\\", "/").lstrip("/")
    if p in ("", "."):
        return "."
    return "/".join(q for q in p.split("/") if q not in ("", "."))


def _abs(path: object, *, must_exist: bool = False, near: str | None = None) -> str:
    """Resolve a client path. A bare name is tried next to the open board
    first (a sibling fetch), then at the project ROOT. Every candidate is
    realpath'd and must land inside ROOT, so a symlink out of the project is
    refused rather than followed. The ordering matters: with the root first,
    `blinky_555.ocd` cannot be reached from a board opened in a subdirectory.
    Paths under `.users/` are private to that account; `.ocd-users` is never
    readable via this resolver."""
    rel = _rel(path)
    root = os.path.realpath(ROOT)
    # board's directory, then the directory the studio started in, then ROOT.
    # ROOT may be wider than the start dir (OCD_ROOT), so it is last; a board
    # under boards/ is unreachable from a sibling directory without the first.
    seeds = [s for s in (near, START_DIR) if s]
    seeds.append(ROOT)
    seen: set[str] = set()
    inside = None
    blocked = False
    shelf_blocked = False
    for s in seeds:
        base = os.path.realpath(s)
        if base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        rp = os.path.realpath(os.path.join(base, rel))
        if rp != root and not rp.startswith(root + os.sep):
            blocked = True  # a traversal: say so, do not call it "missing"
            continue
        # account file + other users' shelves are never a valid client target
        as_rel = os.path.relpath(rp, root).replace(os.sep, "/")
        if as_rel == _USERS_FILE or as_rel.startswith(_USERS_FILE + "/"):
            raise ValueError(f"{rel}: outside the project root")
        owner = _shelf_owner(as_rel)
        if owner is not None:
            who = _REQ_USER.get()
            if not who or owner != who:
                # wrong shelf for this seed (often: open board is under
                # .users/alice/ so bare names tried there first) — keep looking
                # under START_DIR/ROOT instead of hard-failing the request.
                shelf_blocked = True
                continue
        inside = rp
        if not must_exist or os.path.exists(rp):
            return rp
    if inside is None and shelf_blocked and not blocked:
        raise ValueError(f"{rel}: outside your shelf")
    if inside is None and blocked:
        raise ValueError(f"{rel}: outside the project root")
    raise ValueError(f"{rel}: no such file")


def _tree(rel: str = ".", depth: int = 0) -> list[dict[str, str]]:
    """Directory listing, one level. Paths are relative to ROOT, so the client
    can call /fs/open on them without knowing where the root is."""
    out: list[dict[str, str]] = []
    if depth > 3:
        return out
    d = _abs(rel, must_exist=True)
    if not os.path.isdir(d):
        raise ValueError(f"{rel}: not a directory")
    for name in sorted(os.listdir(d)):
        if name.startswith(".") or name in SKIP_DIR:
            continue
        p = os.path.join(d, name)
        r = os.path.relpath(p, ROOT).replace(os.sep, "/")
        if os.path.isdir(p):
            out.append({"name": name, "path": r, "kind": "dir"})
        elif os.path.splitext(name)[1].lower() in TEXT_EXT:
            out.append({"name": name, "path": r, "kind": "file",
                        "bytes": str(os.path.getsize(p))})
    return out


def _read(rel: object) -> str:
    full = _abs(rel, must_exist=True)
    if os.path.splitext(full)[1].lower() not in TEXT_EXT:
        raise ValueError(f"{rel}: not a text file")
    if os.path.getsize(full) > 2_000_000:
        raise ValueError(f"{rel}: too large to edit")
    try:
        return open(full, encoding="utf-8").read()
    except UnicodeDecodeError as e:
        raise ValueError(f"{rel}: not utf-8 text") from e


# ponytail: extension sniffing caps at text-size uploads (2MB); binaries
# (step/stp) ride base64 and land in fp/ — importers read from disk.
IMPORT_EXTS = {".fp", ".kicad_mod", ".lib", ".lbr", ".intlib", ".schdoc",
               ".edf", ".json", ".brd", ".pcb", ".sym", ".step", ".stp"}


def _import_upload(name: str, data: str) -> dict[str, object]:
    """Base64 file upload → fp/ → import_fp/import_sym by extension.

    Footprints land in fp/ (the board's footprint dir, resolved by _lib);
    symbols in sym/; boards (.kicad_pcb/.brd/.pcb sniffed as pcb) open in
    the editor. Returns {note[, text]} — text only when a board was made."""
    import base64
    import binascii
    # ASCII-only: bare isalnum() keeps NFC/NFD lookalikes (caf\u00e9 vs
    # cafe\\u0301) as distinct paths that collide on APFS.
    fn = "".join(c for c in os.path.basename(name)
                 if c.isascii() and (c.isalnum() or c in "_-."))[:80]
    ext = os.path.splitext(fn)[1].lower()
    if not fn or ext not in IMPORT_EXTS:
        return {"error": f"{name or '(no name)'}: import wants "
                + ", ".join(sorted(IMPORT_EXTS))}
    try:
        raw = base64.b64decode(data)
    except (ValueError, binascii.Error) as e:
        return {"error": f"bad upload (not base64): {e}"}
    if len(raw) > 2_000_000:
        return {"error": f"{fn}: over 2MB"}
    sub = "sym" if ext == ".sym" else "fp"
    dest = os.path.join(BASE, sub)
    os.makedirs(dest, exist_ok=True)
    full = os.path.join(dest, fn)
    if os.path.exists(full):
        return {"error": f"{fn} already imported"}
    with open(full, "wb") as f:
        f.write(raw)
    try:
        b = agent.loads(H.src_text, base=BASE)
        if ext in (".brd", ".pcb") or fn.endswith(".kicad_pcb"):
            out = b.import_fp(None, path=full)
            names = [str(k) for k in out if isinstance(out, dict)] or ["board"]
            return {"note": f"imported {fn}: board {', '.join(names[:3])}",
                    "text": agent.dumps(b)}
        elif ext == ".sym":
            out = b.import_sym(path=full)
            return {"note": f"imported {fn}: "
                    + ", ".join(str(k) for k in out)[:200]}
        out = b.import_fp(None, path=full)
        return {"note": f"imported {fn}: "
                + ", ".join(str(k) for k in out)[:200]}
    except (ValueError, KeyError, AssertionError, OSError) as e:
        try:
            os.remove(full)  # a failed import leaves no file behind
        except OSError:
            pass
        return {"error": _exc_for_log(e)}


def _git(*args: str, timeout: float = 20.0) -> str:
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ValueError(f"git {' '.join(args)}: {e}") from e
    if r.returncode:
        raise ValueError((r.stderr or r.stdout).strip()[:300] or "git failed")
    return r.stdout


def _git_log(n: int = 30, path: object = None) -> list[dict[str, str]]:
    """Commits touching the open board (or the whole project when asked).
    %x1f unit separator so a commit subject with spaces survives."""
    args = ["log", f"-{n}", "--date=short",
            "--pretty=format:%h%x1f%ad%x1f%an%x1f%s"]
    target = _rel(path) if path is not None else "."
    if target != ".":
        args += ["--", target]
    out = _git(*args)
    rows = []
    for line in out.splitlines():
        f = line.split("\x1f")
        if len(f) == 4:
            rows.append({"hash": f[0], "date": f[1], "who": f[2], "subject": f[3]})
    return rows


def _git_status() -> dict[str, object]:
    try:
        _git("rev-parse", "--is-inside-work-tree")
    except ValueError:
        return {"repo": False, "branch": "", "files": []}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    rel = _rel(os.path.relpath(SRC, ROOT))
    porcelain = _git("status", "--porcelain", "--", rel).strip()
    dirty = [ln for ln in porcelain.splitlines() if ln.strip()]
    return {"repo": True, "branch": branch, "files": dirty,
            "board": rel, "dirty": bool(dirty)}


def _unified(a: str, b: str, name: str, limit: int = 160) -> str:
    """Unified diff of two texts. Real difflib, capped so a full rewrite
    cannot flood the panel or the model's context."""
    import difflib
    lines = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(), fromfile=f"a/{name}", tofile=f"b/{name}",
        lineterm="", n=2))
    if len(lines) > limit:
        return "\n".join(lines[:limit] + [f"… {len(lines) - limit} more lines"])
    return "\n".join(lines) or "(no textual change)"


# A board of a few thousand parts can report millions of DRC failures (every
# unplaced pin is an ERC error). Serialising that list is what actually kills
# the page: it reached 920 MB on discrete6502. The UI shows a head; the count
# stays honest so nothing is hidden.
MAX_ISSUES = 60
# A dense board is a different product: routing alone is seconds, so the
# studio loads it from the file's own positions and trims the rest.
DENSE_PARTS = 1200
MAX_SEGS = 60000  # traces shipped to the canvas (discrete6502 routes ~26k)


def _brief(issues: object) -> list[str]:
    """First MAX_ISSUES of a (possibly enormous) DRC list, plus one summary
    line when it was cut."""
    items = [str(x) for x in cast(list[object], issues)]
    if len(items) <= MAX_ISSUES:
        return items
    return items[:MAX_ISSUES] + [
        f"… {len(items) - MAX_ISSUES} more of {len(items)} suppressed "
        "(open the CLI report for the full list)"]


def _board_digest() -> str:
    """What the model needs to know about the open board without asking for it:
    header, parts, nets. Keeps a weak model from hallucinating refs."""
    try:
        b = agent.loads(H.src_text, base=BASE)
    except (agent.ParseError, ValueError, KeyError, AssertionError, OSError):
        return f"(the current {os.path.basename(SRC)} does not parse)"
    parts = ", ".join(f"{r}={p.fp}" + (f"({p.value})" if p.value else "")
                      for r, p in sorted(b.parts.items()))
    nets = "; ".join(f"{n}: " + " ".join(f"{r}.{pin}" for r, pin in net.pins)
                     for n, net in sorted(b.nets.items()))
    # a 5k-part board digest can eat the whole context window by itself
    def _trim(label: str, s: str, n: int = 12_000) -> str:
        if len(s) <= n:
            return s
        return s[:n] + f" … ({label} truncated, {len(s) - n} more chars)"
    # account dir names identify people — do not ship them to the LLM host
    return (f"open file: {_path_for_log(os.path.relpath(SRC, ROOT))}  "
            f"board {b.name} {b.width:g}x{b.height:g} {b.layers}L\n"
            f"parts: {_trim('parts', parts)}\nnets: {_trim('nets', nets)}")


# --- knowledgebase: kb/ beside the board, shared with the agent over MCP ---
KB_LIST_LIMIT = 200          # rows the panel renders; search covers the rest
# cordis-boundary: the fetch below is an outside-context emission (§6.1) — it
# downloads vendor PDFs and shells out to the CLI, and a download cannot be
# un-emitted. Compensate by deleting what `kb/sources.tsv` names (kb add/fetch
# record every file it wrote there). The daemon thread is a process resource of
# this shell, like the request loop itself: it exits with the process.


def _kb() -> object:
    """The open board's knowledgebase. KB is directory-bound; the parts map is
    what links a doc back to the refs it covers. Scanning the source for that
    (ref/lcsc/mpn) is ~1.5s cheaper than building a Board on a 5k-part design —
    Context journals every part and net — and the panel asks per interaction on
    a single-threaded server. Cached per revision."""
    from ocdcircuit import kb as _kbmod
    if H.kb_parts is None or H.kb_parts[0] != H.rev:
        H.kb_parts = (H.rev, _kbmod.parts_map(H.src_text))
    return _kbmod.KB(BASE, parts=H.kb_parts[1])


def _kb_list() -> dict[str, object]:
    """Panel listing, bounded: a kb with 1000+ documents would otherwise ship
    every row (99kB of JSON and 1000 DOM nodes) on each poll."""
    k = _kb()
    from ocdcircuit.kb import KB
    assert isinstance(k, KB)
    with H._mu:
        busy, log = H.kb_busy, list(H.kb_log)
    return {"dir": k.dir, "docs": k.docs(limit=KB_LIST_LIMIT), "total": k.count(),
            "limit": KB_LIST_LIMIT, "busy": busy, "log": log}


def _kb_fetch_start() -> dict[str, object]:
    """Fetch out of process: `ocd kb fetch` already does exactly this job, and
    the panel polls /kb/list while it runs.

    Why a subprocess and not a thread: this server is single-threaded, the
    Board it fetches from is ~2.8s of Context journaling on a 5420-part design,
    and CPython's GIL hands that CPU-bound loop the interpreter in 5ms slices —
    measured UI stalls of 0.45-0.64s per request while a worker thread parsed
    it, versus 1.5ms flat with the work in another process."""
    import subprocess
    with H._mu:
        if H.kb_busy:
            return {"started": False, "note": "already fetching",
                    "log": list(H.kb_log)}
        if not os.path.isfile(SRC):
            return {"started": False, "error": f"no such board file: {SRC}"}
        H.kb_busy = True
        H.kb_log = ["fetching datasheets…"]
        src = SRC

    def work() -> None:
        p: subprocess.Popen[str] | None = None
        try:
            # cwd/PYTHONPATH point at the checkout, not the board: ROOT is the
            # project the board lives in, which need not be this repo.
            p = subprocess.Popen([sys.executable, "-m", "apps.ocd", "kb", "fetch", src],
                                 cwd=HERE, env={**os.environ, "PYTHONPATH": HERE},
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            assert p.stdout is not None
            for line in p.stdout:  # the CLI's lines ARE the progress log
                line = line.strip()
                if line:
                    with H._mu:
                        H.kb_log.append(line)
                        H.kb_log[:] = H.kb_log[-12:]
            code = p.wait()
            with H._mu:
                H.kb_log.append(f"done — exit {code}")
        except Exception as e:  # noqa: BLE001 — a worker thread must not die silent
            with H._mu:
                H.kb_log.append(f"error: {e}")
        finally:
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            with H._mu:
                H.kb_busy = False

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "note": "fetching datasheets — the list fills in as they land"}



class H(http.server.BaseHTTPRequestHandler):
    # One RLock for board buffer / hist / props / kb log. ThreadingHTTPServer
    # runs each request on its own thread; compound updates (rev++, hist append,
    # save) must not interleave. RLock: open_file → commit nests.
    _mu = threading.RLock()
    src_text: str = ""
    # git-style text history: every good build commits; undo/redo check out.
    # text-level (not Context undo — each build parses fresh). Cap 100.
    hist: list[str] = []
    redo: list[str] = []
    # last text WE wrote to SRC (/poll tells them apart: disk ==
    # saved means our save or untouched; anything else is external).
    saved_text: str = ""
    # The file H.src_text came from. A /build may carry text for a board other
    # than the open one; writing that text to SRC once destroyed a 298 KB
    # board (discrete6502.ocd became 1.6 KB of a different board). Save only
    # when the text and the destination are the same board.
    save_target: str = ""
    # the open project root (moves when another board is opened) and the
    # chat transcript (bounded; cleared when the open file changes).
    root: str = BASE
    chat: list[dict[str, str]] = []
    # proposals the panel is offering, each fingerprinted with the board
    # revision it was made against, so /chat/apply cannot write a proposal
    # into a board that has moved on since (and a batch is not replayable).
    props: list[dict[str, object]] = []
    rev: int = 0  # bumps whenever the open board's text changes
    # kb/ panel: beside the rest of the shell's state, not in module globals
    # next to it. One owner for the parts cache and the fetch log the worker
    # thread appends to.
    kb_parts: tuple[int, dict[str, dict[str, str]]] | None = None  # (rev, parts)
    kb_log: list[str] = []
    kb_busy: bool = False
    # One LLM turn at a time: a double Enter / retry must not stack spend.
    chat_busy: bool = False

    @staticmethod
    def open_file(path: object) -> None:
        """Switch the open board: SRC/BASE are module globals (every route
        resolves `use` includes against BASE), so set both and re-init.
        ROOT — the project the browser and the agent may touch — stays put:
        it is the directory the studio was started in, and switching boards
        inside it (including to a sibling project) is the point."""
        with H._mu:
            g = globals()
            full = _abs(path, must_exist=True, near=BASE)
            if os.path.splitext(full)[1].lower() != ".ocd":
                raise ValueError(f"{path}: only .ocd boards can be opened")
            g["SRC"] = full
            g["BASE"] = os.path.dirname(full)
            H.src_text = _read(os.path.relpath(full, ROOT))
            H.save_target = SRC  # this text is this board's
            H.hist = [H.src_text]
            H.redo = []
            H.chat = []
            H.props = []
            H.rev += 1  # a different board entirely
            H.saved_text = ""  # force the next save; /poll compares against it

    @staticmethod
    def _apply(path: str, text: str) -> dict[str, object]:
        """Validate then persist a proposal: parse, place, route, DRC. A bad
        proposal changes nothing — the file on disk keeps the last good build.
        Returns {path, state} where state is None for a non-board file."""
        full = _abs(path, near=BASE)
        rel = os.path.relpath(full, ROOT)
        if os.path.splitext(full)[1].lower() not in WRITE_EXT:
            raise ValueError(f"{rel}: refusing that extension (writable: "
                             + ", ".join(sorted(WRITE_EXT)) + ")")
        if rel != os.path.relpath(SRC, ROOT):
            # a sibling file: a sibling .ocd is parsed in place (a broken module
            # is never written), a note or manifest is just text. The client
            # re-inits against the open board either way.
            if os.path.splitext(full)[1].lower() == ".ocd":
                agent.loads(text, base=os.path.dirname(full))
            with open(full, "w", encoding="utf-8") as f:
                f.write(text)
            return {"path": rel, "state": None}
        st = H._build(text, False)  # raises on a parse error: nothing written
        H.src_text = str(st["text"])
        H.commit(H.src_text)  # bumps the revision: earlier proposals go stale
        out: dict[str, object] = {"path": rel, "state": st}
        err = H.save()
        if err:
            out["save_error"] = err
        return out

    @staticmethod
    def _stage(files: dict[str, str]) -> tuple[list[dict[str, object]], list[str]]:
        """Diff every proposed file and queue it for apply/reject. Returns
        (proposals, refused) — a path outside the project is refused here,
        not silently dropped. Each proposal carries the board revision it was
        made against."""
        props: list[dict[str, object]] = []
        refused: list[str] = []
        for path, text in files.items():
            try:
                full = _abs(path, near=BASE)
                rel = os.path.relpath(full, ROOT)
                old = _read(rel) if os.path.exists(full) else ""
            except ValueError as e:
                refused.append(_path_for_log(e))
                continue
            props.append({"id": rel, "path": rel, "text": text,
                          "diff": _unified(old, text, rel), "rev": H.rev})
        # Cap like hist/chat: proposals carry full file texts and unbounded
        # growth across a long session would pin megabytes in RAM.
        H.props = (props + [p for p in H.props if str(p["id"]) not in
                            {str(q["id"]) for q in props}])[:20]
        return props, refused

    @staticmethod
    def ask(message: str, auto: bool) -> dict[str, object]:
        """One chat turn. The model may answer or propose file edits; each
        proposal is diffed, and auto-applied only when it builds DRC-clean."""
        from ocdcircuit import llm as _llm
        with H._mu:
            if H.chat_busy:
                return {"error": "already thinking — wait for the current reply"}
            H.chat_busy = True
        try:
            H.chat.append({"role": "user", "content": message})
            H.chat = H.chat[-24:]
            ctx = _board_digest()
            tools = {"fs.list": lambda p, _b: "\n".join(
                         f"{e['name']}{'/' if e['kind'] == 'dir' else ''}"
                         for e in _tree(p or ".")),
                     "fs.read": lambda p, _b: _read(p)}
            try:
                from ocdcircuit import kb as _kbmod
                _kb = _kbmod.KB(BASE, parts=_kbmod.parts_map(H.src_text))
                _prefs = _kb.prefs_approved()
            except (ValueError, OSError):
                _prefs = ""
            sys = "Current board:\n" + ctx + ("\n\n" + _prefs if _prefs else "")
            msgs = ([{"role": "system", "content": sys}] if ctx else []) + H.chat
            try:
                out = _llm.run(msgs, tools)
            except _llm.LLMError as e:
                H.chat.pop()  # do not keep a turn the model never saw
                return {"error": str(e)}
            H.chat.append({"role": "assistant", "content": str(out["reply"])})
            H.chat = H.chat[-24:]
            res: dict[str, object] = {"reply": out["reply"], "log": out["log"],
                                      "applied": False}
            files = cast(dict[str, str], out["files"])
            if not files:
                return res
            props, refused = H._stage(files)
            res["proposals"] = props
            if refused:
                res["error"] = "; ".join(refused)
            if not props:
                return res
            if not auto:
                return res
            # auto: apply in order, stopping at the first proposal that does not
            # build — later ones were written against a tree this one has changed
            for i, p in enumerate(props):
                one = H.apply_one(str(p["id"]))
                st = cast(dict[str, object] | None, one.get("state"))
                errs = list(cast(list[object], st["errors"])) if st else []
                if one.get("error") or errs:
                    res["applied"] = False
                    res["error"] = str(one.get("error") or "; ".join(
                        str(e) for e in errs[:3]))
                    res["note"] = (f"applied {i} of {len(props)} proposed files; "
                                   "review the rest by hand")
                    res["proposals"] = [q for q in H.props
                                        if str(q["id"]) in {str(r["id"]) for r in props[i:]}]
                    return res
                if st:
                    res["state"] = st
            res["applied"] = True
            res["proposals"] = []
            for p in props:  # each applied file leaves the queue
                H.props = [q for q in H.props if q["id"] != p["id"]]
            return res
        finally:
            with H._mu:
                H.chat_busy = False

    @staticmethod
    def apply_one(pid: str) -> dict[str, object]:
        """Apply one queued proposal by id, refusing one made against an
        older revision of the open board (the text the model read is gone)."""
        with H._mu:
            prop = next((p for p in H.props if str(p["id"]) == pid), None)
            if prop is None:
                return {"error": f"{pid}: no such proposal (ask again)"}
            if int(cast(int, prop["rev"])) != H.rev:
                return {"error": f"{pid}: the board changed since this proposal "
                                 "was made — ask again or apply it by hand"}
            path, text = str(prop["path"]), str(prop["text"])
        out = H._apply(path, text)
        with H._mu:
            H.props = [p for p in H.props if str(p["id"]) != pid]
        return out

    @staticmethod
    def _disk() -> str | None:
        """Board text on disk, or None when the file cannot be read (missing
        or I/O error). Callers must not treat None as empty content — that
        made /poll report a false external edit and /reload a no-op."""
        try:
            from ocdcircuit.util import read_text
            return read_text(SRC)
        except OSError as e:
            print(f"studio: cannot read {_path_for_log(SRC)}: "
                  f"{_path_for_log(e)}", file=sys.stderr)
            return None

    @staticmethod
    def commit(text: str) -> None:
        with H._mu:
            if not H.hist or H.hist[-1] != text:
                H.hist.append(text)
                H.hist = H.hist[-100:]
                H.rev += 1  # real text change: proposals against it are stale
            H.redo.clear()

    # A save must never destroy a board. The studio holds one text buffer but
    # serves any board, and a /build carrying board B once wrote B over board A
    # (discrete6502.ocd: 298 KB -> 1.6 KB). Two guards, both cheap.
    SHRINK = 0.5  # refuse a rewrite that drops more than half the file

    @staticmethod
    def _room_key(q: dict[str, list[str]] | None = None) -> str:
        """Room identity = the open board's path relative to ROOT (default),
        or a ?board= name resolved inside the project (the SSE stream names
        it; POST bodies carry it too). Paths are re-checked by _abs, so a
        traversal is a loud error, not a second room."""
        if q:
            want = (q.get("board") or [""])[0]
            if want:
                full = _abs(want, must_exist=False, near=BASE)
                return os.path.relpath(full, ROOT)
        return os.path.relpath(SRC, ROOT)

    @staticmethod
    def _room_adopt(key: str, text: str, by: str,
                    rev: int | None = None) -> tuple[int, str, str]:
        """Server-built text (build/solve/undo/pick) lands in the room — but
        only for the open board's room: shelf opens just switch SRC, so a
        stale response for another board must not broadcast into this one.
        When `rev` is set, cas_set_text refuses a lost race after validate."""
        from ocdcircuit import collab as _collab
        with H._mu:
            if key != os.path.relpath(SRC, ROOT):
                snap = _collab.get_room(key, text).snapshot()
                return (int(cast(int, snap["rev"])), "other board",
                        str(snap["text"]))
            if rev is None:
                H.src_text = text
                new_rev = _collab.get_room(key, H.src_text).set_text(text, by)
                return (new_rev, "adopted", text)
            room = _collab.get_room(key, H.src_text)
            ok, new_rev, cur = room.cas_set_text(rev, text, by)
            if not ok:
                return (new_rev, "stale", cur)
            H.src_text = text
            return (new_rev, "adopted", text)

    @staticmethod
    def save() -> str | None:
        """Persist the .ocd source of truth to disk (edits are real).
        Returns None on success, or a short reason when the write was refused
        or failed — callers must surface that so a successful build is not
        mistaken for a durable save."""
        with H._mu:
            text = H.src_text if H.src_text.endswith("\n") else H.src_text + "\n"
            if H.save_target and os.path.abspath(H.save_target) != os.path.abspath(SRC):
                msg = (f"refusing to save to {_path_for_log(SRC)}: "
                       f"the buffer belongs to {_path_for_log(H.save_target)}")
                print(f"studio: {msg}", file=sys.stderr)
                return msg
            path = SRC
            try:
                old = os.path.getsize(path)
            except OSError:
                old = 0
            if old and len(text) < old * H.SHRINK:
                msg = (f"refusing to write {len(text)} bytes over {old} bytes "
                       f"at {_path_for_log(path)} (wrong board?)")
                print(f"studio: {msg}", file=sys.stderr)
                return msg
            try:
                _atomic_write(path, text)
                H.saved_text = H.src_text
                H.save_target = path
            except OSError as e:
                msg = (f"save failed: {_path_for_log(path)}: "
                       f"{_path_for_log(e)}")
                print(f"studio: {msg}", file=sys.stderr)
                return msg
            return None

    def _secure_headers(self) -> None:
        """Baseline browser hardening; CSP is frame-ancestors only so the
        inline-script studio keeps working."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

    def _accepts_gzip(self) -> bool:
        raw = (self.headers.get("Accept-Encoding") or "").lower()
        for part in raw.split(","):
            token, _, params = part.strip().partition(";")
            if token.strip() != "gzip":
                continue
            q = 1.0
            for p in params.split(";"):
                p = p.strip()
                if p.startswith("q="):
                    try:
                        q = float(p[2:])
                    except ValueError:
                        q = 0.0
            return q > 0.0
        return False

    def _write_bytes(self, status: int, body: bytes, content_type: str,
                     *, doc: bool = False, cookie: str | None = None) -> None:
        """Complete response. Documents get an ETag + no-cache revalidation;
        HTML/JSON bodies gzip when the client asks and the payload pays for it.
        SSE streams must not use this (buffering would delay the first byte)."""
        tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"' if doc else None
        if doc and tag and self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Vary", "Accept-Encoding")
            self._secure_headers()
            self.end_headers()
            return
        out = body
        enc = False
        if len(body) >= _MIN_GZIP and self._accepts_gzip():
            out = gzip.compress(body, compresslevel=_GZIP_LEVEL)
            enc = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(out)))
        if enc:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        elif doc:
            self.send_header("Vary", "Accept-Encoding")
        if tag:
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
        self._secure_headers()
        if cookie:
            self.send_header("Set-Cookie",
                             f"{_AUTH_COOKIE}={cookie}; Path=/; HttpOnly; "
                             f"SameSite=Lax; Max-Age={int(_SESSION_TTL)}")
        elif cookie == "":
            self.send_header("Set-Cookie",
                             f"{_AUTH_COOKIE}=; Path=/; Max-Age=0")
        self.end_headers()
        self.wfile.write(out)

    def _send(self, obj: object, cookie: str | None = None) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._write_bytes(200, body, "application/json", cookie=cookie)

    def do_GET(self) -> None:
        if self.path.startswith("/fab-logo/"):
            # Public tiles for the landing strip (and any <img> that wants one).
            # Served as real image bytes — not data URIs — so the HTML stays small
            # and browsers can cache the PNGs across visits.
            key = self.path[len("/fab-logo/"):].split("?", 1)[0]
            if not re.fullmatch(r"[a-z0-9-]+", key):
                self.send_response(404)
                self._secure_headers()
                self.end_headers()
                return
            try:
                from ocdcircuit.fab import logo_bytes as _logo_bytes
                raw, ctype = _logo_bytes(key)
            except KeyError:
                self.send_response(404)
                self._secure_headers()
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control",
                             "public, max-age=604800, immutable")
            self._secure_headers()
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/slots":
            # plugin-inventory surface: slot → [ids] (harness inventory shape)
            # left open as a readiness probe (no board or account data).
            inv = {s: SLOTS.report(s) for s in UiSlots.slots}
            self._send(inv)
            return
        user = _authed(self.headers)
        _tok = _REQ_USER.set(user)
        try:
            if self.path == "/poll":
                if user is None:
                    self._send({"error": "log in first", "login": True})
                    return
                try:
                    _ensure_open_board()
                except ValueError as e:
                    self._send({"error": str(e)})
                    return
                import hashlib
                disk = H._disk()
                if disk is None:
                    self._send({"error": f"cannot read open board on disk",
                                "hash": "", "clean": False})
                    return
                self._send({"hash": hashlib.md5(disk.encode("utf-8")).hexdigest(),
                            "clean": disk == H.saved_text})
                return
            if self.path.startswith("/collab/events"):
                # realtime fan-out: Server-Sent Events (stdlib, no websocket dep).
                # ?board= names the room (default: the open board); each event is
                # {rev, by?, users} — the client reloads text on rev change via
                # /collab/sync. Disconnect runs the leave inverse (no ghost users).
                from urllib.parse import parse_qs, urlparse
                if user is None:
                    self.send_response(401)
                    self._secure_headers()
                    self.end_headers()
                    return
                from ocdcircuit import collab as _collab
                try:
                    key = H._room_key(dict(parse_qs(urlparse(self.path).query)))
                    key_n = key.replace("\\", "/")
                    _ensure_shelf_rel(key_n)
                    src_rel = os.path.relpath(SRC, ROOT).replace(os.sep, "/")
                    # seed only from the open buffer when this IS the open board;
                    # a ?board= join must not pour another user's buffer into a
                    # newly created public room.
                    seed = H.src_text if key_n == src_rel else _read(key_n)
                except ValueError:
                    self.send_response(403)
                    self._secure_headers()
                    self.end_headers()
                    return
                room = _collab.get_room(key, seed)
                stream, unsub = room.subscribe()
                leave = room.join(user)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self._secure_headers()
                    self.end_headers()
                    snap = room.snapshot()
                    assert isinstance(snap, dict)
                    self.wfile.write(
                        f"data: {json.dumps({'hello': user, **snap})}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    import queue as _qq
                    idle = 0
                    while True:
                        try:
                            msg = stream.get(timeout=15.0)
                            assert isinstance(msg, dict)
                            self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            idle = 0
                        except _qq.Empty:
                            # SSE comment = heartbeat: proxies/LB kill idle streams
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            idle += 1
                            if idle >= 8 or getattr(self, "_sse_done", False):
                                break
                except (BrokenPipeError, ConnectionResetError, ValueError):
                    pass
                finally:
                    leave()
                    unsub()
                return
            if self.path.startswith("/fs"):
                # project browser: ?dir= picks the directory (default: the board's
                # own). Paths come back relative to ROOT, so /fs/open can take them.
                if user is None:
                    self._send({"error": "log in first", "login": True})
                    return
                try:
                    # src/base in the reply name the open board — refuse when that
                    # board is another user's shelf (tree of a public dir alone
                    # would still leak the private path).
                    _ensure_open_board()
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(self.path).query)
                    base = os.path.relpath(BASE, ROOT).replace(os.sep, "/")
                    first = qs.get("dir") or [base]
                    d = first[0] or base
                    self._send({"root": os.path.relpath(ROOT, os.getcwd()),
                                "src": os.path.relpath(SRC, ROOT).replace(os.sep, "/"),
                                "base": base, "dir": _rel(d),
                                "tree": _tree(d), "vcs": _git_status()})
                except ValueError as e:
                    self._send({"error": str(e)})
                return
            if self.path != "/" and not self.path.startswith("/?"):
                self.send_response(204)  # favicon etc: silent, no console 404
                self._secure_headers()
                self.end_headers()
                return
            # members' workshop: no session cookie → the login screen. /auth/*
            # stays open (it is how you get the cookie). The gate lives here, not
            # in a proxy, so `python -m apps.studio` is the whole setup.
            if user is None:
                body = LOGIN_PAGE.replace("/*__FABS__*/", fab_strip()).encode("utf-8")
                self._write_bytes(200, body, "text/html; charset=utf-8", doc=True)
                return
            from urllib.parse import parse_qs, urlparse
            qs2 = parse_qs(urlparse(self.path).query)
            want = (qs2.get("board") or [""])[0]
            if want:
                # shelf boards only: alnum/_/- inside the user's own dir, else the
                # launch board. Server-side: the cookie names the user, the query
                # names only the file.
                clean = "".join(
                    c for c in want
                    if c.isascii() and (c.isalnum() or c in "_-"))[:32]
                cand = os.path.join(_user_dir(user), (clean or "_") + ".ocd")
                if os.path.isfile(cand):
                    g = globals()
                    g["SRC"], g["BASE"] = cand, os.path.dirname(cand)
                    H.src_text = _read(os.path.relpath(cand, ROOT))
                    H.save_target = cand
                    H.hist, H.redo, H.chat, H.props = [H.src_text], [], [], []
                    H.saved_text = ""
            page = PAGE.replace("/*__TOOLBAR__*/", SLOTS.render("toolbar", None))
            page = page.replace("/*__VIEWS__*/", SLOTS.render("view", None))
            body = page.encode("utf-8")
            self._write_bytes(200, body, "text/html; charset=utf-8", doc=True)
        finally:
            _REQ_USER.reset(_tok)

    def do_POST(self) -> None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._send({"error": "bad Content-Length"})
            return
        if n < 0 or n > _MAX_BODY:
            self._send({"error": "body too large"})
            return
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send({"error": "body must be JSON"})
            return
        if not isinstance(req, dict):
            self._send({"error": "body must be a JSON object"})
            return
        _want = req.get("src")
        _want_log = None if _want is None else _path_for_log(_want)
        print(f"REQ {self.path} src={_path_for_log(os.path.relpath(SRC, ROOT))} "
              f"want={_want_log!r}", flush=True, file=sys.stderr)
        user = _authed(self.headers)
        _tok = _REQ_USER.set(user)
        try:
            if self.path.startswith("/auth/"):
                pass  # the gate is the page; these routes ARE the keyhole
            elif user is None:
                self._send({"error": "log in first", "login": True})
                return
            elif not _shelf_meta_path(self.path) and self.path not in (
                    "/fs/open", "/fs/read"):
                # fs/open+read take an explicit path through _abs (shelf-checked).
                # Every other board route uses process-global SRC — guard it so a
                # peer cannot ride someone else's /?board= shelf open.
                try:
                    _ensure_open_board()
                except ValueError as e:
                    self._send({"error": str(e)})
                    return
            if self.path == "/auth/signup":
                if not _auth_rate_ok(_client_key(self)):
                    self._send({"error": "too many tries — wait a minute"})
                    return
                name = str(req.get("user", "")).strip()
                password = str(req.get("password", ""))
                if not name or not password:
                    self._send({"error": "a name and a password, both"})
                elif not _ok_username(name):
                    self._send({"error": "names are letters, digits, _ and - (32 max)"})
                elif len(password) < 8:
                    self._send({"error": "password needs 8+ characters"})
                elif name in _read_users():
                    self._send({"error": f"{name} exists — log in instead"})
                else:
                    # multi-user: every signup gets its own shelf (.users/<name>/);
                    # the "one studio, one owner" rule died with realtime collab.
                    try:
                        _write_user(name, password)
                    except (ValueError, OSError) as e:
                        self._send({"error": _exc_for_log(e)})
                        return
                    self._send({"ok": True, "user": name}, cookie=_new_session(name))
            elif self.path == "/auth/login":
                if not _auth_rate_ok(_client_key(self)):
                    self._send({"error": "too many tries — wait a minute"})
                    return
                name, password = str(req.get("user", "")).strip(), str(req.get("password", ""))
                if not _check_user(name, password):
                    self._send({"error": "wrong name or password"})
                else:
                    self._send({"ok": True, "user": name}, cookie=_new_session(name))
            elif self.path == "/auth/logout":
                get = getattr(self.headers, "get", None)
                with _AUTH_MU:
                    for chunk in str(get("Cookie", "") if get else "").split(";"):
                        k, _, v = chunk.strip().partition("=")
                        if k.strip() == _AUTH_COOKIE:
                            _SESSIONS.pop(v.strip(), None)
                self._send({"ok": True}, cookie="")
            elif self.path == "/auth/me":
                disp = _read_users().get(user, ("", "", user))[2] if user else None
                self._send({"user": user, "display": disp,
                            "needs_setup": not _read_users()})
            elif self.path == "/auth/profile":
                assert user is not None  # gated above
                disp = _norm_display(str(req.get("display", "")).strip())[:40]
                if not disp:
                    self._send({"error": "a display name can't be blank"})
                    return
                if not _ok_display(disp):
                    self._send({"error": "display name can't contain control "
                                 "chars, format marks, or ':'"})
                    return
                try:
                    _set_display(user, disp)
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
                    return
                self._send({"ok": True, "display": disp})
            elif self.path == "/shelf":
                assert user is not None  # gated above
                self._send({"user": user, "boards": _shelf(user),
                            "templates": _templates()})
            elif self.path == "/shelf/new":
                assert user is not None  # gated above
                raw = str(req.get("name", "")).strip().lower()
                # prompt-box prose ("a wifi sensor node!") degrades to a slug;
                # ASCII-only so shelf filenames stay NFC/NFD-safe across hosts.
                slug = "".join(
                    c if c.isascii() and c.isalnum() else "-" for c in raw).strip("-")
                while "--" in slug:
                    slug = slug.replace("--", "-")
                name = "".join(c for c in slug if c.isalnum() or c in "_-")[:32]
                if not name or name in ("users",):
                    self._send({"error": "give the board a usable name"})
                else:
                    fn = name + ".ocd"
                    full = os.path.join(_user_dir(user), fn)
                    if os.path.exists(full):
                        self._send({"error": f"{fn} already on your shelf"})
                    else:
                        with open(full, "w", encoding="utf-8") as f:
                            f.write(STARTER_OCD.format(name=name))
                        # name is the stem the landing page opens via /?board=
                        self._send({"ok": True, "name": name,
                                    "boards": _shelf(user)})
            elif self.path == "/shelf/from_template":
                assert user is not None  # gated above
                raw = str(req.get("name", ""))
                fn = "".join(c for c in os.path.basename(raw)
                             if c.isascii() and (c.isalnum() or c in "_-."))[:40]
                if not fn.endswith(".ocd"):
                    self._send({"error": "pick a template first"})
                    return
                src = os.path.join(HERE, "boards", fn)
                if not os.path.isfile(src):
                    self._send({"error": f"{fn}: no such template"})
                    return
                # Reuse a byte-identical shelf copy (double-click / retry);
                # mint stem-N only when every existing copy was edited away.
                import filecmp
                import shutil
                stem, n = fn[:-4], 1
                udir = _user_dir(user)
                while True:
                    cand = fn if n == 1 else f"{stem}-{n}.ocd"
                    dst = os.path.join(udir, cand)
                    if os.path.isfile(dst):
                        if filecmp.cmp(src, dst, shallow=False):
                            self._send({"ok": True, "name": cand[:-4],
                                        "boards": _shelf(user)})
                            return
                        n += 1
                        continue
                    shutil.copy2(src, dst)
                    self._send({"ok": True, "name": cand[:-4],
                                "boards": _shelf(user)})
                    return
            elif self.path == "/init":
                self._send(self._build(H.src_text, True))
            elif self.path == "/collab/sync":
                # realtime pull: rev + text + who changed it + presence.
                from ocdcircuit import collab as _collab
                assert user is not None  # gated above
                key = H._room_key()
                room = _collab.get_room(key, H.src_text)
                snap = room.snapshot()  # rev+by+text+users under one lock
                assert isinstance(snap, dict)
                self._send({"board": key, **snap})
            elif self.path == "/collab/push":
                # realtime edit at a rev: match -> accept + rebuild the room
                # text (everyone converges); mismatch -> stale + current rev.
                from ocdcircuit import collab as _collab
                assert user is not None  # gated above
                key = H._room_key()
                room = _collab.get_room(key, H.src_text)
                want = req.get("src")
                if isinstance(want, str) and want and want != key:
                    self._send({"stale": True, "error": f"board changed to "
                                f"{_path_for_log(key)} — edit again"})
                    return
                rev = _i(req.get("rev"), -1)
                snap0 = room.snapshot()
                text = str(req.get("text", snap0["text"]))
                # Cheap pre-check: skip a doomed build. The real fence is
                # cas_set_text after validate (two claim-passers cannot both adopt).
                if not room.claim(rev):
                    snap_s = room.snapshot()
                    self._send({"stale": True, "rev": snap_s["rev"],
                                "text": snap_s["text"],
                                "error": "someone else edited first — reloaded theirs"})
                    return
                try:
                    st = self._build(text, False, req)
                except (ValueError, KeyError, AssertionError) as e:
                    # unparseable push: nothing adopted (claim changed no
                    # state — validate, then cas-adopt).
                    snap_e = room.snapshot()
                    self._send({"error": _exc_for_log(e),
                                "rev": snap_e["rev"], "text": snap_e["text"]})
                    return
                new_rev, how, cur = H._room_adopt(
                    key, str(st["text"]), user, rev=rev)
                if how == "stale":
                    self._send({"stale": True, "rev": new_rev, "text": cur,
                                "error": "someone else edited first — reloaded theirs"})
                    return
                with H._mu:
                    H.save_target = SRC
                    H.commit(H.src_text)
                    serr = H.save()
                st["rev"] = new_rev
                if serr:
                    st["save_error"] = serr
                self._send(st)
            elif self.path == "/collab/cursor":
                # presence heartbeat: x/y/ref + prune the timed-out, no timer.
                from ocdcircuit import collab as _collab
                assert user is not None  # gated above
                room = _collab.get_room(H._room_key(), H.src_text)
                self._send(room.heartbeat(user, _f(req.get("x"), 0.0),
                                          _f(req.get("y"), 0.0),
                                          str(req.get("ref", ""))[:16]))
            elif self.path == "/collab/op":
                # structured op through the `collab` plugin (undoable edits,
                # same fence as every dispatch): {rev, op:{ops:[...]}}.
                from ocdcircuit import collab as _collab
                assert user is not None  # gated above
                key = H._room_key()
                room = _collab.get_room(key, H.src_text)
                rev = _i(req.get("rev"), -1)
                snap0 = room.snapshot()
                if rev != int(cast(int, snap0["rev"])):
                    self._send({"stale": True, "rev": snap0["rev"],
                                "text": snap0["text"],
                                "error": "someone else edited first — reloaded theirs"})
                    return
                b = agent.loads(str(snap0["text"]), base=BASE)
                op = req.get("op", {})
                assert isinstance(op, dict)
                try:
                    res = b.collab(op=op)
                    text = agent.dumps(b)
                    st = self._build(text, False, req)
                except (ValueError, KeyError, AssertionError) as e:
                    snap_e = room.snapshot()
                    self._send({"error": _exc_for_log(e),
                                "rev": snap_e["rev"], "text": snap_e["text"]})
                    return
                new_rev, how, cur = H._room_adopt(
                    key, str(st["text"]), user, rev=rev)
                if how == "stale":
                    self._send({"stale": True, "rev": new_rev, "text": cur,
                                "error": "someone else edited first — reloaded theirs"})
                    return
                with H._mu:
                    H.save_target = SRC
                    H.commit(H.src_text)
                    serr = H.save()
                st["rev"] = new_rev
                st["applied"] = res.get("applied", 0)
                if serr:
                    st["save_error"] = serr
                self._send(st)
            elif self.path == "/load":
                # Open a board without re-placing it: parse + route + DRC only.
                # Placing 5k parts is minutes, and the file already says where
                # they go; `solve` is the explicit ask for a fresh placement.
                self._send(self._build(H.src_text, False, {"no_place": True}))
            elif self.path == "/reload":
                # file-watch: adopt external edits (client asks only when
                # clean, or the user confirmed the banner).
                with H._mu:
                    disk = H._disk()
                    if disk is None:
                        text = None
                    else:
                        H.src_text = disk
                        H.commit(H.src_text)
                        H.saved_text = H.src_text
                        text = H.src_text
                if text is None:
                    self._send({"error": "cannot read board on disk — "
                                "reload aborted, buffer unchanged"})
                    return
                self._send(self._build(text, True))
            elif self.path == "/build":
                with H._mu:
                    cur = H.src_text
                    src_rel = os.path.relpath(SRC, ROOT)
                text = str(req.get("text", cur))
                want = req.get("src")
                if isinstance(want, str) and want and want != src_rel:
                    # the browser was editing a different board when this
                    # keystroke was captured: drop it, the caller re-reads
                    self._send({"stale": True, "error": f"board changed to "
                                f"{_path_for_log(src_rel)} — edit again"})
                    return
                st = self._build(text, False, req)
                with H._mu:
                    H.src_text = str(st["text"])  # only keep good builds
                    H.save_target = SRC
                    H.commit(H.src_text)
                # The client builds the board it has open; if the text was not
                # from SRC at all (a stale tab, a pasted board), do not write it
                # over the file on disk.
                serr = H.save()
                from ocdcircuit import collab as _collab_b
                st["rev"] = _collab_b.get_room(H._room_key(), H.src_text).set_text(
                    H.src_text, _authed(self.headers) or "build")
                if serr:
                    st["save_error"] = serr
                self._send(st)
            elif self.path == "/solve":
                with H._mu:
                    src_text = H.src_text
                st = self._build(src_text, True, req)
                with H._mu:
                    H.src_text = str(st["text"])
                    H.save_target = SRC
                    H.commit(H.src_text)
                    serr = H.save()
                    adopted = H.src_text
                from ocdcircuit import collab as _collab_s
                st["rev"] = _collab_s.get_room(H._room_key(), adopted).set_text(
                    adopted, _authed(self.headers) or "solve")
                if serr:
                    st["save_error"] = serr
                self._send(st)
            elif self.path == "/candidates":
                b = agent.loads(H.src_text, base=BASE)
                pkey = req.get("placer")
                assert pkey is None or isinstance(pkey, str)
                n = _i(req.get("n"), 4)
                cands = b.candidates(n=n, key=pkey,
                                     seed=_i(req.get("seed"), 0),
                                     seeds=1, iters=_i(req.get("iters"), 400))
                # feasibility on the best candidate (unplaced text proves nothing)
                b.restore_candidate(cands[0])
                rbsnap = b.ctx.snapshot()
                try:
                    feas = b.feasible()
                finally:
                    b.ctx.rollback(rbsnap)
                # same shape as MCP candidates (docs: "same shapes as MCP tools")
                cout: dict[str, object] = {
                    "candidates": cands,
                    "feasible": {str(k): v for k, v in feas.items()},
                    "layers": b.layers,
                    "note": "pick one via /pick (same n/seed/iters)"}
                if pkey is not None:
                    cout["placer"] = pkey
                self._send(cout)
            elif self.path == "/pick":
                # (cast is imported at module level; a local import here would
                # shadow it for every earlier branch in this function)
                b = agent.loads(H.src_text, base=BASE)
                pkey2 = req.get("placer")
                assert pkey2 is None or isinstance(pkey2, str)
                idx = _i(req.get("index"), 0)
                cands = b.candidates(n=_i(req.get("n"), 4), key=pkey2,
                                     seed=_i(req.get("seed"), 0),
                                     seeds=1, iters=_i(req.get("iters"), 400))
                if not 0 <= idx < len(cands):
                    self._send({"error": f"index {idx} out of range"})
                    return
                b.restore_candidate(cands[idx])
                router = str(req.get("router", "lroute"))
                b.route_board(router)
                drc = b.check()
                st = board_state(b, agent.dumps(b), [], [
                    {"x1": t.x1, "y1": t.y1, "x2": t.x2,
                     "y2": t.y2, "layer": t.layer, "w": t.width}
                    for t in b.traces], cast(float, cands[idx]["cost"]), drc)
                H._decorate(st, b, b.score().get("tidy", {}), b.feasible(),
                            b.plugins().list("placer"), b.plugins().list("router"),
                            req.get("silk", "full"), b.plugins().list("silk"))
                H.src_text = str(st["text"])
                H.commit(H.src_text)
                serr = H.save()
                from ocdcircuit import collab as _collab_p
                st["rev"] = _collab_p.get_room(H._room_key(), H.src_text).set_text(
                    H.src_text, _authed(self.headers) or "pick")
                if serr:
                    st["save_error"] = serr
                self._send(st)
            elif self.path == "/diff_prev":  # current text vs previous undo-commit
                if len(H.hist) < 2:
                    self._send({"error": "no previous revision"})
                else:
                    a = agent.loads(H.hist[-2], base=BASE)
                    b = agent.loads(H.src_text, base=BASE)
                    self._send({"diff": a.diff(b) or "identical"})
            elif self.path == "/export":
                import base64
                import tempfile as _tf
                b = agent.loads(H.src_text, base=BASE)
                with _tf.TemporaryDirectory() as td:
                    zfn = b.export("bundle", outdir=td)[0]
                    zraw = open(zfn, "rb").read()
                self._send({"zip": base64.b64encode(zraw).decode(),
                            "name": f"{b.name}-fab.zip",
                            "bytes": len(zraw)})
            elif self.path == "/render":
                import base64
                rkey = str(req.get("key", "svg"))  # svg|sch|png|stl|gltf|xray|…
                b = agent.loads(H.src_text, base=BASE)
                b.configure("toml", base=BASE)
                b.place()
                b.route_board()
                # distinct name: earlier branches assign `out` as str
                rendered = b.render(rkey)
                ext = {"svg": "svg", "sch": "sch.svg", "png": "png",
                       "stl": "stl", "gltf": "glb", "xray": "xray.svg"}.get(rkey, rkey)
                if isinstance(rendered, bytes):
                    self._send({"data": base64.b64encode(rendered).decode(),
                                "bin": True, "name": f"{b.name}.{ext}"})
                else:
                    self._send({"data": rendered if isinstance(rendered, str)
                                else "\n".join(rendered),
                                "bin": False, "name": f"{b.name}.{ext}"})
            elif self.path == "/xray":  # fab scan (base64 PNG) vs design
                import base64
                import binascii
                data = req.get("png", req.get("data", ""))
                if not isinstance(data, str) or not data:
                    self._send({"error": "xray needs png=<base64 PNG>"})
                    return
                try:
                    xraw = base64.b64decode(data, validate=True)
                except (ValueError, binascii.Error) as e:
                    self._send({"error": f"bad upload (not base64 PNG): {e}"})
                    return
                b = agent.loads(H.src_text, base=BASE)
                b.configure("toml", base=BASE)
                b.place()
                b.route_board()
                args: dict[str, object] = {}
                for kk, cv in (("dx", _f), ("dy", _f), ("scale", _f),
                               ("thr", _i), ("pxmm", _f)):
                    if req.get(kk) is not None:
                        args[kk] = cv(req.get(kk))
                try:
                    r = b.xray(None, png=xraw, **args)
                except (ValueError, OSError, KeyError, AssertionError) as e:
                    self._send({"error": _exc_for_log(e)})
                    return
                divs = r.get("divs")
                assert isinstance(divs, list)
                self._send({"score": r["score"], "missing": r["missing"],
                            "extra": r["extra"], "divs": divs[:20],
                            "overlay": r["overlay"]})
            elif self.path == "/quote":  # fab price comparison for the open board
                from ocdcircuit.util import as_int as _iiq
                b = agent.loads(H.src_text, base=BASE)
                fabs = req.get("fabs", req.get("fab"))
                if isinstance(fabs, str):
                    fabs = [fabs]
                assert fabs is None or isinstance(fabs, list)
                try:
                    self._send(b.quote(qty=_iiq(req.get("qty"), 5), fabs=fabs,
                                       no_parts=bool(req.get("no_parts", False))))
                except (ValueError, KeyError, AssertionError) as e:
                    self._send({"error": _exc_for_log(e)})
                    return
            elif self.path == "/simulate":  # dc | tran on current text
                what = str(req.get("what", "dc"))
                b = agent.loads(H.src_text, base=BASE)
                if not any(c.get("t") == "sim" for c in b.constraints):
                    self._send({"error": "no sim lines (e.g. `sim vcc VCC 9`)"})
                else:
                    res = b.simulate(what=what)
                    waves = res.get("waves")
                    assert waves is None or isinstance(waves, dict)
                    nets = res.get("nets")
                    assert nets is None or isinstance(nets, dict)
                    self._send({
                        "sim": {str(k): round(float(v), 3) for k, v in nets.items()}
                        if isinstance(nets, dict) else {},
                        "tran": {str(k): [round(float(x), 3) for x in v]
                                 for k, v in waves.items()}
                        if isinstance(waves, dict) else {}})
            elif self.path == "/undo":
                if len(H.hist) < 2:
                    self._send({"error": "nothing to undo"})
                else:
                    H.redo.append(H.hist.pop())
                    H.src_text = H.hist[-1]
                    serr = H.save()
                    from ocdcircuit import collab as _collab_u
                    _collab_u.get_room(H._room_key(), H.src_text).set_text(
                        H.src_text, _authed(self.headers) or "undo")
                    st_u = self._build(H.src_text, False)
                    if serr:
                        st_u["save_error"] = serr
                    self._send(st_u)
            elif self.path == "/redo":
                if not H.redo:
                    self._send({"error": "nothing to redo"})
                else:
                    H.src_text = H.redo.pop()
                    H.commit(H.src_text)
                    serr = H.save()
                    from ocdcircuit import collab as _collab_r
                    _collab_r.get_room(H._room_key(), H.src_text).set_text(
                        H.src_text, _authed(self.headers) or "redo")
                    st_r = self._build(H.src_text, False)
                    if serr:
                        st_r["save_error"] = serr
                    self._send(st_r)
            elif self.path == "/scan":  # photos of a physical board -> draft
                import base64
                import binascii
                import tempfile
                shots = req.get("photos")
                if not isinstance(shots, list) or not shots:
                    self._send({"error": "upload at least one photo"})
                    return
                if len(shots) > 40:
                    self._send({"error": f"{len(shots)} photos is more than "
                                         "this endpoint takes (max 40)"})
                    return
                # Photos + scan artifacts live under a temp dir the browser
                # never opens (views are inlined as data URIs). Wipe it on
                # every exit — success, bad base64, empty shots, scan error —
                # or each /scan leaves multi-MB trees until the process dies.
                import shutil
                work = tempfile.mkdtemp(prefix="ocd-scan-")
                try:
                    paths: list[str] = []
                    try:
                        for i, item in enumerate(shots):
                            if not isinstance(item, dict):
                                continue
                            name = str(item.get("name", f"photo{i}"))
                            # the side hint lives in the filename (pcbscan splits
                            # on it), so keep the user's name, not a temp id
                            safe = os.path.basename(name).replace("..", "_") or f"p{i}"
                            blob = base64.b64decode(str(item.get("data", "")),
                                                    validate=True)
                            dest = os.path.join(work, f"{i:02d}_{safe}")
                            with open(dest, "wb") as fh:
                                fh.write(blob)
                            paths.append(dest)
                    except (ValueError, binascii.Error) as e:
                        self._send({"error": f"bad upload (not base64): {e}"})
                        return
                    if not paths:
                        self._send({"error": "upload at least one photo"})
                        return
                    docs: list[str] = []
                    for i, d in enumerate(req.get("docs") or []):
                        if not isinstance(d, dict):
                            continue
                        try:
                            blob = base64.b64decode(str(d.get("data", "")),
                                                    validate=True)
                        except (ValueError, binascii.Error) as e:
                            self._send({"error": f"bad doc upload (not base64): {e}"})
                            return
                        dp = os.path.join(
                            work, "doc_" + os.path.basename(
                                str(d.get("name", f"doc{i}"))).replace("..", "_"))
                        with open(dp, "wb") as fh:
                            fh.write(blob)
                        docs.append(dp)
                    answers: dict[str, str] = {}
                    for qa in req.get("answers") or []:
                        if isinstance(qa, dict) and qa.get("q"):
                            answers[str(qa["q"])] = str(qa.get("a", ""))
                    from ocdcircuit.circuit import Board as _B
                    try:
                        r = _B("scan").scan(
                            photos=paths, outdir=os.path.join(work, "out"),
                            board_mm=(_f(req.get("mm")) if req.get("mm") else None),
                            note=str(req.get("note", "")),
                            docs=docs or None, answers=answers or None,
                            llm=bool(req.get("llm", True)))
                    except (ValueError, OSError, KeyError, RuntimeError,
                            AssertionError) as e:
                        self._send({"error": _exc_for_log(e)})
                        return
                    draft = ""
                    if isinstance(r.get("draft"), str) and os.path.isfile(str(r["draft"])):
                        with open(str(r["draft"]), encoding="utf-8") as fh:
                            draft = fh.read()
                    report = ""
                    if isinstance(r.get("analysis"), str) and os.path.isfile(str(r["analysis"])):
                        with open(str(r["analysis"]), encoding="utf-8") as fh:
                            report = fh.read()
                    sides = cast(dict[str, object], r.get("sides", {}))
                    # The viewer needs pixels, not paths: the outdir is a server
                    # temp dir the browser cannot reach. Inline the two views it
                    # draws (stitch + contrast) as data URIs at 1024px — ~0.8MB,
                    # against multi-MB analysis text already in this response.
                    from ocdcircuit import pcbscan as _scanmod
                    views: dict[str, dict[str, str]] = {}
                    for side, sv in sides.items():
                        if not isinstance(sv, dict):
                            continue
                        files = sv.get("files")
                        if not isinstance(files, dict):
                            continue
                        got: dict[str, str] = {}
                        for view in ("stitch", "contrast"):
                            fp = files.get(view)
                            if isinstance(fp, str) and os.path.isfile(fp):
                                try:
                                    got[view] = _scanmod._b64_png(fp, 1024)
                                except (OSError, ValueError):
                                    continue
                        if got:
                            views[str(side)] = got
                    self._send({
                        "sides": {k: {kk: vv for kk, vv in
                                      cast(dict[str, object], v).items()
                                      if kk != "files"}
                                  for k, v in sides.items()},
                        "questions": r.get("questions", []),
                        "draft": draft, "analysis": report,
                        "draft_error": r.get("draft_error", ""),
                        "wired": r.get("draft_wired", 0),
                        "floating": r.get("draft_floating", []),
                        "drc": r.get("draft_drc_errors", 0),
                        "drc_lines": r.get("draft_drc", []),
                        "review": r.get("review", {}),
                        "views": views,
                        "outdir": str(r.get("outdir", ""))})
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            elif self.path == "/doctor":  # tooling health, no board needed
                from ocdcircuit.circuit import Board as _B
                r = _B("doctor").doctor()
                self._send({"ok": r["ok"], "checks": r["checks"]})
            elif self.path == "/kb/list":
                self._send(_kb_list())
            elif self.path == "/kb/read":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.read(str(req.get("doc", "")),
                                       start=_i(req.get("start"), 1),
                                       lines=_i(req.get("lines"), 120)))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/kb/search":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.search(str(req.get("q", "")),
                                        limit=_i(req.get("limit"), 8)))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/kb/ask":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.ask(str(req.get("q", "")),
                                      k=_i(req.get("limit"), 6),
                                      answer=bool(req.get("answer"))))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/kb/add":
                from ocdcircuit.kb import KB
                from urllib.parse import urlparse as _uparse
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    src = str(req.get("src", ""))
                    scheme = _uparse(src).scheme.lower()
                    if scheme in ("http", "https"):
                        pass  # kb.add enforces https on download
                    elif src:
                        # local path: absolute stays absolute (realpath + root/
                        # shelf check); relative goes through _abs. Never feed
                        # an abs path to _rel — it lstrips "/" and mis-joins.
                        if os.path.isabs(src):
                            rp = os.path.realpath(src)
                            root = os.path.realpath(ROOT)
                            if rp != root and not rp.startswith(root + os.sep):
                                raise ValueError(
                                    f"{src}: outside the project root")
                            as_rel = os.path.relpath(rp, root).replace(
                                os.sep, "/")
                            _ensure_shelf_rel(as_rel, src)
                            if not os.path.isfile(rp):
                                raise ValueError(f"{src}: no such file")
                            src = rp
                        else:
                            src = _abs(src, must_exist=True, near=BASE)
                    self._send(kb.add(src))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/kb/fetch":
                self._send(_kb_fetch_start())
            elif self.path == "/kb/prefs":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                self._send({"prefs": kb.prefs()})
            elif self.path == "/kb/prefs/add":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.prefs_add(str(req.get("when", "")),
                                            str(req.get("text", ""))))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/kb/prefs/set":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.prefs_set(_i(req.get("id"), -1),
                                            bool(req.get("approved"))))
                except (ValueError, OSError) as e:
                    self._send({"error": _exc_for_log(e)})
            elif self.path == "/chat":
                text = str(req.get("text", "")).strip()
                if not text:
                    self._send({"error": "say something first"})
                    return
                self._send(H.ask(text, bool(req.get("auto"))))
            elif self.path == "/chat/apply":
                applied = H.apply_one(str(req.get("id", "")))
                if "error" not in applied:
                    # a written file that fails a rule: the text is the truth
                    # and the model is told what it broke next turn
                    st0 = cast(dict[str, object] | None, applied["state"])
                    errs0 = list(cast(list[object], st0["errors"])) if st0 else []
                    if errs0:
                        H.chat.append({"role": "user", "content":
                                       "the board now builds but DRC reports: "
                                       + "; ".join(str(e) for e in errs0[:3])})
                        applied["note"] = "applied, but DRC reports " + "; ".join(
                            str(e) for e in errs0[:3])
                    _st = st0
                    if _st is None:  # a non-board file: re-init so the UI matches
                        _st = self._build(H.src_text, False)
                    applied["state"] = _st
                applied["proposals"] = H.props
                self._send(applied)
            elif self.path == "/chat/reject":
                pid = str(req.get("id", ""))
                if pid:
                    H.props = [p for p in H.props if str(p["id"]) != pid]
                else:
                    H.props = []
                self._send({"ok": True, "proposals": H.props})
            elif self.path == "/chat/reset":
                H.chat = []
                H.props = []
                self._send({"ok": True})
            elif self.path == "/fs/open":
                H.open_file(str(req.get("path", "")))
                self._send(self._build(H.src_text, False))
            elif self.path == "/fs/read":
                rel = str(req.get("path", ""))
                self._send({"path": rel, "text": _read(rel)})
            elif self.path == "/fs/import":
                self._send(_import_upload(str(req.get("name", "")),
                                          str(req.get("data", ""))))
            elif self.path == "/vcs":
                self._send({"log": _git_log(40, req.get("path")),
                            "status": _git_status()})
            elif self.path == "/vcs/diff":
                h = str(req.get("hash", ""))
                if h:
                    if not _GIT_HASH_RE.match(h):
                        self._send({"error": "hash must be a hex git object id"})
                        return
                    self._send({"diff": _git("show", "--stat", "--patch",
                                             "--no-color", h)[:20000]})
                else:
                    rel = _rel(os.path.relpath(SRC, ROOT))
                    self._send({"diff": _git("diff", "--no-color", "--", rel)[:20000]})
            elif self.path == "/vcs/commit":
                rel = _rel(os.path.relpath(SRC, ROOT))
                msg = str(req.get("message", "")).strip() or (
                    "studio: update " + _path_for_log(rel))
                if "\n" in msg or "\r" in msg or msg.startswith("-"):
                    self._send({"error": "commit message must be one safe line"})
                    return
                _git("add", "--", rel)
                out = _git("commit", "-m", msg)
                self._send({"ok": True, "commit": out.strip().splitlines()[-1][:200],
                            "log": _git_log(40, rel), "status": _git_status()})
            else:
                # Match the JSON error envelope every other failure uses
                # (docs: any failure returns {"error": ...}; never bare 404).
                self._send({"error": f"unknown path {self.path}"})
        except Exception as e:  # never 500 the UI thread: report, keep serving
            self._send({"error": _exc_for_log(e)})
        finally:
            _REQ_USER.reset(_tok)

    @staticmethod
    def _build(text: str, animate: bool, req: dict[str, object] | None = None) -> dict[str, object]:
        req = req or {}
        b = agent.loads(text, base=BASE)
        b.configure("toml", base=BASE)
        reg = b.plugins()
        placers = reg.list("placer")
        routers = reg.list("router")
        silks = reg.list("silk")
        _pp = b.proj.get("placer")
        _rr = b.proj.get("router")
        _dd = b.proj.get("drc")
        # Always name the engine. The engine's own "choose for me" path
        # (key=None) is the wrong pick here: on discrete6502 an unnamed router
        # took 196s against 1.8s for the explicit `lroute` key. The UI shows
        # which engine ran, so the named default is also the honest one.
        _p, _r = req.get("placer"), req.get("router")
        placer: str = (str(_p) if _p else
                       str(_pp) if isinstance(_pp, str) else
                       (placers[0] if placers else "diffusion"))
        router: str = (str(_r) if _r else
                       str(_rr) if isinstance(_rr, str) else
                       (routers[0] if routers else "lroute"))
        silksel = str(req.get("silk", silks[1] if len(silks) > 1 else silks[0])) if silks else "full"
        b.fab = str(req.get("fab", getattr(b, "fab", "jlc")))
        frames: list[dict[str, object]] = []
        # keystroke path: 1 seed × 100 iters + lroute estimate (~10x maze).
        # solve ▶ keeps full quality: 5 seeds × 500 iters + chosen router.
        quick = not animate and not req.get("full")
        # A dense board multiplies every stage: one quick pass is minutes, so
        # the load path skips placing entirely and the rest of the work is
        # trimmed to what a page can wait for. `dense` tells the client.
        dense = len(b.parts) >= DENSE_PARTS
        # Dense boards are loaded, not solved: placing 5,420 parts is ~9
        # minutes at the cheapest settings the studio can ask for, so the
        # studio shows what the file says and points at the CLI for placement.
        # Anything smaller is placed here as usual (~0.1s for a 10-part board,
        # which is the common case and must not regress).
        place_it = not (dense and not req.get("dense_place"))
        if place_it:
            cost = b.place(placer, seeds=1 if quick else 5,
                           iters=100 if quick else 500,
                           frames=frames if animate else None, every=25)
            assert isinstance(cost, float)
        else:
            cost = 0.0  # no placement: parts keep the positions the file gave them
        rframes: list[dict[str, object]] = []
        n = b.route_board("lroute" if quick else router,
                          frames=rframes if animate else None)
        assert isinstance(n, int)
        drcsel = req.get("drc")
        drc_keys = ([str(drcsel)] if isinstance(drcsel, str)
                    else list(_dd) if isinstance(_dd, list) else None)
        dense_skip: list[str] = []
        drc: DrcReport
        if dense and not req.get("full"):
            # The ask was to see the board, not to wait four minutes for DRC.
            drc = {"errors": [], "warnings": [
                f"DRC not run on this {len(b.parts)}-part board: the check "
                "scales with routed geometry and takes minutes. Run "
                "`python -m apps.ocd check` for the full report."],
                "fab": getattr(b, "fab", "jlc")}
            dense_skip.append("drc")
        else:
            drc = b.check("all", keys=drc_keys)
        assert isinstance(drc, dict)
        # Dense board: the scoring passes alone are ~55s each (and the tidy
        # metrics ~51s), which is what turned a load into a four-minute wait.
        # Skip both and say so; the CLI still reports them, and a normal board
        # is unaffected. The routability probe is a maze pass — also skipped.
        st_tidy: object
        st_score: object
        if dense:
            st_tidy = {"coverage": "skipped (board is dense — run the CLI)"}
            st_score = {"total": 0, "grade": "?", "dense": True}
        else:
            # score() already embeds tidy(); calling both re-ran the full
            # metric card (virgo ~0.8s × 2 per rebuild).
            st_score = b.score()
            st_tidy = st_score.get("tidy", {})
        st_lint = b.lint()
        feas = {} if dense else b.feasible()
        # `net` is not in the payload: the canvas colours by layer, and a dense
        # board has thousands of names to serialise (0.15MB on discrete6502).
        traces: list[dict[str, object]] = [
            {"x1": t.x1, "y1": t.y1, "x2": t.x2,
             "y2": t.y2, "layer": t.layer, "w": t.width}
            for t in b.traces[:MAX_SEGS]]
        # Traces are the biggest part of a dense payload (0.76MB of 2.32MB on
        # discrete6502) and a text edit rarely changes them: the caller sends
        # the hash it holds, and an unchanged list is not re-sent.
        import hashlib as _hashlib
        tkey = _hashlib.sha1(repr(traces).encode("utf-8")).hexdigest()[:12]
        if req.get("thash") == tkey:
            traces = []
        st = board_state(b, agent.dumps(b), frames, traces, cost, drc)
        st["dense"] = dense
        st["compact"] = bool(st.get("compact"))
        st["placed"] = place_it
        st["segcount"] = n
        st["thash"] = tkey
        if dense and not place_it:
            dense_skip.insert(0, "placement")
        st["skipped"] = dense_skip
        H._decorate(st, b, st_tidy, feas, placers, routers, silksel, silks,
                    st_score, st_lint)
        H.src_text = str(st["text"])
        # the room's rev rides every build: the client's push carries it back.
        from ocdcircuit import collab as _collab_b2
        st["rev"] = _collab_b2.get_room(H._room_key(), H.src_text).rev
        return st

    @staticmethod
    def _decorate(st: dict[str, object], b: Board, tidy: object,
                  feas: object, placers: object, routers: object,
                  silksel: object, silks: object, score: object = None,
                  lint: object = None) -> None:
        """Shared state attachments (build + pick agree)."""
        st["tidy"] = tidy
        st["feasible"] = feas
        st["placers"] = placers
        st["routers"] = routers
        st["fabs"] = _fab.list_fabs()
        st["silks"] = silks
        st["silk"] = silksel
        st["score"] = score if score is not None else b.score()
        st["lint"] = lint if lint is not None else b.lint()
        # recommend() is the module API (report-only). Board.recommend
        # exists but has no mounted plugin — studio must call the module.
        st["recommend"] = recommend(b)

    def log_message(self, *a: object) -> None:
        pass


def main() -> None:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: python -m apps.studio [board.ocd]  # OCD_PORT=8077 to change port")
        return
    from ocdcircuit.util import read_text as _read_src
    H.src_text = _read_src(SRC) if os.path.isfile(SRC) else (
        "board demo 40x30\npart R1 R0805 1k\npart C1 C0805 100n\n"
        "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n")
    H.saved_text = H.src_text
    H.save_target = SRC
    H.commit(H.src_text)  # genesis commit — undo floor
    H.root = ROOT        # project browser/agent root (OCD_ROOT or the board's dir)
    H.props = []         # no proposals pending
    H.rev = 0            # revision 1 is the genesis commit above
    port = _envcfg.studio_port()
    # Surface malformed knobs at boot (secrets stay redacted via summary).
    # Bad OCD_PORT already fell back above; other bad values would otherwise
    # only fail on first use (LLM URL, XRAY, …).
    for row in _envcfg.summary():
        if not row["ok"]:
            print(f"studio: bad config {row['name']}: {row['detail']}",
                  file=sys.stderr)
    # Threading: one SSE stream per collaborator blocks its handler for
    # minutes — on a single-threaded server the second user could never even
    # log in while the first one's stream was open. Threads share H/rooms;
    # H._mu + _AUTH_MU + Room._mu serialize the shared state (the GIL does not).
    # ponytail: stdlib ThreadingHTTPServer, no new dep, no refactor.
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    print(f"OCD Studio: http://localhost:{port}  "
          f"({_path_for_log(SRC)}; root={_path_for_log(ROOT)})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
