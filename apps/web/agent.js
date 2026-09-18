// Agent turn: one conversation in the store, rendered by views.js ChatPanel.
// This module owns the entries (prose, the plan checklist, the thought trace,
// the proposal cards), the apply/reject flow, the follow-up chips and the
// busy guard on the send button. applyState, loadVCS and setEditor are
// injected (initAgent): they own the open board, this only asks them to move.
import { ui } from './store.js';
import { $, api } from './core.js';

let d = null; // {applyState, loadVCS, setEditor}

let msgSeq = 0;

function pushMsg(entry) {
  entry.id = ++msgSeq;
  ui.set({msgs: [...ui.state.msgs, entry]});
  return entry;
}

export function msg(who, text) { // one conversation, one ordered list
  const entry = pushMsg({kind: 'msg', who: who === 'you' ? 'me' : who,
    name: who === 'bot' ? 'flux' : who, text: text});
  return {remove() { ui.set({msgs: ui.state.msgs.filter(m => m.id !== entry.id)}); }};
}

function diffLines(text) { // data, not DOM: class per diff line
  return String(text).split('\n').map(ln => ({cls: /^@@|^(\+\+\+|---)/.test(ln) ? 'dl-at'
    : ln.startsWith('+') ? 'dl-add' : ln.startsWith('-') ? 'dl-del' : '', text: ln}));
}

function propose(pr, onQueue) {
  pushMsg({kind: 'prop', prop: pr.id, path: pr.path,
    lines: diffLines(pr.diff || ''), busy: false});
}

function propSet(id, patch) {
  ui.set({msgs: ui.state.msgs.map(m =>
    m.kind === 'prop' && m.prop === id ? {...m, ...patch} : m)});
}

function propDrop(id) {
  ui.set({msgs: ui.state.msgs.filter(m => !(m.kind === 'prop' && m.prop === id))});
}

function setQueue(list) { // a turn may propose several files: one queue, each
  // apply/reject removes its own card without disturbing the others
  ui.set({msgs: ui.state.msgs.filter(m => m.kind !== 'prop')});
  (list || []).forEach(p => propose(p, setQueue));
}

export async function chat(text, auto) {
  text = text.trim();
  const go = $('composer').querySelector('button[type=submit]');
  if (!text || (go && go.disabled) || $('chatclear').disabled) return false;
  if (go) go.disabled = true;
  $('chatclear').disabled = true;
  msg('you', text);
  const wait = msg('bot', 'thinking…');
  let r;
  try { r = await api('/chat', {text, auto: !!auto}); }
  catch (e) {
    msg('err', 'Could not reach Studio. Try sending your message again.');
    return false;
  } finally { if (go) go.disabled = false; $('chatclear').disabled = false; wait.remove(); }
  if (r.error) { msg('err', r.error); if (r.proposals) setQueue(r.proposals); return false; }
  const tn = (r.proposals || []).filter(p => /\.ocd$/.test(p.path || ''));
  if (tn.length) { // plan checklist: the turn's file edits as checkable steps
    pushMsg({kind: 'plan', open: true,
      title: `plan: ${tn.length} file${tn.length === 1 ? '' : 's'} proposed`,
      steps: tn.map(p => '◻ ' + p.path)});
  }
  if (r.log && r.log.length) { // thought trace: what the agent actually did
    pushMsg({kind: 'log',
      title: `thought for ${r.log.length} step${r.log.length === 1 ? '' : 's'}`,
      lines: r.log.map(ln => ({cls: /failed|error/i.test(ln) ? 'bad' : 'ok', text: ln}))});
  }
  msg('bot', r.reply || '(no reply)');
  followups(r.reply || '');
  if (r.note) msg('bot', r.note);
  if (r.proposals && r.proposals.length) setQueue(r.proposals);
  if (r.state) d.applyState(r.state, false);
  if (r.applied) d.loadVCS();
  return true;
}

// follow-up chips: Flux's "Route and verify / Add thermal copper" row.
// Mined from the reply's own next-steps, else the three generic moves.
function followups(reply) {
  const picks = [];
  reply.split('\n').forEach(ln => {
    const m = ln.match(/^(?:\d+[.)]\s*|[-*]\s+)(.{12,80})$/);
    if (m && /rout|check|valid|verif|test|place|thermal|copper|drc|fix/i.test(m[1])
       && picks.length < 3) picks.push(m[1].trim());
  });
  if (!picks.length) picks.push('Route and verify', 'Check DRC', 'Explain this board');
  ui.set({followups: picks.slice(0, 3).map(q =>
    ({label: q.length > 34 ? q.slice(0, 33) + '…' : q, title: q}))});
}

// opening another board drops the old one's proposal cards (the server dropped
// them too): the callers outside this module need just this much of the queue
export function clearProposals() {
  setQueue([]);
}

export function initAgent(deps) {
  d = deps;
  $('msgs').addEventListener('click', async e => { // apply/reject, delegated
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const id = b.dataset.prop, act = b.dataset.act;
    if (ui.state.msgs.some(m => m.kind === 'prop' && m.prop === id && m.busy)) return;
    propSet(id, {busy: true});
    if (act === 'apply') {
      const r = await api('/chat/apply', {id});
      if (r.error) { propSet(id, {busy: false}); msg('err', r.error); return; }
      if (r.state) d.applyState(r.state, false);
      else d.loadVCS();
      propDrop(id);
      msg('bot', 'applied ' + b.dataset.path + (r.state ? '' : ' (not the open board)')
          + (r.note ? ' — ' + r.note : ''));
      setQueue(r.proposals || []);
      d.loadVCS();
      return;
    }
    const r = await api('/chat/reject', {id});
    propDrop(id);
    msg('bot', 'rejected ' + b.dataset.path + ' — nothing was written');
    setQueue(r.proposals || []);
  });
  $('composer').onsubmit = async e => {
    e.preventDefault();
    const input = $('ask'), draft = input.value;
    if (await chat(draft, $('chatauto').checked)) {
      if (input.value === draft) input.value = '';
    }
  };
  $('ask').addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault(); $('composer').requestSubmit();
    }
  });
  $('chatclear').onclick = async () => {
    const btn = $('chatclear');
    if (btn.disabled || !confirm('Clear this conversation and pending proposals? '
                                 + 'Board files will not change.')) return;
    btn.disabled = true;
    try {
      const r = await api('/chat/reset', {});
      if (r.error) { msg('err', r.error); return; }
      ui.set({msgs: [], followups: []});
    } catch (e) { msg('err', 'Could not clear the conversation. Try again.'); }
    finally { btn.disabled = false; }
  };
  $('chat').addEventListener('click', e => { // follow-up chip
    const b = e.target.closest('#followups button');
    if (!b) return;
    chat(b.title, $('chatauto').checked);
  });
}
