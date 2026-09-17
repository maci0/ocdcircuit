"""Realtime multiplayer rooms: one Room per board file.

Google-docs-shaped, stdlib only. The server holds {rev, text} per board:
clients push text at a rev (match -> accept, rev++, broadcast; mismatch ->
409-style stale + current rev, the client reloads) and listen on SSE for
pushes + presence. Presence join returns a disposer — disconnect runs the
inverse (leave broadcast), so no ghost cursors (temporal composability,
paper Alg 1). Conflicts resolve last-writer-wins per rev: the loser adopts
the winner's text via the same banner pattern as the file-watch.

cordis-boundary: the wire (SSE bytes, subscriber threads) is an
outside-context emission (§6.1) — withheld until subscribe/push, no inverse
claimed beyond the leave/dispose handling here. _ROOMS is process state
like the request loop itself: it exits with the process.
"""
from __future__ import annotations
import queue
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from .util import path_for_log

if TYPE_CHECKING:
    from .circuit import Board

COLORS = ("#0f5c37", "#1d5fa8", "#8a2318", "#6b3fa0", "#b9770e", "#0e7c86",
          "#be185d", "#4d7c0f", "#1d4ed8", "#a16207", "#0f766e", "#7e22ce")
HEARTBEAT_TIMEOUT = 15.0  # ponytail: prune lazily on access, no timer thread
# Cap per SSE subscriber: an unbounded Queue never raises Full, so a slow
# (or wedged) client would retain every push until OOM. Dropped revs catch
# up via /collab/sync — same path the Full handler already documents.
SUB_QUEUE_MAX = 64


def color_for(name: str) -> str:
    """Stable pill color per user (sha1, not hash(): PYTHONHASHSEED would
    reshuffle colors every restart — and hash() collides fast on 6 colors)."""
    import hashlib
    return COLORS[int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(COLORS)]


class Room:
    """Live session for one board file: rev + text + presence + fan-out."""

    def __init__(self, key: str, text: str) -> None:
        self.key = key
        self.rev = 0
        self.text = text
        self.by = ""
        self._mu = threading.Lock()
        self._members: dict[str, dict[str, object]] = {}
        self._subs: list[queue.Queue[dict[str, object]]] = []

    def join(self, name: str) -> Callable[[], None]:
        """Presence effect: join now, the disposer leaves (idempotent,
        LIFO-safe — call it from the SSE finally block). Color is the
        first free slot, so everyone in the room is visibly distinct;
        a rejoining name keeps its color while present."""
        with self._mu:
            cur = self._members.get(name)
            color = str(cur.get("color")) if cur else self._free_color(name)
            self._members[name] = {"name": name, "color": color,
                                   "x": 0.0, "y": 0.0, "ref": "",
                                   "t": time.monotonic()}
            rev = self.rev
            users = self._users()
        self._broadcast({"rev": rev, "join": name, "users": users})
        armed = {"on": True}

        def _leave() -> None:
            if not armed["on"]:
                return
            armed["on"] = False
            with self._mu:
                self._members.pop(name, None)
                rev_now = self.rev
                users_now = self._users()
            self._broadcast({"rev": rev_now, "leave": name,
                             "users": users_now})

        return _leave

    def heartbeat(self, name: str, x: float = 0.0, y: float = 0.0,
                  ref: str = "") -> dict[str, object]:
        """Cursor move: refresh presence, prune the timed-out, reply with
        the room snapshot (rev + users). Join is implicit — a sync from a
        stranger is a join, not an error."""
        now = time.monotonic()
        with self._mu:
            m = self._members.get(name)
            if m is None:
                m = {"name": name, "color": self._free_color(name)}
                self._members[name] = m
            m.update({"x": x, "y": y, "ref": ref, "t": now})
            stale = [n for n, v in self._members.items()
                     if now - float(cast(float, v.get("t", 0))) > HEARTBEAT_TIMEOUT]
            for n in stale:
                del self._members[n]
            users = self._users()
            rev = self.rev
            color = str(m.get("color"))
        if stale:
            self._broadcast({"rev": rev, "prune": stale, "users": users})
        return {"rev": rev, "color": color, "users": users}

    def claim(self, rev: int) -> bool:
        """Atomic rev check with no state change: True when the caller may
        build at this rev. Prefer cas_set_text after the build — claim alone
        races when two threads both pass then both adopt. Kept for callers
        that only need a cheap pre-check before expensive work."""
        with self._mu:
            return rev == self.rev

    def push(self, name: str, rev: int, text: str) -> tuple[bool, int]:
        """Text op at a rev: match -> accept (rev++, broadcast), mismatch ->
        stale (caller reloads). Returns (ok, current_rev)."""
        with self._mu:
            if rev != self.rev or text == self.text:
                return (text == self.text and rev == self.rev, self.rev)
            self.text = text
            self.by = name
            self.rev += 1
            rev_now = self.rev
            users = self._users()
        self._broadcast({"rev": rev_now, "by": name, "users": users})
        return (True, rev_now)

    def set_text(self, text: str, by: str) -> int:
        """Adopt server-built text (build/solve/undo all funnel here):
        rev++ only on real change, broadcast either way is noise — so only
        on change."""
        with self._mu:
            if text == self.text:
                return self.rev
            self.text = text
            self.by = by
            self.rev += 1
            rev_now = self.rev
            users = self._users()
        self._broadcast({"rev": rev_now, "by": by, "users": users})
        return rev_now

    def cas_set_text(self, rev: int, text: str, by: str
                     ) -> tuple[bool, int, str]:
        """Validate-then-adopt without the claim race: adopt only if `rev`
        still matches. Returns (ok, current_rev, current_text). Two builders
        that both passed claim at the same rev: one wins, the other gets
        stale + the winner's text."""
        with self._mu:
            if rev != self.rev:
                return (False, self.rev, self.text)
            if text == self.text:
                return (True, self.rev, self.text)
            self.text = text
            self.by = by
            self.rev += 1
            rev_now = self.rev
            users = self._users()
            cur = self.text
        self._broadcast({"rev": rev_now, "by": by, "users": users})
        return (True, rev_now, cur)

    def subscribe(self) -> tuple[queue.Queue[dict[str, object]],
                                 Callable[[], None]]:
        """One SSE stream: the queue gets every broadcast; the disposer
        unsubscribes (idempotent)."""
        q: queue.Queue[dict[str, object]] = queue.Queue(maxsize=SUB_QUEUE_MAX)
        with self._mu:
            self._subs.append(q)
        armed = {"on": True}

        def _unsub() -> None:
            if not armed["on"]:
                return
            armed["on"] = False
            with self._mu:
                if q in self._subs:
                    self._subs.remove(q)

        return (q, _unsub)

    def snapshot(self) -> dict[str, object]:
        """Consistent {rev, by, text, users} under one lock — callers must
        not re-read .text/.rev unlocked beside this (torn across a push)."""
        with self._mu:
            return {"rev": self.rev, "by": self.by, "text": self.text,
                    "users": self._users()}

    def _free_color(self, name: str) -> str:
        """First palette slot nobody holds; past 12 users the hash picks a
        stable fallback (a duplicate beats no color)."""
        taken = {str(v.get("color")) for k, v in self._members.items()
                 if k != name}
        for c in COLORS:
            if c not in taken:
                return c
        return color_for(name)

    def _users(self) -> list[dict[str, object]]:
        now = time.monotonic()
        return [{"name": str(v.get("name")), "color": str(v.get("color")),
                 "x": float(cast(float, v.get("x", 0.0))),
                 "y": float(cast(float, v.get("y", 0.0))),
                 "ref": str(v.get("ref", "")),
                 "ago": round(now - float(cast(float, v.get("t", now))), 1)}
                for _, v in sorted(self._members.items())]

    def _broadcast(self, msg: dict[str, object]) -> None:
        with self._mu:
            subs = list(self._subs)
        dropped = 0
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dropped += 1
        if dropped:
            # a full subscriber queue means a slow client missed this rev —
            # they must catch up via /collab/sync; surface it for the operator
            import sys
            print(f"collab: dropped fan-out to {dropped}/{len(subs)} "
                  f"subscriber(s) (queue full) for room "
                  f"{path_for_log(self.key)!r}",
                  file=sys.stderr)


_ROOMS: dict[str, Room] = {}
_MU = threading.Lock()


def get_room(key: str, text: str) -> Room:
    """The room for a board file (created once, seeded with its text).
    Empty rooms (no members, no subscribers) are dropped on access: a
    session with nobody in it holds no presence worth keeping, and an
    unbounded _ROOMS across hundreds of opened boards is a leak.

    Emptiness is checked under each room's lock while holding `_MU` (lock
    order: `_MU` then `room._mu` — Room methods never take `_MU`)."""
    with _MU:
        room = _ROOMS.get(key)
        if room is None:
            room = Room(key, text)
            _ROOMS[key] = room
        for k, r in list(_ROOMS.items()):
            if k == key:
                continue
            with r._mu:
                empty = not r._members and not r._subs
            if empty:
                _ROOMS.pop(k, None)
        return room


def drop_room(key: str) -> None:
    """Unload inverse of get_room: retire the session for a closed board."""
    with _MU:
        _ROOMS.pop(key, None)


def apply_op(board: Board, op: dict[str, object]) -> dict[str, object]:
    """Validate + apply one collab op on a board (the `collab` plugin owns
    this): {"ops": [apply_patch ops]} for structured edits. Fixable input
    errors propagate unfenced like every other dispatch."""
    from . import agent as _agent
    ops = op.get("ops")
    assert isinstance(ops, list), "collab op wants {ops: [...]}"
    for o in ops:
        assert isinstance(o, dict), f"bad op {o!r}"
    n = _agent.apply_patch(board, ops)
    assert isinstance(n, int)
    return {"applied": n}


if __name__ == "__main__":
    r = get_room("demo", "board demo 40x30\n")
    assert r.rev == 0
    # two users join: presence lists both, colors stable
    leave_a = r.join("alice")
    leave_b = r.join("bob")
    users0 = cast(list[dict[str, object]], r.snapshot()["users"])
    assert {str(u["name"]) for u in users0} == {"alice", "bob"}
    assert color_for("alice") == color_for("alice")
    # push at matching rev wins, broadcasts to the subscriber
    stream, unsub = r.subscribe()
    assert stream.maxsize == SUB_QUEUE_MAX
    ok, rev = r.push("alice", 0, "board demo 40x30\npart R1 R0805 1k\n")
    assert ok and rev == 1
    got = stream.get(timeout=2)
    assert got["by"] == "alice"
    # Full is reachable: fill without a reader, then one more put_nowait
    while stream.qsize() < stream.maxsize:
        stream.put_nowait({"pad": True})
    try:
        stream.put_nowait({"overflow": True})
        raise AssertionError("unbounded subscriber queue")
    except queue.Full:
        pass
    # drain so later broadcasts still land for this test
    while True:
        try:
            stream.get_nowait()
        except queue.Empty:
            break
    # stale push loses: ok=False + current rev, client reloads
    ok2, rev2 = r.push("bob", 0, "board demo 40x30\npart C1 C0805 1n\n")
    assert not ok2 and rev2 == 1
    # heartbeat prunes the timed-out without a timer thread
    r.heartbeat("alice", x=3.0, y=5.0, ref="R1")
    with r._mu:
        r._members["bob"]["t"] = time.monotonic() - HEARTBEAT_TIMEOUT - 1
    users = cast(list[dict[str, object]], r.heartbeat("alice")["users"])
    assert [str(u["name"]) for u in users] == ["alice"]
    # inverses run once: leave/unsub/drop are idempotent
    leave_b()
    leave_b()
    unsub()
    unsub()
    users1 = cast(list[dict[str, object]], r.snapshot()["users"])
    assert [str(u["name"]) for u in users1] == ["alice"]
    leave_a()
    assert r.snapshot()["users"] == []
    drop_room("demo")
    assert get_room("demo", "x").rev == 0  # fresh after unload

    # concurrent cas_set_text: exactly one of N racers at rev 0 adopts
    r2 = get_room("race", "base\n")
    wins: list[str] = []
    barrier = threading.Barrier(8)

    def _race(i: int) -> None:
        barrier.wait()
        ok_r, _rev_r, _txt = r2.cas_set_text(0, f"base\n# {i}\n", f"u{i}")
        if ok_r:
            wins.append(f"u{i}")

    threads = [threading.Thread(target=_race, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and r2.rev == 1, (wins, r2.rev)
    drop_room("race")
    print("COLLAB OK")
