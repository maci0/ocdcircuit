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


def post(base: str, path: str, body: dict[str, object]) -> dict[str, object]:
    q = urllib.request.Request(base + path, json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
    out = urllib.request.urlopen(q, timeout=30).read()
    res = json.loads(out)
    assert isinstance(res, dict)
    return res


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
    fb = subprocess.Popen(
        [sys.executable, "-c",
         "import os; os.environ['OCD_PORT']='bogus'; "
         "import apps.studio as S; S.main()"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                _url.urlopen("http://localhost:8077/slots", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("fallback server did not boot: "
                                 + str(fb.stderr.read()[:300] if fb.stderr else b""))
    finally:
        fb.terminate()
        _err = fb.stderr.read().decode() if fb.stderr else ""
        assert "bad OCD_PORT" in _err, _err[:300]
    # UI contributions are disposable: every _slot() keeps the disposer
    # register() handed back, and unload_ui() runs them LIFO — the previous
    # code dropped the disposer, which made all ten rows permanent module state
    if ROOT not in sys.path:  # in-process import: sys.path[0] is tests/
        sys.path.insert(0, ROOT)
    from apps import studio as _st_ui
    assert "kb" in _st_ui.SLOTS.report("view"), _st_ui.SLOTS.report("view")
    assert len(_st_ui._UI_DISPOSERS) == 10, len(_st_ui._UI_DISPOSERS)
    _st_ui.unload_ui()
    assert _st_ui.SLOTS.report("view") == [] and _st_ui.SLOTS.report("toolbar") == []
    assert _st_ui._UI_DISPOSERS == []
    print("ui slot dispose ok (10 contributions, LIFO, once)")

    port = free_port()
    base = f"http://localhost:{port}"
    env = dict(os.environ, OCD_PORT=str(port))
    board_orig = open(BOARD).read()  # studio saves to disk; restore after
    srv = subprocess.Popen([sys.executable, "-m", "apps.studio", BOARD],
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

        text = open(BOARD).read()
        post(base, "/build", {"text": text, "placer": "diffusion",
                              "router": "maze"})  # warm-up: cold caches aren't UX
        # file-watch: /poll reports clean after our save; an external
        # edit flips it dirty; /reload adopts it (undo keeps ours)
        _p0 = json.loads(urllib.request.urlopen(base + "/poll", timeout=5).read())
        assert _p0["clean"] is True, _p0
        with open(BOARD, "a") as _f:
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

        for key in ("svg", "sch"):
            r = post(base, "/render", {"key": key})
            assert not r.get("error"), (key, r.get("error"))
            assert len(cast(str, r["data"])) > 1000, (key, len(cast(str, r["data"])))
        print("render svg+sch ok")

        # the page ships as one inline script: syntax + the highlight wiring.
        # node is dev-only here — skip rather than fail when it's absent.
        node = shutil.which("node")
        if not node:
            print("no node: editor-highlight check skipped")
        else:
            page = urllib.request.urlopen(base + "/").read().decode()
            pjs = page[page.index("<script>") + 8:page.index("</script>")]
            import re as _re
            fn = _re.search(r"function edHighlight\(\)\{.*?\n\}", pjs, _re.S)
            assert fn, "editor selection does not drive the highlight"
            with tempfile.TemporaryDirectory() as td:
                ent = os.path.join(td, "page.js")
                open(ent, "w").write(pjs)
                _rn = subprocess.run([node, "--check", ent], capture_output=True,
                                     text=True, timeout=60)
                assert _rn.returncode == 0, _rn.stderr[-400:]
                hl = os.path.join(td, "hl.js")
                open(hl, "w").write(HL_STUB + fn.group(0) + HL_DRIVE)
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
        moved = text.replace(m.group(0),
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
            frac, w, h = png_brightness(shot, 420, 120, 860, 750)
            assert (w, h) == (1280, 900), (w, h)
            assert frac > PCB_BRIGHT_MIN, f"PCB black? bright={frac:.4f}"
            errlog = open(log, "rb").read().decode("utf8", "replace")
            bad = [ln for ln in errlog.splitlines()
                   if "CONSOLE" in ln and ("Uncaught" in ln or "ERROR" in ln)]
            assert not bad, bad[:3]
            print(f"screenshot pcb-bright={frac:.4f} console-clean ok")
    finally:
        srv.terminate()
        with open(BOARD, "w") as f:
            f.write(board_orig)

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
            page = urllib.request.urlopen(kbase + "/").read().decode()
            assert "id=kbwrap" in page, "kb panel missing from the page"
            assert "id=kbfetch" in page and "id=kbask" in page, "kb controls missing"
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
