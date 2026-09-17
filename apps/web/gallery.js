// Candidates filmstrip behaviour: generate N layouts, compare two, adopt one.
// The strip and its captions are components (views.js Gallery); the frames are
// painted here through the painter the component registers (thumbPaint.fn).
import { ui } from './store.js';
import { $, api } from './core.js';
import { thumbPaint } from './views.js';

let d = null; // {board, palette, statMsg, applyState, withBusy, drawFeas}

let galSeed = 0;
let galBase = null; // shift-clicked compare base: {i, cand}
let galMeta = {n: 4, seed: 0};
let galCands = [];

export function galDelta(a, b) { // cost delta + parts moved >2mm between candidates
  let moved = 0;
  for (const r in a.pos) {
    const q = b.pos[r];
    if (!q) continue;
    const dx = a.pos[r][0] - q[0], dy = a.pos[r][1] - q[1];
    if (dx * dx + dy * dy > 4) moved++;
  }
  return {dcost: +(b.cost - a.cost).toFixed(1), moved};
}

function galLabel(i, text) { // rewrite one caption
  ui.set({galThumbs: ui.state.galThumbs.map(t => t.i === i ? {...t, label: text} : t)});
}

function galCompare(i, cand) {
  if (galBase && galBase.i === i) { // toggle off
    galBase = null;
    ui.set({galThumbs: galCands.map((c, j) => ({i: j, cand: c, label: `#${j} cost ${c.cost}`}))});
    d.statMsg(`${galCands.length} candidates — click one to pick`, true);
    return;
  }
  if (!galBase) {
    galBase = {i, cand};
    galLabel(i, `#${i} cost ${cand.cost} (base — click another)`);
    d.statMsg(`comparing from #${i} — click another candidate`, true);
    return;
  }
  const x = galDelta(galBase.cand, cand);
  galLabel(i, `#${i} cost ${cand.cost} (Δ${x.dcost >= 0 ? '+' : ''}${x.dcost}, ${x.moved} moved)`);
  d.statMsg(`#${galBase.i}→#${i}: Δcost ${x.dcost >= 0 ? '+' : ''}${x.dcost}, `
    + `${x.moved} parts moved — click to pick`, true);
}

export async function genCands() {
  const S = d.board();
  if (!S) return;
  const n = Math.max(1, Math.min(8, parseInt($('ncand').value || '4', 10)));
  galSeed = (galSeed + 1) % 1000;
  await d.withBusy($('dice'), `generating ${n}…`, async () => {
    const r = await api('/candidates', {placer: $('placer').value, n, seed: galSeed, iters: 400});
    if (r.error) { d.statMsg(r.error); return; }
    galMeta = {n, seed: galSeed};
    galCands = r.candidates;
    galBase = null;
    ui.set({galOpen: true, galThumbs: r.candidates.map((c, i) =>
      ({i, cand: c, label: `#${i} cost ${c.cost}`}))});
    d.drawFeas({feasible: r.feasible, layers: r.layers});
    d.statMsg(`${n} candidates — click to pick, shift-click two to compare`, true);
  });
}

async function pickCand(i) {
  await d.withBusy($('dice'), `picking #${i}…`, async () => {
    const r = await api('/pick', {placer: $('placer').value, router: $('router').value,
      index: i, n: galMeta.n, seed: galMeta.seed, iters: 400, silk: $('silk').value});
    if (r.error) { d.statMsg(r.error); return; }
    d.statMsg('');
    ui.set({galOpen: false});
    d.applyState(r, true);
  });
}

export function initGallery(deps) {
  d = deps;
  thumbPaint.fn = (cand, i, cv) => { // the filmstrip's pixels: canvas, never VDOM
    const S = d.board(), C = d.palette();
    const ctx = cv.getContext('2d'), W = 300, H = 220;
    const s = Math.min(W / S.bw, H / S.bh);
    const ox = (W - S.bw * s) / 2, oy = (H - S.bh * s) / 2;
    ctx.fillStyle = C.paper; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = C.line2; ctx.strokeRect(ox, oy + S.bh * s, S.bw * s, -S.bh * s);
    for (const r in cand.pos) {
      const p = S.parts[r];
      if (!p) continue;
      const [x, y] = cand.pos[r];
      const fixed = S.fixed && S.fixed[r];
      ctx.fillStyle = fixed ? C.wash : C.ink2;
      ctx.fillRect(ox + (x - p.w / 2) * s, oy + (S.bh - y - p.h / 2) * s, p.w * s, p.h * s);
      ctx.strokeStyle = fixed ? C.signal : C.ink;
      ctx.strokeRect(ox + (x - p.w / 2) * s, oy + (S.bh - y - p.h / 2) * s, p.w * s, p.h * s);
    }
  };
  // one click for the whole strip: shift-click compares, plain click adopts
  $('gal').addEventListener('click', e => {
    const b = e.target.closest('button.galpick');
    if (!b) return;
    const i = +b.dataset.i, cand = galCands[i];
    if (e.shiftKey) galCompare(i, cand); else pickCand(i);
  });
}
