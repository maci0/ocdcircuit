// Reactive panel content — Preact + htm. The panels in panels.js are shells
// that render once; these components are the parts of them that change: the
// DRC strip and the tidy metrics. legacy.js shapes the build report into plain
// data and pushes it into the store; the shaping stays with the report.
// htm rule: void elements must self-close.
import html from './html.js';
import { useEffect, useRef } from './vendor/hooks.module.js';
import { useUI } from './store.js';

// DRC reads as a listing: one line per finding, class err/warn/ok, and the
// strip hides itself when there is nothing to say (#drc:empty in the CSS).
export const DrcStrip = () => {
  const items = useUI().drc;
  return html`<div id=drc role=status aria-live=polite
    >${items.map((it, i) => html`<div class=${it.cls} key=${i}>${it.text}</div>`)}</div>`;
};

// tidy: the coverage note in the header, then one row per metric. A dense
// board replaces the rows with the note saying so.
export const TidyBlock = () => {
  const s = useUI();
  return html`<header class=panel-head><span class=panel-title>tidy</span>
    <span class=panel-note id=tidycov>${s.tidyCov}</span></header>
    <div id=tidy>${s.tidyNote
      ? html`<div class=dim>${s.tidyNote}</div>`
      : s.tidyRows.map(([k, v], i) => html`<div key=${i}
          ><span class=dim>${k}</span> ${v.dim ? html`<span class=dim>${v.text}</span>` : v.text}</div>`)}</div>`;
};

// Project files: one level at a time, rows as buttons whose data-* attributes
// say what they are. legacy.js delegates the click on #tree (its behaviour
// owns open/preview/load) — the component never imports behaviour.
const TreeRow = ({e, depth}) => {
  const dir = e.kind === 'dir';
  const size = !dir && e.bytes ? (+e.bytes / 1024).toFixed(1) + 'k' : '';
  return html`<button type=button data-path=${e.path} data-name=${e.name} data-kind=${e.kind}
    class=${'trow ' + (dir ? 'tdir' : 'tfile') + (e.active ? ' active' : '')}
    style=${'padding-left:' + (8 + depth * 13) + 'px'}
    title=${dir ? 'folder — click to open it' : e.path}
    aria-label=${dir ? 'open folder ' + e.name : 'open ' + e.name}
    >${dir ? e.name + '/' : e.name}${size ? html`<span class=tsize>${size}</span>` : ''}</button>`;
};

export const TreePanel = () => {
  const s = useUI();
  return html`<section id=filetree class=side>
    <header class=panel-head><span class=panel-title>project</span>
      <span class=panel-note id=treenote>${s.treeNote}</span>
      <label id=importlbl title="import a footprint, symbol, or board (kicad, eagle, tscircuit, altium, easyeda)"
        >import<input id=importfile type=file hidden /></label></header>
    <div id=tree>${s.treeUp ? html`<${TreeRow} e=${s.treeUp} depth=${0} />` : ''}${s.treeRows.length
      ? s.treeRows.map(e => html`<${TreeRow} key=${e.path} e=${e} depth=${1} />`)
      : html`<div class="trow tdir" role=status>(no text files)</div>`}</div>
    <div id=importstat role=status aria-live=polite>${s.importStat}</div></section>`;
};

// Layer / mark / part visibility: rows are pure description, the change events
// are delegated in legacy.js (it owns VIS, persistence and the canvas repaint).
const VisRow = ({r}) => html`<label class=${r.on ? 'on' : 'off'} title=${r.title}
  ><input type=checkbox id=${r.id} data-key=${r.key} checked=${r.on} />${r.label}</label>`;

export const CuRows = () => {
  const rows = useUI().cuRows;
  return html`<div id=cu class=row>${rows.map(r => html`<${VisRow} r=${r} key=${r.key} />`)}</div>`;
};

export const MarkRows = () => {
  const rows = useUI().markRows;
  return html`<div id=marks class=row>${rows.map(r => html`<${VisRow} r=${r} key=${r.key} />`)}</div>`;
};

// parts: filter + hide/show controls stay where they were, the rows and the
// note are rendered here. Rows carry data-ref; the change event is delegated.
const PartRow = ({r}) => html`<label data-ref=${r.ref} data-hidden=${r.hidden ? '1' : ''}
  class=${(r.on ? '' : 'hidden ') + (r.sel ? 'selpart' : '')}
  ><input type=checkbox data-ref=${r.ref} checked=${r.on} /><span>${r.ref}</span
  ><span class=pv title=${r.value}>${r.value}</span></label>`;

export const PartBlock = () => {
  const s = useUI();
  return html`<div id=partbar><input id=partfilter type=search
      placeholder="filter ref, value, footprint" aria-label="filter parts" />
    <button id=parthide type=button title="hide every part">none</button>
    <button id=partshow type=button title="show every part">all</button>
    <span id=partnote class=panel-note>${s.partNote}</span></div>
    <div id=partlist>${s.partRows.map(r => html`<${PartRow} r=${r} key=${r.ref} />`)}</div>`;
};

// Calculator readouts: the inputs are legacy-wired (their values must survive
// a re-render, so they carry no value prop here), the answers come from state.
export const CalcBlock = () => {
  const c = useUI().calc;
  return html`<details id=calc title="trace width and divider calculators"><summary>calc</summary>
    <label>A <input id=ca size=4 value=1 aria-label="trace current A" /></label>
    <label>dT <input id=cdt size=3 value=10 aria-label="temperature rise C" /></label>
    <div id=cout>${c.c}</div>
    <label>V <input id=dv size=4 value=5 aria-label="divider input V" /></label>
    <label>Rt <input id=drt size=5 value=10k aria-label="divider top R" /></label>
    <label>Rb <input id=drb size=5 value=10k aria-label="divider bottom R" /></label>
    <div id=dout>${c.d}</div>
    <label>w <input id=zw size=4 value=0.3 aria-label="microstrip width mm" /></label>
    <label>h <input id=zh size=4 value=0.2 aria-label="dielectric height mm" /></label>
    <div id=zout>${c.z}</div></details>`;
};

// doctor: one line per check, class ok/err, dim detail
export const HealthOut = () => {
  const items = useUI().doc;
  return html`<div id=docout>${items.length
    ? items.map((it, i) => html`<div class=${it.cls} key=${i}>${it.text}${it.detail
        ? html` <span class=dim>${it.detail}</span>` : ''}</div>`)
    : 'click to check'}</div>`;
};

// Agent conversation: one ordered list, four entry kinds — prose, the plan
// checklist, the thought trace and the proposal cards. legacy.js pushes
// entries and delegates the clicks on #msgs (apply/reject) and #followups.
const Entry = ({m}) => {
  if (m.kind === 'plan') {
    return html`<details class=thought open=${m.open}><summary>${m.title}</summary>
      <ul>${m.steps.map((s, i) => html`<li class=ok key=${i}>${s}</li>`)}</ul></details>`;
  }
  if (m.kind === 'log') {
    return html`<details class=thought><summary>${m.title}</summary>
      <ul>${m.lines.map((l, i) => html`<li class=${l.cls} key=${i}>${l.text}</li>`)}</ul></details>`;
  }
  if (m.kind === 'prop') {
    return html`<div class=prop><div class=phead>proposed edit to <b>${m.path}</b
      ><span class=grow></span
      ><button data-act=apply data-prop=${m.prop} data-path=${m.path}
        disabled=${m.busy}>apply</button
      ><button data-act=reject data-prop=${m.prop} data-path=${m.path}
        disabled=${m.busy}>reject</button></div
      ><pre>${m.lines.map((l, i) => html`<div class=${l.cls} key=${i}>${l.text}</div>`)}</pre></div>`;
  }
  return html`<p class=${'msg ' + m.who}><span class=who>${m.name}</span>${m.text}</p>`;
};

export const ChatPanel = () => {
  const s = useUI();
  const box = useRef(null);
  const n = s.msgs.length;
  useEffect(() => {   // a new entry scrolls the log, like the old append did
    const el = box.current;
    if (el && el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight;
  }, [n]);
  return html`<section id=chat class=side>
    <header class=panel-head><span class=panel-title>agent</span>
      <span class=panel-note id=chatwhere>${s.chatWhere}</span>
      <button id=chatclear title="forget this conversation">clear</button></header>
    <div id=msgs role=log aria-live=polite aria-label="agent conversation"
      ><div id=msglist ref=${box}>${s.msgs.map(m => html`<${Entry} m=${m} key=${m.id} />`)}</div
      ><div id=props></div></div>
    <div id=followups>${s.followups.map((f, i) => html`<button title=${f.title}
      key=${i}>${f.label}</button>`)}</div>
    <form id=composer><textarea id=ask rows=2 aria-label="message to the agent"
      placeholder="ask about this board, or say what to change (Ctrl+Enter)"></textarea>
      <button id=send class=primary type=submit>send</button></form>
    </section>`;
};

// toast: the one transient note, cleared by legacy.js on its timer
export const Toast = () => {
  const t = useUI().toast;
  return t ? html`<div class=toast role=status aria-live=polite>${t}</div>` : null;
};

// revisions: the git log of the board directory. Clicking a row asks the
// server for that commit's diff; the diff <pre> is rendered right after the
// row it belongs to, and only one is ever open.
export const VcsPanel = () => {
  const s = useUI();
  return html`<section id=vcswrap>
    <header class=panel-head><span class=panel-title>revisions</span>
      <span class=panel-note id=vcsnote>${s.vcsNote}</span>
      <span class=panel-note>git history of the board directory</span></header>
    <div id=vcs>${s.vcsMsg
      ? s.vcsMsg
      : s.vcsRevs.map(c => html`<button type=button class=rev key=${c.hash}
          data-hash=${c.hash} aria-label=${'show diff for ' + c.hash + ' ' + c.subject}
          ><span class=rh>${c.hash}</span><span class=rd>${c.date}</span
          ><span class=rs>${c.subject}</span></button>${s.vcsOpen === c.hash
            ? html`<pre>${s.vcsDiff}</pre>` : ''}`)}</div></section>`;
};

// knowledgebase: the document list (or the search hits), the stat line, the
// open document, and the preference rows. legacy.js pushes all of it and
// delegates the clicks; the inputs stay legacy-wired (their values must
// survive a re-render, so they carry no value prop here).
const KbRow = ({r}) => html`<div class=kbrow title=${r.title || ''}
  ><button class=kbname data-doc=${r.doc} data-start=${r.start}>${r.name}</button
  ><span class=kbkind>${r.kind}</span><span class=kbparts>${r.tail || ''}</span></div>`;

const KbPref = ({p}) => html`<div class=kbrow
  ><span class=kbname>${'when ' + p.when + ' :: ' + p.text}</span
  ><span class=kbkind>${p.approved ? 'approved' : 'pending'}</span
  ><button data-pref=${p.id} data-approve=${p.approved ? '0' : '1'}
    title=${p.approved ? 'stop following this' : 'follow this from now on'}
    >${p.approved ? 'reject' : 'approve'}</button></div>`;

export const KbPanel = () => {
  const s = useUI();
  return html`<section id=kbwrap>
    <header class=panel-head><span class=panel-title>knowledgebase</span>
      <span class=panel-note id=kbnote>${s.kbNote}</span>
      <span class=panel-note>notes + datasheets · the agent reads the same files</span></header>
    <div id=kbbar>
      <input id=kbq type=search aria-label="ask the knowledgebase"
        placeholder="ask: what is the input voltage range?  (or a search term)" />
      <button id=kbask class=primary type=button title="passages that answer the question (embeddings)">ask</button>
      <button id=kbgrep type=button title="exact term match, one line per hit">search</button>
      <button id=kbans type=button title="also write an answer with the local model">answer</button></div>
    <div id=kbadd>
      <input id=kburl type=search aria-label="datasheet url or file path"
        placeholder="https://…/datasheet.pdf  or  path/to/note.md" />
      <button id=kbaddbtn type=button title="copy or download it into kb/">add</button>
      <button id=kbfetch type=button title="download the datasheet for every datasheet= / lcsc= part">fetch datasheets</button>
      <button id=kbprefsbtn type=button title="preferences the agent follows without being asked">preferences</button></div>
    <div id=kbprefs style=${s.kbPrefsOpen ? '' : 'display:none'}
      ><div id=kbprefslist>${s.kbPrefs.length
        ? s.kbPrefs.map(p => html`<${KbPref} p=${p} key=${p.id} />`)
        : 'no preferences yet — teach one below'}</div>
      <div id=kbprefsadd><input id=kbwhen aria-label="when this applies" placeholder="when placing connectors" />
        <input id=kbwhat aria-label="what to prefer" placeholder="put them on the board edge" />
        <button id=kbprefsgo type=button title="save as a new preference">remember</button></div></div>
    <div id=kbstat role=status aria-live=polite>${s.kbStat}</div>
    <div id=kblist>${s.kbRows.length
      ? s.kbRows.map(r => html`<${KbRow} r=${r} key=${r.doc + ':' + r.start} />`)
      : 'nothing yet — add a url, or fetch datasheets'}${s.kbTail}</div>
    <pre id=kbview>${s.kbView}</pre>
    </section>`;
};

// Candidates filmstrip. The canvas pixels are not VDOM: legacy.js owns the
// painting, so it registers a painter here and each thumb asks for its frame
// once after mount. Captions and the picked/base wording are state too.
export const thumbPaint = {fn: null};

const Thumb = ({t: cand}) => {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && thumbPaint.fn) thumbPaint.fn(cand.cand, cand.i, ref.current);
  }, [cand]);
  return html`<button type=button class=galpick data-i=${cand.i}
    aria-label=${`adopt candidate ${cand.i}, cost ${cand.cand.cost} (shift-click to compare)`}
    ><canvas ref=${ref} width=300 height=220></canvas
    ><span class=galcap>${cand.label}</span></button>`;
};

export const Gallery = () => {
  const s = useUI();
  return html`<section id=galwrap style=${s.galOpen ? '' : 'display:none'}>
    <header class=panel-head><span class=panel-title>candidates</span>
      <span class=panel-note>click one to adopt it, then drag it on the PCB to nudge and pin</span>
      <span class=panel-note>job file 1F-04 · placer diffusion · 1 seed × 400 iters</span></header>
    <div id=gal>${s.galThumbs.map(t => html`<${Thumb} t=${t} key=${t.i} />`)}</div></section>`;
};

// fab price comparison: one row per fab (its own logo tile) plus the notes
// about substitutes, unpriced parts, low stock and the estimate stamp.
export const QuoteOut = () => {
  const s = useUI();
  return html`<div id=qout role=status aria-live=polite
    >${s.quoteRows.map(r => html`<div class=qrow key=${r.fab}>${r.logo
        ? html`<img class=qlogo src=${r.logo} alt=${r.fab + ' logo'} width=64 height=21 />`
        : ''}<span class=dim>${r.fab}</span> bare $${r.bare}${r.asm
          ? ` asm $${r.asm} ($${r.per}/bd)` : ''}</div>`)}${s.quoteNote.map((n, i) =>
        html`<div class=${n.cls} key=${i}>${n.text}</div>`)}</div>`;
};

// x-ray check: the status line and the divergence list
export const XrayStat = () => html`<div id=xraystat role=status aria-live=polite>${useUI().xrayStat}</div>`;

export const XrayDivs = () => {
  const divs = useUI().xrayDivs;
  return html`<div id=xraydivs>${divs.length
    ? divs.map((d, i) => html`<div key=${i}><span class=dim>${d.kind}</span> ${d.x} ${d.y} ${d.w}x${d.h}mm</div>`)
    : (useUI().xrayStat ? html`<div class=dim>no divergences</div>` : '')}</div>`;
};

// "file changed on disk" strip: the user picks the moment to reload, so this
// is a control, not a notification. The click is delegated in legacy.js.
export const ExtBanner = () => {
  const s = useUI();
  return s.extBanner
    ? html`<button type=button id=extbanner style=${'width:100%;border:0;border-radius:0;'
        + 'background:var(--signal);color:' + (s.dark ? '#06130d' : '#fff')
        + ';padding:10px 14px;min-height:40px;cursor:pointer;font:600 .9rem var(--sans);'
        + 'text-align:left'}>file changed on disk — click to reload (your edits stay in undo)</button>`
    : null;
};
