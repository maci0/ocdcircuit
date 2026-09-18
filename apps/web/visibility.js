// Visibility: what the canvas shows (a view, never a document edit), the
// layer/mark toggles and the parts list. The rows are components (views.js
// CuRows/MarkRows/PartBlock); this owns the state behind them — persisted per
// browser — the change events, and the canvas predicates the renderer asks for
// (visMark / visLayer / layerNames / partShown).
import { ui } from './store.js';
import { $ } from './core.js';

let d = null; // {board, edHl, markDirty}

const MARKLABEL={ref:"designators",value:"values",pads:"pads",pour:"copper pour",grid:"grid"};
let VIS={layers:{},parts:{},marks:{},filter:""};
try{const s=JSON.parse(localStorage.getItem("ocd-studio-vis")||"{}");
  VIS.layers=s.layers||{};VIS.parts=s.parts||{};VIS.marks=s.marks||{};}catch(e){}
function visSave(){try{localStorage.setItem("ocd-studio-vis",JSON.stringify(
  {layers:VIS.layers,parts:VIS.parts,marks:VIS.marks}));}catch(e){}}
export function layerNames(st){ // same stack the KiCad export writes
  const n=Math.max(1,+st.layers||2);
  if(n===1)return["F.Cu"];
  if(n===2)return["F.Cu","B.Cu"];
  return["F.Cu"].concat(Array.from({length:n-2},(_,i)=>`In${i+1}.Cu`),["B.Cu"]);
}
const SILKMARKS=["silk","mask"];
export function visLayer(name,st){
  const v=VIS.layers[name];
  if(v===undefined){
    const s=st.silk||"full";
    if(name==="silk")return s!=="none"&&s!=="off";
    if(name==="mask")return s==="full";
    return true;
  }
  return !!v;
}
const isCu=n=>/\.Cu$/.test(n);
export function visMark(k,st){ // explicit choice wins; otherwise silk/mask decide
  const v=VIS.marks[k];
  if(v!==undefined)return !!v;
  if(!st)return true;
  if(k==="pads")return visLayer("mask",st); // pads are the mask openings
  return visLayer("silk",st);               // ref/value/pour marks
}
export function partShown(r,st){
  return VIS.parts[r]!==false&&partMatches(r,st);
}
function partMatches(r,st){
  const f=VIS.filter.trim().toLowerCase();
  if(!f)return true;
  const p=(st&&st.parts&&st.parts[r])||{};
  return (r+" "+(p.value||"")+" "+(p.fp||"")).toLowerCase().includes(f);
}
function setAllParts(on,st){
  Object.keys((st&&st.parts)||{}).forEach(r=>{VIS.parts[r]=on;});
  visSave();renderParts();markDirty();
}

// --- layer and part visibility controls (view state, never a board edit) --
// visibility rows are components (views.js): this shapes them and handles the
// change events, which stay here because VIS, localStorage and the canvas do
export function renderLayers(st){
  st=st||d.board()||{layers:2,silk:'full'};
  ui.set({
    cuRows:layerNames(st).map(nm=>({key:nm,label:nm,on:visLayer(nm,st),
      id:'lay_'+nm.replace(/[^\w]/g,'_'),title:`copper layer ${nm}`})),
    markRows:[...SILKMARKS.map(nm=>({key:nm,
        label:nm==='silk'?'silkscreen':'mask',on:visLayer(nm,st),
        id:'mark_'+nm,title:`${nm==='silk'?'silkscreen':'mask'} on the PCB canvas`})),
      ...Object.keys(MARKLABEL).filter(k=>k!=='ref'&&k!=='value') // ride the silk toggle
        .map(k=>({key:k,label:MARKLABEL[k],on:visMark(k,st),id:'mark_'+k,
          title:`${MARKLABEL[k]} on the PCB canvas`}))]});
}
// one change listener per list: the rows say which key/ref they belong to
const MAX_ROWS=400; // DOM rows, not parts: 5400 checkboxes brick the page
export function renderParts(st){
  st=st||d.board()||{parts:{}};
  const all=Object.keys(st.parts||{}).sort();
  // a filter searches every part (the list is capped, the search is not)
  const refs=all.filter(r=>partMatches(r,st)).slice(0,MAX_ROWS);
  if(!VIS.filter.trim()&&all.length>MAX_ROWS){
    const shown=new Set(refs);
    d.edHl().forEach(r=>{if(!shown.has(r))refs.push(r);}); // keep the selection reachable
    refs.sort();
  }
  ui.set({partRows:refs.map(r=>{
    const p=st.parts[r]||{};
    return {ref:r,value:[p.value||'',p.fp||''].filter(Boolean).join(' '),
      on:VIS.parts[r]!==false,hidden:false,sel:d.edHl().has(r)};})});
  paintParts(st);
}
export function paintParts(st){
  st=st||d.board()||null;
  const all=Object.keys((st&&st.parts)||{});
  const matching=all.filter(r=>partMatches(r,st)).length;
  const n=ui.state.partRows.length;
  const rows=ui.state.partRows.map(r=>{
    const on=VIS.parts[r.ref]!==false;
    return {...r,on,hidden:!on,sel:d.edHl().has(r.ref)};});
  const hidden=rows.filter(r=>r.hidden).length;
  const filtered=!!VIS.filter.trim();
  ui.set({partRows:rows,
    partNote:!all.length?'no parts'
      :!matching?`no matching parts; clear the filter to see all ${all.length}`
      :`${n-hidden}/${n} shown`+(matching>n
        ?` · first ${n} of ${matching}${filtered?' matching':''} parts`
        :filtered?` · ${matching} matching of ${all.length}`:'')});
}
function onlyBox(open){ // the two panels overlap: never show both
  [['layerbox','partbox'],['partbox','layerbox']].forEach(([a,b])=>{
    if($(a)===open&&$(a).open)$(b).open=false;
  });
}

export function initVisibility(deps) {
  d = deps;
  $('layers').addEventListener('change',e=>{
    const i=e.target.closest('input[data-key]');
    if(!i)return;
    const key=i.dataset.key;
    if(isCu(key)||SILKMARKS.includes(key))VIS.layers[key]=i.checked;
    else VIS.marks[key]=i.checked;
    visSave();
    const st=d.board()||null;
    renderLayers(st);renderParts(st);d.markDirty();
  });
  $('partlist').addEventListener('change',e=>{
    const i=e.target.closest('input[data-ref]');
    if(!i)return;
    VIS.parts[i.dataset.ref]=i.checked;visSave();paintParts();d.markDirty();
  });
  $('partfilter').addEventListener('input',()=>{VIS.filter=$('partfilter').value;renderParts();d.markDirty();});
  $('parthide').onclick=()=>setAllParts(false,d.board());
  $('partshow').onclick=()=>setAllParts(true,d.board());
  $('layersall').onclick=()=>{
    VIS.layers={};VIS.marks={};visSave();
    const st=d.board()||null;renderLayers(st);d.markDirty();
  };
  $('layerbox').addEventListener('toggle',()=>onlyBox($('layerbox')));
  $('partbox').addEventListener('toggle',()=>onlyBox($('partbox')));
  document.addEventListener('keydown',e=>{
    if(e.target===$('ed')||e.target===$('ask'))return; // typing, not a shortcut
    if(e.key==='l'||e.key==='L'){$('layerbox').open=!$('layerbox').open;}
    else if(e.key==='p'||e.key==='P'){$('partbox').open=!$('partbox').open;}
  });
}
