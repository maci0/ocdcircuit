// Hero art: the landing page's one authored visual (canvas, not the VDOM).
// Moved verbatim from apps/studio.py. Two canvases, one paint().
export function paint(c){const x=c.getContext('2d');let t=Math.random()*10; // one authored visual, two canvases
function frame(){if(!c.isConnected)return;
const r=c.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);
c.width=Math.max(1,r.width*d);c.height=Math.max(1,r.height*d);
x.setTransform(d,0,0,d,0,0);const W=r.width,H=r.height;t+=0.004;
const g=x.createLinearGradient(0,0,W,H);
g.addColorStop(0,'#0a1410');g.addColorStop(.55,'#0c1a30');g.addColorStop(1,'#0a0f14');
x.fillStyle=g;x.fillRect(0,0,W,H);
x.fillStyle='rgba(255,255,255,.5)';
for(let i=0;i<90;i++){x.globalAlpha=.12+((i*13)%10)/60;
x.fillRect((i*97.3)%W,(i*57.7)%H,1.2,1.2);}
x.globalAlpha=1;
const bw=Math.max(200,W*.62),bh=Math.max(140,H*.42),ox=(W-bw)/2,oy=(H-bh)/2+20;
x.strokeStyle='rgba(95,216,148,.35)';x.lineWidth=1.5;x.strokeRect(ox,oy,bw,bh);
const cols=['#c0392b','#3a7bd5','#5fd894','#ffd8a0']; // red/blue/signal/key — no category purple
for(let i=0;i<4;i++){const y0=oy+20+i*(bh-40)/3;
x.strokeStyle=cols[i];x.lineWidth=3;x.globalAlpha=.8;
x.beginPath();x.moveTo(ox,y0);
x.bezierCurveTo(ox+bw*.3,y0-40,ox+bw*.6,y0+40,ox+bw,y0-10+((i*29)%30));x.stroke();
const tt=(t+i*.25)%1;
x.fillStyle='#ffd8a0';x.beginPath();
x.arc(ox+bw*tt,y0+Math.sin(tt*6.28+i)*14,3.5,0,7);x.fill();}
x.globalAlpha=1;x.fillStyle='#d9a821';
for(let i=0;i<8;i++){const px=ox+20+i*(bw-40)/7;
x.fillRect(px-3,oy-5,6,4);x.fillRect(px-3,oy+bh+1,6,4);}
if(!matchMedia('(prefers-reduced-motion: reduce)').matches)requestAnimationFrame(frame);}
frame();}

