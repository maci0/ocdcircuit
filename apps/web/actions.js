// Toolbar actions and the shell's chrome: solve/download/sim/stamp/undo, the
// file import, the theme and view tabs, the chat panel toggle, the editor
// shortcuts and the disk-change strip. Everything below is a click handler or
// a one-shot fetch — the runtime is injected (initActions).
import { $, api } from './core.js';
import { ui } from './store.js';
import { commitBoard } from './vcs.js';
import { reloadTree } from './tree.js';
import { setEditor } from './editor.js';
import { renderAll, markDirty } from './canvas.js';
import { drawDRC, drawFeas } from './status.js';
import { genCands } from './gallery.js';

let H = null; // {board, withBusy, statMsg, applyState, push, loadVCS, setCollabRev?}

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
$('chatbtn').onclick=()=>{
  document.body.classList.toggle('chatty');
  const on=document.body.classList.contains('chatty');
  ui.set({chat:on});
  if(on)$('ask').focus();
};

export function initActions(deps) {
  H = deps;
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
  // import: file picker in the project panel → base64 to /fs/import →
  // import_fp/import_sym by extension (key sniffed server-side). Same
  // FileReader pattern as the x-ray upload.
  if($('importfile'))$('importfile').onchange=()=>{const f=$('importfile').files[0];if(!f)return;
    const rd=new FileReader();rd.onload=async()=>{
      const data=String(rd.result).split(',')[1]||'';
      ui.set({importStat:'importing '+f.name+'…'});
      const r=await api('/fs/import',{name:f.name,data});
      ui.set({importStat:r.error||r.note||('imported '+f.name)});
      if(!r.error){reloadTree();if(r.text){setEditor(r.text);H.push();}}};
    rd.readAsDataURL(f);$('importfile').value='';};
  $('solve').onclick=async()=>{
    await H.withBusy($('solve'),'solving…',async()=>{
      const r=await api('/solve',{placer:$('placer').value,router:$('router').value,full:true});
      if(r.error){H.statMsg(r.error);return;}
      H.statMsg('');H.applyState(r,true);
    });
  };
  $('dice').onclick=genCands;
  $('fab_dl').onclick=async()=>{
    await H.withBusy($('fab_dl'),'building zip…',async()=>{
      const r=await api('/export',{});
      if(r.error){H.statMsg(r.error);return;}
      const a=document.createElement('a');
      a.href='data:application/zip;base64,'+r.zip;a.download=r.name;a.click();
      H.statMsg(`${r.name} (${(r.bytes/1024).toFixed(0)}KB)`,true);
    });
  };
  $('dl').onclick=async()=>{ // cycle svg → sch → png → xray (shift-click backwards)
    const keys=['svg','sch','png','xray'];
    dlIdx=(dlIdx+((window.event&&window.event.shiftKey)?-1:1)+keys.length)%keys.length;
    const key=keys[dlIdx];
    ui.set({dlLabel:`download ${key}`}); // keep a word label (glyph alone is not a label)
    await H.withBusy($('dl'),`download ${key}…`,async()=>{
      const r=await api('/render',{key});
      if(r.error){H.statMsg(r.error);return;}
      const a=document.createElement('a');
      a.href=r.bin?`data:application/octet-stream;base64,${r.data}`
        :`data:image/svg+xml,${encodeURIComponent(r.data)}`;
      if(key==='png'&&!r.bin)a.href=`data:image/png;base64,${r.data}`;
      a.download=r.name;a.click();H.statMsg(r.name,true);
    });
  };
  let dlIdx=0;
  let simWhat='dc';
  $('simbtn').onclick=async()=>{ // dc → tran → ac cycle on shift-click
    if(window.event&&window.event.shiftKey)
      simWhat=simWhat==='dc'?'tran':simWhat==='tran'?'ac':'dc';
    ui.set({simLabel:`sim ${simWhat}`,
      simTitle:`simulate the current board (shift-click: ${simWhat==='dc'?'tran':simWhat==='tran'?'ac':'dc'})`});
    await H.withBusy($('simbtn'),`sim ${simWhat}…`,async()=>{
      const r=await api('/simulate',{what:simWhat});
      if(r.error){H.statMsg(r.error);return;}
      if(r.sim&&Object.keys(r.sim).length)S.sim=r.sim;
      if(r.tran&&Object.keys(r.tran).length)S.tran=r.tran;
      if(r.ac&&Object.keys(r.ac).length)S.ac=r.ac;
      H.statMsg('',true);drawDRC(H.board());
    });
  };
  // Ω calculators: same math as ocdcircuit/calc.py, instant, no round-trip
  if($('qgo'))$('qgo').onclick=async()=>{ // fab price comparison for the open board
    await H.withBusy($('qgo'),'comparing…',async()=>{
      const q=Math.max(1,parseInt($('qqty').value)||5);
      const r=await api('/quote',{qty:q,no_parts:$('qbare').checked});
      if(r.error){ui.set({quoteRows:[],quoteNote:[{cls:'err',text:r.error}]});
        H.statMsg(r.error);return;}
      const _asm=(r.rows||[]).find(x=>x.asm)||{};
      const _alt=_asm.via_alt||[],_unp=_asm.unpriced||[],_low=_asm.low_stock||[],_rsk=_asm.risky||[];
      ui.set({
        quoteRows:(r.rows||[]).map(x=>({fab:x.fab,logo:x.logo||'',bare:x.bare_total,
          asm:x.asm_total||'',per:x.asm_per_board||''})),
        quoteNote:[
          ...(_alt.length?[{cls:'dim',text:`via substitute: ${_alt.join(', ')}`}]:[]),
          ...(_unp.length?[{cls:'warn',text:`unpriced: ${_unp.join(', ')}`}]:[]),
          ...(_low.length?[{cls:'warn',text:`low stock: ${_low.join(', ')}`}]:[]),
          ...(_rsk.length?[{cls:'warn',text:`lifecycle risk: ${_rsk.join(', ')}`}]:[]),
          {cls:'dim',text:`${r.stamp} estimates — re-verify before ordering`}]});
      H.statMsg('');
    });
  };
  $('doc').addEventListener('toggle',async()=>{ // lazy: check on first open
    if(!$('doc').open||$('docout').dataset.done)return;
    const r=await api('/doctor',{});
    if(r.error){ui.set({doc:[{cls:'err',text:r.error}]});return;}
    ui.set({doc:[{cls:r.ok?'ok':'warn',
        text:r.ok?'✓ all systems':'degraded: features fall back, nothing crashes'},
      ...r.checks.map(c=>({cls:c.ok?'ok':'err',text:`${c.ok?'✓':'✗'} ${c.name}`,
        detail:c.detail||''}))]});
    $('docout').dataset.done='1';
  });
  // undo/redo: server keeps text history (git-style log); undo restores + rebuilds
  // x-ray: reference download + fab-scan upload vs the design (score + boxes)
  async function hist(op){
    const r=await api(op,{});
    if(r.error){H.statMsg(r.error);return;}
    H.statMsg('');W.setEditor(r.text);W.applyState(r,false);
  }
  $('undo').onclick=()=>hist('/undo');
  $('redo').onclick=()=>hist('/redo');
  $('diffprev').onclick=async()=>{
    const r=await api('/diff_prev',{});
    H.statMsg(r.error||r.diff, !r.error);
  };
  $('stamp').onclick=()=>{ // repeat-layout: stamp another copy of hovered instance
    if(!H.board()||!H.board()||!H.board().hover){H.statMsg('hover an instanced part, then stamp');return;}
    const o=H.board().parts[H.board().hover].owner;
    if(!o){H.statMsg('that part is not in an instance');return;}
    const pre=o.replace(/_$/,'');
    const lines=$('ed').innerText.split('\n');
    const inst=lines.map((l,i)=>({m:l.match(/^instance\s+(\H.board()+)\s+as\s+(\H.board()+?)(?:\s+join\s+(.*))?$/),i}))
      .filter(x=>x.m);
    const src=inst.find(x=>x.m[2]===pre);
    if(!src){H.statMsg(`no instance line for ${pre}`);return;}
    const stem=pre.replace(/\d+$/,'')||pre;
    const nums=inst.map(x=>{const m=x.m[2].match(/(\d+)$/);return m?parseInt(m[1]):0;});
    const next=stem+(Math.max(0,...nums)+1);
    const join=src.m[3]?` join ${src.m[3]}`:'';
    const at=inst[inst.length-1].i;
    lines.splice(at+1,0,`instance ${src.m[1]} as ${next}${join}`);
    $('ed').innerText=lines.join('\n');H.push();
  };
  $('ed').addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('solve').click();}
    else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();commitBoard();}
    else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey){e.preventDefault();hist('/undo');}
    else if((e.ctrlKey||e.metaKey)&&(e.key.toLowerCase()==='y'||(e.key.toLowerCase()==='z'&&e.shiftKey))){e.preventDefault();hist('/redo');}
  });
}

// the disk-change strip: the user picks the moment to reload
export function initWatch(deps) {
  const W = deps;   // its own host: H belongs to initActions
  // file-watch: poll SRC hash; an external edit banners with one-click
  // reload (never auto: a keystroke debounce may be in flight, and
  // auto-reload would clobber it — the user picks the moment).
  let lastHash=null;
  async function watch(){try{
    // /poll is a GET route: api() POSTs, which answered "unknown path /poll"
    // and left this strip unable to fire. Same-origin fetch carries the cookie.
    const p=await fetch('/poll').then(r=>r.json());
    if(lastHash===null){lastHash=p.hash;return;}
    if(p.hash===lastHash||ui.state.extBanner)return;
    if(p.clean)return;  // our own save (or untouched) — nothing external
    lastHash=p.hash;
    ui.set({extBanner:true});   // views.js renders the strip
  }catch(e){}}
  // one delegated listener: the strip asks, this reloads
  $('app').addEventListener('click',async e=>{
    if(!e.target.closest('#extbanner'))return;
    const r=await api('/reload',{});
    setEditor(r.text);H.applyState(r,false);
    ui.set({extBanner:false});lastHash=null;
  });
  setInterval(watch, 2000);
}

// the shell wiring boot() used to do: logout, theme, view tabs and their
// keyboard pattern (APG), plus restoring the remembered theme and view
export function initChrome() {
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
