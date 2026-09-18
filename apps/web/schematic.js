// Schematic edits → .ocd text (two-way binding): click a pin, then a net, and
// the source line is rebuilt; alt-click drops a pin, double-click renames a
// label or adds a part. Everything goes through the editor text and push().
import { $ } from './core.js';
import { ui } from './store.js';
import { markSchDirty } from './canvas.js';

let H = null; // {board, push}
let schSel = null; // selected "REF.PIN"
export const schSelNow = () => schSel;

function schLines(){return $('ed').innerText.split('\n');}
const schEsc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
// parse net lines: {name, idx, pins:[{tok, li}]} (li = line index)
function schNets(){
  const lines=schLines(),out=[];
  lines.forEach((l,li)=>{let m=l.match(/^(\H.board()+?)((?:\s+[LWlw][\d.]+)*)\s*::\s*(.*)$/);
    if(!m){const m2=l.match(/^net\s+(\H.board()+?)(?:\s+[LWlw][\d.]+)*\s*:\s*(.*)$/);if(m2)m=[m2[0],m2[1],"",m2[2]];}
    if(m)out.push({name:m[1],li,pins:m[3].split(/\s*<-->\s*|\s+/).filter(Boolean).filter(t=>t.includes('.'))});});
  return out;
}
function schCommit(lines){$('ed').innerText=lines.join('\n');H.push();}
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
    if(/^\H.board()+(\s+[LWlw][\d.]+)*\s*::/.test(l))
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
  lines.forEach(l=>{const m=l.match(/^part\s+(\H.board()+)/);if(m)have.add(m[1]);});
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
  if(i<0)i=lines.findIndex(l=>/^\s*(net\s|\H.board()+\s*::)/.test(l));
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
export function initSchematic(deps) {
  H = deps;
  const c = $('sch');
c.addEventListener('mousedown',e=>{if(!H.board()||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const p of S._schmap.pins){
    if(Math.abs(mx-p.x)<7&&Math.abs(my-p.y)<7){
      if(e.altKey){schDropPin(p.pp);schSel=null;markSchDirty();return;}
      schSel=(schSel===p.pp)?null:p.pp;markSchDirty();return;}}
  for(const n of S._schmap.nets){
    if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){
      if(schSel){schMovePin(schSel,n.n);schSel=null;}markSchDirty();return;}}
  if(schSel){schNewNet(schSel);return;}
  schSel=null;markSchDirty();});
c.addEventListener('dblclick',e=>{if(!H.board()||!S._schmap)return;const R=c.getBoundingClientRect(),mx=e.clientX-R.left,my=e.clientY-R.top;
  for(const n of S._schmap.nets)if(Math.abs(mx-n.x)<60&&Math.abs(my-n.y)<9){schRename(n.n);return;}
  schAddPart();});
}
