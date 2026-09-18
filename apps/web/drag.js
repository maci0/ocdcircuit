// PCB gestures: drag a part (pin it), shift-drag a trace (shove preview),
// alt-click a trace (rip + reroute), right-click (rotate), double-click
// (unpin). The board, the busy helper and the board writer are injected
// (initDrag); the preview offset belongs to the canvas.
import { $, api } from './core.js';
import { view, segDragNow, setSegDrag } from './canvas.js';
import { partShown } from './visibility.js';
import { rotRefs, unpinRefs } from './editor.js';

let H = null; // {board, withBusy, statMsg, applyState, push, setEditor, markDirty}

// drag parts on pcb
export function initDrag(deps) {
  H = deps;
  const c = $('pcb');
  let drag=null,dragGroup=null;
function hit(mx,my){for(const r in H.board().parts){
  if(!H.board().parts[r]||!partShown(r,H.board()))continue; // hidden parts are not targets
  const p=H.board().parts[r];
  const x=view.ox+p.x*view.s,y=view.oy+(S.bh-p.y)*view.s;
  if(Math.abs(mx-x)<p.w*view.s/2+4&&Math.abs(my-y)<p.h*view.s/2+4)return r;}return null;}
function hitTrace(mx,my){ // nearest segment within 6px → its net
  const h=hitSeg(mx,my);
  return h?h.seg.net:null;}
function hitSeg(mx,my){ // nearest segment object (for drag preview)
  if(!H.board()||!H.board()||!H.board().traces)return null;
  let best=null,bd=36;
  for(const g of H.board().traces){
    const x1=view.ox+g.x1*view.s,y1=view.oy+(S.bh-g.y1)*view.s;
    const x2=view.ox+g.x2*view.s,y2=view.oy+(S.bh-g.y2)*view.s;
    const dx=x2-x1,dy=y2-y1,L2=dx*dx+dy*dy;
    const t=L2?Math.max(0,Math.min(1,((mx-x1)*dx+(my-y1)*dy)/L2)):0;
    const dd=(mx-x1-t*dx)**2+(my-y1-t*dy)**2;
    if(dd<bd){bd=dd;best=g;}}
  return best?{seg:best}:null;}
c.addEventListener('mousedown',async e=>{if(!H.board())return;const R=c.getBoundingClientRect();
  if(e.altKey){ // alt-click a trace: rip + re-route that net
    const net=hitTrace(e.clientX-R.left,e.clientY-R.top);
    if(net)await H.withBusy(c,`rerouting ${net}…`,async()=>{
      const r=await api('/reroute',{net});
      if(r.error){H.statMsg(r.error);return;}
      H.statMsg(r.retried?`${net} re-routed`:`${net} still blocked`,r.retried);H.applyState(r,true);});
    return;}
  if(e.shiftKey){ // shift-drag a trace: preview the shove, release re-routes
    const h=hitSeg(e.clientX-R.left,e.clientY-R.top);
    if(h){setSegDrag({seg:h.seg,dx:0,dy:0,
      x0:e.clientX-R.left,y0:e.clientY-R.top});H.markDirty();}
    return;}
  drag=hit(e.clientX-R.left,e.clientY-R.top);
  // rigid group: an instanced part drags its whole owner-group (offsets kept)
  dragGroup=null;
  if(drag){const o=H.board().parts[drag].owner;
    if(o)dragGroup=Object.keys(H.board().parts).filter(r=>H.board().parts[r].owner===o);}});
c.addEventListener('mousemove',e=>{const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  const sd=segDragNow();
  if(sd){sd.dx=Math.round(((mx-sd.x0)/view.s)*10)/10;
    sd.dy=Math.round(((sd.y0-my)/view.s)*10)/10;H.markDirty();}
  else if(drag){const p=H.board().parts[drag];
    const nx=Math.round(((mx-view.ox)/view.s)*10)/10,ny=Math.round((S.bh-(my-view.oy)/view.s)*10)/10;
    const dx=nx-p.x,dy=ny-p.y;p.x=nx;p.y=ny;
    if(dragGroup)for(const r of dragGroup){if(r===drag)continue;
      const q=H.board().parts[r];q.x=Math.round((q.x+dx)*10)/10;q.y=Math.round((q.y+dy)*10)/10;}
    dirty=true;}
  else if(H.board())H.board().hover=hit(mx,my);});
c.addEventListener('mouseup',async()=>{
  if(segDragNow()){const net=segDragNow().seg.net;setSegDrag(null);H.markDirty();
    await H.withBusy(c,`rerouting ${net}…`,async()=>{
      const r=await api('/reroute',{net});
      if(r.error){H.statMsg(r.error);return;}
      H.statMsg(r.retried?`${net} re-routed`:`${net} still blocked`,r.retried);H.applyState(r,true);});
    return;}
  if(!drag)return;const moved=dragGroup||[drag];dragGroup=null;const r=drag;drag=null;
  const gone=new Set(moved);
  const lines=$('ed').innerText.split('\n').filter(l=>{const m=l.match(/^fix\s+(\H.board()+)\s+at\s/);return !m||!gone.has(m[1]);});
  // drop fix lines right after board/use block (group order kept)
  let idx=lines.findIndex(l=>/^(part|net|fix|keep|route|trace|power|silk)\b/.test(l));if(idx<0)idx=lines.length;
  moved.forEach((rr,i)=>{const p=H.board().parts[rr];lines.splice(idx+i,0,`fix ${rr} at ${p.x} ${p.y}`);});
  // The pin is written either way (it is the truth about where the part is),
  // but re-placing thousands of parts is minutes: ask before hanging the page.
  $('ed').innerText=lines.join('\n');
  if(H.board()&&H.board()&&H.board().dense&&!confirm(
      `Re-place ${Object.keys(H.board().parts).length} parts around the pin?\n\n`
      +'This runs the placer over a dense board and can take minutes.\n'
      +'Cancel keeps the pin and re-loads from the file instead.')){
    loadBoard();return;}
  H.push();});
c.addEventListener('dblclick',()=>{ // unpin: remove fix (whole group if instanced)
  if(!H.board()||!H.board()||!H.board().hover)return;
  const r=H.board().hover,o=H.board().parts[r].owner;
  unpinRefs(new Set(o?Object.keys(H.board().parts).filter(k=>H.board().parts[k].owner===o):[r]));});
c.addEventListener('contextmenu',e=>{ // right-click: rotate here, unpin there
  e.preventDefault();if(!H.board()||!H.board())return;const R=c.getBoundingClientRect();
  const r=hit(e.clientX-R.left,e.clientY-R.top);
  if(!r)return; // empty board: browser menu stays suppressed, nothing to do
  const o=H.board().parts[r].owner;
  rotRefs(new Set(o?Object.keys(H.board().parts).filter(k=>H.board().parts[k].owner===o):[r]));});
}
