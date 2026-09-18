// X-ray check: upload the fabricator's scan, compare it against the design's
// own x-ray render, list the divergences, and hand back the overlay.
import { ui } from './store.js';
import { $, api } from './core.js';

let xrayRaw = '';

export function initXray() {
  if ($('xrayfile')) $('xrayfile').onchange = () => {
    const f = $('xrayfile').files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      xrayRaw = String(rd.result).split(',')[1] || '';
      ui.set({xrayStat: `${f.name} ready — compare to check it`});
    };
    rd.readAsDataURL(f);
  };
  if ($('xraysvg')) $('xraysvg').onclick = async () => {
    const r = await api('/render', {key: 'xray'});
    if (r.error) { ui.set({xrayStat: r.error}); return; }
    const a = document.createElement('a');
    a.href = `data:image/svg+xml,${encodeURIComponent(r.data)}`;
    a.download = r.name;
    a.click();
    ui.set({xrayStat: r.name});
  };
  if ($('xraygo')) $('xraygo').onclick = async () => {
    if (!xrayRaw) { ui.set({xrayStat: 'pick a fab PNG first'}); return; }
    const pv = (id, fb) => {
      const v = parseFloat($(id).value);
      return Number.isFinite(v) ? v : fb;
    };
    const r = await api('/xray', {png: xrayRaw, dx: pv('xraydx', 0), dy: pv('xraydy', 0),
      scale: pv('xraysc', 1), thr: Math.round(pv('xraythr', 100))});
    if (r.error) { ui.set({xrayStat: r.error, xrayDivs: []}); return; }
    ui.set({xrayStat: `score ${r.score} — missing ${r.missing}px extra ${r.extra}px`,
      xrayDivs: (r.divs || []).map(d => ({kind: d.kind, x: d.x, y: d.y, w: d.w, h: d.h}))});
    if (r.overlay) {
      const w = open('', '_blank');
      if (w) w.document.write(r.overlay);
    }
  };
}
