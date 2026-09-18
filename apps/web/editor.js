// The editor: the .ocd text pane, the selection→highlight wiring, and the two
// text edits the canvas gestures make (unpin a fixed part, rotate one).
// edHl/edPin are mutated in place, so the canvas, the schematic, the parts list
// and the visibility rings can hold the same Sets.
import { $ } from './core.js';

let H = null; // {board, markDirty, push}

export const edHl = new Set(), edPin = new Set(); // refs/pins the selection names

export function setEditor(t) { $('ed').innerText = t; }

export function unpinRefs(gone){ // drop fix lines for refs; true when something left
  const lines=$('ed').innerText.split('\n')
    .filter(l=>{const m=l.match(/^fix\s+(\S+)\s+at\s/);return !m||!gone.has(m[1]);});
  if(lines.length===$('ed').innerText.split('\n').length)return false;
  $('ed').innerText=lines.join('\n');push();return true;}
export function rotRefs(refs){ // rotate 90°: bump rot= on the part line (add or +90)
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

export function edHighlight(){
  if(!H.board()||!H.board())return;
  const sel=window.getSelection();
  const txt=(sel&&!sel.isCollapsed&&sel.anchorNode&&$('ed').contains(sel.anchorNode))?String(sel):'';
  const refs=new Set(),pins=new Set();
  txt.split(/[^\w.]+/).forEach(t=>{const r=t.split('.')[0];
    if(!H.board().parts[r])return;
    refs.add(r);
    if(t!==r)pins.add(t);}); // "U1.7" also rings that pin in the schematic
  if(refs.size===edHl.size&&pins.size===edPin.size
     &&[...refs].every(r=>edHl.has(r))&&[...pins].every(p=>edPin.has(p)))return;
  edHl.clear();refs.forEach(r=>edHl.add(r));
  edPin.clear();pins.forEach(p=>edPin.add(p));
  H.markDirty();
}
document.addEventListener('selectionchange',edHighlight);

export function initEditor(deps) {
  H = deps;
  document.addEventListener('selectionchange', edHighlight);
}
