// Landing + gate + shelf — Preact + htm (no build step; see html.js).
// Moved out of the LOGIN_PAGE string in apps/studio.py. Component state replaces
// the scattered getElementById writes; the server stays the only data source.
// The hero art stays imperative (art.js): canvas is not VDOM content.
import { render } from './vendor/preact.module.js';
import { useEffect, useRef, useState } from './vendor/hooks.module.js';
import html from './html.js';
import { api } from './api.js';
import { paint } from './art.js';

// every create path lands in the workshop
const openShelfBoard = name => {
  if (!name) return;
  location.href = '/?board=' + encodeURIComponent(String(name).replace(/\.ocd$/, ''));
};

// copy for the two credential modes — one table, never two branches of markup
const MODES = {
  signup: {
    title: 'Create your Studio account',
    sub: 'Local account beside your boards — then open one and send the link.',
    go: 'Create account',
    swap: 'Have an account? Log in',
  },
  login: {
    title: 'Welcome back',
    sub: 'Log in to open your shelf and pick up where you left off.',
    go: 'Log in',
    swap: 'New here? Create an account',
  },
};

const Brand = () => html`<span class=brand><svg width=22 height=22 viewBox="0 0 20 20" aria-hidden=true><rect x=2 y=2 width=16 height=16 rx=4 fill=none stroke=currentColor stroke-width=1.8></rect><path d="M6.5 7.2 9.3 10l-2.8 2.8" fill=none stroke=#5fd894 stroke-width=1.8 stroke-linecap=round stroke-linejoin=round></path><line x1=11 y1=12.8 x2=14 y2=12.8 stroke=#5fd894 stroke-width=1.8 stroke-linecap=round></line></svg>OCD <em>Studio</em></span>`;

// One canvas, painted once after the first frame so hero text is not blocked.
function Art({id}) {
  const ref = useRef(null);
  useEffect(() => {
    requestAnimationFrame(() => { if (ref.current) paint(ref.current); });
  }, []);
  return html`<canvas id=${id} aria-hidden=true ref=${ref}></canvas>`;
}

// body classes are the stylesheet's switches (DESIGN.md: one class, no second
// stylesheet) — state drives them instead of scattered classList calls
function useBodyClass(name, on) {
  useEffect(() => { document.body.classList.toggle(name, !!on); }, [name, on]);
}

const PEOPLE = [
  ['#5fd894', 'maya', 'dragging the regulator into place',
   'fix U1 at 12.4 18.1\npart C3 C0805 100n\nGND :: U1.1 <--> C3.1'],
  ['#3a7bd5', 'leo', 'wiring the sensor net',
   'net N_SDA :: U2.5 <--> J1.3\nnet N_SCL :: U2.6 <--> J1.4\nroute N_SDA on 0'],
  ['#8a2318', 'priya', 'pouring the ground plane',
   'pour GND on 0\nkeep U1 near C1 3\npower VCC GND'],
];

const Collab = () => html`<div class=collab role=group aria-label="Example: three engineers editing one board">
  ${PEOPLE.map(([hue, who, doing, src]) => html`<div class=person key=${who}>
    <b class=who><i style=${`background:${hue}`}></i>${who}</b>
    <p>${doing}</p>
    <div class=mini>${src}</div>
    <small><b>Illustrative example</b> · not a live session</small></div>`)}
</div>`;

// supported-fabs strip: vendor tiles + profile urls come from /fabs (one source
// of truth in ocdcircuit/fab.py); the PNGs stream from /fab-logo/<key> so they
// stay cacheable and off the first HTML.
const FabTile = ({f}) => html`<a class=fabcell href=${f.url}
  title=${f.name + ' — capabilities'} rel="noopener noreferrer"
  ><img src=${'/fab-logo/' + f.key} alt=${f.name + ' logo'} width=96 height=32
    loading=lazy decoding=async /></a>`;

function Fabs() {
  const [rows, setRows] = useState([]);
  useEffect(() => { fetch('/fabs').then(r => r.json()).then(setRows); }, []);
  if (!rows.length) return null;      // the strip appears with its data
  return html`<div class=fabstrip role=group aria-label="supported fabs">
    <span class=fabkicker>ships to</span>
    ${rows.map(f => html`<${FabTile} key=${f.key} f=${f} />`)}
    <span class=fabfine>logos belong to their owners</span></div>`;
}

const Hero = ({onStart, onLogin}) => html`<header class=hero>
  <${Art} id="art" />
  <nav class=nav><${Brand} /><span class=sp></span>
    <button id=loginbtn type=button onClick=${onLogin}>Log in</button>
    <button id=topcta type=button onClick=${onStart}>Start a board together</button></nav>
  <div class=herobody>
    <h1>Your whole team. One board. Zero merge conflicts.</h1>
    <p class=dek>Open a board, send the link, you're co-editing — every cursor, every part move, every net, in real time.</p>
    <div class=prompt><p>Open a board, send the link — same schematic, two cursors, zero merge conflicts.</p>
      <button id=herogo type=button onClick=${onStart}>Start a board together</button></div>
    <ol class=flowline><li><b>1</b> idea</li><li><b>2</b> schematic</li><li><b>3</b> layout</li><li><b>4</b> make</li></ol>
    <${Collab} />
    <p class=aistory><b>AI beside you</b> — drafts the schematic, places parts, routes traces. You stay the lead engineer.</p>
    <${Fabs} />
  </div></header>`;

// shelf + template cards: one card list, rendered in the shelf and in the modal
const ShelfCard = ({c}) => html`<button class=scard type=button onClick=${c.go}
  ><b>${c.t}</b><span>${c.s}</span><small>${c.m}</small></button>`;

function CardSec({heading, cards}) {
  if (!cards.length) return null;
  return html`<div class=tsec>${heading}</div>
    ${cards.map(c => html`<${ShelfCard} key=${c.t} c=${c} />`)}`;
}

function App() {
  const [me, setMe] = useState(null);
  const [mode, setMode] = useState('signup');
  const [gating, setGating] = useState(false);
  const [shelf, setShelf] = useState(false);
  const [err, setErr] = useState({msg: '', ok: false, field: null});
  const [boards, setBoards] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [dialog, setDialog] = useState(false);
  const [search, setSearch] = useState('');
  const [display, setDisplay] = useState('');
  const [busy, setBusy] = useState('');
  const u = useRef(null), p = useRef(null), errEl = useRef(null), dlg = useRef(null);
  const promptq = useRef(null), nbname = useRef(null);
  const tplBusy = useRef(false);                  // one in-flight copy: double-click must not mint -2/-3
  const npcache = useRef({boards: [], templates: []});

  useBodyClass('gating', gating);
  useBodyClass('shelf', shelf);

  const authErr = (msg, field) => setErr({msg, ok: false, field: field || null});

  // announce + move focus so keyboard/AT users hear the error
  useEffect(() => {
    if (!err.msg) return;
    const el = err.field === 'u' ? u.current
      : err.field === 'p' ? p.current : errEl.current;
    if (el) el.focus();
  }, [err]);

  // native <dialog>: focus trap, Esc, backdrop — no library, no state machine
  useEffect(() => {
    const d = dlg.current;
    if (!d) return;
    if (dialog && !d.open) d.showModal();
    if (!dialog && d.open) d.close();
  }, [dialog]);

  async function showShelf() {                     // shelf after login
    setShelf(true);
    setErr({msg: '', ok: false, field: null});
    const me0 = await api('/auth/me', {});
    if (me0 && me0.user) setMe(me0.user);
    if (me0 && me0.display) setDisplay(me0.display);
    const r = await api('/shelf', {});
    const card = b => ({t: b.name.replace(/\.ocd$/, ''),
      s: b.blurb || `${b.parts} parts · ${b.nets} nets`,
      m: `${b.name} · ${b.mtime}`, go: () => openShelfBoard(b.name)});
    const tpl = t => ({t: t.name.replace(/\.ocd$/, ''), s: t.blurb || 'starter board',
      m: 'template — opens a copy in the workshop', go: () => fromTemplate(t.name)});
    setBoards((r.boards || []).map(card));
    setTemplates((r.templates || []).map(tpl));
    npcache.current = {boards: (r.boards || []).map(card),
                       templates: (r.templates || []).map(tpl)};
  }

  async function fromTemplate(name) {
    if (tplBusy.current) return;
    tplBusy.current = true;
    try {
      const x = await api('/shelf/from_template', {name});
      if (x.error) return authErr(x.error);
      setDialog(false);
      openShelfBoard(x.name);
    } finally { tplBusy.current = false; }
  }

  // first visit: only an existing session skips the hero. A logged-out visitor
  // keeps the hero and picks the mode the form will open in.
  useEffect(() => {
    let live = true;
    api('/auth/me', {}).then(r => {
      if (!live) return;
      if (r.user) { setMe(r.user); setGating(true); showShelf(); }
      else setMode(r.needs_setup ? 'signup' : 'login');
    });
    return () => { live = false; };
  }, []);

  const openGate = m => { setMode(m); setGating(true); };
  const back = () => { setGating(false); setErr({msg: '', ok: false, field: null}); };

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    setErr({msg: '', ok: false, field: null});
    const name = u.current.value.trim();
    if (!name) return authErr("Username can't be blank!", 'u');
    setBusy('go');
    try {
      const r = await api(mode === 'signup' ? '/auth/signup' : '/auth/login',
                          {user: name, password: p.current.value});
      if (r.error) return authErr(r.error);
      setMe(r.user || name);
      await showShelf();
    } finally { setBusy(''); }
  }

  async function newBoard(name, tail) {
    const r = await api('/shelf/new', {name});
    if (r.error) return authErr(r.error + (tail || ''));
    openShelfBoard(r.name);
  }

  async function promptSubmit(e) {
    e.preventDefault();
    const q = promptq.current.value.trim();
    if (!q || busy) return;
    setBusy('prompt');
    try { await newBoard(q, ' — try a shorter name'); } finally { setBusy(''); }
  }

  async function nbSubmit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy('nb');
    try { await newBoard(nbname.current.value, ''); } finally { setBusy(''); }
  }

  async function saveDisplay() {
    if (busy) return;
    setBusy('prof');
    try {
      const r = await api('/auth/profile', {display});
      if (r.error) return authErr(r.error);
      setMe(r.display);
      setErr({msg: 'display name saved', ok: true, field: null});
    } finally { setBusy(''); }
  }

  // new-project modal: the shelf's own lists, filtered client-side. Blank
  // reuses /shelf/new with the search text as the name.
  async function blankProject() {
    if (busy) return;
    setBusy('blank');
    try {
      const r = await api('/shelf/new', {name: search.trim() || 'untitled'});
      if (r.error) return authErr(r.error);
      setDialog(false);
      openShelfBoard(r.name);
    } finally { setBusy(''); }
  }

  const hit = c => (c.t + ' ' + c.s).toLowerCase().includes(search.trim().toLowerCase());
  const npHits = [...npcache.current.boards.filter(hit),
                  ...npcache.current.templates.filter(hit)];
  const M = MODES[mode];

  return html`<${Hero} onStart=${() => openGate('signup')} onLogin=${() => openGate('login')} />
  <main class=gate><div class=form>
    <button id=back type=button class=backlink onClick=${back}><span aria-hidden=true>←</span> Back to the overview</button>
    <${Brand} />
    <h2 id=title>${shelf ? `Welcome, ${me || ''}` : M.title}</h2>
    <p class=sub id=sub>${shelf ? 'Pick a board to open the workshop, or start a new one.' : M.sub}</p>
    <p id=err role=alert aria-live=polite tabindex=-1 class=${err.ok ? 'ok' : ''} ref=${errEl}>${err.msg}</p>
    <form id=f style=${shelf ? 'display:none' : ''} onSubmit=${submit}>
      <label>Username<input id=u ref=${u} autocomplete=username maxlength=32 required
        aria-describedby=err aria-invalid=${err.field === 'u'} /></label>
      <label>Password<input id=p ref=${p} type=password minlength=8 required
        autocomplete=${mode === 'signup' ? 'new-password' : 'current-password'}
        aria-describedby=err aria-invalid=${err.field === 'p'} /></label>
      <button id=go type=submit disabled=${busy === 'go'}>${M.go}</button>
      <button id=swap type=button class=ghost
        onClick=${() => setMode(mode === 'signup' ? 'login' : 'signup')}>${M.swap}</button>
    </form>
    <div id=shelf role=group aria-label="your boards" class=${shelf ? 'has' : ''}>
      ${shelf && !boards.length && !templates.length
        ? html`<span class=fine>no boards yet — describe one above, or New project</span>` : ''}
      <${CardSec} heading="Your boards" cards=${boards} />
      <${CardSec} heading="Templates" cards=${templates} />
    </div>
    <form id=promptbox onSubmit=${promptSubmit}>
      <input id=promptq ref=${promptq} aria-label="describe a board to start"
        placeholder="e.g. 555 blinky, USB-C breakout, 2-layer sensor" />
      <button type=submit disabled=${busy === 'prompt'}>Start</button></form>
    <button id=newprojbtn type=button class=ghost onClick=${() => {
      setSearch(promptq.current ? promptq.current.value || '' : '');
      setDialog(true);
    }}>New project…</button>
    <dialog id=newproj aria-label="new project" ref=${dlg}>
      <h3>New project</h3>
      <input id=npsearch type=search aria-label="search boards and templates"
        placeholder="Search boards and templates" value=${search}
        onInput=${e => setSearch(e.currentTarget.value)} />
      <div id=npgrid>
        <${CardSec} heading="Your boards" cards=${npcache.current.boards.filter(hit)} />
        <${CardSec} heading="Templates" cards=${npcache.current.templates.filter(hit)} />
        ${!npHits.length ? html`<span class=fine>no matches — try blank below</span>` : ''}</div>
      <button id=npblank type=button onClick=${blankProject}
        disabled=${busy === 'blank'}>New blank project</button>
    </dialog>
    <div id=profrow class=shelfonly>
      <input id=profin aria-label="display name" maxlength=40 placeholder="Display name"
        value=${display} onInput=${e => setDisplay(e.currentTarget.value)} />
      <button id=profgo type=button title="save how your name reads on boards and in rooms"
        onClick=${saveDisplay} disabled=${busy === 'prof'}>Save display name</button></div>
    <form id=newboard onSubmit=${nbSubmit}>
      <label>New board<input id=nbname ref=${nbname} placeholder=blinky maxlength=32 /></label>
      <button type=submit disabled=${busy === 'nb'}>New board</button></form>
    <p class=fine>Local-first: accounts live in this studio only (.ocd-users beside the boards).</p>
  </div>
  <figure class=visual><${Art} id="art2" />
    <figcaption>idea → schematic → layout → make · drag parts · solve · fab zip</figcaption></figure>
  </main>`;
}

render(html`<${App} />`, document.getElementById('app'));
