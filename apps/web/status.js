// The build report as words: the feasibility badge, the DRC listing, the tidy
// metrics and the placement note. Shaping only — views.js paints it.
import { ui } from './store.js';

let H = null; // {board, statMsg}

export function drawFeas(r){
  const f=r.feasible||{};
  const ks=Object.keys(f).sort();
  if(!ks.length)return;
  // state is a word: the current layer count is marked, every count reads ok/unroutable
  ui.set({feas:'routing feasibility per layer count: '+ks.map(L=>{
    const v=f[L],here=+L===r.layers;
    const cg=v.congestion?`, congestion ${v.congestion.crossings}/${v.congestion.pairs} crossings`:'' ;
    return `<span class="feasline ${v.ok?'fok':'fbad'}${here?' here':''}" title="${v.segs} segments, ${v.wirelength}mm of wire at ${L} layer${L==='1'?'':'s'}${cg}">${L}L ${v.ok?'routable':'unroutable'}${here?' (this board)':''}</span>`;}).join(' ')});
}
// --- menubar: one menu open at a time, Esc closes, Alt+letter jumps -----
// Native <details> for the popups (no library, same as calc/health/quote
// before them): JS only enforces exclusivity + mnemonics. Shortcuts fire
// the same handlers the buttons always had — ids unchanged.

export function drawDRC(r){
  const items=[]; // [{cls, text}] — same lines, same order, same classes
  const li=r.lint||{errors:[],warnings:[]}; // static source lint, no place/route
  if(li.errors.length)items.push(...li.errors.map(e=>({cls:'err',text:`✗ lint: ${e}`})));
  if(r.errors.length)items.push(...r.errors.map(e=>({cls:'err',text:`✗ ${e}`})));
  else if(!li.errors.length)items.push({cls:'ok',text:'✓ DRC clean ('+r.fab+')'});
  items.push(...r.warnings.slice(0,5).map(w=>({cls:'warn',text:`~ ${w}`})));
  items.push(...(li.warnings||[]).slice(0,3).map(w=>({cls:'warn',text:`~ lint: ${w}`})));
  const rec=(r.recommend&&r.recommend.items)||[];
  items.push(...rec.slice(0,6).map(it=>({cls:'warn',text:`+ ${it.kind}: ${it.msg}`})));
  if(r.sim&&Object.keys(r.sim).length)items.push({cls:'ok',text:'DC: '+Object.entries(r.sim).map(([n,v])=>`${n}=${v}V`).join(' ')});
  if(r.sim_problems&&r.sim_problems.length)items.push(...r.sim_problems.map(p=>({cls:'err',text:`Simulation error: ${p}`})));
  if(r.tran&&Object.keys(r.tran).length)items.push({cls:'ok',text:'Transient: '+Object.entries(r.tran).map(([n,w])=>`${n} ${w[w.length-1].toFixed(2)}V [${Math.min(...w).toFixed(2)},${Math.max(...w).toFixed(2)}] (${w.length}pts)`).join(' · ')});
  if(r.ac&&Object.keys(r.ac).length){const db=v=>20*Math.log10(Math.max(1e-12,Math.abs(v)));
    items.push({cls:'ok',text:'AC: '+Object.entries(r.ac).map(([n,w])=>`${n} ${db(w[w.length-1]).toFixed(1)}dB@${r.f1||''}Hz [${db(Math.max(...w.map(Math.abs))).toFixed(1)}dB pk] (${w.length}pts)`).join(' · ')});}
  ui.set({drc:items});
  drawTidy(r);
}
export function tidyVal(v){ // {text, dim}: data, not markup
  if(v===null||v===undefined)return {text:'n/a',dim:true};
  if(typeof v==='number')return {text:Number.isInteger(v)?String(v):v.toFixed(3)};
  if(typeof v==='object'){const ks=Object.keys(v);
    if(v.total!==undefined&&v.per_net!==undefined)return {text:`total=${v.total}`}; // nested detail lives in STATUS.md
    return {text:ks.map(a=>`${a}=${v[a]}`).join(', ')};}
  return {text:String(v)};
}
export function drawTidy(r){
  const t=r.tidy||{};
  if(r.dense){ // metrics and the routability probe are skipped at this size
    ui.set({tidyCov:'',tidyRows:[],
      tidyNote:'tidy metrics skipped on a dense board '
        +`(${Object.keys(r.parts||{}).length} parts) — run the CLI for the full report`,
      ocd:'OCD n/a (dense)'});
    return;
  }
  const rows=Object.entries(t).filter(([k])=>k!=='coverage'&&k!=='routed_segs')
    .map(([k,v])=>[k,tidyVal(v)]);
  const sc=r.score; // OCD neatness 0-100 next to cost
  ui.set({tidyCov:t.coverage?`(${t.coverage})`:'',tidyRows:rows,tidyNote:'',
    ...(sc&&!sc.dense?{ocd:`OCD ${sc.total}/100 (${sc.grade})`}:{})});
}
// A dense board says so: it is loaded from the file's own positions, its
// metrics are skipped, and its parts list is capped. Never let the page look
// broken when the board is simply large.

export function notePlacement(r){
  const n=Object.keys(r.parts||{}).length;
  const skip=(r.skipped||[]).join(' + ');
  // A dense board usually carries no layout in its file (only pinned parts
  // have positions), so say what it needs and where to get it rather than
  // letting the canvas look broken. `solve` works but is minutes at this size.
  if(r.dense)H.statMsg(`${n} parts, loaded as saved`
    +(skip?` — ${skip} skipped at this size (run the CLI for those)`:'')
    +'. "solve" places it here, but takes minutes.',true);
  else if(r.placed===false)H.statMsg('loaded as saved — solve to re-place',true);
}
export function initStatus(deps) {
  H = deps;
}
