// Revisions: the git log of the board directory, the commit action, and the
// diff the review panel shows. The panel is a component (views.js VcsPanel).
import { ui } from './store.js';
import { $, api } from './core.js';

let d = null;             // {srcRel: () => string, statMsg, toast}
let VC = {};              // last /vcs status: repo/branch/dirty/board

export const vcsStatus = () => VC;
export function setVcs(status) { VC = status || {}; }

export async function loadVCS() {
  const r = await api('/vcs', {path: d.srcRel() || null});
  if (r.error) {
    ui.set({vcsNote: r.error, vcsRevs: [], vcsMsg: '', vcsOpen: '', vcsDiff: ''});
    return;
  }
  VC = r.status || {};
  const note = VC.repo
    ? `${VC.branch} · ` + (VC.dirty ? 'uncommitted changes in ' + VC.board : 'clean')
    : 'not a git repository';
  if (!VC.repo) {
    ui.set({vcsNote: note, vcsRevs: [], vcsOpen: '', vcsDiff: '',
      vcsMsg: 'commit from the toolbar once this directory is a repo'});
    return;
  }
  ui.set({vcsNote: note, vcsMsg: '', vcsOpen: '', vcsDiff: '',
    vcsRevs: (r.log || []).map(c => ({hash: c.hash, date: c.date, subject: c.subject}))});
}

export async function commitBoard() {
  if (!VC.repo) { d.statMsg('not a git repository'); return; }
  const m = prompt('commit message', (d.srcRel() || 'board') + ': ');
  if (!m) return;
  const r = await api('/vcs/commit', {message: m});
  if (r.error) { d.statMsg(r.error); return; }
  d.toast(r.commit || 'committed');
  loadVCS();
}

export function initVcs(deps) {
  d = deps;
  // one diff at a time, rendered after the rev it belongs to
  $('vcs').addEventListener('click', async e => {
    const b = e.target.closest('button.rev');
    if (!b) return;
    const x = await api('/vcs/diff', {hash: b.dataset.hash});
    ui.set({vcsOpen: b.dataset.hash, vcsDiff: x.error || x.diff});
  });
}
