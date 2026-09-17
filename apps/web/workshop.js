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
import { ui, useUI } from './store.js';
import { Panels } from './panels.js';
import { CalcBlock, DiceButton, DlButton, Engines, ExtBanner, FabDlButton,
         HealthOut, QgoButton, QuoteOut, SimButton, SolveButton,
         Toast } from './views.js';

const BRAND_SVG = html`<svg width=20 height=20 viewBox="0 0 20 20" aria-hidden=true focusable=false
  ><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect
  ><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#0f5c37 stroke-width=1.8
    stroke-linecap=round stroke-linejoin=round></path
  ><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#0f5c37 stroke-width=1.8 stroke-linecap=round></line
  ></svg>`;

const Brand = () => html`<span class=brand>${BRAND_SVG}OCD Studio <i>board & PCB workshop</i></span>`;

// chat is a toggle with state behind it (body.chatty + the button look)
const ChatBtn = () => {
  const on = useUI().chat;
  return html`<button id=chatbtn type=button aria-pressed=${on ? 'true' : 'false'}
    class=${on ? 'primary' : ''}
    title="show or hide the agent chat panel">chat</button>`;
};

// calculators / health / quote: the readouts (#cout …) stay legacy-owned
const Tools = () => html`<details class=menu id=m-tools>
  <summary title="agent, calculators, prices, health">Tools</summary><div class=mpop>
  <${ChatBtn} />
  <label class=auto title="apply a proposal without asking, but only when it builds DRC-clean"
    ><input type=checkbox id=chatauto />auto-apply clean proposals</label>
  <${CalcBlock} />
  <details id=doc title="tooling health: python, ngspice, plugins"><summary>health</summary>
    <${HealthOut} /></details>
  <details id=quote title="fab price comparison: bare per fab, JLC assembled"><summary>quote</summary>
    <label>qty <input id=qqty value=5 size=3 aria-label="boards ordered" /></label>
    <label><input type=checkbox id=qbare /> bare only</label>
    <${QgoButton} />
    <${QuoteOut} /></details>
  </div></details>`;

// The menubar is a nav of <details>; legacy.js closes the others on open and
// matches .menubar>details.menu, so this structure is load-bearing.
const Menubar = () => html`<nav class=menubar aria-label="board menus">
  <${SolveButton} />
  <details class=menu id=m-board><summary title="board outputs and layouts">Board</summary><div class=mpop>
    <div class=mrow><${DiceButton} />
      <input id=ncand value=4 size=1 aria-label="candidate count" title="candidate count" /></div>
    <button id=stamp title="stamp another copy of the hovered instance">stamp instance</button>
    <${FabDlButton} />
    <${DlButton} />
    </div></details>
  <details class=menu id=m-edit><summary title="undo history and revisions">Edit</summary><div class=mpop>
    <button id=undo title="undo (Ctrl+Z)">undo<span class=kbd>Ctrl+Z</span></button>
    <button id=redo title="redo (Ctrl+Y)">redo<span class=kbd>Ctrl+Y</span></button>
    <button id=diffprev title="what changed since the previous revision">diff</button>
    <button id=commit title="commit the open file to git (Ctrl+S)">commit<span class=kbd>Ctrl+S</span></button>
    </div></details>
  <${Engines} />
  <details class=menu id=m-sim><summary title="simulate the current board">Simulate</summary><div class=mpop>
    <${SimButton} />
    <div class=mnote>shift-click toggles dc/tran · needs sim lines</div>
    </div></details>
  <${Tools} />
  <details class=menu id=m-live><summary title="who is on this board and how to invite">Share</summary><div class=mpop>
    <div class=mnote id=roomnote>live on this board</div>
    <button id=sharebtn title="copy a link to this board">copy invite link</button></div></details>
  <${StatusPills} />
  </nav>`;

// Status pills: legacy.js owns the numbers (cost, OCD score, feasibility,
// transient status), this renders them. Its own component so a store update
// re-renders only these spans — never the menubar, whose engine <select>s are
// filled by legacy.js and must keep their options.
const StatusPills = () => {
  const s = useUI();
  return html`<div class=mastat>
    <span id=cost class=pill title="total wirelength">${s.cost}</span>
    <span id=ocdscore class=pill title="OCD neatness badge 0-100 — do not compare across boards">${s.ocd}</span>
    <span id=feas class=pill title="routing feasibility per layer count"
      dangerouslySetInnerHTML=${{__html: s.feas}}></span>
    <span id=stat role=status aria-live=polite title=${s.stat}
      class=${!s.stat ? '' : (s.statOk ? 'ok' : 'err')}>${s.stat}</span></div>`;
};

// presence, identity, theme, tabs: all store-driven, each its own subscriber
const RoomPill = () => {
  const s = useUI();
  return html`<span id=room role=status aria-live=polite title=${s.roomTitle}
    class=${'pill' + (s.roomOk ? ' ok' : '')}>${s.room}</span>`;
};
const MePill = () => html`<span id=me class=pill title="logged in as">${useUI().me}</span>`;
const ThemeBtn = () => {
  const s = useUI();
  return html`<button id=themebtn type=button aria-pressed=${s.dark ? 'true' : 'false'}
    title="toggle Flux-dark theme (paper ↔ dark)">${s.dark ? 'paper' : 'dark'}</button>`;
};

const VIEWS = [
  ['all', 'every panel at once (the cockpit)', 'All', true],
  ['pcb', 'PCB layout', 'Layout', false],
  ['sch', 'schematic', 'Schematic', false],
  ['t3d', '3D preview', '3D', false],
  ['docs', 'notes and datasheets', 'Docs', false],
];

const ViewTabs = () => {
  const s = useUI();
  return html`<nav id=viewtabs role=tablist aria-label="views"
    title="pick a view, or All for every panel at once"
    >${VIEWS.map(([v, title, label]) => {
      const on = s.view === v;   // cockpit = All is the active tab
      return html`<button data-v=${v} role=tab key=${v} aria-selected=${on ? 'true' : 'false'}
        tabindex=${on ? 0 : -1} class=${on ? 'on' : ''} title=${title}>${label}</button>`;
    })}</nav>`;
};

// Rendered exactly once: nothing here subscribes, so the engine <select>s and
// the quote/calc inputs keep whatever legacy.js puts inside them.
const Chrome = () => html`<header class=top><div class=inner>
  <${Brand} />
  <${RoomPill} />
  <${MePill} />
  <button id=logoutbtn title="log out of the studio">log out</button>
  <${ThemeBtn} />
  <${ViewTabs} />
  <${Menubar} />
  <span id=plugintools style="display:contents"></span>
  </div></header>
  <${Toast} />
  <${ExtBanner} />
  <main id=panels><${Panels} /><span id=pluginpanels style="display:contents"></span></main>`;

render(html`<${Chrome} />`, document.getElementById('app'));

// Slot islands: the built-in panels are components above; anything a plugin
// registers through the Python slot registry arrives as markup and is mounted
// verbatim, once, inside the island for its slot.
const slots = JSON.parse(document.getElementById('slots').textContent || '{}');
const mount = (id, markup) => {
  const el = document.getElementById(id);
  if (el) el.innerHTML = markup || '';
};
mount('plugintools', slots.toolbar);
mount('pluginpanels', slots.view);
mount('pluginleft', slots['panel-left']);
