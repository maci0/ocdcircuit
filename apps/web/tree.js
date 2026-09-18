// Project files: browse a directory at a time, preview a text file, open a
// board. The rows are a component (views.js TreePanel) and its click is
// delegated here, so the behaviour stays on this side of the line.
// The listing state lives here; boot/openFile update it through setListing.
import { ui } from './store.js';
import { $, api } from './core.js';
import { msg } from './agent.js';

let d = null; // {statMsg, toast, cancelPush, clearProposals, collabReset,
              //  collabStart, applyState, loadVCS, setVcs}

let TREE = [], SRCREL = '', DIR = '.', ROOTREL = '.';

export const srcRel = () => SRCREL;

export function setListing(f) { // one /fs answer → the listing + its note
  // `/fs?dir=` reports both: dir is what was listed, base is the open board's
  DIR = f.dir || f.base || '.';
  ROOTREL = f.root || '.';
  SRCREL = f.src || '';
  TREE = f.tree || [];
  d.setVcs(f.vcs || {});
  treeNote();
  renderTree();
}

// opening a board also repoints the source note and the agent panel
export function setOpenBoard(f) {
  setListing(f);
  ui.set({srcNote: `${SRCREL} · ${f.base || '.'} · saved on every good build`,
    chatWhere: SRCREL});
}

function treeNote() { // one string, three call sites used to write it by hand
  ui.set({treeNote: `${DIR === '.' ? ROOTREL : DIR} · `
    + `${TREE.filter(e => e.kind === 'file').length} files`});
}

export function renderTree() {
  // one level at a time: this directory, then a way back up while inside root
  const up = DIR !== '.'
    ? {name: '.. (' + ((DIR.includes('/') ? DIR.replace(/\/[^/]*$/, '') : '.') === '.'
        ? ROOTREL : DIR.replace(/\/[^/]*$/, '')) + ')',
      path: DIR.includes('/') ? DIR.replace(/\/[^/]*$/, '') : '.', kind: 'dir'}
    : null;
  ui.set({treeUp: up,
    treeRows: TREE.map(e => ({name: e.name, path: e.path, kind: e.kind,
      bytes: e.bytes, active: e.path === SRCREL}))});
}

export function reloadTree() { // re-list the directory we are showing
  return loadTree(DIR);
}

export async function loadTree(dir) {
  const f = await fetch('/fs?dir=' + encodeURIComponent(dir || '.')).then(x => x.json());
  if (f.error) { d.statMsg(f.error); return; }
  setListing(f);
}

async function previewFile(path) {
  const r = await api('/fs/read', {path});
  if (r.error) { d.statMsg(r.error); return; }
  msg('bot', 'preview of ' + path
      + ' (read-only here; open a .ocd in the editor to edit it).\n'
      + r.text.split('\n').slice(0, 40).join('\n'));
}

async function openFile(path) {
  d.cancelPush();  // a queued rebuild of the old board must not follow us here
  const r = await api('/fs/open', {path});
  if (r.error) { d.statMsg(r.error); return; }
  d.statMsg('');
  ui.set({msgs: [], followups: []});
  d.clearProposals();  // the server dropped the old board's proposals with it
  d.collabReset(r.rev !== undefined ? +r.rev : -1); // re-home the room
  d.applyState(r, false);
  d.collabStart(); // join the new board's room: sync + fresh SSE stream
  d.toast('opened ' + path);
  const f = await fetch('/fs').then(x => x.json());
  if (!f.error) setOpenBoard(f);
  d.loadVCS();
}

export function initTree(deps) {
  d = deps;
  $('tree').addEventListener('click', e => {
    const b = e.target.closest('button.trow');
    if (!b || !b.dataset.path) return;
    const path = b.dataset.path;
    if (b.dataset.kind === 'dir') loadTree(path);
    else if ((b.dataset.name || '').endsWith('.ocd')) openFile(path);
    else previewFile(path);
  });
}
