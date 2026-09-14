"""OCD Studio: visual editor. Stdlib only (http.server + inline JS, no deps).

Layout (tmog cockpit: whole-system state at a glance, no tabs hiding answers):
  .ocd editor (highlighted) | PCB canvas | SCH canvas | 3D preview | DRC panel

Interactions:
- edit .ocd → debounce 400ms → rebuild → PCB/SCH/3D/DRC update live
- drag part on PCB → drops `fix REF at x y`, re-solves around it, editor updates
- placer/router/fab/silk/theme dropdowns → re-run with animation frames;
  parts glide (ease-out cubic tween), traces grow net-by-net
- light/dark toggle (themes change skin, never structure)

Run: python studio.py [file.ocd]  → http://localhost:8077
"""
from __future__ import annotations
import http.server
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = ROOT

from ocdcircuit import agent  # noqa: E402
from ocdcircuit import fab as _fab  # noqa: E402
from ocdcircuit.circuit import Board  # noqa: E402
from ocdcircuit.core import UiSlots  # noqa: E402

SLOTS = UiSlots()
# Built-in views (harness-slot shape: shell declares, entries contribute).
# A UI plugin = SLOTS.register(slot, id, fn) + optional /api route.
SLOTS.register("toolbar", "solver-selects",
               lambda s: ("<select id=placer title=placer></select>"
                          "<select id=router title=router></select>"
                          "<select id=fab title=fab></select>"
                          "<select id=silk title=silk></select>"))
SLOTS.register("toolbar", "actions",
               lambda s: ('<button id=theme>light</button><button id=solve>solve ▶</button>'
                          '<button id=undo title="undo (Ctrl+Z)">↩</button>'
                          '<button id=redo title="redo (Ctrl+Y)">↪</button><span id=stat></span>'))
SLOTS.register("view", "editor",
               lambda s: '<section><h3>.OCD — EDIT ME, BOARD FOLLOWS</h3>'
                         '<div id=ed contenteditable spellcheck=false></div></section>')
SLOTS.register("view", "pcb",
               lambda s: '<section id=pcbwrap><h3>PCB — DRAG PARTS, THEY STAY WHERE DROPPED</h3>'
                         '<canvas id=pcb></canvas><div id=drc></div></section>')
SLOTS.register("view", "sch",
               lambda s: '<section id=schwrap><h3>SCHEMATIC — CLICK PIN, CLICK NET TO REWIRE · '
                         'ALT-CLICK DROPS PIN · DOUBLE-CLICK LABEL RENAMES</h3>'
                         '<canvas id=sch></canvas></section>')
SLOTS.register("view", "inspector",
               lambda s: '<section id=wrap3d><h3>3D</h3><canvas id=t3d></canvas>'
                         '<h3>TIDY <span id=tidycov></span></h3><div id=tidy></div></section>')


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)


def _i(v: object, default: int) -> int:
    if v is None:
        return default
    assert isinstance(v, (int, str))
    return int(v)

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "boards", "blinky_555.ocd")
SRC = os.path.abspath(SRC)
BASE = os.path.dirname(SRC)

PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>OCD Studio</title>
<style>
:root{--bg:#0d0d0d;--panel:#141414;--line:#2a2a2a;--tx:#e8e8e8;--dim:#999;--acc:#f1c40f;--ok:#2ecc71;--bad:#e74c3c}
body.light{--bg:#f4f1e8;--panel:#fff;--line:#ccc;--tx:#222;--dim:#666;--acc:#8a6d00;--ok:#1e8449;--bad:#c0392b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:13px/1.45 monospace;height:100vh;display:flex;flex-direction:column}
header{display:flex;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);background:var(--panel)}
header b{color:var(--acc)}header select,header button{background:var(--bg);color:var(--tx);border:1px solid var(--line);font:inherit;padding:3px 8px;border-radius:4px}
main{flex:1;display:grid;grid-template-columns:minmax(300px,420px) 1fr 1fr;grid-template-rows:1fr 1fr;gap:1px;background:var(--line);min-height:0}
section{background:var(--bg);position:relative;min-height:0;display:flex;flex-direction:column}
section h3{margin:0;padding:4px 10px;font-size:11px;color:var(--dim);border-bottom:1px solid var(--line);letter-spacing:1px}
#ed{grid-row:1/3;overflow:auto;white-space:pre;padding:8px;outline:none;font:inherit;flex:1}
#pcbwrap{grid-column:2;grid-row:1/3;position:relative}#schwrap{grid-column:3;grid-row:1}#wrap3d{grid-column:3;grid-row:2;overflow:auto}
#tidy{padding:6px 10px;font-size:12px;overflow:auto}
#tidy div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
canvas{width:100%;height:100%;display:block;cursor:grab}
#drc{position:absolute;bottom:0;left:0;right:0;max-height:38%;overflow:auto;background:color-mix(in srgb,var(--panel) 92%,transparent);border-top:1px solid var(--line);padding:6px 10px;font-size:12px}
.err{color:var(--bad)}.warn{color:var(--acc)}.ok{color:var(--ok)}
.tok-k{color:#7fb4ff}.tok-net{color:#e67e22}.tok-part{color:var(--acc)}.tok-c{color:var(--dim)}.tok-num{color:#2ecc71}
body.light .tok-k{color:#0050a0}body.light .tok-net{color:#b9770e}
#cost{color:var(--dim)}
</style></head><body>
<header><b>OCD</b><span>studio</span><span id=cost></span>
/*__TOOLBAR__*/
</header>
<main>
/*__VIEWS__*/
</main>
<script>
const $=id=>document.getElementById(id);
let S=null, anim=null, theme='dark';
const ease=t=>1-Math.pow(1-t,3);
// state from server: parts{ref:{x,y,w,h}}, traces[{net,x1,y1,x2,y2,layer}], nets, drc, cost
function drawPCB(st, t){ // t: 0..1 trace reveal + part blend handled by caller
  const c=$('pcb'),ctx=c.getContext('2d'),R=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=R.width*dpr;c.height=R.height*dpr;ctx.scale(dpr,dpr);
  const W=R.width,H=R.height,s=Math.min(W/st.bw,H/st.bh),ox=(W-st.bw*s)/2,oy=(H-st.bh*s)/2;
  const X=x=>ox+x*s,Y=y=>oy+(st.bh-y)*s;
  ctx.fillStyle=getComputedStyle(document.body).getPropertyValue('--bg');ctx.fillRect(0,0,W,H);
  ctx.strokeStyle=theme==='dark'?'#123f12':'#ddd6c4';
  for(let gx=0;gx<=st.bw;gx+=5){ctx.beginPath();ctx.moveTo(X(gx),Y(0));ctx.lineTo(X(gx),Y(st.bh));ctx.stroke();}
  for(let gy=0;gy<=st.bh;gy+=5){ctx.beginPath();ctx.moveTo(X(0),Y(gy));ctx.lineTo(X(st.bw),Y(gy));ctx.stroke();}
  ctx.strokeStyle=theme==='dark'?'#1e5a1e':'#999';ctx.strokeRect(X(0),Y(st.bh),st.bw*s,st.bh*s);
  const cols=['#e74c3c','#3498db','#2ecc71','#9b59b6'];
  const n=Math.ceil(st.traces.length*t);
  for(let i=0;i<n;i++){const g=st.traces[i];ctx.strokeStyle=cols[g.layer%4];ctx.lineWidth=Math.max(1,g.w*s);ctx.beginPath();ctx.moveTo(X(g.x1),Y(g.y1));ctx.lineTo(X(g.x2),Y(g.y2));ctx.stroke();}
  for(const r in st.parts){const p=st.parts[r];
    ctx.fillStyle=st.fixed&&st.fixed[r]?'#3a2f00':'#111';ctx.fillRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    ctx.strokeStyle='#f1c40f';ctx.strokeRect(X(p.x-p.w/2),Y(p.y+p.h/2),p.w*s,p.h*s);
    ctx.fillStyle='#fff';ctx.font=`${Math.max(9,p.h*s*0.4)}px monospace`;ctx.textAlign='center';ctx.fillText(r,X(p.x),Y(p.y)+3);
    if(st.silk>0&&p.value){ctx.fillStyle='#999';ctx.font('8px monospace');ctx.fillText(p.value,X(p.x),Y(p.y-p.h/2)+10);}}
  return {s,ox,oy};
}
let view={s:1,ox:0,oy:0};
let schSel=null; // selected "REF.PIN"
function drawSCH(st){
  const c=$('sch'),ctx=c.getContext('2d'),R=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=R.width*dpr;c.height=R.height*dpr;ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,R.width,R.height);
  st._schmap={pins:[],nets:[]};
  const sch=st.sch||{order:[],px:{},rail_y:{},top:70},cols=['#e74c3c','#3498db','#2ecc71','#9b59b6'];
  sch.order.forEach(r=>{ctx.fillStyle='#111';ctx.fillRect(sch.px[r]-50,sch.top-34,100,30);
    ctx.strokeStyle='#e8e8e8';ctx.strokeRect(sch.px[r]-50,sch.top-34,100,30);
    ctx.fillStyle='#e8e8e8';ctx.textAlign='center';ctx.fillText(r,sch.px[r],sch.top-20);});
  Object.keys(st.nets).forEach((n,i)=>{const y=sch.rail_y[n];if(y===undefined)return;
    const xs=st.nets[n].map(pp=>sch.px[pp.split('.')[0]]).filter(x=>x!==undefined);
    if(!xs.length)return;
    ctx.strokeStyle=cols[i%4];ctx.lineWidth=2;ctx.beginPath();
    ctx.moveTo(Math.min(...xs),y);ctx.lineTo(Math.max(...xs),y);ctx.stroke();ctx.lineWidth=1;
    ctx.fillStyle='#e8e8e8';ctx.fillText(n,Math.min(...xs)-8,y+4);
    st._schmap.nets.push({n,x:(Math.min(...xs)+Math.max(...xs))/2,y});
    st.nets[n].forEach(pp=>{const x=sch.px[pp.split('.')[0]];if(x===undefined)return;
      ctx.strokeStyle=cols[i%4];ctx.beginPath();ctx.moveTo(x,sch.top-4);ctx.lineTo(x,y);ctx.stroke();
      const sel=schSel===pp;
      ctx.fillStyle=sel?'#f1c40f':cols[i%4];ctx.beginPath();ctx.arc(x,y,4,0,7);ctx.fill();
      st._schmap.pins.push({pp,net:n,x,y});});});
}
// --- schematic edits → .ocd text (two-way binding) ---
function schLines(){return $('ed').innerText.split('\n');}
const schEsc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
// parse net lines: {name, idx, pins:[{tok, li}]} (li = line index)
function schNets(){
  const lines=schLines(),out=[];
  lines.forEach((l,li)=>{let m=l.match(/^(\S+?)((?:\s+[LWlw][\d.]+)*)\s*::\s*(.*)$/);
    if(!m)m=l.match(/^net\s+(\S+?)(?:\s+[LWlw][\d.]+)*\s*:\s*(.*)$/),m=m&&[m[0],m[1],"",m[2]];
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
      const rx=new RegExp(`\\b${schEsc(ref)}\\.${schEsc(pin)}\\b`);
      lines[n.li]=lines[n.li].replace(rx,'').replace(/:\s*$/ ,':').replace(/\s+/g,' ').replace(/ :/ ,':');}
  }
  for(const n of nets)if(n.name===dst){n.pins.push(`${ref}.${pin}`);
    lines[n.li]=lines[n.li].replace(/:\s*(.*)$/,`: ${n.pins.join(' ')}`);changed=true;}
  if(changed)schCommit(lines);
}
function schDropPin(pp){
  const [ref,pin]=pp.split('.'),lines=schLines(),nets=schNets();
  for(const n of nets){
    const i=n.pins.findIndex(t=>{const [r,p]=t.split('.');return r===ref&&p===pin;});
    if(i>=0){lines[n.li]=lines[n.li].split(':')[0]+': '+n.pins.filter((_,j)=>j!==i).join(' ');
      schCommit(lines);return;}
  }
}
function schRename(net){
  const to=prompt(`rename net ${net} to:`,net);
  if(!to||to===net||!to.match(/^\w+$/))return;
  const esc=schEsc(net);
  const lines=schLines().map(l=>{
    l=l.replace(new RegExp(`^(net\\s+)${esc}\\b`,'$1'+to));
    l=l.replace(new RegExp(`^(route\\s+)${esc}\\b`,'$1'+to));
    l=l.replace(new RegExp(`^(trace\\s+)${esc}\\b`,'$1'+to));
    return l;});
  // power/match/join lists: word-boundary replace on those lines only
  for(let i=0;i<lines.length;i++){
    if(/^(power|match)\b/.test(lines[i])||/\bjoin\b/.test(lines[i]))
      lines[i]=lines[i].replace(new RegExp(`\\b${esc}\\b`, 'g'),to);
  }
  schCommit(lines);
}
(()=>{const c=$('sch');
c.addEventListener('mousedown',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const p of S._schmap.pins){
    if(Math.abs(mx-p.x)<7&&Math.abs(my-p.y)<7){
      if(e.altKey){schDropPin(p.pp);schSel=null;return;}
      schSel=(schSel===p.pp)?null:p.pp;return;}}
  for(const n of S._schmap.nets){
    if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){
      if(schSel){schMovePin(schSel,n.n);schSel=null;}return;}}
  schSel=null;});
c.addEventListener('dblclick',e=>{if(!S||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const n of S._schmap.nets)if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){schRename(n.n);return;}});
})();
function draw3D(st,rot){
  const c=$('t3d'),ctx=c.getContext('2d'),R=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=R.width*dpr;c.height=R.height*dpr;ctx.scale(dpr,dpr);
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
  const MASK={top:'#0f6b0f',front:'#0a4a0a',side:'#0d5c0d'};
  box(0,0,0,st.bw,st.bh,1.6,MASK);
  // copper traces on top layer shimmer gold
  for(const t of st.traces.slice(0,400)){if(t.layer!==0)continue;
    const w=Math.max(0.15,t.w/2);
    faces.push({z:1.75,p:[P(t.x1-w,t.y1-w,1.7),P(t.x2+w,t.y1-w,1.7),P(t.x2+w,t.y2+w,1.7),P(t.x1-w,t.y2+w,1.7)],c:'#c9962e'});}
  const MATS={chip:{top:'#232327',front:'#141416',side:'#1b1b1e'},tant:{top:'#d9a419',front:'#8a6a0a',side:'#b8890f'},
    elec:{top:'#9aa3b5',front:'#5a6270',side:'#767f92'},led:{top:'#e02020',front:'#801010',side:'#b01414'},
    steel:{top:'#c8ccd2',front:'#7a7e85',side:'#9ea3ab'},plastic:{top:'#1e1e22',front:'#101012',side:'#161618'},
    copper:{top:'#d9a832',front:'#8a6a1a',side:'#b8891f'}};
  for(const r in st.parts){const p=st.parts[r];
    const cols=MATS[p.mat]||MATS.chip;
    for(const bd of (p.bodies||[{w:p.w-0.6,h:p.h-0.6,z:1.6,hgt:p.h3d||1,dx:0,dy:0}])){
      box(p.x+bd.dx-bd.w/2,p.y+bd.dy-bd.h/2,bd.z,p.x+bd.dx+bd.w/2,p.y+bd.dy+bd.h/2,bd.z+bd.hgt,cols);}}
  faces.sort((a,b)=>a.z-b.z);
  for(const f of faces){ctx.fillStyle=f.c;ctx.beginPath();ctx.moveTo(f.p[0][0],f.p[0][1]);for(let i=1;i<f.p.length;i++)ctx.lineTo(f.p[i][0],f.p[i][1]);ctx.closePath();ctx.fill();ctx.strokeStyle='rgba(0,0,0,.35)';ctx.stroke();}
}
function renderAll(){if(!S)return;view=drawPCB(S.cur,1);drawSCH(S);rot+=0.003;draw3D(S.cur,rot);}
let rot=0.6;setInterval(renderAll,50);
// animation: tween parts from->to, reveal traces
function animate(frames,traces,done){
  cancelAnimationFrame(anim);
  const from=S.cur.parts;let i=0;
  function step(){
    if(i>=frames.length){S.cur.traces=[];let n=0;
      (function grow(){n+=3;S.cur.traces=traces.slice(0,n);if(n<traces.length)anim=requestAnimationFrame(grow);else{S.cur.traces=traces;done&&done();}})();return;}
    const f=frames[i],to=f.pos;let k=0;const N=14;
    (function tw(){k++;const e=ease(k/N);
      for(const r in to){const a=from[r]||to[r],b=to[r];
        S.cur.parts[r]={...S.cur.parts[r],x:a[0]+(b[0]-a[0])*e,y:a[1]+(b[1]-a[1])*e};}
      $('cost').textContent=`cost ${f.cost}`;
      if(k<N)anim=requestAnimationFrame(tw);else{for(const r in to)S.cur.parts[r]={...S.cur.parts[r],x:to[r][0],y:to[r][1]};i++;step();}})();}
  step();
}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return r.json();}
function highlight(){
  const ed=$('ed');if(document.activeElement===ed)return; // don't clobber caret
  const t=ed.innerText;let h=t.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  h=h.replace(/(^|\n)(board|part|net|use|fix|keep|route|trace|power|silk|join|as|on|at|near|match|diff|pour|keepout|cutout|hole|bend|stiffener|block|instance|end|nc|sim|x|board)(?=[\\s]|$)/g,'$1<span class=tok-k>$2</span>');
  h=h.replace(/(#[^\n]*)/g,'<span class=tok-c>$1</span>');
  // note: lightweight; full tokenize on load only
  ed.innerHTML=h;
}
let deb=null;
$('ed').addEventListener('input',()=>{clearTimeout(deb);deb=setTimeout(push,400);});
async function push(){
  const text=$('ed').innerText;
  const r=await api('/build',{text,placer:$('placer').value,router:$('router').value,fab:$('fab').value,silk:$('silk').value});
  if(r.error){$('stat').textContent=r.error;$('stat').className='err';return;}
  $('stat').textContent='';applyState(r,false);
}
function applyState(r,live){
  S=r;S.cur={parts:JSON.parse(JSON.stringify(r.parts)),traces:[]};
  if(live&&r.frames&&r.frames.length)animate(r.frames,r.traces,()=>{drawDRC(r);});
  else{S.cur.traces=r.traces;$('cost').textContent=`cost ${r.cost}`;drawDRC(r);}
  if(document.activeElement!==$('ed'))setEditor(r.text);
}
function drawDRC(r){
  const d=$('drc');let h='';
  if(r.errors.length)h+=r.errors.map(e=>`<div class=err>✗ ${e}</div>`).join('');
  else h+='<div class=ok>✓ DRC clean ('+r.fab+')</div>';
  h+=r.warnings.slice(0,5).map(w=>`<div class=warn>~ ${w}</div>`).join('');
  if(r.sim&&Object.keys(r.sim).length)h+='<div class=ok>⚡ '+Object.entries(r.sim).map(([n,v])=>`${n}=${v}V`).join(' ')+'</div>';
  d.innerHTML=h;
  drawTidy(r);
}
function tidyVal(v){
  if(v===null||v===undefined)return '<span class=dim>n/a</span>';
  if(typeof v==='number')return Number.isInteger(v)?String(v):v.toFixed(3);
  if(typeof v==='object')return Object.entries(v).map(([a,c])=>`${a}=${c}`).join(', ');
  return String(v);
}
function drawTidy(r){
  const t=r.tidy||{};
  $('tidycov').textContent=t.coverage?`(${t.coverage})`:'';
  const rows=Object.entries(t).filter(([k])=>k!=='coverage'&&k!=='routed_segs')
    .map(([k,v])=>`<div><span class=dim>${k}</span> ${tidyVal(v)}</div>`).join('');
  $('tidy').innerHTML=rows;
}
function setEditor(t){$('ed').innerText=t;}
// drag parts on pcb
(()=>{const c=$('pcb');let drag=null;
c.addEventListener('mousedown',e=>{if(!S)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const r in S.cur.parts){const p=S.cur.parts[r];
    const x=view.ox+p.x*view.s,y=view.oy+(S.bh-p.y)*view.s;
    if(Math.abs(mx-x)<p.w*view.s/2+4&&Math.abs(my-y)<p.h*view.s/2+4){drag=r;break;}}});
c.addEventListener('mousemove',e=>{if(!drag)return;const R=c.getBoundingClientRect();
  const bx=((e.clientX-R.left)-view.ox)/view.s,by=S.bh-((e.clientY-R.top)-view.oy)/view.s;
  const p=S.cur.parts[drag];p.x=Math.round(bx*10)/10;p.y=Math.round(by*10)/10;});
c.addEventListener('mouseup',async()=>{if(!drag)return;const r=drag;drag=null;
  const p=S.cur.parts[r];
  const lines=$('ed').innerText.split('\n').filter(l=>!/^fix\s+\S+\s+at\s/.test(l)||!l.startsWith('fix '+r+' '));
  // drop fix line right after board/use block
  let idx=lines.findIndex(l=>/^(part|net|fix|keep|route|trace|power|silk)\b/.test(l));if(idx<0)idx=lines.length;
  lines.splice(idx,0,`fix ${r} at ${p.x} ${p.y}`);
  $('ed').innerText=lines.join('\n');push();});
c.addEventListener('dblclick',()=>{ // unpin: remove fix, let solver place freely
  if(!S||!S.cur||!S.cur.hover)return;
  const r=S.cur.hover,lines=$('ed').innerText.split('\n')
    .filter(l=>!/^fix\s+\S+\s+at\s/.test(l)||!l.startsWith('fix '+r+' '));
  if(lines.length!==$('ed').innerText.split('\n').length){$('ed').innerText=lines.join('\n');push();}});
c.addEventListener('mousemove',e=>{if(!S||drag)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  S.cur.hover=null;
  for(const r in S.cur.parts){const p=S.cur.parts[r];
    const x=view.ox+p.x*view.s,y=view.oy+(S.bh-p.y)*view.s;
    if(Math.abs(mx-x)<p.w*view.s/2+4&&Math.abs(my-y)<p.h*view.s/2+4){S.cur.hover=r;break;}}});
})();
$('solve').onclick=async()=>{const r=await api('/solve',{placer:$('placer').value,router:$('router').value});applyState(r,true);};
// undo/redo: server keeps text history (git-style log); undo restores + rebuilds
async function hist(op){
  const r=await api(op,{});
  if(r.error){$('stat').textContent=r.error;$('stat').className='err';return;}
  $('stat').textContent='';setEditor(r.text);applyState(r,false);
}
$('undo').onclick=()=>hist('/undo');
$('redo').onclick=()=>hist('/redo');
$('ed').addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey){e.preventDefault();hist('/undo');}
  else if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='y'||(e.key.toLowerCase()==='z'&&e.shiftKey))){e.preventDefault();hist('/redo');}
});
$('theme').onclick=()=>{theme=theme==='dark'?'light':'dark';document.body.className=theme==='light'?'light':'';$('theme').textContent=theme==='dark'?'light':'dark';};
$('placer').onchange=$('router').onchange=$('fab').onchange=$('silk').onchange=push;
(async()=>{const r=await api('/init',{});
  $('placer').innerHTML=r.placers.map(p=>`<option>${p}</option>`).join('');
  $('router').innerHTML=r.routers.map(p=>`<option>${p}</option>`).join('');
  $('silk').innerHTML=r.silks.map(p=>`<option ${p===r.silk?'selected':''}>${p}</option>`).join('');
  $('fab').innerHTML=r.fabs.map(p=>`<option>${p}</option>`).join('');
  setEditor(r.text);applyState(r,false);})();
</script></body></html>
"""


def _sch_state(b: Board) -> dict[str, object]:
    """Schematic geometry for the canvas: same sch_layout() the SVG
    renderer uses, so both pictures always agree."""
    from ocdcircuit.plugins import sch_layout
    lay = sch_layout(b)
    return {"order": lay["order"], "px": lay["px"], "rail_y": lay["rail_y"],
            "top": lay["top"], "W": lay["W"]}


def board_state(b: Board, text: str, frames: list[dict[str, object]],
                traces: list[dict[str, object]], cost: float,
                drc: dict[str, object]) -> dict[str, object]:
    from ocdcircuit.geom3d import body_material
    from ocdcircuit.parts import bodies_of
    from typing import cast
    parts: dict[str, dict[str, object]] = {}
    lib = b._lib()
    for ref, p in b.parts.items():
        h3d = 1.0
        mats: list[str] = []
        bds: list[dict[str, object]] = []
        for body in bodies_of(p.fp, lib):
            mat = body_material(p.fp, body)
            mats.append(mat)
            if "box" in body:
                box3 = body["box"]
                assert isinstance(box3, (list, tuple))
                w2, h2, bh = _f(box3[0]), _f(box3[1]), _f(box3[2])
                h3d = max(h3d, bh)
                ats = body.get("at", [(0.0, 0.0)])
                assert isinstance(ats, list)
                for at in ats:
                    assert isinstance(at, (list, tuple))
                    bds.append({"w": w2, "h": h2, "z": 1.6 + _f(body.get("z", 0)),
                                "hgt": bh, "dx": _f(at[0]), "dy": _f(at[1])})
            elif "cyl" in body:
                cyl2 = body["cyl"]
                assert isinstance(cyl2, (list, tuple))
                r, bh = _f(cyl2[0]), _f(cyl2[1])
                h3d = max(h3d, bh)
                bds.append({"w": r * 2, "h": r * 2, "z": 1.6 + _f(body.get("z", 0)),
                            "hgt": bh, "dx": 0.0, "dy": 0.0})
        # dominant material = tallest body (what you actually see).
        # bodies pre-rotated into board frame (mirrors geom3d.build).
        rot = 0
        try:
            rot = int(p.attrs.get("rot", "0")) % 360
        except ValueError:
            rot = 0
        pw, ph = (p.h, p.w) if rot in (90, 270) else (p.w, p.h)
        if rot in (90, 270):
            for bd in bds:
                bd["w"], bd["h"] = bd["h"], bd["w"]
                bd["dx"], bd["dy"] = p.rot_xy(cast(float, bd["dx"]),
                                              cast(float, bd["dy"]))
        parts[ref] = {"x": p.x, "y": p.y, "w": pw, "h": ph,
                      "value": p.value, "h3d": h3d,
                      "mat": mats[-1] if mats else "chip", "bodies": bds}
    nets = {n: [f"{r}.{pin}" for r, pin in net.pins] for n, net in b.nets.items()}
    fixed = {str(c["ref"]): True for c in b.constraints if c.get("t") == "fixed"}
    sim_nets: dict[str, float] = {}
    if any(c.get("t") == "sim" for c in b.constraints):
        try:
            res = b.simulate()
            raw = res.get("nets", {})
            assert isinstance(raw, dict)
            sim_nets = {str(k): round(float(v), 3) for k, v in raw.items()
                        if isinstance(v, (int, float))}
        except (ValueError, KeyError):
            sim_nets = {}
    return {"text": text, "parts": parts, "nets": nets, "fixed": fixed,
            "bw": b.width, "bh": b.height, "frames": frames,
            "traces": traces, "cost": round(cost, 1), "sim": sim_nets,
            "errors": drc["errors"], "warnings": drc["warnings"],
            "fab": drc.get("fab", "jlc"), "silk": 1, "sch": _sch_state(b)}


class H(http.server.BaseHTTPRequestHandler):
    src_text: str = ""
    # git-style text history: every good build commits; undo/redo check out.
    # text-level (not Context undo — each build parses fresh). Cap 100.
    hist: list[str] = []
    redo: list[str] = []

    @staticmethod
    def commit(text: str) -> None:
        if not H.hist or H.hist[-1] != text:
            H.hist.append(text)
            H.hist = H.hist[-100:]
        H.redo.clear()

    @staticmethod
    def save() -> None:
        """Persist the .ocd source of truth to disk (edits are real)."""
        try:
            with open(SRC, "w") as f:
                f.write(H.src_text if H.src_text.endswith("\n") else H.src_text + "\n")
        except OSError as e:
            print(f"studio: save failed: {e}", file=sys.stderr)

    def _send(self, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/slots":
            # plugin-inventory surface: slot → [ids] (harness inventory shape)
            inv = {s: SLOTS.report(s) for s in UiSlots.slots}
            self._send(inv)
            return
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        page = PAGE.replace("/*__TOOLBAR__*/", SLOTS.render("toolbar", None))
        page = page.replace("/*__VIEWS__*/", SLOTS.render("view", None))
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/init":
                self._send(self._build(H.src_text, True))
            elif self.path == "/build":
                text = str(req.get("text", H.src_text))
                st = self._build(text, False, req)
                H.src_text = str(st["text"])  # only keep good builds
                H.commit(H.src_text)
                H.save()
                self._send(st)
            elif self.path == "/solve":
                st = self._build(H.src_text, True, req)
                H.src_text = str(st["text"])
                H.commit(H.src_text)
                H.save()
                self._send(st)
            elif self.path == "/undo":
                if len(H.hist) < 2:
                    self._send({"error": "nothing to undo"})
                else:
                    H.redo.append(H.hist.pop())
                    H.src_text = H.hist[-1]
                    H.save()
                    self._send(self._build(H.src_text, False))
            elif self.path == "/redo":
                if not H.redo:
                    self._send({"error": "nothing to redo"})
                else:
                    H.src_text = H.redo.pop()
                    H.commit(H.src_text)
                    H.save()
                    self._send(self._build(H.src_text, False))
            else:
                self.send_response(404)
                self.end_headers()
        except (ValueError, KeyError, OSError) as e:
            self._send({"error": str(e)})

    @staticmethod
    def _build(text: str, animate: bool, req: dict[str, object] | None = None) -> dict[str, object]:
        req = req or {}
        b = agent.loads(text, base=BASE)
        reg = b.plugins()
        placers = reg.list("placer")
        routers = reg.list("router")
        silks = reg.list("silk")
        placer = str(req.get("placer", placers[0])) if placers else "diffusion"
        router = str(req.get("router", routers[0])) if routers else "lroute"
        silksel = str(req.get("silk", silks[1] if len(silks) > 1 else silks[0])) if silks else "full"
        b.fab = str(req.get("fab", getattr(b, "fab", "jlc")))
        frames: list[dict[str, object]] = []
        cost = b.place(placer, seeds=5, iters=500, frames=frames if animate else None,
                       every=25)
        assert isinstance(cost, float)
        rframes: list[dict[str, object]] = []
        n = b.route_board(router, frames=rframes if animate else None)
        assert isinstance(n, int)
        drc = b.check()
        assert isinstance(drc, dict)
        st_tidy = b.score(tidy=True)
        traces = [{"net": t.net, "x1": t.x1, "y1": t.y1, "x2": t.x2,
                   "y2": t.y2, "layer": t.layer, "w": t.width}
                  for t in b.traces]
        st = board_state(b, agent.dumps(b), frames, traces, cost, drc)
        st["tidy"] = st_tidy
        st["placers"] = placers
        st["routers"] = routers
        st["fabs"] = _fab.list_fabs()
        st["silks"] = silks
        st["silk"] = silksel
        H.src_text = str(st["text"])
        return st

    def log_message(self, *a: object) -> None:
        pass


def main() -> None:
    H.src_text = open(SRC).read() if os.path.isfile(SRC) else (
        "board demo 40x30\npart R1 R0805 1k\npart C1 C0805 100n\n"
        "net N: R1.2 C1.2\nnet GND: R1.1 C1.1\n")
    H.commit(H.src_text)  # genesis commit — undo floor
    srv = http.server.HTTPServer(("127.0.0.1", 8077), H)
    print(f"OCD Studio: http://localhost:8077  ({SRC})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
