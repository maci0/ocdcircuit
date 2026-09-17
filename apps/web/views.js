// Reactive panel content — Preact + htm. The panels in panels.js are shells
// that render once; these components are the parts of them that change: the
// DRC strip and the tidy metrics. legacy.js shapes the build report into plain
// data and pushes it into the store; the shaping stays with the report.
// htm rule: void elements must self-close.
import html from './html.js';
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
