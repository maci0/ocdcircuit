// Ω calculators: same math as ocdcircuit/calc.py, instant, no round-trip.
// The inputs are legacy-wired components (views.js CalcBlock) and the answers
// ride the calc slice, so nothing writes into a preact-owned node.
import { ui } from './store.js';
import { $ } from './core.js';

export function calcLive() {
  const A = parseFloat($('ca').value) || 0, dT = parseFloat($('cdt').value) || 10;
  const area = Math.pow(A / (0.048 * Math.pow(dT, 0.44)), 1 / 0.725); // IPC-2221 ext 1oz
  const c = `${(area / 1.378 * 0.0254).toFixed(2)}mm ext, via `
    + `${(A / (3 * Math.sqrt(dT / 10))).toFixed(2)}mm drill`;
  const pv = v => {
    const m = String(v).match(/^([\d.]+)(k|M)?$/i);
    return m ? parseFloat(m[1]) * (m[2] ? ({k: 1e3, M: 1e6})[m[2].toLowerCase()] || 1 : 1) : NaN;
  };
  const V = pv($('dv').value), Rt = pv($('drt').value), Rb = pv($('drb').value);
  const d = (V >= 0 && Rt > 0 && Rb > 0) ? `Vout ${(V * Rb / (Rt + Rb)).toFixed(2)}V` : '';
  const zw = parseFloat($('zw').value) || 0, zh = parseFloat($('zh').value) || 0;
  let z = '';
  if (zw > 0 && zh > 0) { // microstrip Z0, closed-form estimate
    const u = zw / zh, er = 4.4;
    const ere = (er + 1) / 2 + (er - 1) / 2 / Math.sqrt(1 + 12 / u);
    const z0 = u <= 1 ? 60 / Math.sqrt(ere) * Math.log(8 / u + u / 4)
      : 120 * Math.PI / (Math.sqrt(ere) * (u + 1.393 + 0.667 * Math.log(u + 1.444)));
    z = `Z0 ~${z0.toFixed(1)}Ω (microstrip FR4, estimate)`;
  }
  ui.set({calc: {c, d, z}});
}

export function initCalc() {
  ['ca', 'cdt', 'dv', 'drt', 'drb', 'zw', 'zh']
    .forEach(id => $(id).addEventListener('input', calcLive));
}
