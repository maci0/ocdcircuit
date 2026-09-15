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
from ocdcircuit.util import as_float as _f, as_int as _i
import http.server
import json
import os
import sys
from collections.abc import Callable
from typing import cast
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = ROOT

from ocdcircuit import agent  # noqa: E402
from ocdcircuit import fab as _fab  # noqa: E402
from ocdcircuit.circuit import Board  # noqa: E402
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
TOOLBAR = (
    '<div class="tbar">'
    '<div class="grp"><span class=lbl>engines</span>'
    '<select id=placer title="placement engine"></select>'
    '<select id=router title="routing engine"></select>'
    '<select id=fab title="fab rules (edge, clearance, min trace)"></select>'
    '<select id=silk title="silkscreen density"></select></div>'
    '<div class="grp"><span class=lbl>build</span>'
    '<button id=solve class=primary title="full solve, 5 seeds x 500 iters (Ctrl+Enter)">solve</button>'
    '<button id=dice title="generate N candidate layouts side by side">candidates</button>'
    '<input id=ncand value=4 size=1 aria-label="candidate count" title="candidate count">'
    '<button id=simbtn title="simulate the current board (shift-click: tran)">sim dc</button>'
    '<button id=stamp title="stamp another copy of the hovered instance">stamp</button></div>'
    '<div class="grp"><span class=lbl>history</span>'
    '<button id=undo title="undo (Ctrl+Z)">undo</button>'
    '<button id=redo title="redo (Ctrl+Y)">redo</button>'
    '<button id=diffprev title="what changed since the previous revision">diff</button>'
    '<button id=commit title="commit the open file to git (Ctrl+S)">commit</button></div>'
    '<div class="grp"><span class=lbl>agent</span>'
    '<button id=chatbtn title="show or hide the agent chat panel">chat</button>'
    '<label class=auto title="apply a proposal without asking, but only when '
    'it builds DRC-clean"><input type=checkbox id=chatauto>auto</label></div>'
    '<div class="grp"><span class=lbl>output</span>'
    '<button id=fab_dl title="download the fab bundle as one zip">fab zip</button>'
    '<button id=dl title="download a render (cycles svg, sch, png, xray; shift-click backwards)">svg</button>'
    '<details id=calc title="trace width and divider calculators"><summary>calc</summary>'
    '<label>A <input id=ca size=4 value=1 aria-label="trace current A"></label>'
    '<label>dT <input id=cdt size=3 value=10 aria-label="temperature rise C"></label>'
    '<div id=cout></div>'
    '<label>V <input id=dv size=4 value=5 aria-label="divider input V"></label>'
    '<label>Rt <input id=drt size=5 value=10k aria-label="divider top R"></label>'
    '<label>Rb <input id=drb size=5 value=10k aria-label="divider bottom R"></label>'
    '<div id=dout></div></details>'
    '<details id=doc title="tooling health: python, ngspice, plugins"><summary>health</summary>'
    '<div id=docout>click to check</div></details></div>'
    '<div class="grp status"><span id=cost class=pill title="total wirelength">cost</span>'
    '<span id=ocdscore class=pill title="OCD neatness, 0-100"></span>'
    '<span id=feas class=pill title="routing feasibility per layer count"></span>'
    '<span id=stat role=status aria-live=polite></span></div>'
    '</div>')
_slot("toolbar", "solver-selects",
               lambda s: TOOLBAR,
               order=1.0)
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
                         '<span class=panel-note id=treenote></span></header>'
                         '<div id=tree></div></section>',
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
                         '<span class=panel-note>drag a part to pin it &middot; double-click to unpin</span>'
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
                         '<button id=kbfetch type=button title="download the datasheet for every datasheet= / lcsc= part">fetch datasheets</button></div>'
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
# hook, glowing prompt card over the board visual, single CTA. FORM: Persuade
# surface in the established world, no seed roll (brief-pinned).
# FINISH: unreviewed and undocumented is unfinished; this build ends with the
# finish review, the verdict, and DESIGN.md
LOGIN_PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>OCD Studio — design PCBs with AI</title>
<link rel=icon href="data:,">
<style>
:root{
--term:#101418;--term-2:#1a2129;--term-line:#2a333d;--term-text:#d8e2dc;
--term-faint:#7f8b94;--term-bad:#ff7364;--term-ok:#5fd894;--term-key:#ffd8a0;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
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
.nav{display:flex;align-items:center;gap:1rem;max-width:70rem;margin:0 auto;width:100%}
.brand{display:flex;align-items:center;gap:.5rem;font-weight:700;font-size:1.1rem;
letter-spacing:-.02em;color:#fff;text-decoration:none}
.brand em{font-style:normal;color:var(--term-ok)}
.nav .sp{flex:1}
.nav button{font:600 .9rem var(--sans);padding:.5rem 1rem;border-radius:8px;cursor:pointer}
#loginbtn{background:transparent;color:var(--term-text);border:1px solid var(--term-line)}
#loginbtn:hover{border-color:var(--term-ok)}
#topcta{background:var(--term-ok);border:1px solid var(--term-ok);color:#06130d}
#topcta:hover{filter:brightness(1.07)}
h1{font-size:clamp(2.2rem,5vw,3.4rem);letter-spacing:-.03em;margin:12vh 0 .3rem;color:#fff}
.dek{color:var(--term-faint);font-size:1.05rem;margin:0 0 2rem}
.prompt{max-width:34rem;margin:0 auto;width:100%;background:rgba(16,20,24,.92);
border:1px solid #7a3fd1;border-radius:14px;padding:1.1rem 1.2rem;text-align:left;
box-shadow:0 0 0 1px rgba(122,63,209,.35),0 18px 60px -12px rgba(122,63,209,.55)}
.prompt p{margin:0 0 .9rem;font-size:1.02rem;line-height:1.7;color:var(--term-text)}
.prompt button{width:100%;font:600 1rem var(--sans);padding:.8rem;border-radius:9px;
border:1px solid var(--term-ok);background:var(--term-ok);color:#06130d;cursor:pointer}
.prompt button:hover{filter:brightness(1.07)}
/* honest strip: the flow, never invented counts (no fake builders stat) */
.flowline{display:flex;gap:.6rem;justify-content:center;flex-wrap:wrap;margin:2.2rem 0 0;
font:.82rem var(--mono);color:var(--term-faint)}
.flowline b{color:var(--term-ok);font-weight:600}
/* proof strip: what the tool does, never invented counts */
/* gate: the account form, one click behind the hero */
.gate{display:none;min-height:100vh;grid-template-columns:minmax(22rem,34rem) 1fr}
body.authed .hero,body.gating .hero{display:none}
body.gating .gate{display:grid}
.form{padding:clamp(2rem,6vh,4.5rem) clamp(1.5rem,4vw,3.5rem);display:flex;flex-direction:column;
justify-content:center;gap:1rem;max-width:30rem;width:100%;margin:0 auto}
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
#err{color:var(--term-bad);font:.85rem var(--mono);min-height:1.4em;margin:0}
#shelf{display:none;gap:.4rem}
#shelf.has{display:grid}
#shelf button{text-align:left;font-family:var(--mono);font-size:.85rem;background:var(--term-2);
color:var(--term-text);border-color:var(--term-line);padding:.6rem .8rem}
#newboard{display:none;gap:.5rem}
#newboard.has{display:grid}
.fine{color:var(--term-faint);font-size:.78rem}
.visual{position:relative;min-height:100vh;overflow:hidden;background:#0a0f14}
.visual canvas{position:absolute;inset:0;width:100%;height:100%}
.visual figcaption{position:absolute;left:1.5rem;bottom:1.2rem;right:1.5rem;color:#fff;
font:.85rem var(--mono);opacity:.85}
a{color:var(--term-ok)}
@media(max-width:760px){.gate{grid-template-columns:1fr}.visual{display:none}}
@media(prefers-reduced-motion:reduce){.hero canvas,.visual canvas{display:none}}
</style></head><body>
<!-- landing: hero first, account form one click behind, shelf after login -->
<header class=hero><canvas id=art aria-hidden=true></canvas>
<nav class=nav><span class=brand><svg width=22 height=22 viewBox="0 0 20 20" aria-hidden=true><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#5fd894 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#5fd894 stroke-width=1.8 stroke-linecap=round></line></svg>OCD <em>Studio</em></span>
<span class=sp></span><button id=loginbtn type=button>Log in</button>
<button id=topcta type=button>Start designing</button></nav>
<h1>Design PCBs with AI</h1>
<p class=dek>What do you want to build today?</p>
<div class=prompt><p>Describe your board — the copilot drafts the schematic,
places parts, routes traces. You stay the lead engineer.</p>
<button id=herogo type=button>Start designing with AI</button></div>
<div class=flowline><span><b>1</b> idea</span><span>→</span><span><b>2</b> schematic</span><span>→</span><span><b>3</b> layout</span><span>→</span><span><b>4</b> make</span></div>
</header>
<main class=gate><div class=form>
<div class=brand><svg width=24 height=24 viewBox="0 0 20 20" aria-hidden=true><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#5fd894 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#5fd894 stroke-width=1.8 stroke-linecap=round></line></svg>OCD <em>Studio</em></div>
<h2 id=title>Create your Studio account</h2>
<p class=sub id=sub>Create an account to start building hardware, from anywhere.</p>
<p id=err role=alert aria-live=polite></p>
<form id=f><label>Username<input id=u autocomplete=username maxlength=32 required></label>
<label>Password<input id=p type=password autocomplete=current-password minlength=8 required></label>
<button id=go type=submit>Create account</button>
<button id=swap type=button class=ghost>Have an account? Log in</button></form>
<div id=shelf role=group aria-label="your boards"></div>
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
const cols=['#c0392b','#3a7bd5','#5fd894','#9b7bd5'];
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
paint($('art'));paint($('art2'));
function gate(){document.body.classList.add('gating');}
$('herogo').onclick=gate;$('topcta').onclick=gate;
$('loginbtn').onclick=()=>{gate();setMode('login');};
async function api(p,b){const r=await fetch(p,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json();}
async function boot(){const r=await api('/auth/me',{});
if(r.user){me=r.user;gate();showShelf();}else if(r.needs_setup){setMode('signup');}else setMode('login');}
function setMode(m){mode=m;gate();
$('title').textContent=m==='signup'?'Create your Studio account':'Log in to OCD Studio';
$('go').textContent=m==='signup'?'Create account':'Log in';
$('swap').textContent=m==='signup'?'Have an account? Log in':'New here? Create an account';}
$('swap').onclick=()=>setMode(mode==='signup'?'login':'signup');
$('f').onsubmit=async e=>{e.preventDefault();$('err').textContent='';
const u=$('u').value.trim(),p=$('p').value;
if(!u){$('err').textContent="Username can't be blank!";return;}
const r=await api(mode==='signup'?'/auth/signup':'/auth/login',{user:u,password:p});
if(r.error){$('err').textContent=r.error;return;}
me=r.user||u;showShelf();};
async function showShelf(){$('f').style.display='none';
$('title').textContent='Welcome, '+me;
$('sub').textContent='Pick a board to open the workshop, or start a new one.';
const r=await api('/shelf',{});
const box=$('shelf');box.innerHTML='';box.classList.add('has');
$('newboard').classList.add('has');
if(!r.boards.length){box.innerHTML='<span class=fine>no boards yet — name one below</span>';}
r.boards.forEach(b=>{const btn=document.createElement('button');
btn.textContent=b.name+' · '+b.mtime;
btn.onclick=()=>location.href='/?board='+encodeURIComponent(b.name.replace(/\.ocd$/,''));
box.appendChild(btn);});}
$('newboard').onsubmit=async e=>{e.preventDefault();
const r=await api('/shelf/new',{name:$('nbname').value});
if(r.error){$('err').textContent=r.error;return;}
showShelf();};
boot();
</script></body></html>
"""

PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>OCD Studio</title>
<link rel=icon href="data:,">
<style>
/* Paper spec sheet, one terminal. Tokens follow the recompile.online design
   system: warm paper ground, white cards, one signal green, one dark surface
   (the job-file editor). Sans carries what a person reads; mono is the
   machine's voice (source, readouts, listings). State is a word in a pill. */
:root{
--paper:#f7f5f0;--paper-2:#efece4;--card:#fffdf8;--line:#e2ddd0;--line-2:#cfc8b6;
--ink:#1a1d21;--ink-2:#4d545c;--ink-3:#7c848c;
--signal:#0f5c37;--signal-ink:#0c4a2d;--signal-wash:#dcefe1;--ok-border:#9cc6aa;
--bad:#8a2318;--bad-wash:#f5c9c2;--danger-border:#d59f96;
--warn:#6b4a00;--warn-wash:#f2dbaa;--warn-border:#cbab72;
--term:#101418;--term-2:#1a2129;--term-line:#2a333d;--term-text:#d8e2dc;
--term-faint:#7f8b94;--term-key:#ffd8a0;--term-ok:#5fd894;--term-bad:#ff7364;
--r:10px;--r-control:9px;--shadow:0 1px 2px rgba(26,29,33,.06),0 8px 24px -12px rgba(26,29,33,.18);
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
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
.tbar{display:flex;align-items:flex-start;gap:18px;flex-wrap:wrap;flex:1}
.grp{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.lbl{font-size:.72rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);margin-right:2px}
.grp.status{margin-left:auto}
button,select,input,summary{font:600 .9rem/1.3 var(--sans);color:var(--ink);background:var(--card);border:1px solid var(--line-2);border-radius:var(--r-control);padding:9px 13px;cursor:pointer}
button:hover,select:hover,summary:hover{background:var(--paper-2)}
button:active{transform:translateY(1px)}
button.primary{background:var(--signal);border-color:var(--signal-ink);color:#fff;box-shadow:0 1px 2px rgba(12,74,45,.3)}
button.primary:hover{background:var(--signal-ink)}
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
#layers label{display:inline-flex;align-items:center;gap:4px;font:.75rem var(--mono);background:var(--paper-2);border:1px solid var(--line);border-radius:999px;padding:3px 9px;cursor:pointer}
#layers label.on{background:var(--signal-wash);border-color:var(--ok-border);color:var(--signal-ink);font-weight:600}
#layers label.off{color:var(--ink-3);text-decoration:line-through}
#layers input{width:auto;margin:0;accent-color:var(--signal)}
#layersall{margin-top:8px;font-size:.78rem;padding:5px 10px}
#partbar{display:flex;gap:6px;align-items:center;padding-bottom:6px;border-bottom:1px solid var(--line)}
#partfilter{width:100%;font-size:.82rem;padding:5px 8px}
#parthide,#partshow{font-size:.75rem;padding:4px 8px}
#partlist{max-height:15rem;overflow:auto;margin-top:4px}
#partlist label{display:flex;align-items:center;gap:6px;font:.8rem var(--mono);padding:2px 4px;border-radius:4px;cursor:pointer;white-space:nowrap}
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
#kbbar,#kbadd{display:flex;gap:8px;align-items:center;padding:10px 14px 0}
#kbbar input,#kbadd input{flex:1;min-width:0}
#kbstat{padding:8px 14px 0;font:.8rem var(--mono);color:var(--ink-2);min-height:1.3em}
#kblist{flex:1;min-height:3rem;overflow:auto;padding:8px 14px}
#kblist .kbrow{display:flex;gap:8px;align-items:baseline;padding:3px 4px;border-radius:4px}
#kblist .kbrow:hover{background:var(--paper-2)}
#kblist button.kbname{flex:1;min-width:0;background:none;border:0;padding:0;font:inherit;color:var(--signal-ink);
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
#tree div.trow{display:flex;gap:6px;align-items:baseline;padding:2px 4px;border-radius:4px;cursor:pointer;white-space:nowrap}
#tree div.trow:hover{background:var(--paper-2)}
#tree div.trow.active{background:var(--signal-wash);color:var(--signal-ink);font-weight:600}
#tree div.tdir{color:var(--ink-3);cursor:default}
#tree .tsize{color:var(--ink-3);font-size:.72rem;margin-left:auto;font-variant-numeric:tabular-nums}
#msgs{overflow:auto;padding:10px 12px;flex:1;min-height:4rem}
.msg{margin:0 0 10px;font-size:.9rem;line-height:1.55}
.msg .who{font-size:.7rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);display:block}
.msg.me{color:var(--ink-2)}
.msg.bot{color:var(--ink)}
.msg.err{color:var(--bad);background:var(--bad-wash);border:1px solid var(--danger-border);border-radius:var(--r-control);padding:8px 10px}
.msg.bot pre{background:var(--paper-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px;overflow:auto;font:.8rem/1.6 var(--mono);margin:6px 0 0}
.msg .tools{color:var(--ink-3);font:.75rem var(--mono);display:block;margin-top:4px}
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
#vcs div.rev{display:flex;gap:10px;align-items:baseline;padding:2px 0;cursor:pointer;white-space:nowrap}
#vcs div.rev:hover{color:var(--signal-ink)}
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
#gal canvas{width:190px;height:140px;border:1px solid var(--line-2);border-radius:6px;background:var(--card);cursor:pointer}
#gal figure{margin:0;text-align:center;font-size:.8rem}
#gal figcaption{color:var(--ink-3);font-variant-numeric:tabular-nums;padding-top:4px}
#gal figure:hover canvas{border-color:var(--signal)}
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
.grp.status{margin-left:0}}
@media(min-width:1500px){#srcpanels{grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);grid-template-rows:minmax(0,1fr)}}
</style></head><body>
<a class=skip href=#ed>skip to the job file</a>
<header class=top><div class=inner>
<span class=brand><svg width=20 height=20 viewBox="0 0 20 20" aria-hidden=true focusable=false><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round></line></svg>OCD Studio <i>board &amp; PCB workshop</i></span>
<span id=me class=pill title="logged in as"></span>
<button id=logoutbtn title="log out of the studio">log out</button>
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
const C={paper:'#f7f5f0',paper2:'#efece4',card:'#fffdf8',line:'#e2ddd0',line2:'#cfc8b6',ink:'#1a1d21',ink2:'#4d545c',ink3:'#7c848c',
  signal:'#0f5c37',wash:'#dcefe1',bad:'#8a2318',warn:'#6b4a00',copper:'#9a7134',
  pad:'#d9a821',padline:'#8a6d00'}; // pad copper: same pair the SVG/PNG renders use
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
      ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);ctx.lineWidth=1;}}
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
function renderAll(){if(!S||!S.cur)return;view=drawPCB(S.cur,1);drawSCH(S);if(spinOn){rot+=0.003;draw3D(S.cur,rot);dirty=true;}}
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
function statMsg(txt,ok){const el=$('stat');el.textContent=txt||'';el.className=!txt?'':ok?'ok':'err';}
let deb=null, pulseq=0; // monotonic: a slow build must not land on a newer board
function cancelPush(){clearTimeout(deb);deb=null;pulseq++;} // switching boards
$('ed').addEventListener('input',()=>{clearTimeout(deb);deb=setTimeout(push,400);});
async function push(){
  const text=$('ed').innerText, seq=++pulseq;
  const r=await api('/build',{text,src:SRCREL,thash:heldThash,placer:$('placer').value,
    router:$('router').value,fab:$('fab').value,silk:$('silk').value});
  if(seq!==pulseq)return;   // the editor moved on (or another board opened)
  if(r.error){statMsg(r.error);S=null;return;}
  statMsg('');applyState(r,false);
}
let heldThash='', heldTraces=[];
function applyState(r,live){
  if(r.thash){ // server skipped the trace list: keep the one we already have
    if(r.thash!==heldThash){heldTraces=r.traces||[];}
    r.traces=(r.traces&&r.traces.length)?r.traces:heldTraces;
    heldThash=r.thash;
  }
  S=r;S.cur=r;markDirty();spinBriefly(); // render live on the state itself (bw/bh/pours/fixed ride along)
  notePlacement(r);
  if(live&&r.frames&&r.frames.length)animate(r.frames,r.traces,()=>{drawDRC(r);});
  else{S.cur.traces=r.traces;$('cost').textContent=`cost ${r.cost}`;drawDRC(r);}
  drawFeas(r);renderLayers(r);renderParts(r);
  if(document.activeElement!==$('ed'))setEditor(r.text);
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
// --- candidate gallery: N layouts, pick → nudge (drag=fix) → re-run ---
let galSeed=0;
function thumb(cand,i){
  const fig=document.createElement('figure');
  const cv=document.createElement('canvas');cv.width=300;cv.height=220;fig.appendChild(cv);
  const cap=document.createElement('figcaption');cap.textContent=`#${i} cost ${cand.cost}`;fig.appendChild(cap);
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
  const r=await api('/candidates',{placer:$('placer').value,n,seed:galSeed,iters:400});
  if(r.error){statMsg(r.error);return;}
  galMeta={n,seed:galSeed};
  const g=$('gal');g.innerHTML='';r.candidates.forEach((c,i)=>g.appendChild(thumb(c,i)));
  $('galwrap').style.display='';
  drawFeas({feasible:r.feasible,layers:r.layers});
}
let galMeta={n:4,seed:0};
async function pickCand(i){
  const r=await api('/pick',{placer:$('placer').value,router:$('router').value,
    index:i,n:galMeta.n,seed:galMeta.seed,iters:400,silk:$('silk').value});
  if(r.error){statMsg(r.error);return;}
  statMsg('');$('galwrap').style.display='none';applyState(r,true);
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
  const gone=new Set(o?Object.keys(S.cur.parts).filter(k=>S.cur.parts[k].owner===o):[r]);
  const lines=$('ed').innerText.split('\n')
    .filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  if(lines.length!==$('ed').innerText.split('\n').length){$('ed').innerText=lines.join('\n');push();}});
})();
$('solve').onclick=async()=>{const r=await api('/solve',{placer:$('placer').value,router:$('router').value,full:true});if(r.error){statMsg(r.error);return;}statMsg('');applyState(r,true);};
$('dice').onclick=genCands;
$('fab_dl').onclick=async()=>{
  const r=await api('/export',{});
  if(r.error){statMsg(r.error);return;}
  const a=document.createElement('a');
  a.href='data:application/zip;base64,'+r.zip;a.download=r.name;a.click();
  statMsg(`${r.name} (${(r.bytes/1024).toFixed(0)}KB)`,true);
};
$('dl').onclick=async()=>{ // cycle svg → sch → png → xray (shift-click backwards)
  const keys=['svg','sch','png','xray'];
  dlIdx=(dlIdx+((window.event&&window.event.shiftKey)?-1:1)+keys.length)%keys.length;
  const key=keys[dlIdx];
  $('dl').textContent=`⤓ ${key}`;
  const r=await api('/render',{key});
  if(r.error){statMsg(r.error);return;}
  const a=document.createElement('a');
  a.href=r.bin?`data:application/octet-stream;base64,${r.data}`
    :`data:image/svg+xml,${encodeURIComponent(r.data)}`;
  if(key==='png'&&!r.bin)a.href=`data:image/png;base64,${r.data}`;
  a.download=r.name;a.click();statMsg(r.name,true);
};
let dlIdx=0;
let simWhat='dc';
$('simbtn').onclick=async()=>{ // dc ⇄ tran on shift-click
  if(window.event&&window.event.shiftKey)simWhat=simWhat==='dc'?'tran':'dc';
  $('simbtn').textContent=`⚡ ${simWhat}`;
  const r=await api('/simulate',{what:simWhat});
  if(r.error){statMsg(r.error);return;}
  if(r.sim&&Object.keys(r.sim).length)S.sim=r.sim;
  if(r.tran&&Object.keys(r.tran).length)S.tran=r.tran;
  statMsg('',true);drawDRC(S);
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
$('doc').addEventListener('toggle',async()=>{ // lazy: check on first open
  if(!$('doc').open||$('docout').dataset.done)return;
  const r=await api('/doctor',{});
  if(r.error){$('docout').textContent=r.error;return;}
  $('docout').innerHTML=(r.ok?'<div class=ok>✓ all systems</div>':'<div class=warn>degraded: features fall back, nothing crashes</div>')
    +r.checks.map(c=>`<div class=${c.ok?'ok':'err'}>${c.ok?'✓':'✗'} ${c.name}${c.detail?' <span class=dim>'+c.detail+'</span>':''}</div>`).join('');
  $('docout').dataset.done='1';
});
// undo/redo: server keeps text history (git-style log); undo restores + rebuilds
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
  const d=document.createElement('div');
  d.className='trow '+(e.kind==='dir'?'tdir':'tfile')+(e.path===SRCREL?' active':'');
  d.style.paddingLeft=(4+depth*13)+'px';
  d.textContent=e.kind==='dir'?e.name+'/':e.name;
  d.title=e.kind==='dir'?'folder — click to open it':e.path;
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
  if(!TREE.length){const e=document.createElement('div');e.className='trow tdir';e.textContent='(no text files)';t.appendChild(e);}
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
  const r=await api('/fs/open',{path});
  if(r.error){statMsg(r.error);return;}
  statMsg('');$('msgs').innerHTML='';
  heldThash='';heldTraces=[];  // a different board: its traces are not ours
  setQueue([]);  // the server dropped the old board's proposals with it
  applyState(r,false);
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
  const w=document.createElement('span');w.className='who';w.textContent=who;
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
  msg('you',text);
  const wait=msg('bot','thinking…');
  const r=await api('/chat',{text,auto:!!auto});
  wait.remove();
  if(r.error){msg('err',r.error);setQueue(r.proposals);return;}
  msg('bot',r.reply||'(no reply)');
  if(r.log&&r.log.length)msg('bot','tools: '+r.log.join(' · '));
  if(r.note)msg('bot',r.note);
  if(r.proposals&&r.proposals.length)setQueue(r.proposals);
  if(r.state)applyState(r.state,false);
  if(r.applied)loadVCS();
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
    const d=document.createElement('div');d.className='rev';
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
  const b=document.createElement('div');b.className='toast';b.textContent=t;
  document.body.appendChild(b);
  setTimeout(()=>b.remove(),2600);
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
  const r=await api('/load',{});
  $('placer').innerHTML=r.placers.map(p=>`<option>${p}</option>`).join('');
  $('router').innerHTML=r.routers.map(p=>`<option>${p}</option>`).join('');
  $('silk').innerHTML=r.silks.map(p=>`<option ${p===r.silk?'selected':''}>${p}</option>`).join('');
  $('fab').innerHTML=r.fabs.map(p=>`<option>${p}</option>`).join('');
  setEditor(r.text);applyState(r,false);
  const f=await fetch('/fs').then(x=>x.json()); // browser state is a GET
  if(f.error){$('treenote').textContent=f.error;return;}
  DIR=f.base||'.';ROOTREL=f.root||'.';TREE=f.tree||[];SRCREL=f.src||'';VC=f.vcs||{};
  $('srcnote').textContent=`${SRCREL} · ${f.base||'.'} · saved on every good build`;
  $('chatwhere').textContent=SRCREL;
  $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
  renderTree();
  loadVCS();
  $('logoutbtn').onclick=async()=>{await api('/auth/logout',{});location.href='/';};
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
  const b=document.createElement('div');
  b.id='extbanner';b.style.cssText='background:#7a3;color:#fff;padding:4px 8px;cursor:pointer';
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
    from ocdcircuit.plugins import sch_layout
    lay = sch_layout(b)
    return {"order": lay["order"], "px": lay["px"], "rail_y": lay["rail_y"],
            "top": lay["top"], "W": lay["W"]}


def board_state(b: Board, text: str, frames: list[dict[str, object]],
                traces: list[dict[str, object]], cost: float,
                drc: dict[str, object]) -> dict[str, object]:
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
ROOT = os.path.abspath(os.environ.get("OCD_ROOT") or BASE)
START_DIR = BASE  # the board directory as launched, before any /fs/open
if not os.path.isdir(ROOT):  # a bad OCD_ROOT must not take the studio down
    print(f"studio: OCD_ROOT {os.environ.get('OCD_ROOT')!r} is not a directory, "
          f"using {BASE}", file=sys.stderr)
    ROOT = BASE


# --- accounts: local users with salted passwords, cookie sessions -----------
# stdlib only (hashlib scrypt + secrets): no new deps. Users live one per line
# in <ROOT>/.ocd-users (name:salt_hex:hash_hex). Sessions are bearer tokens in
# memory — a restart re-asks the login. This studio is single-tenant by
# design: the first signup owns it; later signups are refused (add invites
# when multi-user matters).
_AUTH_COOKIE = "ocd_user"
_USERS_FILE = ".ocd-users"
_SESSIONS: dict[str, str] = {}  # token -> username


def _users_path() -> str:
    return os.path.join(ROOT, _USERS_FILE)


def _read_users() -> dict[str, tuple[str, str]]:
    """name -> (salt_hex, hash_hex). Missing file = no accounts yet."""
    out: dict[str, tuple[str, str]] = {}
    try:
        with open(_users_path()) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) == 3 and parts[0]:
                    out[parts[0]] = (parts[1], parts[2])
    except OSError:
        pass
    return out


def _write_user(name: str, password: str) -> None:
    import hashlib
    import secrets
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    with open(_users_path(), "a", encoding="utf8") as f:
        f.write(f"{name}:{salt.hex()}:{digest.hex()}\n")
    # secrets must never be committed: keep them out of git on first signup
    try:
        gi = os.path.join(ROOT, ".gitignore")
        have = open(gi).read() if os.path.isfile(gi) else ""
        if _USERS_FILE not in have:
            with open(gi, "a", encoding="utf8") as f:
                if have and not have.endswith("\n"):
                    f.write("\n")
                f.write(GITIGNORE_AUTH)
    except OSError:
        pass


def _check_user(name: str, password: str) -> bool:
    import hashlib
    import hmac
    users = _read_users()
    if name not in users:
        return False
    salt_hex, want = users[name]
    try:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=16384, r=8, p=1)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), want)


def _new_session(name: str) -> str:
    import secrets
    tok = secrets.token_urlsafe(32)
    _SESSIONS[tok] = name
    while len(_SESSIONS) > 64:  # ponytail: cap sessions in memory; restart clears all anyway
        _SESSIONS.pop(next(iter(_SESSIONS)))
    return tok


def _authed(headers: object) -> str | None:
    """Username for a valid session cookie, else None."""
    get = getattr(headers, "get", None)
    cookie = get("Cookie", "") if get else ""
    for chunk in str(cookie).split(";"):
        k, _, v = chunk.strip().partition("=")
        if k.strip() == _AUTH_COOKIE and v.strip() in _SESSIONS:
            return _SESSIONS[v.strip()]
    return None


def _user_dir(name: str) -> str:
    """Per-user shelf: <ROOT>/.users/<name>/ for boards (hidden from _tree)."""
    d = os.path.join(ROOT, ".users", name)
    os.makedirs(d, exist_ok=True)
    return d


STARTER_OCD = """board {name} 40x30
part R1 R0805 10k
part C1 C0805 100n
net N: R1.2 C1.1
net GND: R1.1 C1.2
"""


def _shelf(name: str) -> list[dict[str, str]]:
    """The user's boards: name, size, modified."""
    import time
    d = _user_dir(name)
    rows: list[dict[str, str]] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".ocd"):
            continue
        try:
            st = os.stat(os.path.join(d, fn))
            rows.append({"name": fn, "bytes": str(st.st_size),
                         "mtime": time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(st.st_mtime))})
        except OSError:
            continue
    return rows


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
    `blinky_555.ocd` cannot be reached from a board opened in a subdirectory."""
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
    for s in seeds:
        base = os.path.realpath(s)
        if base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        rp = os.path.realpath(os.path.join(base, rel))
        if rp != root and not rp.startswith(root + os.sep):
            blocked = True  # a traversal: say so, do not call it "missing"
            continue
        inside = rp
        if not must_exist or os.path.exists(rp):
            return rp
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
        return open(full, encoding="utf8").read()
    except UnicodeDecodeError as e:
        raise ValueError(f"{rel}: not utf-8 text") from e


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
    except Exception:
        return f"(the current {os.path.basename(SRC)} does not parse)"
    parts = ", ".join(f"{r}={p.fp}" + (f"({p.value})" if p.value else "")
                      for r, p in sorted(b.parts.items()))
    nets = "; ".join(f"{n}: " + " ".join(f"{r}.{pin}" for r, pin in net.pins)
                     for n, net in sorted(b.nets.items()))
    return (f"open file: {os.path.relpath(SRC, ROOT)}  "
            f"board {b.name} {b.width:g}x{b.height:g} {b.layers}L\n"
            f"parts: {parts}\nnets: {nets}")


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
    return {"dir": k.dir, "docs": k.docs(limit=KB_LIST_LIMIT), "total": k.count(),
            "limit": KB_LIST_LIMIT, "busy": H.kb_busy, "log": list(H.kb_log)}


def _kb_fetch_start() -> dict[str, object]:
    """Fetch out of process: `ocd kb fetch` already does exactly this job, and
    the panel polls /kb/list while it runs.

    Why a subprocess and not a thread: this server is single-threaded, the
    Board it fetches from is ~2.8s of Context journaling on a 5420-part design,
    and CPython's GIL hands that CPU-bound loop the interpreter in 5ms slices —
    measured UI stalls of 0.45-0.64s per request while a worker thread parsed
    it, versus 1.5ms flat with the work in another process."""
    import subprocess
    import threading
    if H.kb_busy:
        return {"started": False, "note": "already fetching", "log": list(H.kb_log)}
    if not os.path.isfile(SRC):
        return {"started": False, "error": f"no such board file: {SRC}"}
    H.kb_busy = True
    H.kb_log = ["fetching datasheets…"]

    def work() -> None:
        try:
            # cwd/PYTHONPATH point at the checkout, not the board: ROOT is the
            # project the board lives in, which need not be this repo.
            p = subprocess.Popen([sys.executable, "-m", "apps.ocd", "kb", "fetch", SRC],
                                 cwd=HERE, env={**os.environ, "PYTHONPATH": HERE},
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            assert p.stdout is not None
            for line in p.stdout:  # the CLI's lines ARE the progress log
                line = line.strip()
                if line:
                    H.kb_log.append(line)
                    H.kb_log[:] = H.kb_log[-12:]
            code = p.wait()
            H.kb_log.append(f"done — exit {code}")
        except Exception as e:  # noqa: BLE001 — a worker thread must not die silent
            H.kb_log.append(f"error: {e}")
        H.kb_busy = False

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "note": "fetching datasheets — the list fills in as they land"}



class H(http.server.BaseHTTPRequestHandler):
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

    @staticmethod
    def open_file(path: object) -> None:
        """Switch the open board: SRC/BASE are module globals (every route
        resolves `use` includes against BASE), so set both and re-init.
        ROOT — the project the browser and the agent may touch — stays put:
        it is the directory the studio was started in, and switching boards
        inside it (including to a sibling project) is the point."""
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
            with open(full, "w", encoding="utf8") as f:
                f.write(text)
            return {"path": rel, "state": None}
        st = H._build(text, False)  # raises on a parse error: nothing written
        H.src_text = str(st["text"])
        H.commit(H.src_text)  # bumps the revision: earlier proposals go stale
        H.save()
        return {"path": rel, "state": st}

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
                refused.append(str(e))
                continue
            props.append({"id": rel, "path": rel, "text": text,
                          "diff": _unified(old, text, rel), "rev": H.rev})
        H.props = props + [p for p in H.props if str(p["id"]) not in
                           {str(q["id"]) for q in props}]
        return props, refused

    @staticmethod
    def ask(message: str, auto: bool) -> dict[str, object]:
        """One chat turn. The model may answer or propose file edits; each
        proposal is diffed, and auto-applied only when it builds DRC-clean."""
        from ocdcircuit import llm as _llm
        H.chat.append({"role": "user", "content": message})
        H.chat = H.chat[-24:]
        ctx = _board_digest()
        tools = {"fs.list": lambda p, _b: "\n".join(
                     f"{e['name']}{'/' if e['kind'] == 'dir' else ''}"
                     for e in _tree(p or ".")),
                 "fs.read": lambda p, _b: _read(p)}
        msgs = ([{"role": "system", "content": "Current board:\n" + ctx}]
                if ctx else []) + H.chat
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

    @staticmethod
    def apply_one(pid: str) -> dict[str, object]:
        """Apply one queued proposal by id, refusing one made against an
        older revision of the open board (the text the model read is gone)."""
        prop = next((p for p in H.props if str(p["id"]) == pid), None)
        if prop is None:
            return {"error": f"{pid}: no such proposal (ask again)"}
        if int(cast(int, prop["rev"])) != H.rev:
            return {"error": f"{pid}: the board changed since this proposal "
                             "was made — ask again or apply it by hand"}
        out = H._apply(str(prop["path"]), str(prop["text"]))
        H.props = [p for p in H.props if str(p["id"]) != pid]
        return out

    @staticmethod
    def _disk() -> str:
        try:
            return open(SRC).read()
        except OSError:
            return ""

    @staticmethod
    def commit(text: str) -> None:
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
    def save() -> None:
        """Persist the .ocd source of truth to disk (edits are real)."""
        text = H.src_text if H.src_text.endswith("\n") else H.src_text + "\n"
        if H.save_target and os.path.abspath(H.save_target) != os.path.abspath(SRC):
            print(f"studio: refusing to save to {SRC}: the buffer belongs to "
                  f"{H.save_target}", file=sys.stderr)
            return
        try:
            old = os.path.getsize(SRC)
        except OSError:
            old = 0
        if old and len(text) < old * H.SHRINK:
            print(f"studio: refusing to write {len(text)} bytes over {old} bytes "
                  f"at {SRC} (wrong board?)", file=sys.stderr)
            return
        try:
            with open(SRC, "w") as f:
                f.write(text)
            H.saved_text = H.src_text
            H.save_target = SRC
        except OSError as e:
            print(f"studio: save failed: {e}", file=sys.stderr)

    def _send(self, obj: object, cookie: str | None = None) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            # HttpOnly + SameSite=Lax: the browser holds it, JS never reads it
            self.send_header("Set-Cookie",
                             f"{_AUTH_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax")
        elif cookie == "":
            self.send_header("Set-Cookie",
                             f"{_AUTH_COOKIE}=; Path=/; Max-Age=0")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/slots":
            # plugin-inventory surface: slot → [ids] (harness inventory shape)
            inv = {s: SLOTS.report(s) for s in UiSlots.slots}
            self._send(inv)
            return
        if self.path == "/poll":
            import hashlib
            disk = H._disk()
            self._send({"hash": hashlib.md5(disk.encode()).hexdigest(),
                        "clean": disk == H.saved_text})
            return
        if self.path.startswith("/fs"):
            # project browser: ?dir= picks the directory (default: the board's
            # own). Paths come back relative to ROOT, so /fs/open can take them.
            try:
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                base = os.path.relpath(BASE, ROOT).replace(os.sep, "/")
                d = (q.get("dir") or [base])[0] or base
                self._send({"root": os.path.relpath(ROOT, os.getcwd()),
                            "src": os.path.relpath(SRC, ROOT).replace(os.sep, "/"),
                            "base": base, "dir": _rel(d),
                            "tree": _tree(d), "vcs": _git_status()})
            except ValueError as e:
                self._send({"error": str(e)})
            return
        if self.path != "/" and not self.path.startswith("/?"):
            self.send_response(204)  # favicon etc: silent, no console 404
            self.end_headers()
            return
        # members' workshop: no session cookie → the login screen. /auth/*
        # stays open (it is how you get the cookie). The gate lives here, not
        # in a proxy, so `python -m apps.studio` is the whole setup.
        user = _authed(self.headers)
        if user is None:
            body = LOGIN_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        want = (q.get("board") or [""])[0]
        if want:
            # shelf boards only: alnum/_/- inside the user's own dir, else the
            # launch board. Server-side: the cookie names the user, the query
            # names only the file.
            clean = "".join(c for c in want if c.isalnum() or c in "_-")[:32]
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
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        print(f"REQ {self.path} src={os.path.relpath(SRC, ROOT)} "
              f"want={req.get('src')!r}", flush=True, file=sys.stderr)
        try:
            if self.path.startswith("/auth/"):
                pass  # the gate is the page; these routes ARE the keyhole
            elif _authed(self.headers) is None:
                self._send({"error": "log in first", "login": True})
                return
            if self.path == "/auth/signup":
                name = str(req.get("user", "")).strip()
                password = str(req.get("password", ""))
                if not name or not password:
                    self._send({"error": "a name and a password, both"})
                elif not name.replace("_", "").replace("-", "").isalnum() or len(name) > 32:
                    self._send({"error": "names are letters, digits, _ and - (32 max)"})
                elif len(password) < 8:
                    self._send({"error": "password needs 8+ characters"})
                elif name in _read_users():
                    self._send({"error": f"{name} exists — log in instead"})
                elif _read_users():
                    # single-tenant: first account owns the studio (invites later)
                    self._send({"error": "this studio already has an account"})
                else:
                    _write_user(name, password)
                    self._send({"ok": True, "user": name}, cookie=_new_session(name))
            elif self.path == "/auth/login":
                name, password = str(req.get("user", "")).strip(), str(req.get("password", ""))
                if not _check_user(name, password):
                    self._send({"error": "wrong name or password"})
                else:
                    self._send({"ok": True, "user": name}, cookie=_new_session(name))
            elif self.path == "/auth/logout":
                get = getattr(self.headers, "get", None)
                for chunk in str(get("Cookie", "") if get else "").split(";"):
                    k, _, v = chunk.strip().partition("=")
                    if k.strip() == _AUTH_COOKIE:
                        _SESSIONS.pop(v.strip(), None)
                self._send({"ok": True}, cookie="")
            elif self.path == "/auth/me":
                user = _authed(self.headers)
                self._send({"user": user, "needs_setup": not _read_users()})
            elif self.path == "/shelf":
                user = _authed(self.headers)
                assert user is not None  # gated above
                self._send({"user": user, "boards": _shelf(user)})
            elif self.path == "/shelf/new":
                user = _authed(self.headers)
                assert user is not None  # gated above
                raw = str(req.get("name", "")).strip().lower()
                name = "".join(c for c in raw if c.isalnum() or c in "_-")[:32]
                if not name or name in ("users",):
                    self._send({"error": "give the board a usable name"})
                else:
                    fn = name + ".ocd"
                    full = os.path.join(_user_dir(user), fn)
                    if os.path.exists(full):
                        self._send({"error": f"{fn} already on your shelf"})
                    else:
                        with open(full, "w", encoding="utf8") as f:
                            f.write(STARTER_OCD.format(name=name))
                        self._send({"ok": True, "boards": _shelf(user)})
            elif self.path == "/init":
                self._send(self._build(H.src_text, True))
            elif self.path == "/load":
                # Open a board without re-placing it: parse + route + DRC only.
                # Placing 5k parts is minutes, and the file already says where
                # they go; `solve` is the explicit ask for a fresh placement.
                self._send(self._build(H.src_text, False, {"no_place": True}))
            elif self.path == "/reload":
                # file-watch: adopt external edits (client asks only when
                # clean, or the user confirmed the banner).
                H.src_text = H._disk() or H.src_text
                H.commit(H.src_text)
                H.saved_text = H.src_text
                self._send(self._build(H.src_text, True))
            elif self.path == "/build":
                text = str(req.get("text", H.src_text))
                want = req.get("src")
                if isinstance(want, str) and want and want != os.path.relpath(SRC, ROOT):
                    # the browser was editing a different board when this
                    # keystroke was captured: drop it, the caller re-reads
                    self._send({"stale": True, "error": f"board changed to "
                                f"{os.path.relpath(SRC, ROOT)} — edit again"})
                    return
                st = self._build(text, False, req)
                H.src_text = str(st["text"])  # only keep good builds
                H.save_target = SRC
                H.commit(H.src_text)
                # The client builds the board it has open; if the text was not
                # from SRC at all (a stale tab, a pasted board), do not write it
                # over the file on disk.
                H.save()
                self._send(st)
            elif self.path == "/solve":
                st = self._build(H.src_text, True, req)
                H.src_text = str(st["text"])
                H.save_target = SRC
                H.commit(H.src_text)
                H.save()
                self._send(st)
            elif self.path == "/candidates":
                from ocdcircuit import solver as _solver
                b = agent.loads(H.src_text, base=BASE)
                key = req.get("placer")
                assert key is None or isinstance(key, str)
                n = _i(req.get("n"), 4)
                cands = _solver.candidates(b, n=n, key=key,
                                           seed=_i(req.get("seed"), 0),
                                           seeds=1, iters=_i(req.get("iters"), 400))
                # feasibility on the best candidate (unplaced text proves nothing)
                _solver.restore_candidate(b, cands[0])
                snap = b.ctx.snapshot()
                try:
                    feas = _solver.feasible(b)
                finally:
                    b.ctx.rollback(snap)
                self._send({"candidates": cands, "feasible": feas,
                            "layers": b.layers})
            elif self.path == "/pick":
                from ocdcircuit import solver as _solver
                # (cast is imported at module level; a local import here would
                # shadow it for every earlier branch in this function)
                b = agent.loads(H.src_text, base=BASE)
                key = req.get("placer")
                assert key is None or isinstance(key, str)
                idx = _i(req.get("index"), 0)
                cands = _solver.candidates(b, n=_i(req.get("n"), 4), key=key,
                                           seed=_i(req.get("seed"), 0),
                                           seeds=1, iters=_i(req.get("iters"), 400))
                if not 0 <= idx < len(cands):
                    self._send({"error": f"index {idx} out of range"})
                    return
                _solver.restore_candidate(b, cands[idx])
                router = str(req.get("router", "lroute"))
                b.route_board(router)
                drc = b.check()
                st = board_state(b, agent.dumps(b), [], [
                    {"x1": t.x1, "y1": t.y1, "x2": t.x2,
                     "y2": t.y2, "layer": t.layer, "w": t.width}
                    for t in b.traces], cast(float, cands[idx]["cost"]), drc)
                H._decorate(st, b, b.score(tidy=True), _solver.feasible(b),
                            b.plugins().list("placer"), b.plugins().list("router"),
                            req.get("silk", "full"), b.plugins().list("silk"))
                H.src_text = str(st["text"])
                H.commit(H.src_text)
                H.save()
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
                key = str(req.get("key", "svg"))  # svg|sch|png|stl|gltf|xray|…
                b = agent.loads(H.src_text, base=BASE)
                b.configure("toml", base=BASE)
                b.place()
                b.route_board()
                out = b.render(key)
                ext = {"svg": "svg", "sch": "sch.svg", "png": "png",
                       "stl": "stl", "gltf": "glb", "xray": "xray.svg"}.get(key, key)
                if isinstance(out, bytes):
                    self._send({"data": base64.b64encode(out).decode(),
                                "bin": True, "name": f"{b.name}.{ext}"})
                else:
                    self._send({"data": out if isinstance(out, str) else "\n".join(out),
                                "bin": False, "name": f"{b.name}.{ext}"})
            elif self.path == "/xray":  # fab scan (base64 PNG) vs design
                import base64
                import binascii
                data = req.get("png", req.get("data", ""))
                assert isinstance(data, str) and data
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
                    self._send({"error": f"{type(e).__name__}: {e}"})
                    return
                divs = r.get("divs")
                assert isinstance(divs, list)
                self._send({"score": r["score"], "missing": r["missing"],
                            "extra": r["extra"], "divs": divs[:20],
                            "overlay": r["overlay"]})
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
                    H.save()
                    self._send(self._build(H.src_text, False))
            elif self.path == "/redo":
                if not H.redo:
                    self._send({"error": "nothing to redo"})
                else:
                    H.src_text = H.redo.pop()
                    H.commit(H.src_text)
                    H.save()
                    self._send(self._build(H.src_text, False))
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
                    self._send({"error": f"ValueError: {e}"})
            elif self.path == "/kb/search":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.search(str(req.get("q", "")),
                                        limit=_i(req.get("limit"), 8)))
                except (ValueError, OSError) as e:
                    self._send({"error": f"ValueError: {e}"})
            elif self.path == "/kb/ask":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.ask(str(req.get("q", "")),
                                      k=_i(req.get("limit"), 6),
                                      answer=bool(req.get("answer"))))
                except (ValueError, OSError) as e:
                    self._send({"error": f"ValueError: {e}"})
            elif self.path == "/kb/add":
                from ocdcircuit.kb import KB
                kb = _kb()
                assert isinstance(kb, KB)
                try:
                    self._send(kb.add(str(req.get("src", ""))))
                except (ValueError, OSError) as e:
                    self._send({"error": f"ValueError: {e}"})
            elif self.path == "/kb/fetch":
                self._send(_kb_fetch_start())
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
            elif self.path == "/vcs":
                self._send({"log": _git_log(40, req.get("path")),
                            "status": _git_status()})
            elif self.path == "/vcs/diff":
                h = str(req.get("hash", ""))
                if h:
                    self._send({"diff": _git("show", "--stat", "--patch",
                                             "--no-color", h)[:20000]})
                else:
                    rel = _rel(os.path.relpath(SRC, ROOT))
                    self._send({"diff": _git("diff", "--no-color", "--", rel)[:20000]})
            elif self.path == "/vcs/commit":
                rel = _rel(os.path.relpath(SRC, ROOT))
                msg = str(req.get("message", "")).strip() or "studio: update " + rel
                _git("add", "--", rel)
                out = _git("commit", "-m", msg)
                self._send({"ok": True, "commit": out.strip().splitlines()[-1][:200],
                            "log": _git_log(40, rel), "status": _git_status()})
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as e:  # never 500 the UI thread: report, keep serving
            self._send({"error": f"{type(e).__name__}: {e}"})

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
            st_tidy = b.score(tidy=True)
            st_score = b.score()
        st_lint = b.lint()
        from ocdcircuit import solver as _solver
        feas = {} if dense else _solver.feasible(b)
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
        tkey = _hashlib.sha1(repr(traces).encode()).hexdigest()[:12]
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
        # recommend() is a module function over parts/nets (report-only);
        # it was being called as a Board method, which does not exist.
        st["recommend"] = recommend(b)

    def log_message(self, *a: object) -> None:
        pass


def main() -> None:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: python -m apps.studio [board.ocd]  # OCD_PORT=8077 to change port")
        return
    H.src_text = open(SRC).read() if os.path.isfile(SRC) else (
        "board demo 40x30\npart R1 R0805 1k\npart C1 C0805 100n\n"
        "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n")
    H.saved_text = H.src_text
    H.save_target = SRC
    H.commit(H.src_text)  # genesis commit — undo floor
    H.root = ROOT        # project browser/agent root (OCD_ROOT or the board's dir)
    H.props = []         # no proposals pending
    H.rev = 0            # revision 1 is the genesis commit above
    try:
        port = int(os.environ.get("OCD_PORT", "8077"))
    except ValueError:
        print(f"studio: bad OCD_PORT {os.environ.get('OCD_PORT')!r}, using 8077",
              file=sys.stderr)
        port = 8077
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    print(f"OCD Studio: http://localhost:{port}  ({SRC})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
