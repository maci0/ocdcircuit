// Board canvas: the PCB, schematic and 3D drawings, the palette, the
// render-on-demand loop and the state the renderer owns (view transform, the
// drag preview offset, the schematic dirty flag, the frame dirty flag).
// The open board and the schematic selection are read through the host
// (initCanvas); the editor selection is imported, since the editor owns it.
import { $ } from './core.js';
import { ui } from './store.js';
import { edHl, edPin } from './editor.js';
import { layerNames, partShown, visLayer, visMark } from './visibility.js';
import { collabMeNow, collabUserList } from './collab.js';

let H = null; // {board, schSel}
let anim = null; // the running frame animation (one at a time)

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

export let view={s:1,ox:0,oy:0,t:1};
// board-mm preview offset while shift-dragging a trace. The canvas
// reads it every frame, so it lives with the renderer.
let segDrag=null; // {seg, dx, dy}
const MAX_SEGS=25000; // dense boards route 25k+: drawing all of them every frame is a slideshow
let schDirty=true; // the schematic repaints when nets or selection change
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
      const sel=H.schSel()===pp||edPin.has(pp);
      ctx.fillStyle=sel?C.ink:cols[i%4];ctx.beginPath();ctx.arc(x,y,4,0,7);ctx.fill();
      if(sel){ctx.strokeStyle=C.ink;ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,7);ctx.stroke();ctx.lineWidth=1;}
      st._schmap.pins.push({pp,net:n,x:x*zx,y:y*zx});});});
    ctx.restore();
    schDirty=false;
  }
}
// --- schematic edits → .ocd text (two-way binding) ---

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
export function renderAll(){if(!H.board())return;readTheme();view=drawPCB(H.board(),1);drawSCH(H.board());if(spinOn){rot+=0.003;draw3D(H.board(),rot);dirty=true;}}
let rot=0.6,spinOn=true,spinT=null,dirty=true; // render-on-demand: static board costs zero frames
function loop(){if(dirty){dirty=false;renderAll();}requestAnimationFrame(loop);}
requestAnimationFrame(loop);
export function markDirty(){dirty=true;schDirty=true;}
export function spinBriefly(ms=4000){spinOn=true;dirty=true;clearTimeout(spinT);spinT=setTimeout(()=>spinOn=false,ms);}
// animation: tween parts from->to, reveal traces
export function animate(frames,traces,done){
  cancelAnimationFrame(anim);
  const from=JSON.parse(JSON.stringify(H.board().parts));let i=0;
  function step(){
    if(i>=frames.length){H.board().traces=[];let n=0;dirty=true;
      (function grow(){n+=3;H.board().traces=traces.slice(0,n);dirty=true;if(n<traces.length)anim=requestAnimationFrame(grow);else{H.board().traces=traces;done&&done();}})();return;}
    const f=frames[i],to={};
    for(const r in f.pos){  // hidden parts snap, they do not glide
      const p=H.board().parts[r];
      if(!p||!partShown(r,H.board())){if(p){p.x=f.pos[r][0];p.y=f.pos[r][1];}}
      else to[r]=f.pos[r];
    }
    let k=0;const N=14;
    (function tw(){k++;const e=ease(k/N);
      for(const r in to){const a=from[r]||to[r],b=to[r];
        H.board().parts[r]={...H.board().parts[r],x:a[0]+(b[0]-a[0])*e,y:a[1]+(b[1]-a[1])*e};}
      dirty=true;
      ui.set({cost:`cost ${f.cost}`});
      if(k<N)anim=requestAnimationFrame(tw);else{for(const r in to)H.board().parts[r]={...H.board().parts[r],x:to[r][0],y:to[r][1]};i++;step();}})();}
  step();
}

export const palette = () => C;
export const viewNow = () => view;
export const segDragNow = () => segDrag;
export const setSegDrag = v => { segDrag = v; };
export const markSchDirty = () => { schDirty = true; };

export function initCanvas(deps) {
  H = deps;
  $('t3d').addEventListener('pointerdown', () => spinBriefly(8000));
  loop();
}
