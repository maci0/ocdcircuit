// Photo scan: photos of a real board in, a draft .ocd out. The review UI is a
// component (views.js ScanPanel) and this shapes what it shows; the stitched
// image and its box overlay are painted here through scanPaint.fn.
// push/setEditor come from the runtime (initScan) — they own the open board.
import { ui } from './store.js';
import { $, api } from './core.js';
import { scanPaint } from './views.js';

let d = null; // {push, setEditor}

// photo scan: upload shots of a physical board, get a draft design back
const b64=f=>new Promise(r=>{const d=new FileReader();
  // split(',',1) can only ever return one element, so the old length test was
  // false for every file and the panel sent empty photo data (the server then
  // answered UnidentifiedImageError). Take everything after the first comma.
  d.onload=()=>{const s=String(d.result),i=s.indexOf(',');
    r({name:f.name,data:i>=0?s.slice(i+1):''});};
  d.readAsDataURL(f);});
async function runScan(){
  const fs=[...($('scanfiles').files||[])];
  if(!fs.length){ui.set({scanStat:'choose some photos first'});return;}
  ui.set({scanStat:`stitching ${fs.length} photos — this takes a minute…`,
    scanOut:'',scanEntries:[],scanParts:[],scanReview:false});
  $('scango').disabled=true;
  try{
    const photos=await Promise.all(fs.map(b64));
    const docs=await Promise.all([...($('scandocs').files||[])].map(b64));
    const body={photos,docs,note:$('scannote').value||'',
                answers:ui.state.scanEntries.filter(e=>e.kind==='qa'&&e.a)
                  .map(e=>({q:e.q,a:e.a}))};
    if($('scanmm').value)body.mm=Number($('scanmm').value);
    const r=await api('/scan',body);
    if(r.error){ui.set({scanStat:r.error});return;}
    const sides=Object.entries(r.sides||{}).map(([k,v])=>
      `${k}: ${v.used}/${v.photos} registered, coverage ${v.coverage_mean}`).join(' · ');
    const entries=[];
    Object.entries(r.sides||{}).forEach(([k,v])=>{
      Object.entries(v.dropped_why||{}).forEach(([nm,why])=>{
        entries.push({kind:'note',text:`dropped ${k}/${nm}: ${why}`});});});
    ui.set({scanStat:sides||'scan done',
      scanOut:r.analysis||r.draft_error||'(no analysis)'});
    scanRev = r.review||null; scanViews = r.views||{};
    scanDraft = r.draft||'';
    scanShowReview();
    if(r.draft)entries.push({kind:'open',text:'open this draft in the editor'});
    if(r.draft_error){
      entries.push({kind:'note',text:'draft did not parse: '+r.draft_error});}
    if(r.draft&&!r.draft_error){
      const fl=(r.floating||[]).length;
      entries.push({kind:'note',
        text:`buildability: ${r.wired} parts wired, ${r.drc} DRC error(s)`
          +(fl?` · ${fl} parts have no nets (photos cannot show them) — wire from the datasheet`:'')});}
    (r.questions||[]).forEach(q=>entries.push({kind:'qa',q,a:''}));
    ui.set({scanEntries:entries.map((e,i)=>({...e,i}))});
  }catch(e){ui.set({scanStat:'scan failed: '+e});}
  finally{$('scango').disabled=false;}
}

// scan review: stitch image with detected-part boxes, labels, tooltips.
// Boxes come from the draft's own fix constraints + live footprint sizes
// (the review payload), so an outline cannot drift from the positions the
// edits below act on. mm -> canvas px uses the canvas + mm_per_px from the
// same response; canvas y grows downward, board y grows up.
let scanRev=null, scanViews={}, scanDraft='';
function scanSide(){
  const want=($('scanside')||{}).value||'top';
  if(scanViews[want])return want;
  const ks=Object.keys(scanViews);
  return ks.length?ks[0]:'top';
}
function scanShowReview(){
  const has=(scanRev&&scanRev.parts&&scanRev.parts.length)||Object.keys(scanViews).length;
  ui.set({scanReview:!!has});
  if(!has)return;
  const side=scanSide();
  if($('scanside').value!==side)$('scanside').value=side;
  scanRows();
  scanRedraw();
}
function scanRedraw(){ // ask the component to repaint through scanPaint.fn
  ui.set({scanSeq:ui.state.scanSeq+1});
}
function scanScale(){
  const cv=((scanRev||{}).canvas||{})[scanSide()]||[0,0];
  const mm=(((scanRev||{}).mm_per_px||{})[scanSide()])||0;
  return {cw:cv[0]||0, ch:cv[1]||0, mm:mm||0};
}
scanPaint.fn=(svg,img)=>{ // the overlay: svg, never VDOM (painter from views.js)
  const side=scanSide();
  const views=scanViews[side]||{};
  img.src=views[($('scanviewkind')||{}).value||'stitch']||views.stitch||'';
  const {cw,ch,mm}=scanScale();
  const W=cw||img.naturalWidth||1, H=ch||img.naturalHeight||1;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const showB=$('scanboxes').checked, showL=$('scanlabels').checked;
  const parts=((scanRev||{}).parts||[]);
  let h='';
  // draft x/y grow DOWN the stitch image (the model reads positions off the
  // photo: U4 at y=82 sits low in the frame, matching "bottom-left/centre"
  // in its own inventory). canvas rows grow the same way, so no flip.
  for(const p of parts){
    if(!p.w||!p.h||!mm)continue;
    const x0=(p.x-p.w/2)/mm, x1=(p.x+p.w/2)/mm;
    const y0=(p.y-p.h/2)/mm, y1=(p.y+p.h/2)/mm;
    if(showB)h+=`<rect class="bx${p.uncertain?' unc':''}" x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1-x0).toFixed(1)}" height="${(y1-y0).toFixed(1)}" data-ref="${p.ref}"/>`;
    if(showL)h+=`<text x="${x0.toFixed(1)}" y="${(y0-3).toFixed(1)}" data-ref="${p.ref}">${p.ref}</text>`;
  }
  svg.innerHTML=h;
};
function scanTip(p){
  const bits=[`${p.ref} · ${p.fp}${p.value&&p.value!=='?'?` · ${p.value}`:''}`,
    `${p.w}×${p.h}mm at (${p.x}, ${p.y})`];
  if(p.value==='?')bits.push('value unreadable — confirm it below');
  else if(p.uncertain)bits.push('reading uncertain');
  if(p.note)bits.push(p.note);
  return bits.join(' — ');
}
function scanRows(){ // rows are components (views.js ScanPanel)
  ui.set({scanParts:((scanRev||{}).parts||[]).map(p=>({
    ref:p.ref,fp:p.fp,value:p.value||'?',uncertain:!!p.uncertain,tip:scanTip(p)}))});
}
function scanDraftLines(){return (scanDraft||'').split('\n');}
function scanSetDraft(lines){
  scanDraft=lines.join('\n');
  scanRev.parts=scanRev.parts||[];
}
function scanPartLine(ref){
  const lines=scanDraftLines();
  const i=lines.findIndex(l=>l.startsWith('part '+ref+' ')||l==='part '+ref);
  return {lines,i};
}
function scanCommit(msg){
  d.setEditor(scanDraft);d.push();
  ui.set({scanStat:msg});
}
function scanConfirm(ref){
  // '?'/hedged value -> keep fp+position, mark certain by writing the value
  // the row already shows (no-op textually) only when a real value exists;
  // otherwise ask: a confirm must resolve the '?', not bless it.
  const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
  if(!p)return;
  if(!p.value||p.value==='?'){
    scanEdit(ref, true);
    return;
  }
  // Confirming a NAMED value only clears the hedge flag: the draft line
  // already carries this value, so there is nothing to rewrite — but the
  // trailing model note ("marking likely 45DB041B") described doubt that no
  // longer applies, and leaving it would re-flag the part on reload.
  // Strip a trailing `# ...` note only; x/y/attrs are untouched.
  const {lines,i}=scanPartLine(ref);
  if(i>=0&&/#/.test(lines[i])){
    lines[i]=lines[i].split('#')[0].trimEnd();
    scanSetDraft(lines);
  }
  p.uncertain=false;p.note='';
  scanRows();scanRedraw();
  ui.set({scanStat:`${ref} confirmed as ${p.fp} ${p.value}`});
}
function scanEdit(ref, mustName){
  const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
  if(!p)return;
  const val=prompt(`value for ${ref} (footprint ${p.fp})${p.note?'\nmodel note: '+p.note:''}`, p.value==='?'?'':p.value);
  if(val===null)return;
  const fp=prompt(`footprint for ${ref} (Enter keeps ${p.fp})`, p.fp) || p.fp;
  const {lines,i}=scanPartLine(ref);
  if(i<0){ui.set({scanStat:`${ref} not found in draft text`});return;}
  // splice tokens, keep the rest of the line: the draft may carry attrs the
  // review row does not model (rot=, mpn=...), and a blind rewrite would eat
  // them. x=/y= keep model position unless the line already disagrees.
  // Values with spaces are shlex-quoted the way dumps() quotes them.
  const qv=v=>(/\s|["']/.test(v)?`"${v.replace(/"/g,'\\"')}"`:v);
  const toks=lines[i].split(/\s+/);
  // part REF FP [VALUE] [k=v ...] [# note]
  let j=3;
  if(j<toks.length&&!toks[j].includes('=')&&!toks[j].startsWith('#'))j++;
  toks.splice(2, j-2, fp, ...(val?[qv(val)]:[]));
  lines[i]=toks.join(' ');
  p.value=val;p.fp=fp;p.uncertain=false;p.note='';
  scanSetDraft(lines);scanRows();scanRedraw();
  scanCommit(`${ref} corrected — rebuilding`);
}
function scanRemove(ref){
  const {lines,i}=scanPartLine(ref);
  if(i<0){ui.set({scanStat:`${ref} not found in draft text`});return;}
  lines.splice(i,1);
  // a removed part must not leave dangling pins: a net naming a part that
  // no longer exists fails the whole draft ("net GND: unknown part 'U5'").
  // drop its pins, and drop nets left with fewer than two pins.
  const pinre=new RegExp(`\\b${ref}\\.\\S+`,'g');
  for(let k=lines.length-1;k>=0;k--){
    const ln=lines[k];
    if(!ln.startsWith('net '))continue;
    const cut=ln.replace(pinre,'').replace(/<-->\s*<-->/g,'').trim();
    const pins=(cut.match(/[A-Za-z0-9_]+\.[A-Za-z0-9_]+/g)||[]).length;
    if(pins<2)lines.splice(k,1);
    else lines[k]=cut;
  }
  scanRev.parts=(scanRev.parts||[]).filter(x=>x.ref!==ref);
  scanSetDraft(lines);scanRows();scanRedraw();
  scanCommit(`${ref} removed — rebuilding`);
}
export function initScan(deps) {
  d = deps;
  if ($('scango')) $('scango').onclick = runScan;
  $('scanparts').addEventListener('click', e => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const ref = b.closest('[data-ref]').dataset.ref;
    if (b.dataset.act === 'confirm') scanConfirm(ref);
    else if (b.dataset.act === 'edit') scanEdit(ref);
    else scanRemove(ref);
  });
  $('scanq').addEventListener('click', e => {
    if (!e.target.closest('button[data-act=open]')) return;
    d.setEditor(scanDraft); d.push();
  });
  $('scanq').addEventListener('input', e => { // typed answers live in the entries
    const i = e.target.closest('input[data-qi]');
    if (!i) return;
    const idx = +i.dataset.qi;
    ui.set({scanEntries: ui.state.scanEntries.map(en => en.i === idx ? {...en, a: i.value} : en)});
  });
  if ($('scanside')) $('scanside').onchange = scanShowReview;
  if ($('scanviewkind')) $('scanviewkind').onchange = scanShowReview;
  if ($('scanlabels')) $('scanlabels').onchange = scanShowReview;
  if ($('scanboxes')) $('scanboxes').onchange = scanShowReview;
  if ($('scansvg')) {
    $('scansvg').addEventListener('mousemove', e => {
      const t = e.target;
      const ref = t && t.dataset ? t.dataset.ref : null;
      const p = ((scanRev || {}).parts || []).find(x => x.ref === ref);
      ui.set({scanHover: p ? scanTip(p) : ''});
    });
    $('scansvg').addEventListener('mouseleave', () => ui.set({scanHover: ''}));
  }
}
