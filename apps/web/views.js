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
