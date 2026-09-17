// Workshop chrome — Preact + htm (no build step; see html.js).
// The header (brand, presence, theme, view tabs, the whole menubar) used to be
// a Python string in apps/studio.py; it lives here now. Behaviour stays in
// legacy.js: this module renders the same ids and nothing else, so the canvas
// loops, drag/drop and the collab stream keep talking to real DOM nodes.
// Panels and plugin contributions arrive as markup (slot islands) and are
// mounted once with innerHTML — preact never re-renders what it does not own.
// htm rule: every void element needs its slash (<input ... />).
import { render } from './vendor/preact.module.js';
import html from './html.js';

const BRAND_SVG = html`<svg width=20 height=20 viewBox="0 0 20 20" aria-hidden=true focusable=false
  ><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect
  ><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#0f5c37 stroke-width=1.8
    stroke-linecap=round stroke-linejoin=round></path
  ><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round></line
  ></svg>`;

const Brand = () => html`<span class=brand>${BRAND_SVG}OCD Studio <i>board & PCB workshop</i></span>`;

// engine pickers: legacy.js fills the options and reads the selection
const Engines = () => html`<details class=menu id=m-engines>
  <summary title="placement, routing, fab and silk engines">Engines</summary><div class=mpop>
  <label>placer<select id=placer title="placement engine"></select></label>
  <label>router<select id=router title="routing engine"></select></label>
  <label>fab<select id=fab title="fab rules (edge, clearance, min trace)"></select></label>
  <label>silk<select id=silk title="silkscreen density"></select></label>
  </div></details>`;

// calculators / health / quote: the readouts (#cout …) stay legacy-owned
const Tools = () => html`<details class=menu id=m-tools>
  <summary title="agent, calculators, prices, health">Tools</summary><div class=mpop>
  <button id=chatbtn type=button aria-pressed=false title="show or hide the agent chat panel">chat</button>
  <label class=auto title="apply a proposal without asking, but only when it builds DRC-clean"
    ><input type=checkbox id=chatauto />auto-apply clean proposals</label>
  <details id=calc title="trace width and divider calculators"><summary>calc</summary>
    <label>A <input id=ca size=4 value=1 aria-label="trace current A" /></label>
    <label>dT <input id=cdt size=3 value=10 aria-label="temperature rise C" /></label>
    <div id=cout></div>
    <label>V <input id=dv size=4 value=5 aria-label="divider input V" /></label>
    <label>Rt <input id=drt size=5 value=10k aria-label="divider top R" /></label>
    <label>Rb <input id=drb size=5 value=10k aria-label="divider bottom R" /></label>
    <div id=dout></div>
    <label>w <input id=zw size=4 value=0.3 aria-label="microstrip width mm" /></label>
    <label>h <input id=zh size=4 value=0.2 aria-label="dielectric height mm" /></label>
    <div id=zout></div></details>
  <details id=doc title="tooling health: python, ngspice, plugins"><summary>health</summary>
    <div id=docout>click to check</div></details>
  <details id=quote title="fab price comparison: bare per fab, JLC assembled"><summary>quote</summary>
    <label>qty <input id=qqty value=5 size=3 aria-label="boards ordered" /></label>
    <label><input type=checkbox id=qbare /> bare only</label>
    <button id=qgo type=button class=primary title="compare fab prices for the open board">compare</button>
    <div id=qout role=status aria-live=polite></div></details>
  </div></details>`;

// The menubar is a nav of <details>; legacy.js closes the others on open and
// matches .menubar>details.menu, so this structure is load-bearing.
const Menubar = () => html`<nav class=menubar aria-label="board menus">
  <button id=solve class=primary title="full solve, 5 seeds x 500 iters (Ctrl+Enter)">solve</button>
  <details class=menu id=m-board><summary title="board outputs and layouts">Board</summary><div class=mpop>
    <div class=mrow><button id=dice title="generate N candidate layouts side by side">candidates</button>
      <input id=ncand value=4 size=1 aria-label="candidate count" title="candidate count" /></div>
    <button id=stamp title="stamp another copy of the hovered instance">stamp instance</button>
    <button id=fab_dl title="download the fab bundle as one zip">fab zip</button>
    <button id=dl title="download a render (cycles svg, sch, png, xray; shift-click backwards)">download render</button>
    </div></details>
  <details class=menu id=m-edit><summary title="undo history and revisions">Edit</summary><div class=mpop>
    <button id=undo title="undo (Ctrl+Z)">undo<span class=kbd>Ctrl+Z</span></button>
    <button id=redo title="redo (Ctrl+Y)">redo<span class=kbd>Ctrl+Y</span></button>
    <button id=diffprev title="what changed since the previous revision">diff</button>
    <button id=commit title="commit the open file to git (Ctrl+S)">commit<span class=kbd>Ctrl+S</span></button>
    </div></details>
  <${Engines} />
  <details class=menu id=m-sim><summary title="simulate the current board">Simulate</summary><div class=mpop>
    <button id=simbtn title="simulate the current board (shift-click: tran)">sim dc</button>
    <div class=mnote>shift-click toggles dc/tran · needs sim lines</div>
    </div></details>
  <${Tools} />
  <details class=menu id=m-live><summary title="who is on this board and how to invite">Share</summary><div class=mpop>
    <div class=mnote id=roomnote>live on this board</div>
    <button id=sharebtn title="copy a link to this board">copy invite link</button></div></details>
  <div class=mastat><span id=cost class=pill title="total wirelength">cost</span>
    <span id=ocdscore class=pill title="OCD neatness badge 0-100 — do not compare across boards"></span>
    <span id=feas class=pill title="routing feasibility per layer count"></span>
    <span id=stat role=status aria-live=polite></span></div>
  </nav>`;

const VIEWS = [
  ['all', 'every panel at once (the cockpit)', 'All', true],
  ['pcb', 'PCB layout', 'Layout', false],
  ['sch', 'schematic', 'Schematic', false],
  ['t3d', '3D preview', '3D', false],
  ['docs', 'notes and datasheets', 'Docs', false],
];

const Chrome = () => html`<header class=top><div class=inner>
  <${Brand} />
  <span id=room role=status aria-live=polite class=pill title="who else is on this board right now">solo</span>
  <span id=me class=pill title="logged in as"></span>
  <button id=logoutbtn title="log out of the studio">log out</button>
  <button id=themebtn type=button aria-pressed=false title="toggle Flux-dark theme (paper ↔ dark)">dark</button>
  <nav id=viewtabs role=tablist aria-label="views"
    title="pick a view, or All for every panel at once"
    >${VIEWS.map(([v, title, label, on]) => html`<button data-v=${v} role=tab key=${v}
      aria-selected=${on ? 'true' : 'false'} tabindex=${on ? 0 : -1} class=${on ? 'on' : ''}
      title=${title}>${label}</button>`)}</nav>
  <${Menubar} />
  <span id=plugintools style="display:contents"></span>
  </div></header>
  <main id=panels></main>`;

render(html`<${Chrome} />`, document.getElementById('app'));

// Slot islands: app/studio.py renders whatever is registered (built-in panels
// now, plugin panels always) and this mounts it verbatim, once.
const slots = JSON.parse(document.getElementById('slots').textContent || '{}');
const mount = (id, markup) => {
  const el = document.getElementById(id);
  if (el) el.innerHTML = markup || '';
};
mount('plugintools', slots.toolbar);
mount('panels', slots.view);
