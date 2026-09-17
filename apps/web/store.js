// One tiny observable for the workshop chrome: legacy.js owns the behaviour
// (tab persistence, body classes, the fetched numbers) and pushes values here;
// preact renders the pixels. A store instead of a framework because nothing
// else needs one — plain object, Set of subscribers, no build step.
const state = {
  cost: 'cost', ocd: '', feas: '', stat: '', statOk: true,
  room: 'solo', roomOk: false, roomTitle: 'no one else here yet',
  roomNote: 'live on this board', me: '',
  view: 'all', tabs: false, dark: false, chat: false,
};

const subs = new Set();

export const ui = {
  state,
  set(patch) {
    let hit = false;
    for (const k of Object.keys(patch)) {
      if (state[k] !== patch[k]) { state[k] = patch[k]; hit = true; }
    }
    if (hit) for (const fn of [...subs]) fn();
  },
  sub(fn) {
    subs.add(fn);
    return () => { subs.delete(fn); };
  },
};
