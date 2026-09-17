// One tiny observable for the workshop chrome: legacy.js owns the behaviour
// (tab persistence, body classes, the fetched numbers) and pushes values here;
// preact renders the pixels. A store instead of a framework because nothing
// else needs one — plain object, Set of subscribers, no build step.
const state = {
  cost: 'cost', ocd: '', feas: '', stat: '', statOk: true,
  room: 'solo', roomOk: false, roomTitle: 'no one else here yet',
  roomNote: 'live on this board', me: '',
  view: 'all', tabs: false, dark: false, chat: false,
  // panel content, shaped by legacy.js from the build report and rendered by
  // the components in views.js: the report shaping stays where the report is
  drc: [],            // [{cls: 'err'|'warn'|'ok', text}]
  tidyCov: '',        // "(13/15)" coverage note in the tidy header
  tidyRows: [],       // [[metric, {text, dim}]]
  tidyNote: '',       // the dense-board note (replaces the rows)
  treeUp: null,       // ".. (root)" row while browsing below the root
  treeRows: [],       // [{name, path, kind, bytes, active}]
  treeNote: '',       // "dir · N files"
  importStat: '',     // what the import picker is doing
  cuRows: [],         // copper layer toggles  [{key, label, on, id, title}]
  markRows: [],       // silkscreen/mask/mark toggles (same shape)
  partRows: [],       // [{ref, value, on, hidden, sel}]
  partNote: '',       // "N/N shown" / "N/N of M (capped)"
  toast: '',          // transient bottom-right note (legacy clears it on a timer)
  calc: {c: '', d: '', z: ''},   // calculator readouts
  doc: [],            // doctor: [{cls, text}]
  chatWhere: '',      // the open board's path, in the agent panel header
  msgs: [],           // agent log, ordered: msg | plan | log | prop entries
  followups: [],      // chips under the composer: [{label, title}]
  vcsNote: '',        // "branch · clean|uncommitted" or the not-a-repo note
  vcsRevs: [],        // [{hash, date, subject}]
  vcsMsg: '',         // shown instead of the list when there is no repo
  vcsOpen: '',        // hash whose diff is expanded
  vcsDiff: '',        // that diff's text
  kbNote: 'kb/ beside the board',   // header note: documents · part links · dir
  kbRows: [],         // docs or search hits: [{doc, name, kind, tail, title, start}]
  kbTail: '',         // "… showing N of M documents"
  kbStat: 'click a document to read it',
  kbView: '',         // the document / answer text under the list
  kbPrefsOpen: false, // the preferences block is hidden until asked for
  kbPrefs: [],        // [{id, when, text, approved}]
  galOpen: false,     // the candidates filmstrip is shown while picking
  galThumbs: [],      // [{i, cand, label}] — the canvas is drawn by legacy.js
  quoteRows: [],      // [{fab, logo, bare, asm, per}] fab price comparison
  quoteNote: [],      // [{cls, text}] substitute/unpriced/low-stock/stamp lines
  xrayStat: '',       // x-ray status line
  xrayDivs: [],       // [{kind, x, y, w, h}] divergences from the fab scan
  extBanner: false,   // "file changed on disk" strip
};

import { useEffect, useState } from './vendor/hooks.module.js';

const subs = new Set();

export const ui = {
  state,
  set(patch) {
    let hit = false;
    for (const k of Object.keys(patch)) {
      if (state[k] !== patch[k]) { state[k] = patch[k]; hit = true; }
    }
    if (hit) for (const fn of [...subs]) fn();
  },
  sub(fn) {
    subs.add(fn);
    return () => { subs.delete(fn); };
  },
};

// Subscribe a component to the store: a store update re-renders that component
// and nothing else (the chrome's static parts must never re-render — legacy.js
// fills their selects and readouts). Lives here so panels/views and the chrome
// share one hook.
export function useUI() {
  const [, bump] = useState(0);
  useEffect(() => ui.sub(() => bump(n => n + 1)), []);
  return state;
}
