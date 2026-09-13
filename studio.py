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
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE) if os.path.basename(HERE) != "ocdcircuit" else HERE)

from ocdcircuit import agent  # noqa: E402
from ocdcircuit import fab as _fab  # noqa: E402
from ocdcircuit.circuit import Board  # noqa: E402


def _f(v: object) -> float:
    assert isinstance(v, (int, float, str))
    return float(v)


def _i(v: object, default: int) -> int:
    if v is None:
        return default
    assert isinstance(v, (int, str))
    return int(v)

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "examples", "blinky_555.ocd")
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
#pcbwrap{grid-column:2;grid-row:1/3;position:relative}#schwrap{grid-column:3;grid-row:1}#wrap3d{grid-column:3;grid-row:2}
canvas{width:100%;height:100%;display:block;cursor:grab}
#drc{position:absolute;bottom:0;left:0;right:0;max-height:38%;overflow:auto;background:color-mix(in srgb,var(--panel) 92%,transparent);border-top:1px solid var(--line);padding:6px 10px;font-size:12px}
.err{color:var(--bad)}.warn{color:var(--acc)}.ok{color:var(--ok)}
.tok-k{color:#7fb4ff}.tok-net{color:#e67e22}.tok-part{color:var(--acc)}.tok-c{color:var(--dim)}.tok-num{color:#2ecc71}
body.light .tok-k{color:#0050a0}body.light .tok-net{color:#b9770e}
#cost{color:var(--dim)}
</style></head><body>
<header><b>OCD</b><span>studio</span><span id=cost></span>
<select id=placer title=placer></select><select id=router title=router></select>
<select id=fab title=fab></select><select id=silk title=silk></select>
<button id=theme>light</button><button id=solve>solve ▶</button><span id=stat></span></header>
<main>
<section><h3>.OCD — EDIT ME, BOARD FOLLOWS</h3><div id=ed contenteditable spellcheck=false></div></section>
<section id=pcbwrap><h3>PCB — DRAG PARTS, THEY STAY WHERE DROPPED</h3><canvas id=pcb></canvas><div id=drc></div></section>
<section id=schwrap><h3>SCHEMATIC</h3><canvas id=sch></canvas></section>
<section id=wrap3d><h3>3D</h3><canvas id=t3d></canvas></section>
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
function drawSCH(st){
  const c=$('sch'),ctx=c.getContext('2d'),R=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=R.width*dpr;c.height=R.height*dpr;ctx.scale(dpr,dpr);
  const nets=Object.keys(st.nets),cw=110;ctx.clearRect(0,0,R.width,R.height);
  nets.forEach((n,i)=>{const x=30+i*cw;ctx.strokeStyle=['#e74c3c','#3498db','#2ecc71','#9b59b6'][i%4];ctx.beginPath();ctx.moveTo(x,24);ctx.lineTo(x,R.height-8);ctx.stroke();
    ctx.fillStyle='#e8e8e8';ctx.textAlign='center';ctx.fillText(n,x,16);
    st.nets[n].forEach((pp,j)=>{const y=44+j*30;ctx.fillStyle='#111';ctx.fillRect(x-42,y-10,84,20);ctx.strokeRect(x-42,y-10,84,20);ctx.fillStyle='#e8e8e8';ctx.fillText(pp,x,y+4);});});
}
function draw3D(st,rot){
  const c=$('t3d'),ctx=c.getContext('2d'),R=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=R.width*dpr;c.height=R.height*dpr;ctx.scale(dpr,dpr);
  const cx=R.width/2,cy=R.height/2+20,s=Math.min(R.width/(st.bw+20),R.height/(st.bh+14));
  const P=(x,y,z)=>{const a=rot,dx=x-st.bw/2,dy=y-st.bh/2;
    const rx=dx*Math.cos(a)-dy*Math.sin(a),ry=(dx*Math.sin(a)+dy*Math.cos(a))*0.5-z*0.9;
    return [cx+rx*s,cy+ry*s];};
  const faces=[];
  const slab=[[0,0],[st.bw,0],[st.bw,st.bh],[0,st.bh]].map(p=>P(p[0],p[1],0));
  const top=slab.map(p=>P(p[0],p[1],0)); // computed below properly
  function box(x0,y0,z0,x1,y1,z1,col){const c000=P(x0,y0,z0),c100=P(x1,y0,z0),c110=P(x1,y1,z0),c010=P(x0,y1,z0),c001=P(x0,y0,z1),c101=P(x1,y0,z1),c111=P(x1,y1,z1),c011=P(x0,y1,z1);
    faces.push({z:(z0+z1)/2,p:[c101,c111,c011,c001],c:col});faces.push({z:z0,p:[c000,c100,c110,c010],c:'#0a2a0a'});
    faces.push({z:(z0+z1)/2,p:[c000,c100,c101,c001],c:col});faces.push({z:(z0+z1)/2,p:[c100,c110,c111,c101],c:col});}
  box(0,0,0,st.bw,st.bh,1.6,'#0d5c0d');
  for(const r in st.parts){const p=st.parts[r];const h=p.h3d||1;
    box(p.x-p.w/2+0.3,p.y-p.h/2+0.3,1.6,p.x+p.w/2-0.3,p.y+p.h/2-0.3,1.6+h,'#1a1a1a');}
  faces.sort((a,b)=>a.z-b.z);
  for(const f of faces){ctx.fillStyle=f.c;ctx.beginPath();ctx.moveTo(f.p[0][0],f.p[0][1]);for(let i=1;i<f.p.length;i++)ctx.lineTo(f.p[i][0],f.p[i][1]);ctx.closePath();ctx.fill();ctx.strokeStyle='#000';ctx.stroke();}
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
  h=h.replace(/(^|\n)(board|part|net|use|fix|keep|route|trace|power|silk|join|as|on|at|near|x|board)(?=[\\s]|$)/g,'$1<span class=tok-k>$2</span>');
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
  d.innerHTML=h;
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
})();
$('solve').onclick=async()=>{const r=await api('/solve',{placer:$('placer').value,router:$('router').value});applyState(r,true);};
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


def board_state(b: Board, text: str, frames: list[dict[str, object]],
                traces: list[dict[str, object]], cost: float,
                drc: dict[str, object]) -> dict[str, object]:
    from ocdcircuit.parts import bodies_of
    from typing import cast
    parts: dict[str, dict[str, object]] = {}
    lib = b._lib()
    for ref, p in b.parts.items():
        h3d = 1.0
        for body in bodies_of(p.fp, lib):
            if "box" in body:
                box3 = body["box"]
                assert isinstance(box3, (list, tuple))
                h3d = max(h3d, _f(box3[2]))
            elif "cyl" in body:
                cyl2 = body["cyl"]
                assert isinstance(cyl2, (list, tuple))
                h3d = max(h3d, _f(cyl2[1]))
        parts[ref] = {"x": p.x, "y": p.y, "w": p.w, "h": p.h,
                      "value": p.value, "h3d": h3d}
    nets = {n: [f"{r}.{pin}" for r, pin in net.pins] for n, net in b.nets.items()}
    fixed = {str(c["ref"]): True for c in b.constraints if c.get("t") == "fixed"}
    return {"text": text, "parts": parts, "nets": nets, "fixed": fixed,
            "bw": b.width, "bh": b.height, "frames": frames,
            "traces": traces, "cost": round(cost, 1),
            "errors": drc["errors"], "warnings": drc["warnings"],
            "fab": drc.get("fab", "jlc"), "silk": 1}


class H(http.server.BaseHTTPRequestHandler):
    src_text: str = ""

    def _send(self, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        body = PAGE.encode()
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
                self._send(st)
            elif self.path == "/solve":
                st = self._build(H.src_text, True, req)
                H.src_text = str(st["text"])
                self._send(st)
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
        traces = [{"net": t.net, "x1": t.x1, "y1": t.y1, "x2": t.x2,
                   "y2": t.y2, "layer": t.layer, "w": t.width}
                  for t in b.traces]
        st = board_state(b, agent.dumps(b), frames, traces, cost, drc)
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
    srv = http.server.HTTPServer(("127.0.0.1", 8077), H)
    print(f"OCD Studio: http://localhost:8077  ({SRC})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
