// Built-in workshop panels — Preact + htm (no build step; see html.js).
// Moved out of the Python slot lambdas in apps/studio.py. These render once
// and are never re-rendered: every canvas, tree, list and readout inside them
// is owned by legacy.js, which fills these exact ids after mount. Plugins keep
// contributing markup through the Python slot registry, which mounts once into
// the #pluginpanels / #pluginleft islands.
// htm rule: void elements must self-close (<input ... />, <img ... />).
import html from './html.js';
import { ChatPanel, CuRows, MarkRows, PartBlock, DrcStrip, TidyBlock,
         TreePanel, VcsPanel } from './views.js';

const FileTree = () => html`<${TreePanel} />`;

const Chat = () => html`<${ChatPanel} />`;

const EditorPanel = () => html`<section id=edwrap>
  <header class=panel-head><span class=panel-title>job file</span>
    <span class=panel-note id=srcnote>board.ocd · saved on every good build</span>
    <span class=panel-note>edit here or drag on the PCB · rebuilds in 0.4s</span></header>
  <div id=ed contenteditable spellcheck=false role=textbox aria-multiline=true
    aria-label=".ocd source, edits rebuild the board"></div>
  <div id=srcpanels>
    <${FileTree} />
    <${Chat} />
    <span id=pluginleft style="display:contents"></span>
  </div></section>`;

const GalleryPanel = () => html`<section id=galwrap style="display:none">
  <header class=panel-head><span class=panel-title>candidates</span>
    <span class=panel-note>click one to adopt it, then drag it on the PCB to nudge and pin</span>
    <span class=panel-note>job file 1F-04 · placer diffusion · 1 seed × 400 iters</span></header>
  <div id=gal></div></section>`;

const PcbPanel = () => html`<section id=pcbwrap>
  <header class=panel-head><span class=panel-title>PCB</span>
    <span class=panel-note>drag a part to pin it · double-click unpins · right-click rotates · alt-click a trace re-routes its net · shift-drag a trace previews the shove</span>
    <details id=layerbox title="show or hide layers and marks on this canvas">
      <summary>layers</summary>
      <div id=layers role=group aria-label="visible layers">
        <span class=lbl>copper</span><${CuRows} />
        <span class=lbl>marks</span><${MarkRows} />
        <button id=layersall type=button>show all</button></div></details>
    <details id=partbox title="show or hide individual parts on this canvas">
      <summary>parts</summary>
      <div id=partpanel role=group aria-label="visible parts">
        <${PartBlock} /></div></details></header>
  <div class=platewrap><canvas id=pcb role=img aria-label="PCB layout"></canvas>
    <${DrcStrip} /></div></section>`;

const SchPanel = () => html`<section id=schwrap>
  <header class=panel-head><span class=panel-title>schematic</span>
    <span class=panel-note>click a pin then a net to rewire · alt-click drops a pin · double-click a label renames it</span></header>
  <canvas id=sch role=img aria-label="schematic"></canvas></section>`;

const InspectorPanel = () => html`<section id=wrap3d>
  <header class=panel-head><span class=panel-title>3D</span>
    <span class=panel-note>click to spin</span></header>
  <canvas id=t3d role=img aria-label="3D board preview"></canvas>
  <header class=panel-head><span class=panel-title>x-ray</span>
    <span class=panel-note>reference + fab scan check</span></header>
  <div id=xraybar><input id=xrayfile type=file accept="image/png,.png"
    aria-label="fab x-ray PNG to compare against the design" />
    <button id=xraysvg type=button title="the reference x-ray view">reference</button>
    <button id=xraygo type=button class=primary title="compare the chosen scan against the design">compare</button>
    <label>dx <input id=xraydx value=0 size=3 aria-label="scan x offset mm" /></label>
    <label>dy <input id=xraydy value=0 size=3 aria-label="scan y offset mm" /></label>
    <label>sc <input id=xraysc value=1 size=4 aria-label="scan scale" /></label>
    <label>cu <input id=xraythr value=100 size=3 aria-label="copper brightness cutoff" /></label></div>
  <div id=xraystat role=status aria-live=polite></div>
  <div id=xraydivs></div>
  <${TidyBlock} /></section>`;

const ScanPanel = () => html`<section id=scanwrap>
  <header class=panel-head><span class=panel-title>photo scan</span>
    <span class=panel-note>photos of a real board → draft design</span></header>
  <div id=scanbar>
    <input id=scanfiles type=file multiple accept="image/*"
      aria-label="photos of the board, both sides" />
    <label>mm <input id=scanmm size=4 value="" aria-label="board width in mm, if known" /></label>
    <button id=scango type=button class=primary
      title="stitch, enhance, and reverse-engineer">analyse</button></div>
  <div id=scanbar2>
    <input id=scannote type=search aria-label="what this board is"
      placeholder="what is it? e.g. scope PSU pulled from a dead unit" />
    <input id=scandocs type=file multiple accept=".pdf,.txt,.md"
      aria-label="manual or datasheet" /></div>
  <div id=scanstat role=status aria-live=polite
    >name files with “top” / “bottom” so the sides are split ·
    shoot whole-board frames plus mid-range ones; very tight close-ups often fail to line up</div>
  <div id=scanq></div>
  <div id=scanview hidden>
    <div id=scanviewbar>
      <label>side <select id=scanside aria-label="board side to view">
        <option value=top>top</option><option value=bottom>bottom</option>
        </select></label>
      <label>view <select id=scanviewkind aria-label="which enhancement to show">
        <option value=stitch>photo</option><option value=contrast>markings</option>
        </select></label>
      <label class=scancheck><input id=scanlabels type=checkbox checked /> labels</label>
      <label class=scancheck><input id=scanboxes type=checkbox checked /> outlines</label>
      <span id=scanhover role=status aria-live=polite></span></div>
    <div class=scanstage><img id=scanimg alt="stitched board photo" />
      <svg id=scansvg aria-hidden=true></svg></div>
    <div id=scanparts></div></div>
  <pre id=scanout></pre>
  </section>`;

const KbPanel = () => html`<section id=kbwrap>
  <header class=panel-head><span class=panel-title>knowledgebase</span>
    <span class=panel-note id=kbnote>kb/ beside the board</span>
    <span class=panel-note>notes + datasheets · the agent reads the same files</span></header>
  <div id=kbbar>
    <input id=kbq type=search aria-label="ask the knowledgebase"
      placeholder="ask: what is the input voltage range?  (or a search term)" />
    <button id=kbask class=primary type=button title="passages that answer the question (embeddings)">ask</button>
    <button id=kbgrep type=button title="exact term match, one line per hit">search</button>
    <button id=kbans type=button title="also write an answer with the local model">answer</button></div>
  <div id=kbadd>
    <input id=kburl type=search aria-label="datasheet url or file path"
      placeholder="https://…/datasheet.pdf  or  path/to/note.md" />
    <button id=kbaddbtn type=button title="copy or download it into kb/">add</button>
    <button id=kbfetch type=button title="download the datasheet for every datasheet= / lcsc= part">fetch datasheets</button>
    <button id=kbprefsbtn type=button title="preferences the agent follows without being asked">preferences</button></div>
  <div id=kbprefs style="display:none"><div id=kbprefslist></div>
    <div id=kbprefsadd><input id=kbwhen aria-label="when this applies" placeholder="when placing connectors" />
      <input id=kbwhat aria-label="what to prefer" placeholder="put them on the board edge" />
      <button id=kbprefsgo type=button title="save as a new preference">remember</button></div></div>
  <div id=kbstat role=status aria-live=polite>click a document to read it</div>
  <div id=kblist></div>
  <pre id=kbview></pre>
  </section>`;

// slot render order (panel order in the cockpit)
export const Panels = () => html`<${EditorPanel} /><${PcbPanel} /><${SchPanel} /><${InspectorPanel} /><${GalleryPanel} /><${VcsPanel} /><${ScanPanel} /><${KbPanel} />`;
