// Realtime collab: one SSE stream per board, rev-guarded pushes, presence.
// Same banner pattern as the file-watch: a rev mismatch means someone else
// edited first, so reload their text (never auto-merge, never clobber).
// The workshop's runtime is injected at boot (initCollab) instead of imported:
// this module needs live reads of the board, the view and the selection, and
// legacy.js keeps those.
import { ui } from './store.js';
import { $, api } from './core.js';

let d = null; // {markDirty, cancelPush, setEditor, push, view, board, edHl, toast}

export function initCollab(deps) {
  d = deps;
}

let collabRev = -1, collabOn = false, collabUsers = [], collabTimer = null;
let collabMe = '', collabES = null, collabSyncSeq = 0;

export const collabRevNow = () => collabRev;
export const collabUserList = () => collabUsers;
export const collabMeNow = () => collabMe;
export function setCollabRev(v) { collabRev = v; }

export function collabPaint(users) {
  collabUsers = users || [];
  const others = collabUsers.filter(u => u.name !== collabMe);
  // the pill never grows past three names no matter the room size —
  // the full roster lives in the tooltip.
  const head = collabUsers.slice(0, 3).map(u => u.name).join(', ')
    + (collabUsers.length > 3 ? ` +${collabUsers.length - 3}` : '');
  ui.set({room: others.length ? `${others.length + 1} here: ${head}`
      : ((collabUsers.length ? 'solo · ' + head : 'solo')),
    roomOk: !!others.length,
    roomTitle: collabUsers.map(u => `${u.name}${u.ref ? ' on ' + u.ref : ''}`).join('\n')
      || 'no one else here yet',
    roomNote: others.length ? `live now: ${head}`
      : 'just you here — copy the link to co-edit'});
  d.markDirty();
}

export async function collabSync() { // pull the room's text (first connect + on rev bump)
  const seq = ++collabSyncSeq;
  const r = await api('/collab/sync', {});
  if (seq !== collabSyncSeq) return; // a newer sync is already in flight
  if (r.error || r.text === undefined) return;
  collabMe = r.hello || collabMe;
  collabPaint(r.users);
  if (r.rev !== undefined) collabRev = +r.rev;
  const ed = $('ed');
  if (r.text !== ed.innerText && document.activeElement !== ed) {
    d.cancelPush(); d.setEditor(r.text); d.push(); // parse + render their text
  }
}

export function collabStart() {
  if (collabOn) return;
  collabOn = true;
  collabSync();
  if (collabES) { try { collabES.close(); } catch (err) { /* already closed */ } }
  const es = collabES = new EventSource('/collab/events');
  es.onmessage = e => {
    let m; try { m = JSON.parse(e.data); } catch (err) { return; }
    if (m.hello !== undefined) collabMe = m.hello;
    if (m.users) collabPaint(m.users);
    if (m.rev !== undefined && +m.rev !== collabRev && (m.by || '') !== collabMe
       && (m.by !== undefined || m.rev > collabRev)) { // somebody's push landed
      collabRev = +m.rev; collabSync();
    }
  };
  es.onerror = () => { // the stream drops (sleep, proxy): re-sync, the next rev heals
    if (collabES !== es) return; // rehomed already — this stream is dead, stay dead
    try { es.close(); } catch (err) { /* already closed */ }
    collabES = null; collabOn = false; setTimeout(collabStart, 3000);
  };
  // presence: cursor + selected ref, every 5s (the server prunes at 15s)
  clearInterval(collabTimer);
  collabTimer = setInterval(async () => {
    try {
      const v = d.view();
      const b = d.board();
      const r = await api('/collab/cursor', {x: v.t || 0, y: 0,
        ref: ((b && b.cur && b.cur.hover) || (d.edHl.size ? [...d.edHl][0] : ''))});
      if (r.users) collabPaint(r.users);
      if (r.rev !== undefined) collabRev = +r.rev;
    } catch (err) { /* offline: the next tick retries */ }
  }, 5000);
  const sh = $('sharebtn');
  if (sh) sh.onclick = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      d.toast('link copied — send it to your collaborator');
    } catch (err) {
      d.toast('copy failed — select the URL from the address bar');
    }
  };
}

// opening another board: kill the old stream, drop the old room's rev, and a
// queued sync must not land on the new room
export function collabReset(rev) {
  collabSyncSeq++;
  if (collabES) { try { collabES.close(); } catch (err) { /* already closed */ } }
  collabES = null;
  collabOn = false;
  collabRev = rev === undefined ? -1 : rev;
  collabPaint([]);
}
