// The board runtime: the open board's state, the rebuild path (push →
// applyState), the status word, the busy-button helper, the toast, boot() and
// the wiring that hands each module its slice of the runtime.
import { $, api } from './core.js';
import { ui } from './store.js';
import { initTree, setOpenBoard, srcRel } from './tree.js';
import { initVcs, loadVCS, setVcs } from './vcs.js';
import { initKb } from './kb.js';
import { clearProposals, initAgent, msg } from './agent.js';
import { initGallery } from './gallery.js';
import { initScan } from './scan.js';
import { initXray } from './xray.js';
import { initCalc } from './calc.js';
import { edHl, edPin, initEditor, setEditor } from './editor.js';
import { initSchematic, schSelNow } from './schematic.js';
import { drawDRC, drawFeas, drawTidy, initStatus, notePlacement } from './status.js';
import { initVisibility, renderLayers, renderParts } from './visibility.js';
import { animate, initCanvas, markDirty, palette, renderAll, spinBriefly,
         viewNow } from './canvas.js';
import { initDrag } from './drag.js';
import { initActions, initChrome, initWatch } from './actions.js';
import { collabReset, collabRevNow, collabStart, initCollab, setCollabRev } from './collab.js';

let S=null;
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
  const r=await api('/collab/push',{text,rev:collabRevNow(),src:srcRel(),thash:heldThash,
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
  setOpenBoard(f);
  loadVCS();

}

// --- wiring: each module gets the runtime it needs, once --------------------
initCanvas({board:()=>S&&S.cur,schSel:schSelNow});
initSchematic({board:()=>S&&S.cur,push});
initEditor({board:()=>S&&S.cur,markDirty,push});
initStatus({statMsg});
initVisibility({board:()=>S&&S.cur,edHl:()=>edHl,markDirty});
initDrag({board:()=>S&&S.cur,withBusy,statMsg,applyState,push,setEditor,markDirty});
initCollab({markDirty,cancelPush,setEditor,push,toast,
  view:viewNow,board:()=>S,edHl:edHl});
initKb();
initVcs({srcRel:srcRel,statMsg,toast});
initTree({statMsg,toast,cancelPush,clearProposals,collabReset,collabStart,
  applyState,loadVCS,setVcs});
initGallery({board:()=>S,palette:palette,statMsg,applyState,withBusy,drawFeas});
initScan({push,setEditor});
initAgent({applyState,loadVCS,setEditor});
initActions({board:()=>S&&S.cur,withBusy,statMsg,applyState,push,loadVCS});
initWatch({applyState,setEditor,renderAll});
initChrome();
initXray();
initCalc();
(async()=>{await boot();})();
