// Workshop behaviour, moved verbatim out of apps/studio.py.
// Canvas render loops, drag/drop and the collab SSE stream stay imperative
// on purpose: they drive the DOM directly, not the VDOM.
// Loaded as a module AFTER workshop.js, so every id it reaches for exists.
// NOTE: modules are strict mode — never assign to an undeclared name here.
import { ui } from './store.js';
const $=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
const j=await r.json();
if(j&&j.login){location.href='/';throw new Error('login');} // session died mid-work
return j;}
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
    for(const u of collabUsers){if(u.ref!==r||u.name===collabMe)continue;
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
  const r=await api('/collab/push',{text,rev:collabRev,src:SRCREL,thash:heldThash,
    placer:$('placer').value,router:$('router').value,
    fab:$('fab').value,silk:$('silk').value});
  if(seq!==pulseq)return;   // the editor moved on (or another board opened)
  if(r.stale){ // someone else edited first: adopt their text (undo keeps ours)
    cancelPush();
    if(r.text!==undefined)setEditor(r.text);
    if(r.rev!==undefined)collabRev=+r.rev;
    statMsg(r.error||'reloaded a collaborator edit (yours is in undo)',true);
    push();return;
  }
  if(r.error){statMsg(r.error);S=null;return;}
  statMsg('');applyState(r,false);
}

let heldThash='', heldTraces=[];
let collabRev=-1, collabOn=false, collabUsers=[], collabTimer=null, collabMe='';
function applyState(r,live){
  if(r.thash){ // server skipped the trace list: keep the one we already have
    if(r.thash!==heldThash){heldTraces=r.traces||[];}
    r.traces=(r.traces&&r.traces.length)?r.traces:heldTraces;
    heldThash=r.thash;
  }
  if(r.rev!==undefined&&r.rev!==null)collabRev=+r.rev; // the room's rev rides every build
  S=r;S.cur=r;markDirty();spinBriefly(); // render live on the state itself (bw/bh/pours/fixed ride along)
  notePlacement(r);
  if(live&&r.frames&&r.frames.length)animate(r.frames,r.traces,()=>{drawDRC(r);});
  else{S.cur.traces=r.traces;ui.set({cost:`cost ${r.cost}`});drawDRC(r);}
  drawFeas(r);renderLayers(r);renderParts(r);
  if(document.activeElement!==$('ed'))setEditor(r.text);
}
// --- realtime collab: one SSE stream per board, rev-guarded pushes --------
// Same banner pattern as the file-watch: a rev mismatch means someone else
// edited first, so reload their text (never auto-merge, never clobber).
function collabPaint(users){
  collabUsers=users||[];
  const others=collabUsers.filter(u=>u.name!==collabMe);
  // the pill never grows past three names no matter the room size —
  // the full roster lives in the tooltip.
  const head=collabUsers.slice(0,3).map(u=>u.name).join(', ')
    +(collabUsers.length>3?` +${collabUsers.length-3}`:'');
  ui.set({room:others.length?`${others.length+1} here: ${head}`
      :((collabUsers.length?'solo · '+head:'solo')),
    roomOk:!!others.length,
    roomTitle:collabUsers.map(u=>`${u.name}${u.ref?' on '+u.ref:''}`).join('\n')||'no one else here yet',
    roomNote:others.length?`live now: ${head}`:'just you here — copy the link to co-edit'});
  markDirty();
}
let collabSyncSeq=0; // monotonic: a slow sync must not land on a newer room
async function collabSync(){ // pull the room's text (first connect + on rev bump)
  const seq=++collabSyncSeq;
  const r=await api('/collab/sync',{});
  if(seq!==collabSyncSeq)return; // a newer sync is already in flight
  if(r.error||r.text===undefined)return;
  collabMe=r.hello||collabMe;
  collabPaint(r.users);
  if(r.rev!==undefined)collabRev=+r.rev;
  if(r.text!==$('ed').innerText&&document.activeElement!==$('ed')){
    cancelPush();setEditor(r.text);push(); // parse + render their text
  }
}
let collabES=null; // one stream per board: rehomed on openFile (old room dies)
function collabStart(){
  if(collabOn)return;collabOn=true;
  collabSync();
  if(collabES){try{collabES.close();}catch(err){}}
  const es=collabES=new EventSource('/collab/events');
  es.onmessage=e=>{
    let m;try{m=JSON.parse(e.data);}catch(err){return;}
    if(m.hello!==undefined)collabMe=m.hello;
    if(m.users)collabPaint(m.users);
    if(m.rev!==undefined&&+m.rev!==collabRev&&(m.by||'')!==collabMe
       &&(m.by!==undefined||m.rev>collabRev)){ // somebody's push landed
      collabRev=+m.rev;collabSync();
    }
  };
  es.onerror=()=>{ // the stream drops (sleep, proxy): re-sync, the next rev heals
    if(collabES!==es)return; // rehomed already — this stream is dead, stay dead
    try{es.close();}catch(err){}
    collabES=null;collabOn=false;setTimeout(collabStart,3000);
  };
  // presence: cursor + selected ref, every 5s (the server prunes at 15s)
  clearInterval(collabTimer);
  collabTimer=setInterval(async()=>{
    try{
      const r=await api('/collab/cursor',{x:view.t||0,y:0,
        ref:((S&&S.cur&&S.cur.hover)||(edHl.size?[...edHl][0]:''))});
      if(r.users)collabPaint(r.users);
      if(r.rev!==undefined)collabRev=+r.rev;
    }catch(err){}
  },5000);
  const sh=$('sharebtn');
  if(sh)sh.onclick=async()=>{
    try{await navigator.clipboard.writeText(location.href);
      toast('link copied — send it to your collaborator');}
    catch(err){toast('copy failed — select the URL from the address bar');}
  };
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
function renderLayers(st){
  const cu=$('cu'),marks=$('marks');
  if(!cu)return;
  st=st||(S&&S.cur)||{layers:2,silk:'full'};
  cu.innerHTML='';marks.innerHTML='';
  layerNames(st).forEach(nm=>{
    const on=visLayer(nm,st);
    const id='lay_'+nm.replace(/[^\w]/g,'_');
    cu.appendChild(layerRow(nm,nm,on,id));
  });
  SILKMARKS.forEach(nm=>{
    const on=visLayer(nm,st);
    marks.appendChild(layerRow(nm==='silk'?'silkscreen':'mask',nm,on,'mark_'+nm));
  });
  Object.keys(MARKLABEL).forEach(k=>{ // ref/value ride the silkscreen toggle
    if(k==='ref'||k==='value')return;
    marks.appendChild(layerRow(MARKLABEL[k],k,visMark(k,st),'mark_'+k));
  });
}
function layerRow(label,key,on,id){
  const l=document.createElement('label');
  l.className=on?'on':'off';
  const i=document.createElement('input');
  i.type='checkbox';i.checked=on;i.id=id;
  i.onchange=()=>{
    if(isCu(key))VIS.layers[key]=i.checked; else if(SILKMARKS.includes(key))VIS.layers[key]=i.checked;
    else VIS.marks[key]=i.checked;
    visSave();
    const st=(S&&S.cur)||null;
    renderLayers(st);renderParts(st);markDirty();
  };
  l.append(i,document.createTextNode(label));
  l.title=isCu(key)?`copper layer ${key}`:`${label} on the PCB canvas`;
  return l;
}
const MAX_ROWS=400; // DOM rows, not parts: 5400 checkboxes brick the page
function renderParts(st){
  const box=$('partlist');
  if(!box)return;
  st=st||(S&&S.cur)||{parts:{}};
  const all=Object.keys(st.parts||{}).sort();
  // a filter searches every part (the list is capped, the search is not)
  const refs=(VIS.filter?all.filter(r=>partShown(r,st)):all).slice(0,MAX_ROWS);
  if(!VIS.filter&&all.length>MAX_ROWS){
    const shown=new Set(refs);
    edHl.forEach(r=>{if(!shown.has(r))refs.push(r);}); // keep the selection reachable
    refs.sort();
  }
  const same=box.children.length===refs.length
    && refs.every((r,i)=>box.children[i]&&box.children[i].dataset.ref===r);
  if(!same)box.innerHTML=''; // first draw, or a different board
  refs.forEach((r,i)=>{
    let l=box.children[i];
    if(!l||l.dataset.ref!==r){
      l=document.createElement('label');
      l.dataset.ref=r;
      const cb=document.createElement('input');
      cb.type='checkbox';
      cb.onchange=()=>{VIS.parts[r]=cb.checked;visSave();paintParts();markDirty();};
      const nm=document.createElement('span');nm.textContent=r;
      const v=document.createElement('span');v.className='pv';
      l.append(cb,nm,v);
      if(box.children[i])box.replaceChild(l,box.children[i]);
      else box.appendChild(l);
    }
    const p=st.parts[r]||{};
    const v=l.querySelector('.pv');
    v.textContent=[p.value||'',p.fp||''].filter(Boolean).join(' '); // filterable
    v.title=v.textContent;
  });
  paintParts();
}
function paintParts(){ // the rows are the source of truth for the note
  const box=$('partlist');
  if(!box)return;
  const total=Object.keys((S&&S.cur&&S.cur.parts)||{}).length;
  const refs=Array.from(box.children).map(l=>l.dataset.ref).filter(Boolean);
  let hidden=0;
  refs.forEach((r,i)=>{
    const l=box.children[i];
    if(!l)return;
    const on=VIS.parts[r]!==false;
    if(!on)hidden++;
    l.dataset.hidden=on?'':'1';
    l.className=(on?'':'hidden ')+(edHl.has(r)?'selpart':'');
    l.querySelector('input').checked=on;
    // the filter greys the rest, it does not detach the rows (no stale nodes)
    l.style.opacity='1'; // a filter selects rows; it cannot dim rows that are not here
  });
  $('partnote').textContent=!total?'no parts'
    :(total>refs.length?`${refs.length-hidden}/${refs.length} of ${total} (capped)`
                       :`${refs.length-hidden}/${refs.length} shown`);
}
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
let galSeed=0;
let galBase=null; // shift-clicked compare base: {i, cand}
function galDelta(a,b){ // cost delta + parts moved >2mm between candidates
  let moved=0;for(const r in a.pos){const q=b.pos[r];if(!q)continue;
    const dx=a.pos[r][0]-q[0],dy=a.pos[r][1]-q[1];
    if(dx*dx+dy*dy>4)moved++;}
  return {dcost:+(b.cost-a.cost).toFixed(1),moved};
}
function thumb(cand,i){
  const fig=document.createElement('button');fig.type='button';fig.className='galpick';
  fig.setAttribute('aria-label',`adopt candidate ${i}, cost ${cand.cost} (shift-click to compare)`);
  const cv=document.createElement('canvas');cv.width=300;cv.height=220;fig.appendChild(cv);
  const cap=document.createElement('span');cap.className='galcap';cap.textContent=`#${i} cost ${cand.cost}`;fig.appendChild(cap);
  fig.onclick=e=>{if(e&&e.shiftKey)galCompare(i,cand,fig);else pickCand(i);};
  const ctx=cv.getContext('2d'),W=300,H=220,s=Math.min(W/S.bw,H/S.bh),ox=(W-S.bw*s)/2,oy=(H-S.bh*s)/2;
  ctx.fillStyle=C.paper;ctx.fillRect(0,0,W,H);
  ctx.strokeStyle=C.line2;ctx.strokeRect(ox,oy+S.bh*s,S.bw*s,-S.bh*s);
  for(const r in cand.pos){const p=S.parts[r];if(!p)continue;
    const [x,y]=cand.pos[r];
    const fixed=S.fixed&&S.fixed[r];
    ctx.fillStyle=fixed?C.wash:C.ink2;ctx.fillRect(ox+(x-p.w/2)*s,oy+(S.bh-y-p.h/2)*s,p.w*s,p.h*s);
    ctx.strokeStyle=fixed?C.signal:C.ink;ctx.strokeRect(ox+(x-p.w/2)*s,oy+(S.bh-y-p.h/2)*s,p.w*s,p.h*s);}
  return fig;
}
async function genCands(){
  if(!S)return;
  const n=Math.max(1,Math.min(8,parseInt($('ncand').value||'4',10)));
  galSeed=(galSeed+1)%1000;
  await withBusy($('dice'),`generating ${n}…`,async()=>{
    const r=await api('/candidates',{placer:$('placer').value,n,seed:galSeed,iters:400});
    if(r.error){statMsg(r.error);return;}
    galMeta={n,seed:galSeed};galCands=r.candidates;galBase=null;
    const g=$('gal');g.innerHTML='';r.candidates.forEach((c,i)=>g.appendChild(thumb(c,i)));
    $('galwrap').style.display='';
    drawFeas({feasible:r.feasible,layers:r.layers});
    statMsg(`${n} candidates — click to pick, shift-click two to compare`,true);
  });
}
let galMeta={n:4,seed:0};
function galCompare(i,cand,fig){
  const g=$('gal');
  if(galBase&&galBase.i===i){ // toggle off
    galBase=null;g.querySelectorAll('.galcap').forEach((c,j)=>{c.textContent=`#${j} cost ${galCands[j].cost}`;});
    statMsg(`${galCands.length} candidates — click one to pick`,true);return;
  }
  if(!galBase){galBase={i,cand};
    fig.querySelector('.galcap').textContent=`#${i} cost ${cand.cost} (base — click another)`;
    statMsg(`comparing from #${i} — click another candidate`,true);return;}
  const d=galDelta(galBase.cand,cand);
  fig.querySelector('.galcap').textContent=
    `#${i} cost ${cand.cost} (Δ${d.dcost>=0?'+':''}${d.dcost}, ${d.moved} moved)`;
  statMsg(`#${galBase.i}→#${i}: Δcost ${d.dcost>=0?'+':''}${d.dcost}, ${d.moved} parts moved — click to pick`,true);
}
let galCands=[];
async function pickCand(i){
  await withBusy($('dice'),`picking #${i}…`,async()=>{
    const r=await api('/pick',{placer:$('placer').value,router:$('router').value,
      index:i,n:galMeta.n,seed:galMeta.seed,iters:400,silk:$('silk').value});
    if(r.error){statMsg(r.error);return;}
    statMsg('');$('galwrap').style.display='none';applyState(r,true);
  });
}
function drawDRC(r){
  const d=$('drc');let h='';
  const li=r.lint||{errors:[],warnings:[]}; // static source lint, no place/route
  if(li.errors.length)h+=li.errors.map(e=>`<div class=err>✗ lint: ${e}</div>`).join('');
  if(r.errors.length)h+=r.errors.map(e=>`<div class=err>✗ ${e}</div>`).join('');
  else if(!li.errors.length)h+='<div class=ok>✓ DRC clean ('+r.fab+')</div>';
  h+=r.warnings.slice(0,5).map(w=>`<div class=warn>~ ${w}</div>`).join('');
  h+=(li.warnings||[]).slice(0,3).map(w=>`<div class=warn>~ lint: ${w}</div>`).join('');
  const rec=(r.recommend&&r.recommend.items)||[];
  h+=rec.slice(0,6).map(it=>`<div class=warn>+ ${it.kind}: ${it.msg}</div>`).join('');
  if(r.sim&&Object.keys(r.sim).length)h+='<div class=ok>⚡ '+Object.entries(r.sim).map(([n,v])=>`${n}=${v}V`).join(' ')+'</div>';
  if(r.sim_problems&&r.sim_problems.length)h+=r.sim_problems.map(p=>`<div class=err>⚡✗ ${p}</div>`).join('');
  if(r.tran&&Object.keys(r.tran).length)h+='<div class=ok>⚡tran '+Object.entries(r.tran).map(([n,w])=>`${n} ${w[w.length-1].toFixed(2)}V [${Math.min(...w).toFixed(2)},${Math.max(...w).toFixed(2)}] (${w.length}pts)`).join(' · ')+'</div>';
  if(r.ac&&Object.keys(r.ac).length){const db=v=>20*Math.log10(Math.max(1e-12,Math.abs(v)));
    h+='<div class=ok>⚡ac '+Object.entries(r.ac).map(([n,w])=>`${n} ${db(w[w.length-1]).toFixed(1)}dB@${r.f1||''}Hz [${db(Math.max(...w.map(Math.abs))).toFixed(1)}dB pk] (${w.length}pts)`).join(' · ')+'</div>';}
  d.innerHTML=h;
  drawTidy(r);
}
function tidyVal(v){
  if(v===null||v===undefined)return '<span class=dim>n/a</span>';
  if(typeof v==='number')return Number.isInteger(v)?String(v):v.toFixed(3);
  if(typeof v==='object'){const ks=Object.keys(v);
    if(v.total!==undefined&&v.per_net!==undefined)return `total=${v.total}`; // nested detail lives in STATUS.md
    return ks.map(a=>`${a}=${v[a]}`).join(', ');}
  return String(v);
}
function drawTidy(r){
  const t=r.tidy||{};
  if(r.dense){ // metrics and the routability probe are skipped at this size
    $('tidycov').textContent='';
    $('tidy').innerHTML='<div class=dim>tidy metrics skipped on a dense board '
      +`(${Object.keys(r.parts||{}).length} parts) — run the CLI for the full report</div>`;
    ui.set({ocd:'OCD n/a (dense)'});
    return;
  }
  $('tidycov').textContent=t.coverage?`(${t.coverage})`:'';
  const rows=Object.entries(t).filter(([k])=>k!=='coverage'&&k!=='routed_segs')
    .map(([k,v])=>`<div><span class=dim>${k}</span> ${tidyVal(v)}</div>`).join('');
  $('tidy').innerHTML=rows;
  const sc=r.score; // OCD neatness 0-100 next to cost
  if(sc&&!sc.dense)ui.set({ocd:`OCD ${sc.total}/100 (${sc.grade})`});
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
    $('importstat').textContent='importing '+f.name+'…';
    const r=await api('/fs/import',{name:f.name,data});
    $('importstat').textContent=r.error||r.note||('imported '+f.name);
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
  $('dl').textContent=`download ${key}`; // keep a word label (glyph alone is not a label)
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
  $('simbtn').textContent=`sim ${simWhat}`;
  $('simbtn').title=`simulate the current board (shift-click: ${simWhat==='dc'?'tran':simWhat==='tran'?'ac':'dc'})`;
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
  $('cout').textContent=`${(area/1.378*0.0254).toFixed(2)}mm ext, via ${(A/(3*Math.sqrt(dT/10))).toFixed(2)}mm drill`;
  const pv=v=>{const m=String(v).match(/^([\d.]+)(k|M)?$/i);return m?parseFloat(m[1])*(m[2]?({k:1e3,M:1e6})[m[2].toLowerCase()]||1:1):NaN;};
  const V=pv($('dv').value),Rt=pv($('drt').value),Rb=pv($('drb').value);
  $('dout').textContent=(V>=0&&Rt>0&&Rb>0)?`Vout ${(V*Rb/(Rt+Rb)).toFixed(2)}V`:'';
  const zw=parseFloat($('zw').value)||0,zh=parseFloat($('zh').value)||0;
  if(zw>0&&zh>0){const u=zw/zh,er=4.4;
    const ere=(er+1)/2+(er-1)/2/Math.sqrt(1+12/u);
    const z=u<=1?60/Math.sqrt(ere)*Math.log(8/u+u/4)
      :120*Math.PI/(Math.sqrt(ere)*(u+1.393+0.667*Math.log(u+1.444)));
    $('zout').textContent=`Z0 ~${z.toFixed(1)}Ω (microstrip FR4, estimate)`;
  }else $('zout').textContent='';
}
['ca','cdt','dv','drt','drb','zw','zh'].forEach(id=>$(id).addEventListener('input',calcLive));
if($('qgo'))$('qgo').onclick=async()=>{ // fab price comparison for the open board
  await withBusy($('qgo'),'comparing…',async()=>{
    const q=Math.max(1,parseInt($('qqty').value)||5);
    const r=await api('/quote',{qty:q,no_parts:$('qbare').checked});
    if(r.error){$('qout').textContent=r.error;statMsg(r.error);return;}
    const _asm=(r.rows||[]).find(x=>x.asm)||{};
    const _alt=_asm.via_alt||[],_unp=_asm.unpriced||[],_low=_asm.low_stock||[];
    $('qout').innerHTML=(r.rows||[]).map(x=>
      `<div class=qrow>${x.logo?`<img class=qlogo src="${x.logo}" alt="${x.fab} logo" width=64 height=21>`:''}<span class=dim>${x.fab}</span> bare $${x.bare_total}${x.asm_total?` asm $${x.asm_total} ($${x.asm_per_board}/bd)`:''}</div>`).join('')
      +(_alt.length?`<div class=dim>via substitute: ${_alt.join(', ')}</div>`:'')
      +(_unp.length?`<div class=warn>unpriced: ${_unp.join(', ')}</div>`:'')
      +(_low.length?`<div class=warn>low stock: ${_low.join(', ')}</div>`:'')
      +`<div class=dim>${r.stamp} estimates — re-verify before ordering</div>`;
    statMsg('');
  });
};
$('doc').addEventListener('toggle',async()=>{ // lazy: check on first open
  if(!$('doc').open||$('docout').dataset.done)return;
  const r=await api('/doctor',{});
  if(r.error){$('docout').textContent=r.error;return;}
  $('docout').innerHTML=(r.ok?'<div class=ok>✓ all systems</div>':'<div class=warn>degraded: features fall back, nothing crashes</div>')
    +r.checks.map(c=>`<div class=${c.ok?'ok':'err'}>${c.ok?'✓':'✗'} ${c.name}${c.detail?' <span class=dim>'+c.detail+'</span>':''}</div>`).join('');
  $('docout').dataset.done='1';
});
// undo/redo: server keeps text history (git-style log); undo restores + rebuilds
// photo scan: upload shots of a physical board, get a draft design back
const scanAsk=[];
const b64=f=>new Promise(r=>{const d=new FileReader();
  d.onload=()=>r({name:f.name,data:String(d.result).split(',',1).length>1?String(d.result).slice(String(d.result).indexOf(',')+1):''});
  d.readAsDataURL(f);});
if($('scango'))$('scango').onclick=async()=>{
  const fs=[...($('scanfiles').files||[])];
  if(!fs.length){$('scanstat').textContent='choose some photos first';return;}
  $('scanstat').textContent=`stitching ${fs.length} photos — this takes a minute…`;
  $('scango').disabled=true;$('scanout').textContent='';$('scanq').innerHTML='';
  try{
    const photos=await Promise.all(fs.map(b64));
    const docs=await Promise.all([...($('scandocs').files||[])].map(b64));
    const body={photos,docs,note:$('scannote').value||'',
                answers:scanAsk.filter(x=>x.a).map(x=>({q:x.q,a:x.a}))};
    if($('scanmm').value)body.mm=Number($('scanmm').value);
    const r=await api('/scan',body);
    if(r.error){$('scanstat').textContent=r.error;return;}
    const sides=Object.entries(r.sides||{}).map(([k,v])=>
      `${k}: ${v.used}/${v.photos} registered, coverage ${v.coverage_mean}`).join(' · ');
    $('scanstat').textContent=sides||'scan done';
    Object.entries(r.sides||{}).forEach(([k,v])=>{
      Object.entries(v.dropped_why||{}).forEach(([nm,why])=>{
        const d=document.createElement('div');d.className='panel-note';
        d.textContent=`dropped ${k}/${nm}: ${why}`;$('scanq').appendChild(d);});});
    $('scanout').textContent=r.analysis||r.draft_error||'(no analysis)';
    scanRev = r.review||null; scanViews = r.views||{};
    scanDraft = r.draft||'';
    scanShowReview();
    if(r.draft){const b=document.createElement('button');b.type='button';
      b.className='primary';b.textContent='open this draft in the editor';
      b.onclick=()=>{setEditor(r.draft);push();};
      $('scanq').appendChild(b);}
    if(r.draft_error){const w=document.createElement('div');
      w.className='panel-note';w.textContent='draft did not parse: '+r.draft_error;
      $('scanq').appendChild(w);}
    if(r.draft&&!r.draft_error){const w=document.createElement('div');
      w.className='panel-note';
      const fl=(r.floating||[]).length;
      w.textContent=`buildability: ${r.wired} parts wired, ${r.drc} DRC error(s)`
        +(fl?` · ${fl} parts have no nets (photos cannot show them) — wire from the datasheet`:'');
      $('scanq').appendChild(w);}
    (r.questions||[]).forEach(q=>{
      const row=document.createElement('div');row.className='scanqa';
      const lab=document.createElement('label');lab.textContent=q;
      const inp=document.createElement('input');inp.placeholder='your answer — then analyse again';
      inp.setAttribute('aria-label',q);
      const rec={q,a:''};scanAsk.push(rec);
      inp.oninput=()=>{rec.a=inp.value;};
      row.appendChild(lab);row.appendChild(inp);$('scanq').appendChild(row);});
  }catch(e){$('scanstat').textContent='scan failed: '+e;}
  finally{$('scango').disabled=false;}
};

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
  $('scanview').hidden=!has;
  if(!has)return;
  const side=scanSide();
  if($('scanside').value!==side)$('scanside').value=side;
  scanDraw();
  scanRows();
}
function scanScale(){
  const cv=((scanRev||{}).canvas||{})[scanSide()]||[0,0];
  const mm=(((scanRev||{}).mm_per_px||{})[scanSide()])||0;
  return {cw:cv[0]||0, ch:cv[1]||0, mm:mm||0};
}
function scanDraw(){
  const img=$('scanimg'), svg=$('scansvg');
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
}
function scanTip(p){
  const bits=[`${p.ref} · ${p.fp}${p.value&&p.value!=='?'?` · ${p.value}`:''}`,
    `${p.w}×${p.h}mm at (${p.x}, ${p.y})`];
  if(p.value==='?')bits.push('value unreadable — confirm it below');
  else if(p.uncertain)bits.push('reading uncertain');
  if(p.note)bits.push(p.note);
  return bits.join(' — ');
}
function scanRows(){
  const box=$('scanparts');box.innerHTML='';
  for(const p of ((scanRev||{}).parts||[])){
    const row=document.createElement('div');
    row.className='scanprow'+(p.uncertain?' unc':'');
    row.dataset.ref=p.ref;
    row.title=scanTip(p);
    const lbl=document.createElement('b');lbl.textContent=p.ref;row.appendChild(lbl);
    const dim=document.createElement('span');dim.className='dim';
    dim.textContent=`${p.fp} · ${p.value||'?'}`;row.appendChild(dim);
    if(p.uncertain){
      const fix=document.createElement('button');fix.type='button';
      fix.textContent='confirm';fix.title=`accept ${p.ref} as ${p.fp} ${p.value||'?'}`;
      fix.onclick=()=>scanConfirm(p.ref);
      row.appendChild(fix);
      const ed=document.createElement('button');ed.type='button';
      ed.textContent='edit';ed.title=`correct ${p.ref} (value / footprint)`;
      ed.onclick=()=>scanEdit(p.ref);
      row.appendChild(ed);
    }
    const del=document.createElement('button');del.type='button';
    del.textContent='remove';del.title=`drop ${p.ref} from the draft`;
    del.onclick=()=>scanRemove(p.ref);
    row.appendChild(del);
    box.appendChild(row);
  }
}
// review edits rewrite the DRAFT text, never Board state: the draft is the
// review's working copy and /build re-parses it, so the editor, solver and
// undo history all see the same change they would from a hand edit.
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
  setEditor(scanDraft);push();
  $('scanstat').textContent=msg;
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
  scanDraw();scanRows();
  $('scanstat').textContent=`${ref} confirmed as ${p.fp} ${p.value}`;
}
function scanEdit(ref, mustName){
  const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
  if(!p)return;
  const val=prompt(`value for ${ref} (footprint ${p.fp})${p.note?'\nmodel note: '+p.note:''}`, p.value==='?'?'':p.value);
  if(val===null)return;
  const fp=prompt(`footprint for ${ref} (Enter keeps ${p.fp})`, p.fp) || p.fp;
  const {lines,i}=scanPartLine(ref);
  if(i<0){$('scanstat').textContent=`${ref} not found in draft text`;return;}
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
  scanSetDraft(lines);scanDraw();scanRows();
  scanCommit(`${ref} corrected — rebuilding`);
}
function scanRemove(ref){
  const {lines,i}=scanPartLine(ref);
  if(i<0){$('scanstat').textContent=`${ref} not found in draft text`;return;}
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
  scanSetDraft(lines);scanDraw();scanRows();
  scanCommit(`${ref} removed — rebuilding`);
}
if($('scanside'))$('scanside').onchange=scanShowReview;
if($('scanviewkind'))$('scanviewkind').onchange=scanShowReview;
if($('scanlabels'))$('scanlabels').onchange=scanShowReview;
if($('scanboxes'))$('scanboxes').onchange=scanShowReview;
if($('scansvg')){
  $('scansvg').addEventListener('mousemove',e=>{
    const t=e.target;
    const ref=t&&t.dataset?t.dataset.ref:null;
    const p=((scanRev||{}).parts||[]).find(x=>x.ref===ref);
    $('scanhover').textContent=p?scanTip(p):'';
  });
  $('scansvg').addEventListener('mouseleave',()=>{$('scanhover').textContent='';});
}

// x-ray: reference download + fab-scan upload vs the design (score + boxes)
let xrayRaw='';
if($('xrayfile'))$('xrayfile').onchange=()=>{const f=$('xrayfile').files[0];if(!f)return;
  const rd=new FileReader();rd.onload=()=>{xrayRaw=String(rd.result).split(',',1)[1]||'';
    $('xraystat').textContent=`${f.name} ready — compare to check it`;};
  rd.readAsDataURL(f);};
if($('xraysvg'))$('xraysvg').onclick=async()=>{
  const r=await api('/render',{key:'xray'});if(r.error){$('xraystat').textContent=r.error;return;}
  const a=document.createElement('a');
  a.href=`data:image/svg+xml,${encodeURIComponent(r.data)}`;
  a.download=r.name;a.click();$('xraystat').textContent=r.name;};
if($('xraygo'))$('xraygo').onclick=async()=>{
  if(!xrayRaw){$('xraystat').textContent='pick a fab PNG first';return;}
  const pv=(id,fb)=>{const v=parseFloat($(id).value);return Number.isFinite(v)?v:fb;};
  const r=await api('/xray',{png:xrayRaw,dx:pv('xraydx',0),dy:pv('xraydy',0),
    scale:pv('xraysc',1),thr:Math.round(pv('xraythr',100))});
  if(r.error){$('xraystat').textContent=r.error;return;}
  $('xraystat').textContent=`score ${r.score} — missing ${r.missing}px extra ${r.extra}px`;
  $('xraydivs').innerHTML=(r.divs||[]).map(d=>
    `<div><span class=dim>${d.kind}</span> ${d.x} ${d.y} ${d.w}x${d.h}mm</div>`).join('')
    ||'<div class=dim>no divergences</div>';
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
let TREE=[],SRCREL='',VC={},DIR='.',ROOTREL='.';
function treeRow(e,depth){
  const d=document.createElement('button');d.type='button';
  d.className='trow '+(e.kind==='dir'?'tdir':'tfile')+(e.path===SRCREL?' active':'');
  d.style.paddingLeft=(8+depth*13)+'px';
  d.textContent=e.kind==='dir'?e.name+'/':e.name;
  d.title=e.kind==='dir'?'folder — click to open it':e.path;
  d.setAttribute('aria-label',e.kind==='dir'
    ?('open folder '+e.name):('open '+e.name));
  if(e.kind==='file'){
    if(e.bytes){const s=document.createElement('span');s.className='tsize';
      s.textContent=(+e.bytes/1024).toFixed(1)+'k';d.appendChild(s);}
    d.onclick=()=>e.name.endsWith('.ocd')?openFile(e.path):previewFile(e.path);
  }else{
    d.onclick=()=>loadTree(e.path);
  }
  return d;
}
function renderTree(){
  const t=$('tree');t.innerHTML='';
  // one level at a time: this directory, then a way back up while inside root
  if(DIR!=='.'){const up=DIR.includes('/')?DIR.replace(/\/[^/]*$/,''):'.';
    t.appendChild(treeRow({name:'.. ('+(up==='.'?ROOTREL:up)+')',path:up,kind:'dir'},0));}
  if(!TREE.length){const e=document.createElement('div');e.className='trow tdir';
    e.setAttribute('role','status');e.textContent='(no text files)';t.appendChild(e);}
  TREE.forEach(e=>t.appendChild(treeRow(e,1)));
}
async function loadTree(dir){
  const f=await fetch('/fs?dir='+encodeURIComponent(dir||'.')).then(x=>x.json());
  if(f.error){statMsg(f.error);return;}
  DIR=f.dir||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';VC=f.vcs||{};
  TREE=f.tree||[];
  $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
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
  collabSyncSeq++; // a sync for the old board must not land on the new one
  if(collabES){try{collabES.close();}catch(err){}collabES=null;}
  collabOn=false; // collabStart re-opens the stream on the new board's room
  const r=await api('/fs/open',{path});
  if(r.error){statMsg(r.error);return;}
  statMsg('');$('msgs').innerHTML='';
  heldThash='';heldTraces=[];  // a different board: its traces are not ours
  setQueue([]);  // the server dropped the old board's proposals with it
  collabRev=(r.rev!==undefined)?+r.rev:-1; // re-home the room to the new board
  collabPaint([]);
  applyState(r,false);
  collabStart(); // join the new board's room: sync + fresh SSE stream
  toast('opened '+path);
  const f=await fetch('/fs').then(x=>x.json());
  if(!f.error){DIR=f.base||'.';ROOTREL=f.root||'.';SRCREL=f.src||'';TREE=f.tree||[];
    $('srcnote').textContent=`${SRCREL} · ${f.base||'.'} · saved on every good build`;
    $('chatwhere').textContent=SRCREL;
    $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
    renderTree();}
  loadVCS();
}
// --- agent chat --------------------------------------------------------
function msg(who,text){
  const el=document.createElement('p');
  el.className='msg '+(who==='you'?'me':who);
  const w=document.createElement('span');w.className='who';
  w.textContent=who==='bot'?'flux':who; // the agent has a name, like its counterpart
  el.appendChild(w);
  el.appendChild(document.createTextNode(text)); // textContent: never innerHTML
  $('msgs').appendChild(el);
  $('msgs').scrollTop=$('msgs').scrollHeight;
  return el;
}
function renderDiff(p,text){
  p.textContent='';
  String(text).split('\n').forEach(ln=>{
    const d=document.createElement('div');
    d.className=ln.startsWith('@@')||/^(\+\+\+|---)/.test(ln)?'dl-at'
      :ln.startsWith('+')?'dl-add':ln.startsWith('-')?'dl-del':'';
    d.textContent=ln;
    p.appendChild(d);
  });
}
function propose(pr,onQueue){
  const box=document.createElement('div');box.className='prop';
  const head=document.createElement('div');head.className='phead';
  const b=document.createElement('b');b.textContent=pr.path;
  const g=document.createElement('span');g.className='grow';
  const apply=document.createElement('button');apply.textContent='apply';
  const drop=document.createElement('button');drop.textContent='reject';
  head.append('proposed edit to ',b,g,apply,drop);
  const pre=document.createElement('pre');renderDiff(pre,pr.diff||'');
  box.append(head,pre);
  apply.onclick=async()=>{
    apply.disabled=drop.disabled=true;
    const r=await api('/chat/apply',{id:pr.id});
    if(r.error){msg('err',r.error);apply.disabled=drop.disabled=false;return;}
    if(r.state)applyState(r.state,false);
    else loadVCS();
    box.remove();
    msg('bot','applied '+pr.path+(r.state?'':' (not the open board)')
        +(r.note?' — '+r.note:''));
    if(onQueue)onQueue(r.proposals||[]);
    loadVCS();
  };
  drop.onclick=async()=>{
    apply.disabled=drop.disabled=true;
    const r=await api('/chat/reject',{id:pr.id});
    box.remove();
    msg('bot','rejected '+pr.path+' — nothing was written');
    if(onQueue)onQueue(r.proposals||[]);
  };
  $('msgs').appendChild(box);
  $('msgs').scrollTop=$('msgs').scrollHeight;
  return box;
}
// a turn may propose several files: every card lives in one queue, and each
// apply/reject removes its own card without disturbing the others
function setQueue(list){
  $('msgs').querySelectorAll('.prop').forEach(e=>e.remove());
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
    const det=document.createElement('details');det.className='thought';det.open=true;
    const sum=document.createElement('summary');
    sum.textContent=`plan: ${tn.length} file${tn.length===1?'':'s'} proposed`;
    const ul=document.createElement('ul');
    tn.forEach(p=>{const li=document.createElement('li');li.className='ok';
      li.textContent='◻ '+p.path;ul.appendChild(li);});
    det.append(sum,ul);$('msgs').appendChild(det);
    $('msgs').scrollTop=$('msgs').scrollHeight;
  }
  if(r.log&&r.log.length){ // thought trace: what the agent actually did
    const det=document.createElement('details');det.className='thought';
    const sum=document.createElement('summary');
    sum.textContent=`thought for ${r.log.length} step${r.log.length===1?'':'s'}`;
    const ul=document.createElement('ul');
    r.log.forEach(ln=>{const li=document.createElement('li');
      li.className=/failed|error/i.test(ln)?'bad':'ok';li.textContent=ln;
      ul.appendChild(li);});
    det.append(sum,ul);$('msgs').appendChild(det);
    $('msgs').scrollTop=$('msgs').scrollHeight;
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
function followups(reply){
  let box=$('followups');
  if(!box){box=document.createElement('div');box.id='followups';
    $('composer').before(box);}
  box.innerHTML='';
  const picks=[];
  reply.split('\n').forEach(ln=>{
    const m=ln.match(/^(?:\d+[.)]\s*|[-*]\s+)(.{12,80})$/);
    if(m&&/rout|check|valid|verif|test|place|thermal|copper|drc|fix/i.test(m[1])
       &&picks.length<3)picks.push(m[1].trim());});
  if(!picks.length)picks.push('Route and verify','Check DRC','Explain this board');
  picks.slice(0,3).forEach(q=>{const b=document.createElement('button');
    b.textContent=q.length>34?q.slice(0,33)+'…':q;b.title=q;
    b.onclick=()=>chat(q,$('chatauto').checked);
    box.appendChild(b);});
}
$('composer').addEventListener('submit',e=>{
  e.preventDefault();
  const t=$('ask').value.trim();if(!t)return;
  $('ask').value='';
  chat(t,$('chatauto').checked);
});
$('ask').addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('composer').requestSubmit();}
});
$('chatclear').onclick=async()=>{await api('/chat/reset',{});$('msgs').innerHTML='';};
// --- revisions: the board directory's git log --------------------------
async function loadVCS(){
  const r=await api('/vcs',{path:SRCREL||null});
  if(r.error){$('vcsnote').textContent=r.error;return;}
  VC=r.status||{};
  $('vcsnote').textContent=VC.repo
    ? `${VC.branch} · `+(VC.dirty?'uncommitted changes in '+VC.board:'clean')
    : 'not a git repository';
  const box=$('vcs');box.innerHTML='';
  if(!VC.repo){box.textContent='commit from the toolbar once this directory is a repo';return;}
  (r.log||[]).forEach(c=>{
    const d=document.createElement('button');d.type='button';d.className='rev';
    d.setAttribute('aria-label','show diff for '+c.hash+' '+c.subject);
    const h=document.createElement('span');h.className='rh';h.textContent=c.hash;
    const dt=document.createElement('span');dt.className='rd';dt.textContent=c.date;
    const s=document.createElement('span');s.className='rs';s.textContent=c.subject;
    d.append(h,dt,s);
    d.onclick=async()=>{
      const x=await api('/vcs/diff',{hash:c.hash});
      const pre=document.createElement('pre');pre.textContent=x.error||x.diff;
      const old=box.querySelector('pre');if(old)old.remove();
      d.after(pre);
    };
    box.appendChild(d);
  });
}
async function commitBoard(){
  if(!VC.repo){statMsg('not a git repository');return;}
  const m=prompt('commit message',(SRCREL||'board')+': ');
  if(!m)return;
  const r=await api('/vcs/commit',{message:m});
  if(r.error){statMsg(r.error);return;}
  toast(r.commit||'committed');
  loadVCS();
}
function toast(t){
  const b=document.createElement('div');b.className='toast';
  b.setAttribute('role','status');b.setAttribute('aria-live','polite');
  b.textContent=t;
  document.body.appendChild(b);
  setTimeout(()=>b.remove(),4200);
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
  $('placer').innerHTML=r.placers.map(p=>`<option>${p}</option>`).join('');
  $('router').innerHTML=r.routers.map(p=>`<option>${p}</option>`).join('');
  $('silk').innerHTML=r.silks.map(p=>`<option ${p===r.silk?'selected':''}>${p}</option>`).join('');
  $('fab').innerHTML=r.fabs.map(p=>`<option>${p}</option>`).join('');
  setEditor(r.text);applyState(r,false);
  if(r.rev!==undefined)collabRev=+r.rev; // the room's rev from the first load
  collabStart(); // realtime: SSE fan-out + presence from here on
  if(f.error){$('treenote').textContent=f.error;return;}
  DIR=f.base||'.';ROOTREL=f.root||'.';TREE=f.tree||[];SRCREL=f.src||'';VC=f.vcs||{};
  $('srcnote').textContent=`${SRCREL} · ${f.base||'.'} · saved on every good build`;
  $('chatwhere').textContent=SRCREL;
  $('treenote').textContent=`${DIR==='.'?ROOTREL:DIR} · ${TREE.filter(e=>e.kind==='file').length} files`;
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
  const p=await api('/poll',{});
  if(lastHash===null){lastHash=p.hash;return;}
  if(p.hash===lastHash||$('extbanner'))return;
  if(p.clean)return;  // our own save (or untouched) — nothing external
  lastHash=p.hash;
  const b=document.createElement('button');b.type='button';
  b.id='extbanner';
  b.style.cssText='width:100%;border:0;border-radius:0;background:var(--signal);color:#fff;'
    +'padding:10px 14px;min-height:40px;cursor:pointer;font:600 .9rem var(--sans);text-align:left';
  if(document.body.classList.contains('dark'))b.style.color='#06130d';
  b.textContent='file changed on disk — click to reload (your edits stay in undo)';
  b.onclick=async()=>{const r=await api('/reload',{});setEditor(r.text);applyState(r,false);b.remove();lastHash=null;};
  document.body.prepend(b);
}catch(e){}}
// --- knowledgebase panel: notes + datasheets, the agent's own files -----
let KBDOCS=[];
function kbRow(name,kind,tail){
  const r=document.createElement('div');r.className='kbrow';
  const b=document.createElement('button');b.className='kbname';b.textContent=name;
  const k=document.createElement('span');k.className='kbkind';k.textContent=kind;
  const t=document.createElement('span');t.className='kbparts';t.textContent=tail||'';
  r.append(b,k,t);return {row:r,btn:b};
}
async function kbLoad(){
  const r=await api('/kb/list',{});
  if(r.error){$('kbnote').textContent=r.error;return;}
  KBDOCS=r.docs||[];
  const parts=KBDOCS.reduce((n,d)=>n+(d.parts||[]).length,0);
  $('kbnote').textContent=`${KBDOCS.length} document${KBDOCS.length===1?'':'s'} · `
    +`${parts} part link${parts===1?'':'s'} · `+(r.dir||'kb/');
  const box=$('kblist');box.innerHTML='';
  KBDOCS.forEach(d=>{
    const {row,btn}=kbRow(d.name,d.kind+(d.source?' · from url':''),
                          (d.parts||[]).join(' '));
    btn.title=d.source?('source: '+d.source):d.name;
    btn.onclick=()=>kbOpen(d.name,1);
    box.appendChild(row);
  });
  if(!KBDOCS.length)box.textContent='nothing yet — add a url, or fetch datasheets';
  else if(r.total&&r.total>KBDOCS.length)
    box.appendChild(document.createTextNode('… showing '+KBDOCS.length+' of '+r.total
      +' documents — ask or search to reach the rest'));
  if(r.busy)$('kbstat').textContent='fetching datasheets…';
  else if((r.log||[]).length)$('kbstat').textContent=(r.log||[]).slice(-3).join(' · ');
}
async function kbOpen(doc,start){
  const r=await api('/kb/read',{doc,start:start||1,lines:120});
  if(r.error){$('kbstat').textContent=r.error;return;}
  $('kbview').textContent=`${doc}  lines ${r.start}-${r.end} of ${r.total_lines}\n\n${r.text}`;
}
async function kbGo(semantic,answer){
  const q=$('kbq').value.trim();if(!q){$('kbstat').textContent='type a question first';return;}
  $('kbstat').textContent=answer?'asking the local model…':'searching kb/…';
  const r=await api(semantic?'/kb/ask':'/kb/search',{q,limit:8,answer:!!answer});
  if(r.error){$('kbstat').textContent=r.error;return;}
  const hits=r.passages||r.hits||[];
  $('kbstat').textContent=`${hits.length} hit${hits.length===1?'':'s'}`
    +(r.method?' · '+r.method+(r.model?' · '+r.model:''):'')
    +((r.note&&!r.answer)?' · '+r.note:'');
  const box=$('kblist');box.innerHTML='';
  hits.forEach(h=>{
    const line=h.line!==undefined?h.line:h.start;
    const {row,btn}=kbRow(h.doc||'(no doc)',line!==undefined?'line '+line:'passage',
                          h.score!==undefined?String(h.score):'');
    btn.onclick=()=>kbOpen(h.doc,Math.max(1,(line||1)-3));
    row.title=String(h.text||'').slice(0,400);
    box.appendChild(row);
  });
  if(r.answer)$('kbview').textContent='answer (from kb/ only)'
    +(r.answer_note?'\n('+r.answer_note+')':'')
    +'\n\n'+r.answer;
  else if(r.answer_error)$('kbview').textContent='(no written answer: '+r.answer_error+')';
}
$('kbask').onclick=()=>kbGo(true,false);
$('kbgrep').onclick=()=>kbGo(false,false);
$('kbans').onclick=()=>kbGo(true,true);
$('kbaddbtn').onclick=async()=>{
  const src=$('kburl').value.trim();if(!src)return;
  $('kbstat').textContent='adding '+src+'…';
  const r=await api('/kb/add',{src});
  if(r.error){$('kbstat').textContent=r.error;return;}
  $('kbstat').textContent=`added ${r.added} (${r.bytes} bytes)`;
  $('kburl').value='';kbLoad();
};
$('kbfetch').onclick=async()=>{
  const r=await api('/kb/fetch',{});
  $('kbstat').textContent=r.error||r.note||'fetch started';
  kbLoad();
};
// preferences: Flux's Knowledge approvals. The file is the store; the
// buttons flip the `# ok` suffix and the agent reads what is approved.
$('kbprefsbtn').onclick=async()=>{
  const box=$('kbprefs'),open=box.style.display!=='none';
  box.style.display=open?'none':'';
  if(!open)kbPrefs();
};
async function kbPrefs(){
  const r=await api('/kb/prefs',{});
  const box=$('kbprefslist');box.innerHTML='';
  if(r.error){$('kbstat').textContent=r.error;return;}
  (r.prefs||[]).forEach(p=>{
    const d=document.createElement('div');d.className='kbrow';
    const b=document.createElement('span');b.className='kbname';
    b.textContent=`when ${p.when} :: ${p.text}`;
    const k=document.createElement('span');k.className='kbkind';
    k.textContent=p.approved?'approved':'pending';
    const t=document.createElement('button');t.textContent=p.approved?'reject':'approve';
    t.title=p.approved?'stop following this':'follow this from now on';
    t.onclick=async()=>{
      const x=await api('/kb/prefs/set',{id:p.id,approved:!p.approved});
      if(x.error)$('kbstat').textContent=x.error;else kbPrefs();};
    d.append(b,k,t);box.appendChild(d);});
  if(!(r.prefs||[]).length)box.textContent='no preferences yet — teach one below';
}
$('kbprefsgo').onclick=async()=>{
  if($('kbprefsgo').disabled)return;$('kbprefsgo').disabled=true;
  try{const r=await api('/kb/prefs/add',{when:$('kbwhen').value,text:$('kbwhat').value});
    if(r.error){$('kbstat').textContent=r.error;return;}
    $('kbwhen').value='';$('kbwhat').value='';kbPrefs();}
  finally{$('kbprefsgo').disabled=false;}
};
$('kbq').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();kbGo(true,false);}});
$('kburl').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();$('kbaddbtn').click();}});
kbLoad();
setInterval(()=>{if($('kbstat').textContent.startsWith('fetching'))kbLoad();},3000);
setInterval(watch,2000);
