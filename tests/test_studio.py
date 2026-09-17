"""Studio CI smoke gate: boot, build budget, endpoints, headless render.

Stdlib only (asserts, no framework): subprocess + urllib + zlib PNG parse.
Run: python tests/test_studio.py  (also wired into `make test`).
Skips the browser half when no chromium binary is found (server half still runs).
"""
from __future__ import annotations
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from typing import cast

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOARD = os.path.join(ROOT, "boards", "blinky_555.ocd")
BUILD_BUDGET = 0.2  # seconds, the goal number; steady-state only (see warm-up)
PCB_BRIGHT_MIN = 0.02  # healthy shot = 0.08, black-PCB shot = 0.0000

# editor text selection → PCB/SCH highlight: drive the studio's own
# edHighlight() against DOM stubs (no browser needed) and read what it picked.
HL_STUB = """
let dirty=0;const markDirty=()=>{dirty++;};
const $=()=>({contains:n=>!!(n&&n.ed)});
const S={cur:{parts:{U1:1,R1:1,R2:1,PSU_J1:1}}};
let sel={isCollapsed:false,anchorNode:{ed:1},text:'',toString(){return this.text;}};
const window={getSelection:()=>sel};
let edHl=new Set(), edPin=new Set();
"""
HL_DRIVE = """
function pick(text,inside){sel={isCollapsed:text==='',anchorNode:inside?{ed:1}:{ed:0},
  text,toString(){return text;}};edHighlight();}
const out=[];
pick('fix PSU_J1 at -0.5 20.8',true);out.push([...edHl].join(','));
pick('U1.7',true);out.push([...edHl].join(',')+':'+[...edPin].join(','));
pick('N_DIS L1 :: R1.2 <--> R2.1 <--> U1.7',true);out.push([...edHl].sort().join(','));
const d=dirty;pick('U1.7',false);out.push((dirty>d)+':'+[...edHl].length);
console.log(out.join('|'));
"""
HL_EXPECT = "PSU_J1|U1:U1.7|R1,R2,U1|true:0"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


_JAR: dict[str, str] = {}  # base -> "ocd_user=..." (login gate sessions)


def post(base: str, path: str, body: dict[str, object],
         cookie: str = "") -> dict[str, object]:
    q = urllib.request.Request(base + path, json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
    ck = cookie or _JAR.get(base, "")
    if ck:
        q.add_header("Cookie", ck)
    out = urllib.request.urlopen(q, timeout=30).read()
    res = json.loads(out)
    assert isinstance(res, dict)
    return res


def get(base: str, path: str) -> bytes:
    q = urllib.request.Request(base + path)
    if _JAR.get(base):
        q.add_header("Cookie", _JAR[base])
    out = urllib.request.urlopen(q, timeout=30).read()
    assert isinstance(out, bytes)
    return out


def login(base: str) -> str:
    """First account on a throwaway ROOT: signup returns the session cookie."""
    import http.client as _hc
    from urllib.parse import urlparse as _up
    u = _up(base)
    assert u.hostname
    c = _hc.HTTPConnection(u.hostname, u.port, timeout=30)
    c.request("POST", "/auth/signup",
              json.dumps({"user": "tester", "password": "testtest12"}),
              {"Content-Type": "application/json"})
    r = c.getresponse()
    setck = r.getheader("Set-Cookie", "")
    res = json.loads(r.read())
    assert not res.get("error"), res
    assert "ocd_user=" in setck, setck
    assert "Max-Age=86400" in setck, setck  # cookie dies with server session TTL
    _JAR[base] = setck.split(";")[0].strip()
    return _JAR[base]


def main() -> None:
    # --help answers usage without booting a server (mcp + studio)
    mh = subprocess.run([sys.executable, "-m", "apps.mcp", "--help"],
                        cwd=ROOT, capture_output=True, timeout=30)
    assert mh.returncode == 0 and b"stdio server" in mh.stdout, mh
    sh = subprocess.run([sys.executable, "-m", "apps.studio", "--help"],
                        cwd=ROOT, capture_output=True, timeout=30)
    assert sh.returncode == 0 and b"OCD_PORT" in sh.stdout, sh
    # bad OCD_PORT falls back to 8077 with a stderr note (no traceback):
    # the server boots and serves instead of dying in main()
    import urllib.request as _url
    # A studio already listening on 8077 (a developer's `make run`) would answer
    # the probe below, so the URL check would pass without OUR child ever
    # booting — and stderr would still be empty, failing the note assertion for
    # the wrong reason. Probe first and, when it is busy, assert only the note.
    _busy = False
    try:
        _url.urlopen("http://localhost:8077/slots", timeout=1).read()
        _busy = True
        print("8077 already serving: checking the stderr note only")
    except OSError:
        pass
    fb = subprocess.Popen(
        [sys.executable, "-c",
         "import os; os.environ['OCD_PORT']='bogus'; "
         "import apps.studio as S; S.main()"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _booted = False
    try:
        if not _busy:
            for _ in range(100):
                try:
                    _url.urlopen("http://localhost:8077/slots", timeout=1).read()
                    _booted = True
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            time.sleep(1.0)  # our child only needs to print its note
    finally:
        fb.terminate()
        _err = fb.stderr.read().decode() if fb.stderr else ""
    assert "bad OCD_PORT" in _err, _err[:300]
    if not _busy:
        assert _booted, "fallback server did not boot on 8077"
    # UI contributions are disposable: every _slot() keeps the disposer
    # register() handed back, and unload_ui() runs them LIFO — the previous
    # code dropped the disposer, which made all ten rows permanent module state
    if ROOT not in sys.path:  # in-process import: sys.path[0] is tests/
        sys.path.insert(0, ROOT)
    from apps import studio as _st_ui
    assert "kb" in _st_ui.SLOTS.report("view"), _st_ui.SLOTS.report("view")
    assert "inspector" in _st_ui.SLOTS.report("view"), _st_ui.SLOTS.report("view")
    assert "scan" in _st_ui.SLOTS.report("view"), _st_ui.SLOTS.report("view")
    assert len(_st_ui._UI_DISPOSERS) == 12, len(_st_ui._UI_DISPOSERS)
    _st_ui.unload_ui()
    assert _st_ui.SLOTS.report("view") == [] and _st_ui.SLOTS.report("toolbar") == []
    assert _st_ui._UI_DISPOSERS == []
    print("ui slot dispose ok (12 contributions, LIFO, once)")

    # session/auth caches: expired tokens must not crowd out live sessions,
    # and the rate-limit map must stay bounded under IP spray.
    _st_ui._SESSIONS.clear()
    _st_ui._AUTH_HITS.clear()
    for i in range(_st_ui._SESSIONS_MAX):
        _st_ui._SESSIONS[f"exp{i}"] = (f"u{i}", time.monotonic() - 1)
    _live = _st_ui._new_session("alice")
    assert _live in _st_ui._SESSIONS
    assert all(not t.startswith("exp") for t in _st_ui._SESSIONS), _st_ui._SESSIONS
    for i in range(_st_ui._AUTH_HITS_MAX + 50):
        assert _st_ui._auth_rate_ok(f"spray{i}")
    assert len(_st_ui._AUTH_HITS) <= _st_ui._AUTH_HITS_MAX
    _st_ui._SESSIONS.clear()
    _st_ui._AUTH_HITS.clear()
    # shelf account names must not appear in stderr paths
    assert _st_ui._path_for_log(".users/alice/board.ocd") == ".users/*/board.ocd"
    assert _st_ui._path_for_log("/tmp/x/.users/bob") == "/tmp/x/.users/*"
    assert "alice" not in _st_ui._path_for_log("boards/.users/alice/y.ocd")
    assert _st_ui._path_for_log("boards/blinky.ocd") == "boards/blinky.ocd"
    # OSError / API text can embed absolute shelf paths — scrub those too
    _ose = OSError(2, "No such file", "/tmp/proj/.users/carol/x.ocd")
    assert "carol" not in _st_ui._path_for_log(_ose)
    assert ".users/*" in _st_ui._path_for_log(_ose)
    assert "carol" not in _st_ui._exc_for_log(_ose)
    assert _st_ui._exc_for_log(_ose).split(":", 1)[0].endswith("Error")
    # cross-shelf denial must not name the other account for the peer
    _tok = _st_ui._REQ_USER.set("bob")
    try:
        try:
            _st_ui._ensure_shelf_rel(".users/alice/board.ocd")
            raise AssertionError("expected shelf denial")
        except ValueError as _ve:
            assert "alice" not in str(_ve), _ve
            assert ".users/*" in str(_ve), _ve
    finally:
        _st_ui._REQ_USER.reset(_tok)
    # unreadable .ocd-users must not look like "no accounts" — that opened a
    # wipe-on-signup path. Only a missing file means empty.
    _td_u = tempfile.mkdtemp(prefix="ocd-users-")
    _upath = os.path.join(_td_u, ".ocd-users")
    open(_upath, "w", encoding="utf-8").write("alice:aa:bb\n")
    _orig_up = _st_ui._users_path
    _st_ui._users_path = lambda: _upath
    try:
        assert "alice" in _st_ui._read_users()
        os.remove(_upath)
        assert _st_ui._read_users() == {}  # missing ≡ no accounts yet
        os.mkdir(_upath)  # path exists but is not a file → OSError, not empty
        try:
            _st_ui._read_users()
            raise AssertionError("directory-as-users-file looked like no accounts")
        except OSError:
            pass
        try:
            _st_ui._write_user("eve", "password12")
            raise AssertionError("signup overwrote an unreadable users path")
        except OSError:
            pass
        assert os.path.isdir(_upath)  # not replaced with a wiped file
    finally:
        _st_ui._users_path = _orig_up
        shutil.rmtree(_td_u)
    print("session/auth cache bounds ok")

    port = free_port()
    base = f"http://localhost:{port}"
    # auth writes .ocd-users + .users/ beside ROOT: point the server at a
    # throwaway copy so the test never litters the repo (nor trips the
    # single-tenant signup on a dirty checkout).
    troot = tempfile.mkdtemp(prefix="ocd-auth-")
    for _fn in ("blinky_555.ocd", "psu.ocd"):
        shutil.copy(os.path.join(ROOT, "boards", _fn), os.path.join(troot, _fn))
    env = dict(os.environ, OCD_PORT=str(port), OCD_ROOT=troot)
    srv = subprocess.Popen([sys.executable, "-m", "apps.studio",
                            os.path.join(troot, "blinky_555.ocd")],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/slots", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("studio did not boot")

        # landing replaces the logged-out screen: hero up top, login one
        # click behind, shelf after. No cookie → hero + POSTs refused.
        _gate = urllib.request.urlopen(base + "/").read().decode()
        assert "Your whole team. One board." in _gate, _gate[:200]
        assert "id=herogo" in _gate and "id=ed" not in _gate, _gate[:200]
        # wire bytes: landing HTML alone (fab logos are /fab-logo/*); gzip cuts
        # transfer further. ETag + If-None-Match must 304 so a revalidate is
        # not a full re-send.
        import gzip as _gz
        import http.client as _hc_gz
        from urllib.parse import urlparse as _up_gz
        _ug = _up_gz(base)
        assert _ug.hostname
        _cg = _hc_gz.HTTPConnection(_ug.hostname, _ug.port, timeout=30)
        _cg.request("GET", "/", headers={"Accept-Encoding": "gzip"})
        _rg = _cg.getresponse()
        _gz_body = _rg.read()
        assert _rg.getheader("Content-Encoding") == "gzip", _rg.getheaders()
        assert _rg.getheader("Vary") == "Accept-Encoding", _rg.getheaders()
        _etag = _rg.getheader("ETag")
        assert _etag and _etag.startswith('"'), _etag
        assert _rg.getheader("Cache-Control") == "no-cache"
        _plain = _gz.decompress(_gz_body).decode()
        assert "Your whole team. One board." in _plain
        assert 'src="/fab-logo/' in _plain, _plain[:400]
        # fab tiles stay on /fab-logo/*; brand favicon may be a tiny SVG data URI
        assert "data:image/png" not in _plain, _plain[:400]
        assert 'rel=icon href="data:image/svg+xml,' in _plain, _plain[:500]
        assert len(_plain) < 40_000, len(_plain)  # was ~120 KB with inlined tiles
        assert len(_gz_body) < len(_plain) * 0.5, (len(_gz_body), len(_plain))
        print(f"landing gzip: {len(_plain)} -> {len(_gz_body)} bytes")
        _cg.request("GET", "/", headers={"If-None-Match": _etag,
                                         "Accept-Encoding": "gzip"})
        _r304 = _cg.getresponse()
        _r304.read()
        assert _r304.status == 304, (_r304.status, _r304.getheaders())
        _cg.request("GET", "/fab-logo/oshpark")
        _rlogo = _cg.getresponse()
        _logo = _rlogo.read()
        assert _rlogo.status == 200 and _rlogo.getheader("Content-Type") == "image/png"
        assert "immutable" in (_rlogo.getheader("Cache-Control") or "")
        assert _logo[:8] == b"\x89PNG\r\n\x1a\n", _logo[:16]
        print(f"landing ETag 304 ok; fab-logo/oshpark={len(_logo)} B")
        _refused = post(base, "/build", {"text": "x"})
        assert _refused.get("login") is True, _refused
        _fs_deny = json.loads(urllib.request.urlopen(base + "/fs", timeout=5).read())
        assert _fs_deny.get("login") is True, _fs_deny
        _poll_deny = json.loads(urllib.request.urlopen(base + "/poll", timeout=5).read())
        assert _poll_deny.get("login") is True, _poll_deny
        _bad = post(base, "/auth/login",
                    {"user": "tester", "password": "wrongwrong"})
        assert "error" in _bad, _bad
        login(base)
        _dup = post(base, "/auth/signup",
                    {"user": "second", "password": "testtest12"})
        assert not _dup.get("error"), _dup  # multi-user: own shelf each
        _me = post(base, "/auth/me", {})
        assert _me.get("user") == "tester", _me
        assert _me.get("display") == "tester", _me  # default display = name
        _prof = post(base, "/auth/profile", {"display": "Ada Circuit"})
        assert not _prof.get("error"), _prof
        assert post(base, "/auth/me", {})["display"] == "Ada Circuit"
        _profblank = post(base, "/auth/profile", {"display": "  "})
        assert "error" in _profblank, _profblank
        _profinject = post(base, "/auth/profile",
                           {"display": "x\nevil:deadbeef:deadbeef"})
        assert "error" in _profinject, _profinject
        _profcolon = post(base, "/auth/profile", {"display": "has:colon"})
        assert "error" in _profcolon, _profcolon
        _profzw = post(base, "/auth/profile",
                       {"display": "Ada\u200bCircuit"})  # ZWSP
        assert "error" in _profzw, _profzw
        # NFD paste (e + combining acute) stores as NFC caf\u00e9
        _nfd = post(base, "/auth/profile", {"display": "cafe\u0301"})
        assert not _nfd.get("error"), _nfd
        assert _nfd.get("display") == "caf\u00e9", _nfd
        assert post(base, "/auth/me", {})["display"] == "caf\u00e9"
        _unic = post(base, "/auth/signup",
                     {"user": "caf\u00e9", "password": "testtest99"})
        assert "error" in _unic, _unic  # ASCII usernames only
        _tr = post(base, "/auth/signup",
                   {"user": "I\u0307stanbul", "password": "testtest99"})
        assert "error" in _tr, _tr  # combining marks / non-ASCII rejected
        _slug = post(base, "/shelf/new", {"name": "capteur caf\u00e9"})
        assert not _slug.get("error"), _slug
        assert _slug.get("name") == "capteur-caf", _slug  # ASCII slug only
        # import upload: Unicode letters stripped (NFC/NFD must not land on disk)
        import base64 as _b64_u
        _fp_src = b"footprint TINY 1x1\npad 1 0 0 0.5 0.5\n"
        _fp_body = _b64_u.b64encode(_fp_src).decode()
        _imp_nfc = post(base, "/fs/import",
                        {"name": "caf\u00e9.fp", "data": _fp_body})
        assert not _imp_nfc.get("error"), _imp_nfc
        assert "imported caf.fp" in str(_imp_nfc.get("note", "")), _imp_nfc
        _imp_nfd = post(base, "/fs/import",
                        {"name": "cafe\u0301.fp", "data": _fp_body})
        # combining mark dropped → cafe.fp (distinct ASCII stem, not a lookalike path)
        assert not _imp_nfd.get("error"), _imp_nfd
        assert "imported cafe.fp" in str(_imp_nfd.get("note", "")), _imp_nfd
        # shelf isolation: second user cannot /fs/read the first user's board
        _mine = post(base, "/shelf/new", {"name": "private-board"})
        assert not _mine.get("error"), _mine
        import http.client as _hc_id
        from urllib.parse import urlparse as _up_id
        _uid = _up_id(base)
        assert _uid.hostname
        _cid = _hc_id.HTTPConnection(_uid.hostname, _uid.port, timeout=30)
        _cid.request("POST", "/auth/login",
                     json.dumps({"user": "second", "password": "testtest12"}),
                     {"Content-Type": "application/json"})
        _rid = _cid.getresponse()
        _ck_second = _rid.getheader("Set-Cookie", "").split(";")[0].strip()
        _rid.read()
        _steal = post(base, "/fs/read",
                      {"path": ".users/tester/private-board.ocd"},
                      cookie=_ck_second)
        assert "error" in _steal and "shelf" in str(_steal["error"]).lower(), _steal
        _idor_ok = post(base, "/fs/read",
                        {"path": ".users/tester/private-board.ocd"})
        assert "text" in _idor_ok, _idor_ok  # owner (tester jar) can still read
        # open the private shelf board as tester (process-global SRC switches).
        # A second authed peer must not /init, /export, /collab/sync, or /poll it.
        _open_priv = get(base, "/?board=private-board").decode()
        assert "id=ed" in _open_priv, _open_priv[:200]
        _peer_init = post(base, "/init", {}, cookie=_ck_second)
        assert "error" in _peer_init and "shelf" in str(
            _peer_init["error"]).lower(), _peer_init
        _peer_sync = post(base, "/collab/sync", {}, cookie=_ck_second)
        assert "error" in _peer_sync and "shelf" in str(
            _peer_sync["error"]).lower(), _peer_sync
        _peer_exp = post(base, "/export", {}, cookie=_ck_second)
        assert "error" in _peer_exp and "shelf" in str(
            _peer_exp["error"]).lower(), _peer_exp
        import http.client as _hc_poll
        _cpoll = _hc_poll.HTTPConnection(_uid.hostname, _uid.port, timeout=30)
        _cpoll.request("GET", "/poll", headers={"Cookie": _ck_second})
        _rpoll = _cpoll.getresponse()
        _peer_poll = json.loads(_rpoll.read())
        assert "error" in _peer_poll and "shelf" in str(
            _peer_poll["error"]).lower(), _peer_poll
        # owner still reaches the open private board
        _own_init = post(base, "/init", {})
        assert "error" not in _own_init, _own_init
        # peer can still switch SRC onto a shared launch board and work there
        _peer_open = post(base, "/fs/open", {"path": "blinky_555.ocd"},
                          cookie=_ck_second)
        assert "error" not in _peer_open, _peer_open
        _peer_ok = post(base, "/init", {}, cookie=_ck_second)
        assert "error" not in _peer_ok, _peer_ok
        # kb/add must not copy another shelf board via a raw path (open board
        # is shared now, so this hits _abs — not the open-board guard)
        _kb_steal = post(base, "/kb/add",
                         {"src": ".users/tester/private-board.ocd"},
                         cookie=_ck_second)
        assert "error" in _kb_steal and "shelf" in str(
            _kb_steal["error"]).lower(), _kb_steal
        # restore tester onto the launch board for the rest of the suite
        _back = post(base, "/fs/open", {"path": "blinky_555.ocd"})
        assert "error" not in _back, _back
        _in = get(base, "/").decode()
        assert "id=ed" in _in, _in[:200]
        assert "id=importfile" in _in and "id=importstat" in _in, "import picker missing"
        # static shell markers (in the HTML) …
        for frag in ("id=viewtabs", "data-v=all", "data-v=pcb", "data-v=sch",
                     "data-v=t3d", "data-v=docs", "id=themebtn"):
            assert frag in _in, f"flux work missing: {frag}"
        # …plus runtime-built pieces (in the inline script, created by JS)
        # and the followups CSS rule (in <style>, not <script>)
        _pjs = _in[_in.index("<script>") + 8:_in.index("</script>")]
        for frag in ("setDark", "setView", "showCockpit", "followups", "thought",
                     "contextmenu", "rotRefs", "unpinRefs"):
            assert frag in _pjs, f"flux work missing: {frag}"
        assert "followups:empty" in _in, "flux work missing: followups:empty"
        # proper menus: solve stays top-level, the rest lives in named menus
        for frag in ("id=m-board", "id=m-edit", "id=m-engines", "id=m-sim",
                      "id=m-tools", "id=m-live", "id=roomnote"):
            assert frag in _in, f"menu missing: {frag}"
        # every action keeps its id (handlers never rebind)
        for frag in ("id=solve", "id=dice", "id=stamp", "id=fab_dl", "id=dl",
                      "id=undo", "id=redo", "id=diffprev", "id=commit",
                      "id=placer", "id=router", "id=fab", "id=silk",
                      "id=simbtn", "id=chatbtn", "id=chatauto",
                      "id=sharebtn", "id=room"):
            assert frag in _in, f"control id missing: {frag}"
        print("flux agent-rail + tabs + dark ok")
        _sh = post(base, "/shelf", {})
        assert _sh.get("user") == "tester" and isinstance(_sh.get("boards"), list), _sh
        _templates = cast(list[dict[str, object]], _sh.get("templates"))
        assert any(t.get("name") == "blinky_555.ocd" for t in _templates), _sh
        _nb = post(base, "/shelf/new", {"name": "hello"})
        assert not _nb.get("error"), _nb
        assert _nb.get("name") == "hello", _nb  # stem for /?board= open
        _boards = _nb.get("boards")
        assert isinstance(_boards, list)
        assert any(isinstance(b, dict) and b.get("name") == "hello.ocd"
                   for b in _boards), _nb
        _hello = next(b for b in _boards
                      if isinstance(b, dict) and b.get("name") == "hello.ocd")
        # shelf mtime is a UTC wall stamp — not host-local (TZ-dependent)
        assert str(_hello.get("mtime", "")).endswith(" UTC"), _hello
        _nb2 = post(base, "/shelf/new", {"name": "hello"})
        assert "already on your shelf" in str(_nb2.get("error")), _nb2
        _slug = post(base, "/shelf/new", {"name": "A wifi sensor node!"})
        assert not _slug.get("error"), _slug  # prose degrades to a slug
        assert _slug.get("name") == "a-wifi-sensor-node", _slug
        _tmpl = post(base, "/shelf/from_template", {"name": "blinky_555.ocd"})
        assert not _tmpl.get("error"), _tmpl  # template opens a copy
        assert _tmpl.get("name") == "blinky_555", _tmpl
        _bad = post(base, "/shelf/from_template", {"name": "../../etc/passwd"})
        assert "error" in _bad, _bad
        import base64 as _b64
        _imp = post(base, "/fs/import", {"name": "x.exe", "data": _b64.b64encode(b"hi").decode()})
        assert "error" in _imp and "import wants" in str(_imp["error"]), _imp
        _trav = post(base, "/fs/import", {"name": "../../x.fp", "data": _b64.b64encode(b"hi").decode()})
        assert "error" in _trav, _trav  # never writes outside fp/
        _lp = urllib.request.urlopen(base + "/").read().decode()  # logged out → landing
        for frag in ("id=newprojbtn", "id=newproj", "id=npsearch",
                     "id=npgrid", "id=npblank", "npRender", "_npcache",
                     "openShelfBoard"):
            assert frag in _lp, f"new-project modal missing: {frag}"
        _w = get(base, "/").decode()  # still authed: workshop
        assert "id=ed" in _w, _w[:200]
        assert "withBusy" in _w, "long-action busy feedback missing"
        assert "download ${key}" in _w or "download ${" in _w, "download label must stay a word"
        assert "sim ${simWhat}" in _w or "sim ${" in _w, "sim label must stay a word"
        # modal lives on the landing page, but its data path is the shelf:
        # template copy + blank-from-search-text both work while authed
        _t2 = post(base, "/shelf/from_template", {"name": "psu.ocd"})
        assert not _t2.get("error"), _t2
        assert _t2.get("name") == "psu", _t2
        _np = post(base, "/shelf/new", {"name": "modal blank"})
        _np_boards = cast(list[dict[str, object]], _np["boards"])
        assert any(b.get("name") == "modal-blank.ocd" for b in _np_boards), _np
        assert _np.get("name") == "modal-blank", _np
        _lo = post(base, "/auth/logout", {})
        assert _lo.get("ok") is True, _lo
        _JAR.pop(base, None)
        _out = urllib.request.urlopen(base + "/").read().decode()  # logged out: landing
        assert "Your whole team. One board." in _out, _out[:200]
        assert _out.count("class=fabcell") == 11, _out.count("class=fabcell")
        # users persist on disk, so re-signup refuses — log back in instead
        import http.client as _hc2
        from urllib.parse import urlparse as _up2
        _u2 = _up2(base)
        assert _u2.hostname
        _c2 = _hc2.HTTPConnection(_u2.hostname, _u2.port, timeout=30)
        _c2.request("POST", "/auth/login",
                    json.dumps({"user": "tester", "password": "testtest12"}),
                    {"Content-Type": "application/json"})
        _r2 = _c2.getresponse()
        _ck2 = _r2.getheader("Set-Cookie", "")
        assert "ocd_user=" in _ck2, _ck2
        _JAR[base] = _ck2.split(";")[0].strip()
        print("auth gate + shelf ok")

        text = open(BOARD).read()
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})  # warm-up: cold caches aren't UX
        # file-watch: /poll reports clean after our save; an external
        # edit flips it dirty; /reload adopts it (undo keeps ours).
        # NOTE: SRC is the troot copy (OCD_ROOT), so poll + edit touch the
        # copy; the repo BOARD stays pristine.
        tboard = os.path.join(troot, "blinky_555.ocd")
        _p0 = json.loads(get(base, "/poll"))
        assert _p0["clean"] is True, _p0
        with open(tboard, "a") as _f:
            _f.write("# external edit\n")
        _p1 = json.loads(get(base, "/poll"))
        assert _p1["clean"] is False, _p1
        _r = post(base, "/reload", {})
        assert "error" not in _r, _r
        _p2 = json.loads(get(base, "/poll"))
        assert _p2["clean"] is True, _p2
        t = time.monotonic()
        d = post(base, "/build", {"text": text, "placer": "diffusion",
                                 "router": "maze"})
        dt = time.monotonic() - t
        assert not d.get("error"), d.get("error")
        assert dt < BUILD_BUDGET, f"quick build {dt:.2f}s over {BUILD_BUDGET}s"
        parts = cast(dict[str, object], d["parts"])
        score = cast(dict[str, object], d["score"])
        lint = cast(dict[str, object], d["lint"])
        assert len(parts) == 10, len(parts)
        assert cast(float, score["total"]) > 0, score
        assert isinstance(lint["errors"], list), lint
        print(f"build {dt:.2f}s score={score['total']} ok")

        # realtime collab: two users, one board — sync/push/cursor/op/SSE.
        # Second signup gets its own shelf (multi-user); both edit the open
        # board's room: rev-guarded push, stale loser reloads, presence lists
        # both, structured ops go through the `collab` plugin, and the SSE
        # stream fans the push out.
        import http.client as _hc3
        from urllib.parse import urlparse as _up3
        _u3 = _up3(base)
        assert _u3.hostname
        _c3 = _hc3.HTTPConnection(_u3.hostname, _u3.port, timeout=30)
        _c3.request("POST", "/auth/signup",
                    json.dumps({"user": "teammate", "password": "testtest12"}),
                    {"Content-Type": "application/json"})
        _r3 = _c3.getresponse()
        _ck3 = _r3.getheader("Set-Cookie", "")
        assert "ocd_user=" in _ck3, _ck3
        _mate = _ck3.split(";")[0].strip()
        _r3.read()
        _cs = post(base, "/collab/sync", {})
        assert "R1" in str(_cs.get("text")), _cs
        _cs_rev = cast(int, _cs.get("rev"))
        _cs_text = str(_cs.get("text"))
        import re as _re2
        _m0 = _re2.search(r"part R1 R0805 (\S+)", _cs_text)
        assert _m0, "R1 line missing from room text"
        _edit = _cs_text.replace("part R1 R0805 " + _m0.group(1),
                                 "part R1 R0805 9k9", 1)
        assert _edit != _cs_text, "test edit must differ from room text"
        _t0 = time.monotonic()

        _cp = post(base, "/collab/push", {"rev": _cs_rev, "text": _edit})
        _cpdt = time.monotonic() - _t0
        assert not _cp.get("error"), _cp.get("error")
        assert "9k9" in str(_cp.get("text")), "push text adopted"
        # rev rides every build response (applyState); absent = unchanged
        _cp_rev = cast(int, _cp.get("rev"))
        assert _cp_rev >= _cs_rev, _cp
        assert _cpdt < BUILD_BUDGET, f"collab push {_cpdt:.2f}s over budget"
        assert "9k9" in str(_cp.get("text")), "push text adopted"
        # rev is room state, not test state: re-read it after every op
        _sync1 = post(base, "/collab/sync", {}, cookie=_mate)
        _rev1 = cast(int, _sync1.get("rev"))
        # stale loser: rev 0 is always behind (room only advances)
        _sync0 = post(base, "/collab/sync", {}, cookie=_mate)
        _stale = post(base, "/collab/push", {"rev": 0, "text": text},
                       cookie=_mate)
        assert _stale.get("stale") is True, _stale
        _bad = post(base, "/collab/push", {"rev": _rev1, "text": "garbage ((("})
        assert "error" in _bad, _bad
        _sync2 = post(base, "/collab/sync", {}, cookie=_mate)
        assert cast(int, _sync2.get("rev")) == _rev1, _sync2  # bad push never landed
        post(base, "/collab/cursor", {"x": 3, "y": 5, "ref": "R1"})
        _cur = post(base, "/collab/cursor", {"x": 9, "y": 2, "ref": "C1"},
                     cookie=_mate)
        _cur_users = cast(list[dict[str, object]], _cur.get("users"))
        _names = {str(u.get("name")) for u in _cur_users}
        assert {"tester", "teammate"} <= _names, _cur["users"]
        # a third cursor joins the same room: presence is N-wide, colors differ
        _c4 = _hc3.HTTPConnection(_u3.hostname, _u3.port, timeout=30)
        _c4.request("POST", "/auth/signup",
                    json.dumps({"user": "third", "password": "testtest12"}),
                    {"Content-Type": "application/json"})
        _r4 = _c4.getresponse()
        _ck4 = _r4.getheader("Set-Cookie", "")
        assert "ocd_user=" in _ck4, _ck4
        _third = _ck4.split(";")[0].strip()
        _r4.read()
        _cur3 = post(base, "/collab/cursor", {"x": 1, "y": 1, "ref": "R2"},
                     cookie=_third)
        _names3 = {str(u.get("name"))
                   for u in cast(list[dict[str, object]], _cur3.get("users"))}
        assert {"tester", "teammate", "third"} <= _names3, _cur3["users"]
        _cols = {str(u.get("color"))
                 for u in cast(list[dict[str, object]], _cur3.get("users"))}
        assert len(_cols) == 3, _cur3["users"]  # stable color per user
        _cop = post(base, "/collab/op", {"rev": _rev1,
            "op": {"ops": [{"op": "move_part", "ref": "R1", "x": 7, "y": 8}]}},
            cookie=_mate)
        assert _cop.get("applied") == 1, _cop
        _ev = urllib.request.Request(base + "/collab/events")
        _ev.add_header("Cookie", _mate)
        _seen: list[str] = []
        _er = urllib.request.urlopen(_ev, timeout=25)
        _t1 = time.monotonic()
        _buf = b""
        _done = False
        while time.monotonic() - _t1 < 20 and not _done:
            _chunk = _er.read(1)
            if not _chunk:
                break
            _buf += _chunk
            while b"\n\n" in _buf:
                _raw, _buf = _buf.split(b"\n\n", 1)
                for _ln in _raw.split(b"\n"):
                    if _ln.startswith(b"data:"):
                        _seen.append(_ln[5:].decode())
                        if len(_seen) >= 2:
                            _done = True
                            break
                if _done:
                    break
        _er.close()
        assert any('"hello": "teammate"' in _e for _e in _seen), _seen
        print(f"collab ok (push {_cpdt * 1000:.0f}ms, 2 users, stale+presence+op+SSE)")

        _ui = urllib.request.urlopen(urllib.request.Request(
            base + "/", headers={"Cookie": _JAR.get(base, "")})).read().decode()
        assert "scanwrap" in _ui and "scanfiles" in _ui, "scan panel not served"

        for key in ("svg", "sch", "xray"):
            r = post(base, "/render", {"key": key})
            assert not r.get("error"), (key, r.get("error"))
            assert len(cast(str, r["data"])) > 1000, (key, len(cast(str, r["data"])))
        print("render svg+sch+xray ok")
        # x-ray compare: own PNG mostly agrees, bad upload is an error not a 500
        _own = post(base, "/render", {"key": "png"})
        assert not _own.get("error"), _own.get("error")
        _xc = post(base, "/xray", {"png": _own["data"]})
        assert not _xc.get("error"), _xc.get("error")
        assert cast(float, _xc["score"]) > 40, _xc
        assert isinstance(_xc["divs"], list) and "overlay" in _xc, _xc
        _xb = post(base, "/xray", {"png": "!!!not-base64!!!"})
        assert "error" in _xb, _xb
        assert "error" in post(base, "/xray", {}), "empty png not refused"
        print(f"xray compare ok (score={_xc['score']})")
        # photo scan: the panel is served, junk is refused, and a real image
        # goes through the deterministic half (llm=False needs no endpoint).
        assert "error" in post(base, "/scan", {"photos": []}), "empty not refused"
        assert "error" in post(base, "/scan", {"photos": [1, "x"]}), \
            "non-dict photos not refused"
        assert "error" in post(base, "/scan",
                               {"photos": [{"name": "a.png", "data": "!!"}]})
        # unknown POST path uses the JSON error envelope (not a bare 404)
        _unk = post(base, "/no-such-route", {})
        assert "error" in _unk and "unknown path" in str(_unk["error"]), _unk
        try:
            import numpy  # noqa: F401
        except ImportError:
            print("scan ui ok (uploads refused cleanly; no numpy for a real run)")
        else:
            _sr = post(base, "/scan",
                       {"photos": [{"name": "top.png", "data": _own["data"]}],
                        "mm": 40, "llm": False})
            assert not _sr.get("error"), _sr
            assert cast(dict[str, object],
                        cast(dict[str, object], _sr["sides"])["top"])["used"] == 1, _sr
            print("scan ui ok (panel + refusals + one photo through the pipeline)")
        # quote route: cheapest-first bare table + JLC assembly, bad fab errors
        _qq = post(base, "/quote", {"qty": 5})
        assert not _qq.get("error"), _qq.get("error")
        _qrows = cast(list[dict[str, object]], _qq["rows"])
        assert len(_qrows) == 11 and _qrows[0]["fab"] == "jlc", _qrows[:2]
        assert "asm_total" in [r for r in _qrows if r["fab"] == "jlc"][0]
        _qqb = post(base, "/quote", {"qty": 5, "no_parts": True})
        assert all("asm_total" not in r
                   for r in cast(list[dict[str, object]], _qqb["rows"]))
        _qqbad = post(base, "/quote", {"fabs": ["nope"]})
        assert "error" in _qqbad, _qqbad
        print(f"quote ok (cheapest={_qrows[0]['fab']} bare=${_qrows[0]['bare_total']})")

        # the page ships as one inline script: syntax + the highlight wiring.
        # node is dev-only here — skip rather than fail when it's absent.
        node = shutil.which("node")
        if not node:
            print("no node: editor-highlight check skipped")
        else:
            page = get(base, "/").decode()
            pjs = page[page.index("<script>") + 8:page.index("</script>")]
            import re as _re
            _fnm: object = _re.search(r"function edHighlight\(\)\{.*?\n\}", pjs, _re.S)
            assert _fnm, "editor selection does not drive the highlight"
            assert isinstance(_fnm, _re.Match)
            src = _fnm.group(0)
            assert src
            with tempfile.TemporaryDirectory() as td:
                ent = os.path.join(td, "page.js")
                open(ent, "w").write(pjs)
                _rn = subprocess.run([node, "--check", ent], capture_output=True,
                                     text=True, timeout=60)
                assert _rn.returncode == 0, _rn.stderr[-400:]
                hl = os.path.join(td, "hl.js")
                open(hl, "w").write(HL_STUB + src + HL_DRIVE)
                _rn = subprocess.run([node, hl], capture_output=True, text=True,
                                     timeout=60)
                assert _rn.returncode == 0, _rn.stderr[-400:]
                assert _rn.stdout.strip() == HL_EXPECT, _rn.stdout
            assert pjs.count("edHl.has") >= 2, "PCB + SCH must both read edHl"
            print("editor highlight → pcb/sch ok")

        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})
        import re
        m = re.search(r"fix (\S+) at ([\d.]+) ([\d.]+)", text)
        assert m, "blinky needs a fix line for the diff check"
        m0 = m.group(0)
        assert m0 and m.group(1)
        moved = text.replace(m0,
                             f"fix {m.group(1)} at {float(m.group(2)) + 1} {m.group(3)}", 1)
        d2 = post(base, "/build", {"text": moved, "placer": "diffusion",
                                   "router": "maze"})
        assert not d2.get("error"), d2.get("error")
        dd = post(base, "/diff_prev", {})
        assert m.group(1) in str(dd.get("diff")), dd
        print("diff_prev ok:", dd["diff"])

        no_sim = post(base, "/simulate", {"what": "dc"})
        assert "error" in no_sim, no_sim  # blinky has no sim lines
        rc = ("board t 40x30\npart R1 R0805 10k\npart C1 C0805 100n\n"
              "net VIN: R1.1\nnet VO: R1.2 C1.1\nnet GND: C1.2\n"
              "sim vcc VIN 0 5\nsim tran 0.005 500\nsim probe VO\n")
        post(base, "/build", {"text": rc, "placer": "diffusion",
                              "router": "maze"})
        dc = post(base, "/simulate", {"what": "dc"})
        assert not dc.get("error"), dc
        tran = cast(dict[str, object], post(base, "/simulate", {"what": "tran"})["tran"])
        tr = cast(list[object], tran["VO"])
        assert len(tr) == 500 and abs(cast(float, tr[-1]) - 5.0) < 0.05, tr[-3:]
        print(f"simulate dc+tran ok (VO final={tr[-1]}V)")

        gal = post(base, "/candidates", {"placer": "diffusion", "n": 2,
                                         "seed": 3, "iters": 30})
        cands = cast(list[dict[str, object]], gal["candidates"])
        assert len(cands) == 2, gal
        assert gal.get("placer") == "diffusion" and "note" in gal, gal
        assert all(isinstance(k, str)
                   for k in cast(dict[str, object], gal["feasible"])), gal
        pk = post(base, "/pick", {"placer": "diffusion", "router": "lroute",
                                  "n": 2, "seed": 3, "iters": 30, "index": 0})
        assert not pk.get("error"), pk
        assert cast(list[object], pk["traces"]), "picked board routes"
        print(f"gallery ok ({len(cands)} candidates, pick routed)")
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})

        doc = post(base, "/doctor", {})
        checks = cast(list[dict[str, object]], doc["checks"])
        assert checks, doc
        bad = [c["name"] for c in checks if not c["ok"]]
        print(f"doctor ok={doc['ok']} ({len(checks)} checks"
              + (f", degraded: {bad}" if bad else "") + ")")
        # undo/redo walk the text history; export writes fab files
        u = post(base, "/undo", {})
        assert not u.get("error"), u
        r2 = post(base, "/redo", {})
        assert not r2.get("error"), r2
        print("undo/redo ok")
        sv = post(base, "/solve", {"placer": "diffusion", "router": "maze",
                                   "full": True})
        assert not sv.get("error"), sv
        assert cast(list[object], sv["traces"]), "solve routes full quality"
        print(f"solve ok (cost={sv['cost']})")
        ex = post(base, "/export", {})
        assert not ex.get("error"), ex
        assert cast(int, ex["bytes"]) > 1000, ex
        assert str(ex["name"]).endswith("-fab.zip"), ex
        print(f"export ok ({ex['bytes']} byte bundle)")
        # trace delta: a rebuild that carries the caller's trace hash must not
        # re-send the trace list, and the hash must match the list it stands in
        tb = post(base, "/build", {"text": text, "placer": "diffusion",
                                   "router": "maze"})
        th = str(tb.get("thash") or "")
        assert th and cast(list[object], tb["traces"]), (th, len(cast(list[object], tb["traces"])))
        tb2 = post(base, "/build", {"text": text, "thash": th,
                                    "placer": "diffusion", "router": "maze"})
        assert tb2.get("thash") == th, (tb2.get("thash"), th)
        assert tb2["traces"] == [], "traces re-sent despite an unchanged hash"
        print(f"trace delta ok (hash {th}, {len(cast(list[object], tb['traces']))} segments elided)")
        # rebuild blinky: /build saves to disk, screenshot must see blinky
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})

        pico = open(os.path.join(ROOT, "boards", "pico_tmc2209",
                                 "pico_tmc2209.ocd")).read()
        dp = post(base, "/build", {"text": pico, "placer": "compact",
                                   "router": "lroute"})
        assert not dp.get("error"), dp.get("error")
        pparts = cast(dict[str, dict[str, object]], dp["parts"])
        owners = {str(p.get("owner", "")) for p in pparts.values()}
        assert {"Z1_", "Z2_", "Z3_"} <= owners, owners  # instance groups exposed
        print(f"instances ok (owners={sorted(o for o in owners if o)})")
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})

        from ocdcircuit.doctor import find_chromium
        chrom = find_chromium()
        if not chrom:
            print("no chromium: browser half skipped")
            return
        # headless chromium sends no session cookie, so it sees the
        # landing hero: the shot must be console-clean; the markup asserts
        # the hero above.
        with tempfile.TemporaryDirectory() as td:
            shot = os.path.join(td, "shot.png")
            log = os.path.join(td, "console.log")
            with open(log, "wb") as lf:
                p = subprocess.run(
                    [chrom, "--headless=new", "--no-sandbox", "--disable-gpu",
                     f"--window-size=1280,900", "--virtual-time-budget=12000",
                     f"--screenshot={shot}", "--enable-logging=stderr",
                     base + "/"], capture_output=False, stderr=lf, timeout=120)
            assert os.path.isfile(shot), "no screenshot"
            errlog = open(log, "rb").read().decode("utf8", "replace")
            bad = [ln for ln in errlog.splitlines()
                   if "CONSOLE" in ln and ("Uncaught" in ln or "ERROR" in ln)]
            assert not bad, bad[:3]
            print("screenshot landing console-clean ok")
    finally:
        srv.terminate()
        shutil.rmtree(troot, ignore_errors=True)
        _JAR.pop(base, None)

    # --- knowledgebase panel: its own throwaway project (kb/ lives beside the
    # .ocd, and the repo's boards/ must not collect test documents) ----------
    with tempfile.TemporaryDirectory() as kd:
        kboard = os.path.join(kd, "kbpanel.ocd")
        with open(kboard, "w") as f:
            f.write("board kbpanel 20x10 2L\n"
                    "part U1 SOIC8 NE555 lcsc=C1525 datasheet=https://example.invalid/ds.pdf\n"
                    "part R1 R0805 10k\nnet N: U1.1 R1.1\n")
        os.makedirs(os.path.join(kd, "kb", "datasheets"))
        with open(os.path.join(kd, "kb", "NOTES.md"), "w") as f:
            f.write("# notes\nInput supply must stay between 2.7 and 5.5 volts.\n"
                    "Thermal pad soldered to the ground plane.\n")
        kport = free_port()
        kbase = f"http://localhost:{kport}"
        ksrv = subprocess.Popen([sys.executable, "-m", "apps.studio", kboard],
                                cwd=ROOT, env=dict(os.environ, OCD_PORT=str(kport)),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(kbase + "/slots", timeout=1).read()
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("kb studio did not boot")
            login(kbase)
            page = get(kbase, "/").decode()
            assert "id=kbwrap" in page, "kb panel missing from the page"
            assert "id=kbfetch" in page and "id=kbask" in page, "kb controls missing"
            assert "id=kbprefsbtn" in page, "prefs button missing from the page"
            assert "id=xraybar" in page, "xray panel missing from the page"
            assert "id=xraygo" in page and "id=xrayfile" in page, "xray controls missing"
            assert "id=quote" in page, "quote panel missing from the page"
            assert "id=qgo" in page and "id=qqty" in page, "quote controls missing"
            _qq0 = post(kbase, "/quote", {"qty": 5, "no_parts": True})
            assert not _qq0.get("error"), _qq0.get("error")
            _q0rows = cast(list[dict[str, object]], _qq0["rows"])
            assert len(_q0rows) == 11, len(_q0rows)
            assert all(str(r.get("logo", "")).startswith("data:image/png;base64,")
                       for r in _q0rows), "every quote row wears its fab logo"
            slots = json.loads(urllib.request.urlopen(kbase + "/slots", timeout=5).read())
            assert "kb" in slots["view"], slots
            kl = post(kbase, "/kb/list", {})
            assert not kl.get("error"), kl
            assert str(kl["dir"]).endswith("kb"), kl["dir"]
            assert [d["name"] for d in cast(list[dict[str, object]], kl["docs"])] == ["NOTES.md"], kl
            kr = post(kbase, "/kb/read", {"doc": "NOTES.md", "start": 2, "lines": 1})
            assert "2.7 and 5.5 volts" in str(kr["text"]), kr
            kbad = post(kbase, "/kb/read", {"doc": "../../etc/passwd"})
            assert "outside the knowledgebase" in str(kbad.get("error")), kbad
            ks = post(kbase, "/kb/search", {"q": "thermal"})
            assert [h["line"] for h in cast(list[dict[str, object]], ks["hits"])] == [3], ks
            ka = post(kbase, "/kb/ask", {"q": "how much voltage can it take?", "limit": 2})
            assert not ka.get("error"), ka
            assert ka["method"] in ("embeddings", "lexical"), ka  # model optional
            kps = cast(list[dict[str, object]], ka["passages"])
            assert kps and kps[0]["doc"] == "NOTES.md", ka
            kadd = post(kbase, "/kb/add", {"src": os.path.join(kd, "kb", "NOTES.md")})
            assert "NOTES-2.md" in str(kadd.get("added")), kadd  # never clobbers
            kbadadd = post(kbase, "/kb/add", {"src": "https://example.invalid/x.pdf"})
            assert "error" in kbadadd, kbadadd
            # fetch runs in a worker thread (this server is single-threaded) and
            # still answers /kb/list while it works
            kf = post(kbase, "/kb/fetch", {})
            assert not kf.get("error"), kf
            for _ in range(80):
                time.sleep(0.25)
                if not cast(bool, post(kbase, "/kb/list", {})["busy"]):
                    break
            klog = " ".join(" · ".join(
                cast(list[str], post(kbase, "/kb/list", {})["log"])).split())
            assert "skip R1:" in klog, klog     # no datasheet=/lcsc= attr
            assert "FAIL U1:" in klog, klog     # the pinned url cannot resolve
            kp = post(kbase, "/kb/prefs", {})
            assert kp.get("prefs") == [], kp  # no PREFS.md yet
            kpa = post(kbase, "/kb/prefs/add",
                       {"when": "placing connectors", "text": "board edge"})
            assert not kpa.get("error"), kpa
            kp2 = post(kbase, "/kb/prefs", {})
            _prefs = cast(list[dict[str, object]], kp2.get("prefs"))
            assert len(_prefs) == 1 and not _prefs[0]["approved"], kp2
            _kpset = post(kbase, "/kb/prefs/set", {"id": 0, "approved": True})
            assert not _kpset.get("error"), _kpset
            _prefs2 = cast(list[dict[str, object]],
                           post(kbase, "/kb/prefs", {}).get("prefs"))
            assert _prefs2[0]["approved"] is True
            kpbad = post(kbase, "/kb/prefs/set", {"id": 9, "approved": True})
            assert "error" in kpbad, kpbad
            print("kb panel ok (list/read/search/ask/add/fetch/prefs)")
        finally:
            ksrv.terminate()
    print("STUDIO OK")


if __name__ == "__main__":
    main()
