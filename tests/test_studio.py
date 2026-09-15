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
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

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
    _JAR[base] = setck.split(";")[0].strip()
    return _JAR[base]


def png_brightness(path: str, x0: int, y0: int, x1: int, y1: int,
                   thr: int = 80) -> tuple[float, int, int]:
    """Bright-pixel fraction in a crop. Minimal PNG reader (non-interlaced
    truecolor); raises on anything else — screenshots are that shape."""
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, w, h, ctype, idat = 8, 0, 0, 0, b""
    while pos < len(d):
        (ln,) = struct.unpack(">I", d[pos:pos + 4])
        typ = d[pos + 4:pos + 8]
        if typ == b"IHDR":
            w, h, _bd, ctype, _cp, _fl, _iv = struct.unpack(">IIBBBBB", d[pos + 8:pos + 21])
            assert ctype in (2, 6), f"ctype {ctype}"
        elif typ == b"IDAT":
            idat += d[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    ch = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * ch
    n = t = 0
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        prev = line
        if y0 <= y < y1:
            for x in range(x0, x1):
                o = x * ch  # line is the current row, not the image
                t += 1
                if max(line[o:o + 3]) > thr:
                    n += 1
    # walk remaining rows without storing the whole image
    return (n / t if t else 0.0), w, h


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
    assert len(_st_ui._UI_DISPOSERS) == 10, len(_st_ui._UI_DISPOSERS)
    _st_ui.unload_ui()
    assert _st_ui.SLOTS.report("view") == [] and _st_ui.SLOTS.report("toolbar") == []
    assert _st_ui._UI_DISPOSERS == []
    print("ui slot dispose ok (10 contributions, LIFO, once)")

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
        assert "Design PCBs with AI" in _gate, _gate[:200]
        assert "id=herogo" in _gate and "id=ed" not in _gate, _gate[:200]
        _refused = post(base, "/build", {"text": "x"})
        assert _refused.get("login") is True, _refused
        _bad = post(base, "/auth/login",
                    {"user": "tester", "password": "wrongwrong"})
        assert "error" in _bad, _bad
        login(base)
        _dup = post(base, "/auth/signup",
                    {"user": "second", "password": "testtest12"})
        assert "already has an account" in str(_dup.get("error")), _dup
        _me = post(base, "/auth/me", {})
        assert _me.get("user") == "tester", _me
        _in = get(base, "/").decode()
        assert "id=ed" in _in, _in[:200]
        # static shell markers (in the HTML) …
        for frag in ("id=viewtabs", "data-v=pcb", "data-v=sch", "data-v=t3d",
                     "data-v=docs", "id=themebtn"):
            assert frag in _in, f"flux work missing: {frag}"
        # …plus runtime-built pieces (in the inline script, created by JS)
        # and the followups CSS rule (in <style>, not <script>)
        _pjs = _in[_in.index("<script>") + 8:_in.index("</script>")]
        for frag in ("setDark", "setView", "followups", "thought",
                     "contextmenu", "rotRefs", "unpinRefs"):
            assert frag in _pjs, f"flux work missing: {frag}"
        assert "followups:empty" in _in, "flux work missing: followups:empty"
        print("flux agent-rail + tabs + dark ok")
        _sh = post(base, "/shelf", {})
        assert _sh.get("user") == "tester" and isinstance(_sh.get("boards"), list), _sh
        _nb = post(base, "/shelf/new", {"name": "hello"})
        assert not _nb.get("error"), _nb
        _boards = _nb.get("boards")
        assert isinstance(_boards, list)
        assert any(isinstance(b, dict) and b.get("name") == "hello.ocd"
                   for b in _boards), _nb
        _nb2 = post(base, "/shelf/new", {"name": "hello"})
        assert "already on your shelf" in str(_nb2.get("error")), _nb2
        _lo = post(base, "/auth/logout", {})
        assert _lo.get("ok") is True, _lo
        _JAR.pop(base, None)
        _out = urllib.request.urlopen(base + "/").read().decode()
        assert "Design PCBs with AI" in _out, _out[:200]
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
        _p0 = json.loads(urllib.request.urlopen(base + "/poll", timeout=5).read())
        assert _p0["clean"] is True, _p0
        with open(tboard, "a") as _f:
            _f.write("# external edit\n")
        _p1 = json.loads(urllib.request.urlopen(base + "/poll", timeout=5).read())
        assert _p1["clean"] is False, _p1
        _r = post(base, "/reload", {})
        assert "error" not in _r, _r
        _p2 = json.loads(urllib.request.urlopen(base + "/poll", timeout=5).read())
        assert _p2["clean"] is True, _p2
        t = time.time()
        d = post(base, "/build", {"text": text, "placer": "diffusion",
                                 "router": "maze"})
        dt = time.time() - t
        assert not d.get("error"), d.get("error")
        assert dt < BUILD_BUDGET, f"quick build {dt:.2f}s over {BUILD_BUDGET}s"
        from typing import cast
        parts = cast(dict[str, object], d["parts"])
        score = cast(dict[str, object], d["score"])
        lint = cast(dict[str, object], d["lint"])
        assert len(parts) == 10, len(parts)
        assert cast(float, score["total"]) > 0, score
        assert isinstance(lint["errors"], list), lint
        print(f"build {dt:.2f}s score={score['total']} ok")

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
        print(f"xray compare ok (score={_xc['score']})")
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

        chrom = (shutil.which("chromium") or shutil.which("chromium-browser")
                 or shutil.which("google-chrome") or shutil.which("chrome"))
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
            assert "id=xraybar" in page, "xray panel missing from the page"
            assert "id=xraygo" in page and "id=xrayfile" in page, "xray controls missing"
            assert "id=quote" in page, "quote panel missing from the page"
            assert "id=qgo" in page and "id=qqty" in page, "quote controls missing"
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
            print("kb panel ok (list/read/search/ask/add/fetch)")
        finally:
            ksrv.terminate()
    print("STUDIO OK")


if __name__ == "__main__":
    main()
