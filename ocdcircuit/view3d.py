"""Self-contained interactive 3D page: WebGL1, no CDN, no three.js.
Parses our textured glTF (POSITION+NORMAL+TEXCOORD_0, data-URI buffer),
orbit drag + wheel zoom. ~140 lines of JS."""
from __future__ import annotations
from typing import TYPE_CHECKING
import json

if TYPE_CHECKING:
    from .circuit import Board

JS = r"""
const doc=GLTF_DOC, canvas=document.getElementById('v'), gl=canvas.getContext('webgl');
function resize(){canvas.width=innerWidth;canvas.height=innerHeight-40;gl.viewport(0,0,canvas.width,canvas.height);}
addEventListener('resize',resize);resize();
const vs='attribute vec3 p;attribute vec3 n;attribute vec2 u;uniform mat4 m;varying vec3 vN;varying vec2 vU;void main(){gl_Position=m*vec4(p,1.0);vN=n;vU=u;}';
const fs='precision mediump float;uniform sampler2D tx;uniform vec3 l;varying vec3 vN;varying vec2 vU;void main(){vec3 c=texture2D(tx,vU).rgb;float d=max(dot(normalize(vN),l),0.0);gl_FragColor=vec4(c*(0.35+0.65*d),1.0);}';
function sh(t,s){const h=gl.createShader(t);gl.shaderSource(h,s);gl.compileShader(h);return h;}
const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);gl.useProgram(pr);
if(!gl.getProgramParameter(pr,gl.LINK_STATUS))document.title='LINK-FAIL:'+gl.getProgramInfoLog(pr);
const locP=gl.getAttribLocation(pr,'p'),locN=gl.getAttribLocation(pr,'n'),locU=gl.getAttribLocation(pr,'u'),locM=gl.getUniformLocation(pr,'m'),locT=gl.getUniformLocation(pr,'tx'),locL=gl.getUniformLocation(pr,'l');
const raw=atob(doc.buffers[0].uri.split(',')[1]);
const bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
const dv=new DataView(bytes.buffer);
function accData(ai){const acc=doc.accessors[ai],view=doc.bufferViews[acc.bufferView];
  const n=acc.count*(acc.type==='VEC2'?2:3),out=new Float32Array(n);
  for(let i=0;i<n;i++)out[i]=dv.getFloat32(view.byteOffset+i*4,true);return {out,n:acc.count};}
function vbuf(data){const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);return b;}
const meshes=[];
doc.meshes.forEach((mesh,mi)=>{
  const at=mesh.primitives[0].attributes;
  const pos=accData(at.POSITION),nrm=accData(at.NORMAL),uv=accData(at.TEXCOORD_0);
  const tx=doc.textures[mi],img=doc.images[tx.source],iv=doc.bufferViews[img.bufferView];
  // image PNG lives in its own bufferView of the same buffer
  const pngBytes=bytes.slice(iv.byteOffset,iv.byteOffset+iv.byteLength);
  const blob=new Blob([pngBytes],{type:'image/png'});
  const tex=gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D,tex);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([200,200,200,255]));
  const im=new Image();
  im.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,tex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,im);gl.generateMipmap(gl.TEXTURE_2D);};
  im.src=URL.createObjectURL(blob);
  meshes.push({pbuf:vbuf(pos.out),nbuf:vbuf(nrm.out),ubuf:vbuf(uv.out),n:pos.n,tex});
});
// board extents for framing (POSITION accessors only — UV min/max differ)
let mn=[1e9,1e9,1e9],mx=[-1e9,-1e9,-1e9];
doc.meshes.forEach((mesh)=>{const ai=mesh.primitives[0].attributes.POSITION;
  const a=doc.accessors[ai];
  for(let i=0;i<3;i++){mn[i]=Math.min(mn[i],a.min[i]);mx[i]=Math.max(mx[i],a.max[i]);}});
const ctr=[(mn[0]+mx[0])/2,(mn[1]+mx[1])/2,(mn[2]+mx[2])/2];
const span=Math.max(mx[0]-mn[0],mx[1]-mn[1],1);
let yaw=0.6,pitch=0.9,dist=span*2.2;
let drag=null;
canvas.addEventListener('mousedown',e=>{drag=[e.clientX,e.clientY];});
addEventListener('mouseup',()=>drag=null);
addEventListener('mousemove',e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*0.008;pitch=Math.min(1.5,Math.max(0.15,pitch+(e.clientY-drag[1])*0.008));drag=[e.clientX,e.clientY];});
canvas.addEventListener('wheel',e=>{dist*=1+Math.sign(e.deltaY)*0.1;dist=Math.max(span*0.5,dist);e.preventDefault();},{passive:false});
function persp(f,a,n,f_){const t=1/Math.tan(f/2);return [t/a,0,0,0, 0,t,0,0, 0,0,(f_+n)/(n-f_),-1, 0,0,2*f_*n/(n-f_),0];}
function mul(a,b){const o=new Array(16).fill(0);for(let i=0;i<4;i++)for(let j=0;j<4;j++)for(let k=0;k<4;k++)o[j*4+i]+=a[k*4+i]*b[j*4+k];return o;}
function draw(){
  gl.clearColor(0.05,0.05,0.05,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);
  const eye=[ctr[0]+dist*Math.cos(pitch)*Math.cos(yaw),ctr[1]+dist*Math.cos(pitch)*Math.sin(yaw),ctr[2]+dist*Math.sin(pitch)];
  const fw=[ctr[0]-eye[0],ctr[1]-eye[1],ctr[2]-eye[2]],fl=Math.hypot(...fw),f=fw.map(v=>v/fl);
  let r=[-f[1],f[0],0];const rl=Math.hypot(...r)||1;r=r.map(v=>v/rl);
  const u=[f[1]*r[2]-f[2]*r[1],f[2]*r[0]-f[0]*r[2],f[0]*r[1]-f[1]*r[0]];
  const view=[r[0],u[0],-f[0],0, r[1],u[1],-f[1],0, r[2],u[2],-f[2],0,
    -(r[0]*eye[0]+r[1]*eye[1]+r[2]*eye[2]),-(u[0]*eye[0]+u[1]*eye[1]+u[2]*eye[2]),f[0]*eye[0]+f[1]*eye[1]+f[2]*eye[2],1];
  const m=mul(persp(0.7,canvas.width/canvas.height,1,span*10),view);
  gl.uniformMatrix4fv(locM,false,new Float32Array(m));
  gl.uniform3f(locL,0.4,0.5,0.75);
  gl.uniform1i(locT,0);
  for(const mesh of meshes){
    gl.bindBuffer(gl.ARRAY_BUFFER,mesh.pbuf);
    gl.enableVertexAttribArray(locP);gl.vertexAttribPointer(locP,3,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,mesh.nbuf);
    gl.enableVertexAttribArray(locN);gl.vertexAttribPointer(locN,3,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,mesh.ubuf);
    gl.enableVertexAttribArray(locU);gl.vertexAttribPointer(locU,2,gl.FLOAT,false,0,0);
    gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,mesh.tex);
    gl.drawArrays(gl.TRIANGLES,0,mesh.n);
  }
  requestAnimationFrame(draw);
}
draw();
"""


def page(board: Board, gltf_json: str) -> str:
    """Full HTML page with embedded glTF + viewer."""
    doc = json.loads(gltf_json)
    js = JS.replace("GLTF_DOC", json.dumps(doc))
    name = board.name.replace("<", "&lt;")
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>{name} — 3D</title>
<style>html,body{{margin:0;height:100%;background:#0d0d0d;color:#e8e8e8;
font:13px/1.4 monospace;overflow:hidden}}
header{{padding:6px 12px;background:#141414;border-bottom:1px solid #2a2a2a}}
canvas{{display:block;cursor:grab}}</style></head><body>
<header><b>{name}</b> — drag to orbit · wheel to zoom ·
{len(board.parts)} parts · {board.width:g}×{board.height:g}mm</header>
<canvas id=v></canvas>
<script>{js}</script></body></html>
"""


if __name__ == "__main__":
    import sys
    from ocdcircuit import agent
    from ocdcircuit.geom3d import to_gltf
    b = agent.loads(open(sys.argv[1]).read())
    b.place()
    b.route_board()
    open("preview3d.html", "w").write(page(b, to_gltf(b)))
    print("preview3d.html written")
