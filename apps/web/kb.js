// Knowledgebase panel behaviour: notes + datasheets, the agent's own files.
// The panel itself is a component (views.js KbPanel); this shapes what it shows
// and keeps the fetches, the file writes and the preference calls.
import { ui } from './store.js';
import { $, api } from './core.js';

let KBDOCS = [];

export async function kbLoad() {
  const r = await api('/kb/list', {});
  if (r.error) { ui.set({kbNote: r.error}); return; }
  KBDOCS = r.docs || [];
  const parts = KBDOCS.reduce((n, d) => n + (d.parts || []).length, 0);
  ui.set({
    kbNote: `${KBDOCS.length} document${KBDOCS.length === 1 ? '' : 's'} · `
      + `${parts} part link${parts === 1 ? '' : 's'} · ` + (r.dir || 'kb/'),
    kbRows: KBDOCS.map(d => ({doc: d.name, name: d.name,
      kind: d.kind + (d.source ? ' · from url' : ''), tail: (d.parts || []).join(' '),
      title: d.source ? ('source: ' + d.source) : d.name, start: 1})),
    kbTail: (KBDOCS.length && r.total && r.total > KBDOCS.length)
      ? `… showing ${KBDOCS.length} of ${r.total} documents — ask or search to reach the rest`
      : '',
    kbStat: r.busy ? 'fetching datasheets…'
      : ((r.log || []).length ? (r.log || []).slice(-3).join(' · ') : '')});
}

export async function kbOpen(doc, start) {
  const r = await api('/kb/read', {doc, start: start || 1, lines: 120});
  if (r.error) { ui.set({kbStat: r.error}); return; }
  ui.set({kbView: `${doc}  lines ${r.start}-${r.end} of ${r.total_lines}\n\n${r.text}`});
}

async function kbGo(semantic, answer) {
  const q = $('kbq').value.trim();
  if (!q) { ui.set({kbStat: 'type a question first'}); return; }
  ui.set({kbStat: answer ? 'asking the local model…' : 'searching kb/…'});
  const r = await api(semantic ? '/kb/ask' : '/kb/search', {q, limit: 8, answer: !!answer});
  if (r.error) { ui.set({kbStat: r.error}); return; }
  const hits = r.passages || r.hits || [];
  ui.set({
    kbStat: `${hits.length} hit${hits.length === 1 ? '' : 's'}`
      + (r.method ? ' · ' + r.method + (r.model ? ' · ' + r.model : '') : '')
      + ((r.note && !r.answer) ? ' · ' + r.note : ''),
    kbRows: hits.map(h => {
      const line = h.line !== undefined ? h.line : h.start;
      return {doc: h.doc || '(no doc)', name: h.doc || '(no doc)',
        kind: line !== undefined ? 'line ' + line : 'passage',
        tail: h.score !== undefined ? String(h.score) : '',
        title: String(h.text || '').slice(0, 400),
        start: Math.max(1, (line || 1) - 3)};
    }),
    kbTail: '',
    kbView: r.answer ? ('answer (from kb/ only)'
      + (r.answer_note ? '\n(' + r.answer_note + ')' : '') + '\n\n' + r.answer)
      : (r.answer_error ? ('(no written answer: ' + r.answer_error + ')') : '')});
}

async function kbPrefs() {
  const r = await api('/kb/prefs', {});
  if (r.error) { ui.set({kbStat: r.error}); return; }
  ui.set({kbPrefs: (r.prefs || []).map(p => ({id: p.id, when: p.when,
    text: p.text, approved: !!p.approved}))});
}

export function initKb() {
  // the rows name their document and line; the click comes back here
  $('kblist').addEventListener('click', e => {
    const b = e.target.closest('button.kbname[data-doc]');
    if (b) kbOpen(b.dataset.doc, +(b.dataset.start || 1));
  });
  $('kbask').onclick = () => kbGo(true, false);
  $('kbgrep').onclick = () => kbGo(false, false);
  $('kbans').onclick = () => kbGo(true, true);
  $('kbaddbtn').onclick = async () => {
    const src = $('kburl').value.trim();
    if (!src) return;
    ui.set({kbStat: 'adding ' + src + '…'});
    const r = await api('/kb/add', {src});
    if (r.error) { ui.set({kbStat: r.error}); return; }
    ui.set({kbStat: `added ${r.added} (${r.bytes} bytes)`});
    $('kburl').value = ''; kbLoad();
  };
  $('kbfetch').onclick = async () => {
    const r = await api('/kb/fetch', {});
    ui.set({kbStat: r.error || r.note || 'fetch started'});
    kbLoad();
  };
  // preferences: Flux's Knowledge approvals. The file is the store; the buttons
  // flip the `# ok` suffix and the agent reads what is approved.
  $('kbprefsbtn').onclick = async () => {
    const open = ui.state.kbPrefsOpen;
    ui.set({kbPrefsOpen: !open});
    if (!open) kbPrefs();
  };
  $('kbprefslist').addEventListener('click', async e => {
    const b = e.target.closest('button[data-pref]');
    if (!b) return;
    const x = await api('/kb/prefs/set',
      {id: +b.dataset.pref, approved: b.dataset.approve === '1'});
    if (x.error) ui.set({kbStat: x.error}); else kbPrefs();
  });
  $('kbprefsgo').onclick = async () => {
    if ($('kbprefsgo').disabled) return;
    $('kbprefsgo').disabled = true;
    try {
      const r = await api('/kb/prefs/add',
        {when: $('kbwhen').value, text: $('kbwhat').value});
      if (r.error) { ui.set({kbStat: r.error}); return; }
      $('kbwhen').value = ''; $('kbwhat').value = ''; kbPrefs();
    } finally { $('kbprefsgo').disabled = false; }
  };
  $('kbq').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); kbGo(true, false); }
  });
  $('kburl').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('kbaddbtn').click(); }
  });
  kbLoad();
  setInterval(() => {
    if (ui.state.kbStat.startsWith('fetching')) kbLoad();
  }, 3000);
}
