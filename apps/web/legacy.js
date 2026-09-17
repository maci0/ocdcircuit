// Workshop behaviour, moved verbatim out of apps/studio.py.
// Canvas render loops, drag/drop and the collab SSE stream stay imperative
// on purpose: they drive the DOM directly, not the VDOM.
// Loaded as a module AFTER workshop.js, so every id it reaches for exists.
// NOTE: modules are strict mode — never assign to an undeclared name here.
import { ui } from './store.js';
import { scanPaint, thumbPaint } from './views.js';
import { $, api } from './core.js';
import { collabMeNow, collabReset, collabRevNow, collabStart, collabUserList,
         initCollab, setCollabRev } from './collab.js';
import { initKb } from './kb.js';
import { commitBoard, initVcs, loadVCS, setVcs } from './vcs.js';
import { genCands, initGallery } from './gallery.js';
import { initScan } from './scan.js';
let S=null, anim=null;
const ease=t=>1-Math.pow(1-t,3);
function fit(cv){ // size canvas once per real resize; dpr capped (4x pixels buy nothing)
  const R=cv.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);
  const w=Math.max(1,Math.round(R.width*dpr)),h=Math.max(1,Math.round(R.height*dpr));
  if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return [ctx,R];
}
const TRACECOLS=['#8a2318','#1d5fa8','#0f5c37','#6b3fa0']; // net hues: red/blue/green/violet
// canvas palette: paper ground, ink marks, one signal green (matches the sheet)
// dark theme re-points these at paint time (readTheme), never at draw time.
const C={paper:'#f7f5f0',paper2:'#efece4',card:'#fffdf8',line:'#e2ddd0',line2:'#cfc8b6',ink:'#1a1d21',ink2:'#4d545c',ink3:'#5c646c',
  signal:'#0f5c37',wash:'#dcefe1',bad:'#8a2318',warn:'#6b4a00',copper:'#9a7134',
  pad:'#d9a821',padline:'#8a6d00'}; // pad copper: same pair the SVG/PNG renders use
const CDARK={paper:'#101418',paper2:'#1a2129',card:'#161c22',line:'#2a333d',line2:'#3a4550',ink:'#d8e2dc',ink2:'#aeb8c0',ink3:'#7f8b94',
  signal:'#5fd894',wash:'#123526',bad:'#ff7364',warn:'#ffd8a0',copper:'#c9962e',
  pad:'#d9a821',padline:'#8a6d00'};
const CLIGHT={...C};
function readTheme(){ // Flux-dark: one class on body re-points the palette
  const dark=document.body.classList.contains('dark');
  Object.assign(C,dark?CDARK:CLIGHT);
  return dark;}
const bgCol=C.paper; // PCB ground; getComputedStyle per frame forces a style flush
// copper pour: a spec-sheet hatch with the thermal gaps punched out of it, so
// the routing underneath still reads through the flood.
let pourCv=null;
function pourTile(px,py,w,h,cuts){
  if(!pourCv){pourCv=document.createElement('canvas');}
  if(pourCv.width!==px||pourCv.height!==py){pourCv.width=px;pourCv.height=py;}
  const g=pourCv.getContext('2d');
  g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,px,py);
  g.strokeStyle=C.copper;g.globalAlpha=0.5;g.lineWidth=1.3; // 45 deg hatch, screen scale
  for(let d=-py;d<px;d+=7){g.beginPath();g.moveTo(d,0);g.lineTo(d+py,py);g.stroke();}
  g.globalAlpha=1;
  g.globalCompositeOperation='destination-out'; // cutouts become holes, not slabs
  for(const r of cuts)g.fillRect(r[0],r[1],r[2]-r[0],r[3]-r[1]);
  g.globalCompositeOperation='source-over';
  return {cv:pourCv,ox:w,oy:h};
}
// --- visibility: what the canvas shows (a view, never a document edit) ---
// Off-list marks: ref, value, pads, pour, grid. Layers are keyed by the same
// index traces carry (0 = F.Cu, 1 = B.Cu, then In1.Cu...), so the toggles name
// exactly the layers the fab export does. Persisted per browser.
const MARKLABEL={ref:"designators",value:"values",pads:"pads",pour:"copper pour",grid:"grid"};
let VIS={layers:{},parts:{},marks:{},filter:""};
try{const s=JSON.parse(localStorage.getItem("ocd-studio-vis")||"{}");
  VIS.layers=s.layers||{};VIS.parts=s.parts||{};VIS.marks=s.marks||{};}catch(e){}
function visSave(){try{localStorage.setItem("ocd-studio-vis",JSON.stringify(
  {layers:VIS.layers,parts:VIS.parts,marks:VIS.marks}));}catch(e){}}
function layerNames(st){ // same stack the KiCad export writes
  const n=Math.max(1,+st.layers||2);
  if(n===1)return["F.Cu"];
  if(n===2)return["F.Cu","B.Cu"];
  return["F.Cu"].concat(Array.from({length:n-2},(_,i)=>`In${i+1}.Cu`),["B.Cu"]);
}
const SILKMARKS=["silk","mask"];
function visLayer(name,st){
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
function visMark(k,st){ // explicit choice wins; otherwise silk/mask decide
  const v=VIS.marks[k];
  if(v!==undefined)return !!v;
  if(!st)return true;
  if(k==="pads")return visLayer("mask",st); // pads are the mask openings
  return visLayer("silk",st);               // ref/value/pour marks
}
function partShown(r,st){ // visibility + the parts filter
  if(VIS.parts[r]===false)return false;
  const f=VIS.filter.trim().toLowerCase();
  if(!f)return true;
  const p=(st&&st.parts&&st.parts[r])||{};
  return (r+" "+(p.value||"")+" "+(p.fp||"")).toLowerCase().includes(f);
}
function setAllParts(on,st){
  Object.keys((st&&st.parts)||{}).forEach(r=>{VIS.parts[r]=on;});
  visSave();renderParts();markDirty();
}
function drawPCB(st, t){ // t: 0..1 trace reveal + part blend handled by caller
  view.t=t;
  const [ctx,R]=fit($('pcb'));
  const s0=Math.min(R.width/st.bw,R.height/st.bh),s=Math.max(1,s0);
  const ox=(R.width-st.bw*s)/2,oy=(R.height-st.bh*s)/2;
  const X=x=>ox+x*s,Y=y=>oy+(st.bh-y)*s;
  ctx.fillStyle=bgCol;ctx.fillRect(0,0,R.width,R.height);
  const cuts=(st.cuts&&st.cuts['0'])||[];
  if(visMark('grid',st)){
    ctx.strokeStyle=C.line; // board grid sits under the copper, not on top of it
    for(let gx=0;gx<=st.bw;gx+=5){ctx.beginPath();ctx.moveTo(X(gx),Y(0));ctx.lineTo(X(gx),Y(st.bh));ctx.stroke();}
    for(let gy=0;gy<=st.bh;gy+=5){ctx.beginPath();ctx.moveTo(X(0),Y(gy));ctx.lineTo(X(st.bw),Y(gy));ctx.stroke();}
  }
  if(visMark('pour',st)&&st.pours&&Object.values(st.pours).some(lls=>lls.includes(0))){
    const e=st.edge||0.3;
    const px=Math.ceil((st.bw-2*e)*s),py=Math.ceil((st.bh-2*e)*s);
    const tile=pourTile(px,py,(st.bw-2*e)*s,(st.bh-2*e)*s,cuts);
    ctx.drawImage(tile.cv,0,0,px,py,X(e),Y(st.bh-e),tile.ox,tile.oy);
  }
  ctx.strokeStyle=C.line2;ctx.strokeRect(X(0),Y(st.bh),st.bw*s,st.bh*s);
  const cols=TRACECOLS;
  const names=layerNames(st);
  const n=Math.min(Math.ceil(st.traces.length*t),MAX_SEGS);
  // Label every part on a normal board; on a dense one the refs overlap into
  // noise, so draw a readable sample (every 6th) and always the selected ones.
  const labelEvery=st.compact?6:1;
  let labelSeq=0;
  // Batch traces: one path per (layer, width) instead of a stroke() per
  // segment. On a dense board this is the whole frame — 9.6k strokes -> a
  // handful — and it is the same pixels (same colour, same line width, each
  // segment still an independent pair of points).
  const buckets=new Map();
  for(let i=0;i<n;i++){const g=st.traces[i];
    const nm=names[g.layer];
    if(nm&&!visLayer(nm,st))continue; // layer hidden: not drawn, not batched
    const lw=Math.max(1,g.w*s);
    const key=g.layer+'|'+lw;
    let b=buckets.get(key);
    if(!b){b={color:cols[g.layer%4],lw,segs:[]};buckets.set(key,b);}
    b.segs.push(g);}
  for(const b of buckets.values()){
    ctx.strokeStyle=b.color;ctx.lineWidth=b.lw;ctx.beginPath();
    for(const g of b.segs){
      // shift-drag preview: the dragged segment draws at its drop point
      let ox=0,oy=0;
      if(segDrag&&segDrag.seg===g){ox=segDrag.dx*s;oy=-segDrag.dy*s;}
      // a fresh moveTo per segment: segments stay separate (no spurious joins)
      ctx.moveTo(X(g.x1)+ox,Y(g.y1)+oy);ctx.lineTo(X(g.x2)+ox,Y(g.y2)+oy);
      ctx.moveTo(X(g.x2)+ox,Y(g.y2)+oy);
    }
    ctx.stroke();}
  if(segDrag){ // drop target: crosshair at the dragged midpoint
    ctx.strokeStyle=C.signal;ctx.lineWidth=1.5;ctx.beginPath();
    const mx=X((segDrag.seg.x1+segDrag.seg.x2)/2)+segDrag.dx*s;
    const my=Y((segDrag.seg.y1+segDrag.seg.y2)/2)-segDrag.dy*s;
    ctx.moveTo(mx-8,my);ctx.lineTo(mx+8,my);ctx.moveTo(mx,my-8);ctx.lineTo(mx,my+8);ctx.stroke();}
  for(const r in st.parts){
    if(!partShown(r,st))continue;
    const p=st.parts[r];
    ctx.fillStyle=st.fixed&&st.fixed[r]?C.wash:C.ink2; // pinned parts wear the signal wash
    ctx.fillRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    ctx.strokeStyle=st.fixed&&st.fixed[r]?C.signal:C.ink;
    ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    // the footprint itself, not its box: real pads + drills + pin-1, so an 0805
    // and a SOIC8 stop looking alike (same geometry the SVG/PNG renders use)
    if(visMark('pads',st))for(const pd of (p.pads||[])){
      const cx=X(p.x+pd.x),cy=Y(p.y+pd.y),pw=Math.max(1,pd.w*s),ph=Math.max(1,pd.h*s);
      ctx.fillStyle=C.pad;ctx.strokeStyle=C.padline;
      ctx.fillRect(cx-pw/2,cy-ph/2,pw,ph);ctx.strokeRect(cx-pw/2,cy-ph/2,pw,ph);
      if(pd.d>0){ctx.fillStyle=bgCol;ctx.beginPath();ctx.arc(cx,cy,Math.max(0.8,pd.d*s/2),0,7);ctx.fill();}
      if(pd.p1){ctx.fillStyle=C.ink3;ctx.beginPath();ctx.arc(cx,cy,Math.max(0.8,0.22*s),0,7);ctx.fill();}}
    // On a dense board every label overlaps its neighbours anyway (5,420 refs
    // in one canvas): draw a readable sample instead of 5,420 glyph runs.
    const drawRef=labelEvery===1||(labelSeq%labelEvery===0)||edHl.has(r);
    labelSeq++;
    const fs=Math.min(12,Math.max(7,p.h*s*0.32)); // never wider than the box
    if(drawRef){
      ctx.font=`${fs}px ui-monospace,Menlo,monospace`;ctx.textAlign='center';ctx.textBaseline='middle';
      if(visMark('ref',st)&&visLayer('silk',st)){
        ctx.fillStyle=st.fixed&&st.fixed[r]?C.signal:'#f7f5f0';
        const name=r.length*fs*0.62>p.w*s?r.slice(0,Math.max(1,Math.floor(p.w*s/(fs*0.62))))+'…':r;
        ctx.fillText(name,X(p.x),Y(p.y));
      }
      ctx.textBaseline='alphabetic';
      if(visMark('value',st)&&visLayer('silk',st)&&st.silk!=='ref'&&p.value&&p.h*s>18){
        ctx.fillStyle=C.ink3;ctx.font=`${Math.min(9,fs*0.8)}px ui-monospace,Menlo,monospace`;
        ctx.fillText(p.value,X(p.x),Y(p.y-p.h/2)+10);}
    }
    if(edHl.has(r)){ctx.strokeStyle=C.signal;ctx.lineWidth=3; // editor text selection → ring
      ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);ctx.lineWidth=1;}
    // collaborators: whoever has this part selected rings it in their color.
    // Several cursors can share one part — stack the name tags so N users
    // stay readable instead of overprinting.
    let stack=0;
    for(const u of collabUserList()){if(u.ref!==r||u.name===collabMeNow())continue;
      const pad=3+stack*2;
      ctx.strokeStyle=u.color||'#1d5fa8';ctx.lineWidth=2;ctx.setLineDash([4,3]);
      ctx.strokeRect(X(p.x-p.w/2)-pad,Y(p.y+p.h/2)-pad,p.w*s+pad*2,p.h*s+pad*2);
      ctx.setLineDash([]);ctx.fillStyle=u.color||'#1d5fa8';
      ctx.font='10px ui-monospace,Menlo,monospace';ctx.textAlign='left';
      ctx.fillText(u.name,X(p.x-p.w/2)-pad,Y(p.y+p.h/2)-pad-3-stack*11);ctx.lineWidth=1;
      stack++;}}
  // instance groups (block stamping): shared dashed outline + tag, one hue per owner
  const groups={};
  for(const r in st.parts){if(!partShown(r,st))continue;
    const p=st.parts[r];if(!p.owner)continue;
    const g=groups[p.owner]||(groups[p.owner]=[1e9,1e9,-1e9,-1e9]);
    g[0]=Math.min(g[0],p.x-p.w/2);g[1]=Math.min(g[1],p.y-p.h/2);
    g[2]=Math.max(g[2],p.x+p.w/2);g[3]=Math.max(g[3],p.y+p.h/2);}
  const hues=Object.keys(groups);
  hues.forEach((o,i)=>{const g=groups[o],c=`hsl(${(i*137)%360},55%,35%)`;
    ctx.strokeStyle=c;ctx.setLineDash([5,3]);
    ctx.strokeRect(X(g[0]-1),Y(g[3]+1),(g[2]-g[0]+2)*s,(g[3]-g[1]+2)*s);
    ctx.setLineDash([]);ctx.fillStyle=c;ctx.font='10px ui-monospace,Menlo,monospace';ctx.textAlign='left';
    ctx.fillText(o.replace(/_$/,''),X(g[0]-1),Y(g[3]+1)-3);});
  return {s,ox,oy};
}
let view={s:1,ox:0,oy:0,t:1};
const MAX_SEGS=25000; // dense boards route 25k+: drawing all of them every frame is a slideshow
let schSel=null, schDirty=true; // selected "REF.PIN"
let edHl=new Set(), edPin=new Set(); // refs/pins named by the editor's text selection
function drawSCH(st){
  if(schDirty){ // static until nets/selection change — not 20fps
    const [ctx,R]=fit($('sch'));
    ctx.fillStyle=C.card||C.paper;ctx.fillRect(0,0,R.width,R.height);
    st._schmap={pins:[],nets:[]};
    const sch=st.sch||{order:[],px:{},rail_y:{},top:70,W:0},cols=TRACECOLS;
    const zw=sch.W||Math.max(...Object.values(sch.px),1)+60; // world px → fit
    const zx=Math.min(1,R.width/Math.max(1,zw)); // shrink-to-fit only, never upscale
    ctx.save();ctx.scale(zx,zx);
  (sch.sections||[]).forEach(s=>{ctx.fillStyle=C.ink2;ctx.textAlign='center';
    ctx.font='12px ui-monospace,Menlo,monospace';
    ctx.fillText(s.name,(s.x0+s.x1)/2,sch.top-48);});
  sch.order.forEach(r=>{const hi=edHl.has(r); // editor selection lights the same box
    ctx.fillStyle=hi?C.wash:C.paper2;ctx.fillRect(sch.px[r]-50,sch.top-34,100,30);
    ctx.strokeStyle=hi?C.signal:C.ink;ctx.lineWidth=hi?2:1;
    ctx.strokeRect(sch.px[r]-50,sch.top-34,100,30);ctx.lineWidth=1;
    ctx.fillStyle=hi?C.signal:C.ink;ctx.textAlign='center';ctx.font='12px ui-monospace,Menlo,monospace';ctx.fillText(r,sch.px[r],sch.top-20);});
  Object.keys(st.nets).forEach((n,i)=>{const y=sch.rail_y[n];if(y===undefined)return;
    const xs=st.nets[n].map(pp=>sch.px[pp.split('.')[0]]).filter(x=>x!==undefined);
    if(!xs.length)return;
    ctx.strokeStyle=cols[i%4];ctx.lineWidth=2;ctx.beginPath();
    ctx.moveTo(Math.min(...xs),y);ctx.lineTo(Math.max(...xs),y);ctx.stroke();ctx.lineWidth=1;
    ctx.fillStyle=C.ink2;ctx.font='11px ui-monospace,Menlo,monospace';ctx.fillText(n,Math.min(...xs)-8,y+4);
    st._schmap.nets.push({n,x:((Math.min(...xs)+Math.max(...xs))/2)*zx,y:y*zx});
    st.nets[n].forEach(pp=>{const x=sch.px[pp.split('.')[0]];if(x===undefined)return;
      ctx.strokeStyle=cols[i%4];ctx.beginPath();ctx.moveTo(x,sch.top-4);ctx.lineTo(x,y);ctx.stroke();
      const sel=schSel===pp||edPin.has(pp);
      ctx.fillStyle=sel?C.ink:cols[i%4];ctx.beginPath();ctx.arc(x,y,4,0,7);ctx.fill();
      if(sel){ctx.strokeStyle=C.ink;ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,7);ctx.stroke();ctx.lineWidth=1;}
      st._schmap.pins.push({pp,net:n,x:x*zx,y:y*zx});});});
    ctx.restore();
    schDirty=false;
  }
}
// --- schematic edits → .ocd text (two-way binding) ---
function schLines(){return $('ed').innerText.split('\n');}
const schEsc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
// parse net lines: {name, idx, pins:[{tok, li}]} (li = line index)
function schNets(){
  const lines=schLines(),out=[];
  lines.forEach((l,li)=>{let m=l.match(/^(\S+?)((?:\s+[LWlw][\d.]+)*)\s*::\s*(.*)$/);
    if(!m){const m2=l.match(/^net\s+(\S+?)(?:\s+[LWlw][\d.]+)*\s*:\s*(.*)$/);if(m2)m=[m2[0],m2[1],"",m2[2]];}
    if(m)out.push({name:m[1],li,pins:m[3].split(/\s*<-->\s*|\s+/).filter(Boolean).filter(t=>t.includes('.'))});});
  return out;
}
function schCommit(lines){$('ed').innerText=lines.join('\n');push();}
function schMovePin(pp,dst){
  const [ref,pin]=pp.split('.'),lines=schLines(),nets=schNets();
  let changed=false;
  for(const n of nets){
    const i=n.pins.findIndex(t=>{const [r,p]=t.split('.');return r===ref&&p===pin;});
    if(i>=0&&n.name!==dst){n.pins.splice(i,1);changed=true;
      lines[n.li]=schSplitPins(lines[n.li],n.pins);}
  }
  for(const n of nets)if(n.name===dst){n.pins.push(`${ref}.${pin}`);
    lines[n.li]=schSplitPins(lines[n.li],n.pins);changed=true;}
  if(changed)schCommit(lines);
}
function schSplitPins(line,pins){ // rebuild one net line: join with space-<-->, never ':'
  const i=line.indexOf('::')>=0?line.indexOf('::'):line.indexOf(':');
  const head=line.slice(0,i+ (line[i+1]===':'?2:1) ).replace(/\s+$/,'');
  return pins.length?head+' '+pins.join(' <--> '):head;
}
function schDropPin(pp){
  const [ref,pin]=pp.split('.'),lines=schLines(),nets=schNets();
  for(const n of nets){
    const i=n.pins.findIndex(t=>{const [r,p]=t.split('.');return r===ref&&p===pin;});
    if(i>=0){lines[n.li]=schSplitPins(lines[n.li],n.pins.filter((_,j)=>j!==i));
      schCommit(lines);return;}
  }
}
function schRename(net){
  const to=prompt(`rename net ${net} to:`,net);
  if(!to||to===net||!/^\w+$/.test(to))return;
  const esc=schEsc(net);
  const lines=schLines().map(l=>{
    if(/^(net\s+|route\s+|trace\s+|power\s+|match\s+)/.test(l))
      return l.replace(new RegExp(`^((?:net|route|trace|power|match)\\s+)${esc}\\b`,'$1'+to));
    if(/^\S+(\s+[LWlw][\d.]+)*\s*::/.test(l))
      return l.replace(new RegExp(`^${esc}(?=\\s|::)`),to);
    return l;});
  // join lists: word-boundary replace on those lines only
  for(let i=0;i<lines.length;i++){
    if(/\bjoin\b/.test(lines[i]))
      lines[i]=lines[i].replace(new RegExp(`\\b${esc}\\b`, 'g'),to);
  }
  schCommit(lines);
}
function schNextRef(){
  const lines=schLines(),have=new Set();
  lines.forEach(l=>{const m=l.match(/^part\s+(\S+)/);if(m)have.add(m[1]);});
  let i=1;while(have.has('U'+i))i++;return 'U'+i;
}
function schNextNet(){
  const have=new Set(schNets().map(n=>n.name));
  let i=1;while(have.has('N'+i))i++;return 'N'+i;
}
function schAddPart(){
  const ref=prompt('new part ref:',schNextRef());
  if(!ref||!/^\w+$/.test(ref)){if(ref!==null)statMsg('ref must be word chars');return;}
  if(schLines().some(l=>l.match(new RegExp('^part\\s+'+schEsc(ref)+'\\b')))){statMsg(ref+' already exists');return;}
  const fp=prompt(`footprint for ${ref} (e.g. R0805, SOIC8):`,'R0805');
  if(fp===null)return;
  if(!fp.trim()){statMsg('footprint cannot be blank');return;}
  const lines=schLines();
  let i=lines.findIndex(l=>/^\s*part\s/.test(l));
  if(i<0)i=lines.findIndex(l=>/^\s*(net\s|\S+\s*::)/.test(l));
  if(i<0)i=lines.length;
  lines.splice(i,0,`part ${ref} ${fp.trim()}`);
  schCommit(lines);statMsg(`added ${ref}`,true);
}
function schNewNet(pp){
  const name=prompt('new net name:',schNextNet());
  if(!name||!/^\w+$/.test(name)){if(name!==null)statMsg('net name must be word chars');return;}
  const lines=schLines();
  lines.push(`${name} :: ${pp}`);
  schCommit(lines);schSel=null;
}
(()=>{const c=$('sch');
c.addEventListener('mousedown',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const p of S._schmap.pins){
    if(Math.abs(mx-p.x)<7&&Math.abs(my-p.y)<7){
      if(e.altKey){schDropPin(p.pp);schSel=null;schDirty=true;return;}
      schSel=(schSel===p.pp)?null:p.pp;schDirty=true;return;}}
  for(const n of S._schmap.nets){
    if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){
      if(schSel){schMovePin(schSel,n.n);schSel=null;}schDirty=true;return;}}
  if(schSel){schNewNet(schSel);return;}
  schSel=null;schDirty=true;});
c.addEventListener('dblclick',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const n of S._schmap.nets)if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){schRename(n.n);return;}
  schAddPart();});
})();
function draw3D(st,rot){
  const [ctx,R]=fit($('t3d'));
  ctx.fillStyle=C.card||C.paper;ctx.fillRect(0,0,R.width,R.height);
  const cx=R.width/2,cy=R.height/2+20,s=Math.min(R.width/(st.bw+20),R.height/(st.bh+14));
  const P=(x,y,z)=>{const a=rot,dx=x-st.bw/2,dy=y-st.bh/2;
    const rx=dx*Math.cos(a)-dy*Math.sin(a),ry=(dx*Math.sin(a)+dy*Math.cos(a))*0.5-z*0.9;
    return [cx+rx*s,cy+ry*s];};
  const faces=[];
  // lambert-ish: top faces full color, sides shaded by facing
  function shade(hex,k){const n=parseInt(hex.slice(1),16);
    const r=Math.min(255,((n>>16)&255)*k)|0,g=Math.min(255,((n>>8)&255)*k)|0,b=Math.min(255,(n&255)*k)|0;
    return `rgb(${r},${g},${b})`;}
  function box(x0,y0,z0,x1,y1,z1,cols){const c000=P(x0,y0,z0),c100=P(x1,y0,z0),c110=P(x1,y1,z0),c010=P(x0,y1,z0),c001=P(x0,y0,z1),c101=P(x1,y0,z1),c111=P(x1,y1,z1),c011=P(x0,y1,z1);
    // cols: {top, front, side} — top brightest (tmog: brightest = live data)
    faces.push({z:z1,p:[c001,c101,c111,c011],c:cols.top});
    faces.push({z:z0,p:[c000,c100,c110,c010],c:shade(cols.top,0.35)});
    faces.push({z:(z0+z1)/2,p:[c000,c100,c101,c001],c:cols.front});
    faces.push({z:(z0+z1)/2,p:[c100,c110,c111,c101],c:cols.side});
    faces.push({z:(z0+z1)/2,p:[c110,c010,c011,c111],c:shade(cols.front,0.8)});
    faces.push({z:(z0+z1)/2,p:[c010,c000,c001,c011],c:shade(cols.side,0.8)});}
  function cyl(cxx,cyy,rad,z0,z1,cols){ // round bodies (electrolytics, headers)
    const N=12,top=[],bot=[];
    for(let i=0;i<N;i++){const a=i/N*2*Math.PI;
      top.push(P(cxx+rad*Math.cos(a),cyy+rad*Math.sin(a),z1));
      bot.push(P(cxx+rad*Math.cos(a),cyy+rad*Math.sin(a),z0));}
    for(let i=0;i<N;i++){const j=(i+1)%N;
      faces.push({z:(z0+z1)/2,p:[bot[i],bot[j],top[j],top[i]],c:cols.side});}
    faces.push({z:z1,p:top,c:cols.top});
    faces.push({z:z0,p:bot,c:shade(cols.top,0.35)});}
  const MASK={top:'#0f5c37',front:'#0a3d25',side:'#0c4a2d'};
  box(0,0,0,st.bw,st.bh,1.6,MASK);
  // copper traces on top layer shimmer gold (hidden with that layer)
  if(visLayer(layerNames(st)[0],st))
    for(const g of st.traces.slice(0,400)){if(g.layer!==0)continue;
      const w=Math.max(0.15,g.w/2);
      faces.push({z:1.75,p:[P(g.x1-w,g.y1-w,1.7),P(g.x2+w,g.y1-w,1.7),P(g.x2+w,g.y2+w,1.7),P(g.x1-w,g.y2+w,1.7)],c:'#c9962e'});}
  const MATS={chip:{top:'#3a3f45',front:'#22262b',side:'#2c3136'},tant:{top:'#d9a419',front:'#8a6a0a',side:'#b8890f'},
    elec:{top:'#9aa3b5',front:'#5a6270',side:'#767f92'},led:{top:'#c0392b',front:'#7a1a12',side:'#96261a'},
    steel:{top:'#c8ccd2',front:'#7a7e85',side:'#9ea3ab'},plastic:{top:'#2b2f34',front:'#16191d',side:'#212528'},
    copper:{top:'#d9a832',front:'#8a6a1a',side:'#b8891f'}};
  for(const r in st.parts){
    if(!partShown(r,st))continue; // the 3D view hides what the PCB view hides
    const p=st.parts[r];
    const cols=MATS[p.mat]||MATS.chip;
    for(const bd of (p.bodies||[{w:p.w-0.6,h:p.h-0.6,z:1.6,hgt:p.h3d||1,dx:0,dy:0}])){
      if(bd.cyl){cyl(p.x+bd.dx,p.y+bd.dy,bd.w/2,bd.z,bd.z+bd.hgt,cols);continue;}
      box(p.x+bd.dx-bd.w/2,p.y+bd.dy-bd.h/2,bd.z,p.x+bd.dx+bd.w/2,p.y+bd.dy+bd.h/2,bd.z+bd.hgt,cols);}}
  faces.sort((a,b)=>a.z-b.z);
  for(const f of faces){ctx.fillStyle=f.c;ctx.beginPath();ctx.moveTo(f.p[0][0],f.p[0][1]);for(let i=1;i<f.p.length;i++)ctx.lineTo(f.p[i][0],f.p[i][1]);ctx.closePath();ctx.fill();ctx.strokeStyle='rgba(0,0,0,.28)';ctx.stroke();}
}
function renderAll(){if(!S||!S.cur)return;readTheme();view=drawPCB(S.cur,1);drawSCH(S);if(spinOn){rot+=0.003;draw3D(S.cur,rot);dirty=true;}}
let rot=0.6,spinOn=true,spinT=null,dirty=true; // render-on-demand: static board costs zero frames
function loop(){if(dirty){dirty=false;renderAll();}requestAnimationFrame(loop);}
requestAnimationFrame(loop);
function markDirty(){dirty=true;schDirty=true;}
function spinBriefly(ms=4000){spinOn=true;dirty=true;clearTimeout(spinT);spinT=setTimeout(()=>spinOn=false,ms);}
$('t3d').addEventListener('pointerdown',()=>spinBriefly(8000));
// animation: tween parts from->to, reveal traces
function animate(frames,traces,done){
  cancelAnimationFrame(anim);
  const from=JSON.parse(JSON.stringify(S.cur.parts));let i=0;
  function step(){
    if(i>=frames.length){S.cur.traces=[];let n=0;dirty=true;
      (function grow(){n+=3;S.cur.traces=traces.slice(0,n);dirty=true;if(n<traces.length)anim=requestAnimationFrame(grow);else{S.cur.traces=traces;done&&done();}})();return;}
    const f=frames[i],to={};
    for(const r in f.pos){  // hidden parts snap, they do not glide
      const p=S.cur.parts[r];
      if(!p||!partShown(r,S.cur)){if(p){p.x=f.pos[r][0];p.y=f.pos[r][1];}}
      else to[r]=f.pos[r];
    }
    let k=0;const N=14;
    (function tw(){k++;const e=ease(k/N);
      for(const r in to){const a=from[r]||to[r],b=to[r];
        S.cur.parts[r]={...S.cur.parts[r],x:a[0]+(b[0]-a[0])*e,y:a[1]+(b[1]-a[1])*e};}
      dirty=true;
      ui.set({cost:`cost ${f.cost}`});
      if(k<N)anim=requestAnimationFrame(tw);else{for(const r in to)S.cur.parts[r]={...S.cur.parts[r],x:to[r][0],y:to[r][1]};i++;step();}})();}
  step();
}
function statMsg(txt,ok){ui.set({stat:txt||'',statOk:!!ok});}
async function withBusy(btn,label,fn){ // long actions: disable + say what is happening
  if(!btn||btn.disabled)return;
  const was=btn.textContent;btn.disabled=true;
  if(label){btn.textContent=label;statMsg(label,true);}
  try{return await fn();}
  finally{btn.disabled=false;btn.textContent=was;}}
let deb=null, pulseq=0; // monotonic: a slow build must not land on a newer board
function cancelPush(){clearTimeout(deb);deb=null;pulseq++;} // switching boards
$('ed').addEventListener('input',()=>{clearTimeout(deb);deb=setTimeout(push,400);});
async function push(){
  const text=$('ed').innerText, seq=++pulseq;
  const r=await api('/collab/push',{text,rev:collabRevNow(),src:SRCREL,thash:heldThash,
    placer:$('placer').value,router:$('router').value,
    fab:$('fab').value,silk:$('silk').value});
  if(seq!==pulseq)return;   // the editor moved on (or another board opened)
  if(r.stale){ // someone else edited first: adopt their text (undo keeps ours)
    cancelPush();
    if(r.text!==undefined)setEditor(r.text);
    if(r.rev!==undefined)setCollabRev(+r.rev);
    statMsg(r.error||'reloaded a collaborator edit (yours is in undo)',true);
    push();return;
  }
  if(r.error){statMsg(r.error);S=null;return;}
  statMsg('');applyState(r,false);
}

let heldThash='', heldTraces=[];
function applyState(r,live){
  if(r.thash){ // server skipped the trace list: keep the one we already have
    if(r.thash!==heldThash){heldTraces=r.traces||[];}
    r.traces=(r.traces&&r.traces.length)?r.traces:heldTraces;
    heldThash=r.thash;
  }
  if(r.rev!==undefined&&r.rev!==null)setCollabRev(+r.rev); // the room's rev rides every build
  S=r;S.cur=r;markDirty();spinBriefly(); // render live on the state itself (bw/bh/pours/fixed ride along)
  notePlacement(r);
  if(live&&r.frames&&r.frames.length)animate(r.frames,r.traces,()=>{drawDRC(r);});
  else{S.cur.traces=r.traces;ui.set({cost:`cost ${r.cost}`});drawDRC(r);}
  drawFeas(r);renderLayers(r);renderParts(r);
  if(document.activeElement!==$('ed'))setEditor(r.text);
}
function drawFeas(r){
  const f=r.feasible||{};
  const ks=Object.keys(f).sort();
  if(!ks.length)return;
  // state is a word: the current layer count is marked, every count reads ok/unroutable
  ui.set({feas:'routing feasibility per layer count: '+ks.map(L=>{
    const v=f[L],here=+L===r.layers;
    const cg=v.congestion?`, congestion ${v.congestion.crossings}/${v.congestion.pairs} crossings`:'' ;
    return `<span class="feasline ${v.ok?'fok':'fbad'}${here?' here':''}" title="${v.segs} segments, ${v.wirelength}mm of wire at ${L} layer${L==='1'?'':'s'}${cg}">${L}L ${v.ok?'routable':'unroutable'}${here?' (this board)':''}</span>`;}).join(' ')});
}
// --- layer and part visibility controls (view state, never a board edit) --
// visibility rows are components (views.js): this shapes them and handles the
// change events, which stay here because VIS, localStorage and the canvas do
function renderLayers(st){
  st=st||(S&&S.cur)||{layers:2,silk:'full'};
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
$('layers').addEventListener('change',e=>{
  const i=e.target.closest('input[data-key]');
  if(!i)return;
  const key=i.dataset.key;
  if(isCu(key)||SILKMARKS.includes(key))VIS.layers[key]=i.checked;
  else VIS.marks[key]=i.checked;
  visSave();
  const st=(S&&S.cur)||null;
  renderLayers(st);renderParts(st);markDirty();
});
const MAX_ROWS=400; // DOM rows, not parts: 5400 checkboxes brick the page
function renderParts(st){
  st=st||(S&&S.cur)||{parts:{}};
  const all=Object.keys(st.parts||{}).sort();
  // a filter searches every part (the list is capped, the search is not)
  const refs=(VIS.filter?all.filter(r=>partShown(r,st)):all).slice(0,MAX_ROWS);
  if(!VIS.filter&&all.length>MAX_ROWS){
    const shown=new Set(refs);
    edHl.forEach(r=>{if(!shown.has(r))refs.push(r);}); // keep the selection reachable
    refs.sort();
  }
  ui.set({partRows:refs.map(r=>{
    const p=st.parts[r]||{};
    return {ref:r,value:[p.value||'',p.fp||''].filter(Boolean).join(' '),
      on:VIS.parts[r]!==false,hidden:false,sel:edHl.has(r)};})});
  paintParts(refs.length,all.length);
}
function paintParts(shown){ // the rows are the source of truth for the note
  const st=(S&&S.cur)||null;
  const all=Object.keys((st&&st.parts)||{}).length;
  const n=shown===undefined?ui.state.partRows.length:shown;
  const rows=ui.state.partRows.map(r=>{
    const on=VIS.parts[r.ref]!==false;
    return {...r,on,hidden:!on,sel:edHl.has(r.ref)};});
  const hidden=rows.filter(r=>r.hidden).length;
  ui.set({partRows:rows,
    partNote:!all?'no parts'
      :(all>n?`${n-hidden}/${n} of ${all} (capped)`:`${n-hidden}/${n} shown`)});
}
$('partlist').addEventListener('change',e=>{
  const i=e.target.closest('input[data-ref]');
  if(!i)return;
  VIS.parts[i.dataset.ref]=i.checked;visSave();paintParts();markDirty();
});
$('partfilter').addEventListener('input',()=>{VIS.filter=$('partfilter').value;renderParts();markDirty();});
$('parthide').onclick=()=>setAllParts(false,S&&S.cur);
$('partshow').onclick=()=>setAllParts(true,S&&S.cur);
$('layersall').onclick=()=>{
  VIS.layers={};VIS.marks={};visSave();
  const st=(S&&S.cur)||null;renderLayers(st);markDirty();
};
function onlyBox(open){ // the two panels overlap: never show both
  [['layerbox','partbox'],['partbox','layerbox']].forEach(([a,b])=>{
    if($(a)===open&&$(a).open)$(b).open=false;
  });
}
$('layerbox').addEventListener('toggle',()=>onlyBox($('layerbox')));
$('partbox').addEventListener('toggle',()=>onlyBox($('partbox')));
document.addEventListener('keydown',e=>{
  if(e.target===$('ed')||e.target===$('ask'))return; // typing, not a shortcut
  if(e.key==='l'||e.key==='L'){$('layerbox').open=!$('layerbox').open;}
  else if(e.key==='p'||e.key==='P'){$('partbox').open=!$('partbox').open;}
});
// --- menubar: one menu open at a time, Esc closes, Alt+letter jumps -----
// Native <details> for the popups (no library, same as calc/health/quote
// before them): JS only enforces exclusivity + mnemonics. Shortcuts fire
// the same handlers the buttons always had — ids unchanged.
document.querySelectorAll('.menubar>details.menu').forEach(d=>{
  d.addEventListener('toggle',()=>{
    if(!d.open)return;
    document.querySelectorAll('.menubar>details.menu').forEach(o=>{if(o!==d)o.open=false;});
  });
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){document.querySelectorAll('.menubar>details.menu').forEach(d=>{d.open=false;});return;}
  if(!e.altKey||e.ctrlKey||e.metaKey)return;
  if(e.target===$('ed')||e.target===$('ask'))return;
  const k=e.key.toLowerCase();
  const map={b:'m-board',e:'m-edit',g:'m-engines',s:'m-sim',t:'m-tools'};
  if(map[k]){e.preventDefault();const d=$(map[k]);d.open=!d.open;}
});
// --- candidate gallery: N layouts, pick → nudge (drag=fix) → re-run ---
// The DRC strip and the tidy list are components now (views.js): shape the
// report into plain lines here, where the report is, and let preact paint it.
function drawDRC(r){
  const items=[]; // [{cls, text}] — same lines, same order, same classes
  const li=r.lint||{errors:[],warnings:[]}; // static source lint, no place/route
  if(li.errors.length)items.push(...li.errors.map(e=>({cls:'err',text:`✗ lint: ${e}`})));
  if(r.errors.length)items.push(...r.errors.map(e=>({cls:'err',text:`✗ ${e}`})));
  else if(!li.errors.length)items.push({cls:'ok',text:'✓ DRC clean ('+r.fab+')'});
  items.push(...r.warnings.slice(0,5).map(w=>({cls:'warn',text:`~ ${w}`})));
  items.push(...(li.warnings||[]).slice(0,3).map(w=>({cls:'warn',text:`~ lint: ${w}`})));
  const rec=(r.recommend&&r.recommend.items)||[];
  items.push(...rec.slice(0,6).map(it=>({cls:'warn',text:`+ ${it.kind}: ${it.msg}`})));
  if(r.sim&&Object.keys(r.sim).length)items.push({cls:'ok',text:'⚡ '+Object.entries(r.sim).map(([n,v])=>`${n}=${v}V`).join(' ')});
  if(r.sim_problems&&r.sim_problems.length)items.push(...r.sim_problems.map(p=>({cls:'err',text:`⚡✗ ${p}`})));
  if(r.tran&&Object.keys(r.tran).length)items.push({cls:'ok',text:'⚡tran '+Object.entries(r.tran).map(([n,w])=>`${n} ${w[w.length-1].toFixed(2)}V [${Math.min(...w).toFixed(2)},${Math.max(...w).toFixed(2)}] (${w.length}pts)`).join(' · ')});
  if(r.ac&&Object.keys(r.ac).length){const db=v=>20*Math.log10(Math.max(1e-12,Math.abs(v)));
    items.push({cls:'ok',text:'⚡ac '+Object.entries(r.ac).map(([n,w])=>`${n} ${db(w[w.length-1]).toFixed(1)}dB@${r.f1||''}Hz [${db(Math.max(...w.map(Math.abs))).toFixed(1)}dB pk] (${w.length}pts)`).join(' · ')});}
  ui.set({drc:items});
  drawTidy(r);
}
function tidyVal(v){ // {text, dim}: data, not markup
  if(v===null||v===undefined)return {text:'n/a',dim:true};
  if(typeof v==='number')return {text:Number.isInteger(v)?String(v):v.toFixed(3)};
  if(typeof v==='object'){const ks=Object.keys(v);
    if(v.total!==undefined&&v.per_net!==undefined)return {text:`total=${v.total}`}; // nested detail lives in STATUS.md
    return {text:ks.map(a=>`${a}=${v[a]}`).join(', ')};}
  return {text:String(v)};
}
function drawTidy(r){
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
function notePlacement(r){
  const n=Object.keys(r.parts||{}).length;
  const skip=(r.skipped||[]).join(' + ');
  // A dense board usually carries no layout in its file (only pinned parts
  // have positions), so say what it needs and where to get it rather than
  // letting the canvas look broken. `solve` works but is minutes at this size.
  if(r.dense)statMsg(`${n} parts, loaded as saved`
    +(skip?` — ${skip} skipped at this size (run the CLI for those)`:'')
    +'. "solve" places it here, but takes minutes.',true);
  else if(r.placed===false)statMsg('loaded as saved — solve to re-place',true);
}
function setEditor(t){$('ed').innerText=t;}
// import: file picker in the project panel → base64 to /fs/import →
// import_fp/import_sym by extension (key sniffed server-side). Same
// FileReader pattern as the x-ray upload.
if($('importfile'))$('importfile').onchange=()=>{const f=$('importfile').files[0];if(!f)return;
  const rd=new FileReader();rd.onload=async()=>{
    const data=String(rd.result).split(',',1)[1]||'';
    ui.set({importStat:'importing '+f.name+'…'});
    const r=await api('/fs/import',{name:f.name,data});
    ui.set({importStat:r.error||r.note||('imported '+f.name)});
    if(!r.error){loadTree(DIR);if(r.text){setEditor(r.text);push();}}};
  rd.readAsDataURL(f);$('importfile').value='';};
function unpinRefs(gone){ // drop fix lines for refs; true when something left
  const lines=$('ed').innerText.split('\n')
    .filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  if(lines.length===$('ed').innerText.split('\n').length)return false;
  $('ed').innerText=lines.join('\n');push();return true;}
function rotRefs(refs){ // rotate 90°: bump rot= on the part line (add or +90)
  const lines=$('ed').innerText.split('\n');
  const hit=new Set();
  const out=lines.map(l=>{const m=l.match(/^part\s+(\S+)\s+(\S+)(.*)$/);
    if(!m||!refs.has(m[1]))return l;hit.add(m[1]);
    const cur=(m[3].match(/rot=(\d+)/)||[])[1];
    const nx=cur?((+cur+90)%360):90;
    const rest=m[3].replace(/\s*rot=\d+/,'');
    return `part ${m[1]} ${m[2]} rot=${nx}${rest}`;});
  const miss=[...refs].filter(r=>!hit.has(r));
  if(miss.length){statMsg('no part line for '+miss.join(', '));return;}
  $('ed').innerText=out.join('\n');push();}
// board-mm preview offset while shift-dragging a trace. Declared here, not
// inside the drag IIFE below: drawPCB() (outer scope) reads it on every frame,
// so an IIFE-local binding made the first frame with traces throw
// "segDrag is not defined" and killed the render loop before it re-armed.
let segDrag=null; // {seg, dx, dy}
// drag parts on pcb
(()=>{const c=$('pcb');let drag=null,dragGroup=null;
function hit(mx,my){for(const r in S.cur.parts){
  if(!S.cur.parts[r]||!partShown(r,S.cur))continue; // hidden parts are not targets
  const p=S.cur.parts[r];
  const x=view.ox+p.x*view.s,y=view.oy+(S.bh-p.y)*view.s;
  if(Math.abs(mx-x)<p.w*view.s/2+4&&Math.abs(my-y)<p.h*view.s/2+4)return r;}return null;}
function hitTrace(mx,my){ // nearest segment within 6px → its net
  const h=hitSeg(mx,my);
  return h?h.seg.net:null;}
function hitSeg(mx,my){ // nearest segment object (for drag preview)
  if(!S||!S.cur||!S.cur.traces)return null;
  let best=null,bd=36;
  for(const g of S.cur.traces){
    const x1=view.ox+g.x1*view.s,y1=view.oy+(S.bh-g.y1)*view.s;
    const x2=view.ox+g.x2*view.s,y2=view.oy+(S.bh-g.y2)*view.s;
    const dx=x2-x1,dy=y2-y1,L2=dx*dx+dy*dy;
    const t=L2?Math.max(0,Math.min(1,((mx-x1)*dx+(my-y1)*dy)/L2)):0;
    const dd=(mx-x1-t*dx)**2+(my-y1-t*dy)**2;
    if(dd<bd){bd=dd;best=g;}}
  return best?{seg:best}:null;}
c.addEventListener('mousedown',async e=>{if(!S)return;const R=c.getBoundingClientRect();
  if(e.altKey){ // alt-click a trace: rip + re-route that net
    const net=hitTrace(e.clientX-R.left,e.clientY-R.top);
    if(net)await withBusy(c,`rerouting ${net}…`,async()=>{
      const r=await api('/reroute',{net});
      if(r.error){statMsg(r.error);return;}
      statMsg(r.retried?`${net} re-routed`:`${net} still blocked`,r.retried);applyState(r,true);});
    return;}
  if(e.shiftKey){ // shift-drag a trace: preview the shove, release re-routes
    const h=hitSeg(e.clientX-R.left,e.clientY-R.top);
    if(h){segDrag={seg:h.seg,dx:0,dy:0,
      x0:e.clientX-R.left,y0:e.clientY-R.top};dirty=true;}
    return;}
  drag=hit(e.clientX-R.left,e.clientY-R.top);
  // rigid group: an instanced part drags its whole owner-group (offsets kept)
  dragGroup=null;
  if(drag){const o=S.cur.parts[drag].owner;
    if(o)dragGroup=Object.keys(S.cur.parts).filter(r=>S.cur.parts[r].owner===o);}});
c.addEventListener('mousemove',e=>{const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  if(segDrag){segDrag.dx=Math.round(((mx-segDrag.x0)/view.s)*10)/10;
    segDrag.dy=Math.round(((segDrag.y0-my)/view.s)*10)/10;dirty=true;}
  else if(drag){const p=S.cur.parts[drag];
    const nx=Math.round(((mx-view.ox)/view.s)*10)/10,ny=Math.round((S.bh-(my-view.oy)/view.s)*10)/10;
    const dx=nx-p.x,dy=ny-p.y;p.x=nx;p.y=ny;
    if(dragGroup)for(const r of dragGroup){if(r===drag)continue;
      const q=S.cur.parts[r];q.x=Math.round((q.x+dx)*10)/10;q.y=Math.round((q.y+dy)*10)/10;}
    dirty=true;}
  else if(S)S.cur.hover=hit(mx,my);});
c.addEventListener('mouseup',async()=>{
  if(segDrag){const net=segDrag.seg.net;segDrag=null;dirty=true;
    await withBusy(c,`rerouting ${net}…`,async()=>{
      const r=await api('/reroute',{net});
      if(r.error){statMsg(r.error);return;}
      statMsg(r.retried?`${net} re-routed`:`${net} still blocked`,r.retried);applyState(r,true);});
    return;}
  if(!drag)return;const moved=dragGroup||[drag];dragGroup=null;const r=drag;drag=null;
  const gone=new Set(moved);
  const lines=$('ed').innerText.split('\n').filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  // drop fix lines right after board/use block (group order kept)
  let idx=lines.findIndex(l=>/^(part|net|fix|keep|route|trace|power|silk)\b/.test(l));if(idx<0)idx=lines.length;
  moved.forEach((rr,i)=>{const p=S.cur.parts[rr];lines.splice(idx+i,0,`fix ${rr} at ${p.x} ${p.y}`);});
  // The pin is written either way (it is the truth about where the part is),
  // but re-placing thousands of parts is minutes: ask before hanging the page.
  $('ed').innerText=lines.join('\n');
  if(S&&S.cur&&S.cur.dense&&!confirm(
      `Re-place ${Object.keys(S.cur.parts).length} parts around the pin?\n\n`
      +'This runs the placer over a dense board and can take minutes.\n'
      +'Cancel keeps the pin and re-loads from the file instead.')){
    loadBoard();return;}
  push();});
c.addEventListener('dblclick',()=>{ // unpin: remove fix (whole group if instanced)
  if(!S||!S.cur||!S.cur.hover)return;
  const r=S.cur.hover,o=S.cur.parts[r].owner;
  unpinRefs(new Set(o?Object.keys(S.cur.parts).filter(k=>S.cur.parts[k].owner===o):[r]));});
c.addEventListener('contextmenu',e=>{ // right-click: rotate here, unpin there
  e.preventDefault();if(!S||!S.cur)return;const R=c.getBoundingClientRect();
  const r=hit(e.clientX-R.left,e.clientY-R.top);
  if(!r)return; // empty board: browser menu stays suppressed, nothing to do
  const o=S.cur.parts[r].owner;
  rotRefs(new Set(o?Object.keys(S.cur.parts).filter(k=>S.cur.parts[k].owner===o):[r]));});
})();
$('solve').onclick=async()=>{
  await withBusy($('solve'),'solving…',async()=>{
    const r=await api('/solve',{placer:$('placer').value,router:$('router').value,full:true});
    if(r.error){statMsg(r.error);return;}
    statMsg('');applyState(r,true);
  });
};
$('dice').onclick=genCands;
$('fab_dl').onclick=async()=>{
  await withBusy($('fab_dl'),'building zip…',async()=>{
    const r=await api('/export',{});
    if(r.error){statMsg(r.error);return;}
    const a=document.createElement('a');
    a.href='data:application/zip;base64,'+r.zip;a.download=r.name;a.click();
    statMsg(`${r.name} (${(r.bytes/1024).toFixed(0)}KB)`,true);
  });
};
$('dl').onclick=async()=>{ // cycle svg → sch → png → xray (shift-click backwards)
  const keys=['svg','sch','png','xray'];
  dlIdx=(dlIdx+((window.event&&window.event.shiftKey)?-1:1)+keys.length)%keys.length;
  const key=keys[dlIdx];
  ui.set({dlLabel:`download ${key}`}); // keep a word label (glyph alone is not a label)
  await withBusy($('dl'),`download ${key}…`,async()=>{
    const r=await api('/render',{key});
    if(r.error){statMsg(r.error);return;}
    const a=document.createElement('a');
    a.href=r.bin?`data:application/octet-stream;base64,${r.data}`
      :`data:image/svg+xml,${encodeURIComponent(r.data)}`;
    if(key==='png'&&!r.bin)a.href=`data:image/png;base64,${r.data}`;
    a.download=r.name;a.click();statMsg(r.name,true);
  });
};
let dlIdx=0;
let simWhat='dc';
$('simbtn').onclick=async()=>{ // dc → tran → ac cycle on shift-click
  if(window.event&&window.event.shiftKey)
    simWhat=simWhat==='dc'?'tran':simWhat==='tran'?'ac':'dc';
  ui.set({simLabel:`sim ${simWhat}`,
    simTitle:`simulate the current board (shift-click: ${simWhat==='dc'?'tran':simWhat==='tran'?'ac':'dc'})`});
  await withBusy($('simbtn'),`sim ${simWhat}…`,async()=>{
    const r=await api('/simulate',{what:simWhat});
    if(r.error){statMsg(r.error);return;}
    if(r.sim&&Object.keys(r.sim).length)S.sim=r.sim;
    if(r.tran&&Object.keys(r.tran).length)S.tran=r.tran;
    if(r.ac&&Object.keys(r.ac).length)S.ac=r.ac;
    statMsg('',true);drawDRC(S);
  });
};
// Ω calculators: same math as ocdcircuit/calc.py, instant, no round-trip
function calcLive(){
  const A=parseFloat($('ca').value)||0,dT=parseFloat($('cdt').value)||10;
  const area=Math.pow(A/(0.048*Math.pow(dT,0.44)),1/0.725); // IPC-2221 ext 1oz
  const c=`${(area/1.378*0.0254).toFixed(2)}mm ext, via ${(A/(3*Math.sqrt(dT/10))).toFixed(2)}mm drill`;
  const pv=v=>{const m=String(v).match(/^([\d.]+)(k|M)?$/i);return m?parseFloat(m[1])*(m[2]?({k:1e3,M:1e6})[m[2].toLowerCase()]||1:1):NaN;};
  const V=pv($('dv').value),Rt=pv($('drt').value),Rb=pv($('drb').value);
  const d=(V>=0&&Rt>0&&Rb>0)?`Vout ${(V*Rb/(Rt+Rb)).toFixed(2)}V`:'';
  const zw=parseFloat($('zw').value)||0,zh=parseFloat($('zh').value)||0;
  let z='';
  if(zw>0&&zh>0){const u=zw/zh,er=4.4;
    const ere=(er+1)/2+(er-1)/2/Math.sqrt(1+12/u);
    const z0=u<=1?60/Math.sqrt(ere)*Math.log(8/u+u/4)
      :120*Math.PI/(Math.sqrt(ere)*(u+1.393+0.667*Math.log(u+1.444)));
    z=`Z0 ~${z0.toFixed(1)}Ω (microstrip FR4, estimate)`;}
  ui.set({calc:{c,d,z}});
}
['ca','cdt','dv','drt','drb','zw','zh'].forEach(id=>$(id).addEventListener('input',calcLive));
if($('qgo'))$('qgo').onclick=async()=>{ // fab price comparison for the open board
  await withBusy($('qgo'),'comparing…',async()=>{
    const q=Math.max(1,parseInt($('qqty').value)||5);
    const r=await api('/quote',{qty:q,no_parts:$('qbare').checked});
    if(r.error){ui.set({quoteRows:[],quoteNote:[{cls:'err',text:r.error}]});
      statMsg(r.error);return;}
    const _asm=(r.rows||[]).find(x=>x.asm)||{};
    const _alt=_asm.via_alt||[],_unp=_asm.unpriced||[],_low=_asm.low_stock||[],_rsk=_asm.risky||[];
    ui.set({
      quoteRows:(r.rows||[]).map(x=>({fab:x.fab,logo:x.logo||'',bare:x.bare_total,
        asm:x.asm_total||'',per:x.asm_per_board||''})),
      quoteNote:[
        ...(_alt.length?[{cls:'dim',text:`via substitute: ${_alt.join(', ')}`}]:[]),
        ...(_unp.length?[{cls:'warn',text:`unpriced: ${_unp.join(', ')}`}]:[]),
        ...(_low.length?[{cls:'warn',text:`low stock: ${_low.join(', ')}`}]:[]),
        ...(_rsk.length?[{cls:'warn',text:`lifecycle risk: ${_rsk.join(', ')}`}]:[]),
        {cls:'dim',text:`${r.stamp} estimates — re-verify before ordering`}]});
    statMsg('');
  });
};
$('doc').addEventListener('toggle',async()=>{ // lazy: check on first open
  if(!$('doc').open||$('docout').dataset.done)return;
  const r=await api('/doctor',{});
  if(r.error){ui.set({doc:[{cls:'err',text:r.error}]});return;}
  ui.set({doc:[{cls:r.ok?'ok':'warn',
      text:r.ok?'✓ all systems':'degraded: features fall back, nothing crashes'},
    ...r.checks.map(c=>({cls:c.ok?'ok':'err',text:`${c.ok?'✓':'✗'} ${c.name}`,
      detail:c.detail||''}))]});
  $('docout').dataset.done='1';
});
// undo/redo: server keeps text history (git-style log); undo restores + rebuilds
// x-ray: reference download + fab-scan upload vs the design (score + boxes)
let xrayRaw='';
if($('xrayfile'))$('xrayfile').onchange=()=>{const f=$('xrayfile').files[0];if(!f)return;
  const rd=new FileReader();rd.onload=()=>{xrayRaw=String(rd.result).split(',',1)[1]||'';
    ui.set({xrayStat:`${f.name} ready — compare to check it`});};
  rd.readAsDataURL(f);};
if($('xraysvg'))$('xraysvg').onclick=async()=>{
  const r=await api('/render',{key:'xray'});if(r.error){ui.set({xrayStat:r.error});return;}
  const a=document.createElement('a');
  a.href=`data:image/svg+xml,${encodeURIComponent(r.data)}`;
  a.download=r.name;a.click();ui.set({xrayStat:r.name});};
if($('xraygo'))$('xraygo').onclick=async()=>{
  if(!xrayRaw){ui.set({xrayStat:'pick a fab PNG first'});return;}
  const pv=(id,fb)=>{const v=parseFloat($(id).value);return Number.isFinite(v)?v:fb;};
  const r=await api('/xray',{png:xrayRaw,dx:pv('xraydx',0),dy:pv('xraydy',0),
    scale:pv('xraysc',1),thr:Math.round(pv('xraythr',100))});
  if(r.error){ui.set({xrayStat:r.error,xrayDivs:[]});return;}
  ui.set({xrayStat:`score ${r.score} — missing ${r.missing}px extra ${r.extra}px`,
    xrayDivs:(r.divs||[]).map(d=>({kind:d.kind,x:d.x,y:d.y,w:d.w,h:d.h}))});
  if(r.overlay){const w=open('','_blank');if(w)w.document.write(r.overlay);}};
async function hist(op){
  const r=await api(op,{});
  if(r.error){statMsg(r.error);return;}
  statMsg('');setEditor(r.text);applyState(r,false);
}
$('undo').onclick=()=>hist('/undo');
$('redo').onclick=()=>hist('/redo');
$('diffprev').onclick=async()=>{
  const r=await api('/diff_prev',{});
  statMsg(r.error||r.diff, !r.error);
};
$('stamp').onclick=()=>{ // repeat-layout: stamp another copy of hovered instance
  if(!S||!S.cur||!S.cur.hover){statMsg('hover an instanced part, then stamp');return;}
  const o=S.cur.parts[S.cur.hover].owner;
  if(!o){statMsg('that part is not in an instance');return;}
  const pre=o.replace(/_$/,'');
  const lines=$('ed').innerText.split('\n');
  const inst=lines.map((l,i)=>({m:l.match(/^instance\s+(\S+)\s+as\s+(\S+?)(?:\s+join\s+(.*))?$/),i}))
    .filter(x=>x.m);
  const src=inst.find(x=>x.m[2]===pre);
  if(!src){statMsg(`no instance line for ${pre}`);return;}
  const stem=pre.replace(/\d+$/,'')||pre;
  const nums=inst.map(x=>{const m=x.m[2].match(/(\d+)$/);return m?parseInt(m[1]):0;});
  const next=stem+(Math.max(0,...nums)+1);
  const join=src.m[3]?` join ${src.m[3]}`:'';
  const at=inst[inst.length-1].i;
  lines.splice(at+1,0,`instance ${src.m[1]} as ${next}${join}`);
  $('ed').innerText=lines.join('\n');push();
};
$('ed').addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('solve').click();}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();commitBoard();}
  else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey){e.preventDefault();hist('/undo');}
  else if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='y'||(e.key.toLowerCase()==='z'&&e.shiftKey))){e.preventDefault();hist('/redo');}
});
// --- project files: browse, open, and say which file is the board -------
let TREE=[],SRCREL='',DIR='.',ROOTREL='.';
// rows are components (views.js TreePanel): the click is delegated here, so
// the behaviour stays in this module and the component only describes a row
$('tree').addEventListener('click',e=>{
  const b=e.target.closest('button.trow');if(!b||!b.dataset.path)return;
  const path=b.dataset.path;
  if(b.dataset.kind==='dir')loadTree(path);
  else if((b.dataset.name||'').endsWith('.ocd'))openFile(path);
  else previewFile(path);
});
function treeNote(){ // one string, three call sites used to write it by hand
  ui.set({treeNote:`${DIR==='.'?ROOTREL:DIR} · `
    +`${TREE.filter(e=>e.kind==='file').length} files`});
}
function renderTree(){
  // one level at a time: this directory, then a way back up while inside root
  const up=DIR!=='.'
    ?{name:'.. ('+((DIR.includes('/')?DIR.replace(/\/[^/]*$/,''):'.')==='.'
        ?ROOTREL:DIR.replace(/\/[^/]*$/,''))+')',
      path:DIR.includes('/')?DIR.replace(/\/[^/]*$/,''):'.',kind:'dir'}
    :null;
  ui.set({treeUp:up,
    treeRows:TREE.map(e=>({name:e.name,path:e.path,kind:e.kind,
      bytes:e.bytes,active:e.path===SRCREL}))});
}
async function loadTree(dir){
  const f=await fetch('/fs?dir='+encodeURIComponent(dir||'.')).then(x=>x.json());
  if(f.error){statMsg(f.error);return;}
  DIR=f.dir||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';setVcs(f.vcs||{});
  TREE=f.tree||[];
  treeNote();
  renderTree();
}
async function previewFile(path){
  const r=await api('/fs/read',{path});
  if(r.error){statMsg(r.error);return;}
  msg('bot','preview of '+path+' (read-only here; open a .ocd in the editor to edit it).\n'
      +r.text.split('\n').slice(0,40).join('\n'));
}
async function openFile(path){
  cancelPush();  // a queued rebuild of the old board must not follow us here
  const r=await api('/fs/open',{path});
  if(r.error){statMsg(r.error);return;}
  statMsg('');ui.set({msgs:[],followups:[]});
  heldThash='';heldTraces=[];  // a different board: its traces are not ours
  setQueue([]);  // the server dropped the old board's proposals with it
  collabReset(r.rev!==undefined?+r.rev:-1); // re-home the room to the new board
  applyState(r,false);
  collabStart(); // join the new board's room: sync + fresh SSE stream
  toast('opened '+path);
  const f=await fetch('/fs').then(x=>x.json());
  if(!f.error){DIR=f.base||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';TREE=f.tree||[];
    ui.set({srcNote:`${SRCREL} · ${f.base||'.'} · saved on every good build`});
    ui.set({chatWhere:SRCREL});
    treeNote();
    renderTree();}
  loadVCS();
}
// --- agent chat --------------------------------------------------------
let msgSeq=0;
function pushMsg(entry){
  entry.id=++msgSeq;
  ui.set({msgs:[...ui.state.msgs,entry]});
  return entry;
}
function msg(who,text){ // one conversation, one ordered list (views.js renders it)
  const entry=pushMsg({kind:'msg',who:who==='you'?'me':who,
    name:who==='bot'?'flux':who,text:text});
  return {remove(){ui.set({msgs:ui.state.msgs.filter(m=>m.id!==entry.id)});}};
}
function diffLines(text){ // data, not DOM: class per diff line
  return String(text).split('\n').map(ln=>({cls:/^@@|^(\+\+\+|---)/.test(ln)?'dl-at'
    :ln.startsWith('+')?'dl-add':ln.startsWith('-')?'dl-del':'',text:ln}));
}
function propose(pr,onQueue){
  pushMsg({kind:'prop',prop:pr.id,path:pr.path,lines:diffLines(pr.diff||''),busy:false});
}
function propSet(id,patch){
  ui.set({msgs:ui.state.msgs.map(m=>m.kind==='prop'&&m.prop===id?{...m,...patch}:m)});
}
function propDrop(id){
  ui.set({msgs:ui.state.msgs.filter(m=>!(m.kind==='prop'&&m.prop===id))});
}
$('msgs').addEventListener('click',async e=>{ // apply/reject, delegated
  const b=e.target.closest('button[data-act]');
  if(!b)return;
  const id=b.dataset.prop,act=b.dataset.act;
  if(ui.state.msgs.some(m=>m.kind==='prop'&&m.prop===id&&m.busy))return;
  propSet(id,{busy:true});
  if(act==='apply'){
    const r=await api('/chat/apply',{id});
    if(r.error){propSet(id,{busy:false});msg('err',r.error);return;}
    if(r.state)applyState(r.state,false);
    else loadVCS();
    propDrop(id);
    msg('bot','applied '+b.dataset.path+(r.state?'':' (not the open board)')
        +(r.note?' — '+r.note:''));
    setQueue(r.proposals||[]);
    loadVCS();
    return;
  }
  const r=await api('/chat/reject',{id});
  propDrop(id);
  msg('bot','rejected '+b.dataset.path+' — nothing was written');
  setQueue(r.proposals||[]);
});
// a turn may propose several files: every card lives in one queue, and each
// apply/reject removes its own card without disturbing the others
function setQueue(list){
  ui.set({msgs:ui.state.msgs.filter(m=>m.kind!=='prop')});
  (list||[]).forEach(p=>propose(p,setQueue));
}
async function chat(text,auto){
  const go=$('composer').querySelector('button[type=submit]');
  if(go&&go.disabled)return; // one turn at a time — a double Enter doubles LLM spend
  if(go)go.disabled=true;
  msg('you',text);
  const wait=msg('bot','thinking…');
  let r;
  try{r=await api('/chat',{text,auto:!!auto});}
  finally{if(go)go.disabled=false;wait.remove();}
  if(r.error){msg('err',r.error);setQueue(r.proposals);return;}
  const tn=(r.proposals||[]).filter(p=>/\.ocd$/.test(p.path||''));
  if(tn.length){ // plan checklist: the turn's file edits as checkable steps
    pushMsg({kind:'plan',open:true,
      title:`plan: ${tn.length} file${tn.length===1?'':'s'} proposed`,
      steps:tn.map(p=>'◻ '+p.path)});
  }
  if(r.log&&r.log.length){ // thought trace: what the agent actually did
    pushMsg({kind:'log',
      title:`thought for ${r.log.length} step${r.log.length===1?'':'s'}`,
      lines:r.log.map(ln=>({cls:/failed|error/i.test(ln)?'bad':'ok',text:ln}))});
  }
  msg('bot',r.reply||'(no reply)');
  followups(r.reply||'');
  if(r.note)msg('bot',r.note);
  if(r.proposals&&r.proposals.length)setQueue(r.proposals);
  if(r.state)applyState(r.state,false);
  if(r.applied)loadVCS();
}
// follow-up chips: Flux's "Route and verify / Add thermal copper" row.
// Mined from the reply's own next-steps, else the three generic moves.
function followups(reply){ // chips ride the store; the click is delegated below
  const picks=[];
  reply.split('\n').forEach(ln=>{
    const m=ln.match(/^(?:\d+[.)]\s*|[-*]\s+)(.{12,80})$/);
    if(m&&/rout|check|valid|verif|test|place|thermal|copper|drc|fix/i.test(m[1])
       &&picks.length<3)picks.push(m[1].trim());});
  if(!picks.length)picks.push('Route and verify','Check DRC','Explain this board');
  ui.set({followups:picks.slice(0,3).map(q=>({label:q.length>34?q.slice(0,33)+'…':q,title:q}))});
}
$('chat').addEventListener('click',e=>{ // follow-up chip
  const b=e.target.closest('#followups button');
  if(!b)return;
  chat(b.title,$('chatauto').checked);
});
function toast(t){
  ui.set({toast:t});                 // views.js renders the one toast node
  clearTimeout(toast._t);
  toast._t=setTimeout(()=>ui.set({toast:''}),4200);
}
async function loadBoard(){ // parse + route what is on disk; never re-place
  const r=await api('/load',{});
  if(r.error){statMsg(r.error);return;}
  setEditor(r.text);applyState(r,false);
}
async function boot(){
  // whoami: a stale cookie lands here sessionless — bounce to the gate.
  try{const me=await api('/auth/me',{});
    if(me&&me.user){ui.set({me:me.user});}
    else{location.href='/';return;}}catch(e){location.href='/';return;}
  // /load parses the file and routes what is there. It does not re-place:
  // a 5k-part board takes minutes to place, and the file already says where
  // the parts go. `solve` is the explicit ask for a fresh placement.
  // /load and /fs are independent — fetch in parallel (was a waterfall).
  const [r,f]=await Promise.all([api('/load',{}),fetch('/fs').then(x=>x.json())]);
  ui.set({placers:r.placers,routers:r.routers,silks:r.silks,
    fabOpts:r.fabs,silkSel:r.silk});   // views.js Engines renders the options
  setEditor(r.text);applyState(r,false);
  if(r.rev!==undefined)setCollabRev(+r.rev); // the room's rev from the first load
  collabStart(); // realtime: SSE fan-out + presence from here on
  if(f.error){ui.set({treeNote:f.error});return;}
  DIR=f.base||'.';ROOTREL=f.root||'.';TREE=f.tree||[];SRCREL=f.src||'';setVcs(f.vcs||{});
  ui.set({srcNote:`${SRCREL} · ${f.base||'.'} · saved on every good build`});
  ui.set({chatWhere:SRCREL});
  treeNote();
  renderTree();
  loadVCS();
  $('logoutbtn').onclick=async()=>{await api('/auth/logout',{});location.href='/';};
  try{if(JSON.parse(localStorage.getItem('ocd-studio-dark')||'0'))setDark(true);}catch(e){}
  $('themebtn').onclick=()=>setDark(!document.body.classList.contains('dark'));
  document.querySelectorAll('#viewtabs button').forEach(b=>b.onclick=()=>setView(b.dataset.v));
  try{const v=localStorage.getItem('ocd-studio-view');if(v&&v!=='all')setView(v);}catch(e){}
  // cockpit ↔ tabs: All (or double-click any tab) returns to every panel
  document.querySelectorAll('#viewtabs button').forEach(b=>b.ondblclick=()=>showCockpit());
  // tablist: arrow keys move selection (APG pattern); Home/End jump ends
  $('viewtabs').addEventListener('keydown',e=>{
    const tabs=[...$('viewtabs').querySelectorAll('[role=tab]')];
    const i=tabs.indexOf(document.activeElement);
    if(i<0)return;
    let n=-1;
    if(e.key==='ArrowRight'||e.key==='ArrowDown')n=(i+1)%tabs.length;
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp')n=(i-1+tabs.length)%tabs.length;
    else if(e.key==='Home')n=0;
    else if(e.key==='End')n=tabs.length-1;
    else if(e.key==='Enter'||e.key===' '){e.preventDefault();setView(tabs[i].dataset.v);return;}
    if(n<0)return;
    e.preventDefault();
    if(document.body.classList.contains('tabs')){setView(tabs[n].dataset.v);}
    else{tabs.forEach((t,j)=>t.tabIndex=j===n?0:-1);}
    tabs[n].focus();
  });
}
function setDark(on){
  document.body.classList.toggle('dark',on);
  ui.set({dark:!!on});
  try{localStorage.setItem('ocd-studio-dark',on?'1':'0');}catch(e){}
  markDirty();
}
function showCockpit(){ // every panel at once — the default, and a real control (All)
  document.body.classList.remove('tabs');
  delete document.body.dataset.view;
  ui.set({tabs:false,view:'all'});   // tab classes are rendered from this
  try{localStorage.setItem('ocd-studio-view','all');}catch(e){}
  renderAll();markDirty();
}
function setView(v){
  if(v==='all'){showCockpit();return;}
  let tabs=document.body.classList.contains('tabs');
  if(!tabs){ // first pick enables tab mode (cockpit is the default)
    document.body.classList.add('tabs');tabs=true;}
  document.body.dataset.view=v;
  ui.set({tabs:true,view:v});
  try{localStorage.setItem('ocd-studio-view',v);}catch(e){}
  renderAll();markDirty();
}
// selecting text in the editor highlights every ref it names, on PCB and SCH
function edHighlight(){
  if(!S||!S.cur)return;
  const sel=window.getSelection();
  const txt=(sel&&!sel.isCollapsed&&sel.anchorNode&&$('ed').contains(sel.anchorNode))?String(sel):'';
  const refs=new Set(),pins=new Set();
  txt.split(/[^\w.]+/).forEach(t=>{const r=t.split('.')[0];
    if(!S.cur.parts[r])return;
    refs.add(r);
    if(t!==r)pins.add(t);}); // "U1.7" also rings that pin in the schematic
  if(refs.size===edHl.size&&pins.size===edPin.size
     &&[...refs].every(r=>edHl.has(r))&&[...pins].every(p=>edPin.has(p)))return;
  edHl=refs;edPin=pins;markDirty();
}
document.addEventListener('selectionchange',edHighlight);
$('placer').onchange=$('router').onchange=$('fab').onchange=$('silk').onchange=push;
$('chatbtn').onclick=()=>{
  document.body.classList.toggle('chatty');
  const on=document.body.classList.contains('chatty');
  ui.set({chat:on});
  if(on)$('ask').focus();
};
// the collab stream needs live reads of the board, the view and the
// selection: hand it the runtime instead of importing the cycle back
initCollab({markDirty,cancelPush,setEditor,push,toast,
  view:()=>view,board:()=>S,edHl});
initKb();   // knowledgebase panel: fetches, prefs and its own polling
initVcs({srcRel:()=>SRCREL,statMsg,toast});
initGallery({board:()=>S,palette:()=>C,statMsg,applyState,withBusy,drawFeas});
initScan({push,setEditor});
(async()=>{await boot();})();
if(location.search.includes('perf')){
setTimeout(()=>{
  const st=S&&S.cur||{};
  const P=CanvasRenderingContext2D.prototype;
  const cnt={stroke:0,fill:0,fillRect:0,beginPath:0,fillText:0};
  const orig={};
  Object.keys(cnt).forEach(k=>{orig[k]=P[k];
    P[k]=function(){cnt[k]++;return orig[k].apply(this,arguments);};});
  drawPCB(st,1);
  Object.keys(cnt).forEach(k=>{P[k]=orig[k];});
  const parts=Object.keys(st.parts||{}).length;
  document.title='DRAW parts='+parts+' traces='+(st.traces||[]).length
    +' strokes='+cnt.stroke+' begins='+cnt.beginPath+' fills='+cnt.fill+' texts='+cnt.fillText;
},2500);}


// file-watch: poll SRC hash; an external edit banners with one-click
// reload (never auto: a keystroke debounce may be in flight, and
// auto-reload would clobber it — the user picks the moment).
let lastHash=null;
async function watch(){try{
  // /poll is a GET route: api() POSTs, which answered "unknown path /poll"
  // and left this strip unable to fire. Same-origin fetch carries the cookie.
  const p=await fetch('/poll').then(r=>r.json());
  if(lastHash===null){lastHash=p.hash;return;}
  if(p.hash===lastHash||ui.state.extBanner)return;
  if(p.clean)return;  // our own save (or untouched) — nothing external
  lastHash=p.hash;
  ui.set({extBanner:true});   // views.js renders the strip
}catch(e){}}
// one delegated listener: the strip asks, this reloads
$('app').addEventListener('click',async e=>{
  if(!e.target.closest('#extbanner'))return;
  const r=await api('/reload',{});
  setEditor(r.text);applyState(r,false);
  ui.set({extBanner:false});lastHash=null;
});
setInterval(watch,2000);
