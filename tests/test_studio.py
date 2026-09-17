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
import unittest
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


class SessionExpiryTests(unittest.TestCase):
    def test_session_deadline_is_exclusive(self) -> None:
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio

        for elapsed in (studio._SESSION_TTL - 0.5,
                        studio._SESSION_TTL, studio._SESSION_TTL + 0.5):
            with self.subTest(elapsed=elapsed), \
                    patch.object(studio, "_SESSIONS", {}), \
                    patch("apps.studio.time.monotonic", return_value=100.0) as clock:
                token = studio._new_session("alice")
                clock.return_value = 100.0 + elapsed
                headers = {"Cookie": f"{studio._AUTH_COOKIE}={token}"}
                live = elapsed < studio._SESSION_TTL
                self.assertEqual(studio._authed(headers), "alice" if live else None)
                self.assertEqual(token in studio._SESSIONS, live)

    def test_session_purge_at_deadline(self) -> None:
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio

        with patch.object(studio, "_SESSIONS", {}), \
                patch("apps.studio.time.monotonic", return_value=100.0):
            token = studio._new_session("alice")
            deadline = 100.0 + studio._SESSION_TTL
            with studio._AUTH_MU:
                studio._purge_sessions(deadline - 0.5)
                self.assertIn(token, studio._SESSIONS)
                studio._purge_sessions(deadline)
                self.assertNotIn(token, studio._SESSIONS)


class CompressionTests(unittest.TestCase):
    def test_compression_preserves_or_reduces_transfer_size(self) -> None:
        import gzip
        import io
        import random
        from email.message import Message
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio

        opaque = random.Random(0).randbytes(2048)
        cases = (
            ("image/png", b"png" * 1024, False),
            ("font/woff2", b"font" * 1024, False),
            ("application/zip", b"archive" * 1024, False),
            ("text/javascript; charset=utf-8", opaque, False),
            ("text/css; charset=utf-8", b"body{}", False),
            ("text/javascript; charset=utf-8", b"const value = 1;\n" * 1024, True),
            ("image/svg+xml", b"<svg></svg>" * 1024, True),
        )
        for content_type, body, compressed in cases:
            with self.subTest(content_type=content_type, compressed=compressed):
                handler = studio.H.__new__(studio.H)
                handler.headers = Message()
                handler.headers["Accept-Encoding"] = "gzip"
                handler.wfile = io.BytesIO()
                with patch.object(handler, "send_response"), \
                        patch.object(handler, "send_header") as send_header, \
                        patch.object(handler, "end_headers"):
                    handler._write_bytes(200, body, content_type, doc=True)
                headers = dict(call.args for call in send_header.call_args_list)
                wire = handler.wfile.getvalue()
                self.assertEqual(headers.get("Content-Encoding") == "gzip", compressed)
                self.assertEqual(int(headers["Content-Length"]), len(wire))
                self.assertEqual(gzip.decompress(wire) if compressed else wire, body)
                self.assertLessEqual(len(wire), len(body))
                print(f"{content_type}: {len(body)} -> {len(wire)} bytes")


class SimulationReadoutTests(unittest.TestCase):
    def test_analysis_labels_and_empty_results(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "apps", "web", "legacy.js")) as source:
            script = source.read().split("function drawDRC(r){", 1)[1].split(
                "function tidyVal", 1)[0]
        drive = """
import assert from 'node:assert/strict';
const ui = {state:{}, set(patch){Object.assign(this.state, patch);}};
const drawTidy = () => {};
""" + "function drawDRC(r){" + script + """
const base = {errors:[], warnings:[], fab:'jlc'};
drawDRC({...base, sim:{VO:5}, sim_problems:['VO below target'],
  tran:{VO:[0,5]}, ac:{VO:[1,0.1]}, f1:1000});
assert.deepEqual(ui.state.drc.slice(1), [
  {cls:'ok', text:'DC: VO=5V'},
  {cls:'err', text:'Simulation error: VO below target'},
  {cls:'ok', text:'Transient: VO 5.00V [0.00,5.00] (2pts)'},
  {cls:'ok', text:'AC: VO -20.0dB@1000Hz [0.0dB pk] (2pts)'}
]);
for (const result of [base, {...base, sim:{}, sim_problems:[], tran:{}, ac:{}}]) {
  drawDRC(result);
  assert.equal(ui.state.drc.length, 1);
  assert.match(ui.state.drc[0].text, /DRC clean/);
}
"""
        result = subprocess.run(
            [node, "--input-type=module"], input=drive, cwd=ROOT,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class LandingTests(unittest.TestCase):
    def test_first_paint_modules_are_discovered_in_document(self) -> None:
        import gzip
        import io
        from email.message import Message
        from html.parser import HTMLParser
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio

        links: list[str] = []

        class Links(HTMLParser):
            def handle_starttag(self, tag: str,
                                attrs: list[tuple[str, str | None]]) -> None:
                values = dict(attrs)
                href = values.get("href")
                if tag == "link" and values.get("rel") == "modulepreload" and href:
                    links.append(href)

        handler = studio.H.__new__(studio.H)
        handler.path = "/"
        handler.headers = Message()
        handler.headers["Accept-Encoding"] = "gzip"
        handler.wfile = io.BytesIO()
        with patch.object(studio, "_authed", return_value=None), \
                patch.object(handler, "send_response"), \
                patch.object(handler, "send_header"), \
                patch.object(handler, "end_headers"):
            handler.do_GET()
        wire = handler.wfile.getvalue()
        document = gzip.decompress(wire).decode("utf-8")
        Links().feed(document)
        print(f"landing document: {len(wire)} gzip bytes; "
              f"{len(links)} module dependencies discovered in HTML")
        self.assertCountEqual(links, [
            "/web/vendor/preact.module.js",
            "/web/vendor/hooks.module.js",
            "/web/vendor/htm.module.js",
            "/web/html.js",
            "/web/api.js",
            "/web/art.js",
        ])
        for href in links:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "apps", href[1:])))

    def test_collaboration_is_labeled_as_illustrative(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "apps", "web", "landing.js")) as source:
            script = source.read().split("const PEOPLE =", 1)[1].split(
                "const FabTile =", 1)[0]
        drive = """
import assert from 'node:assert/strict';
import html from './apps/web/html.js';
""" + "const PEOPLE =" + script + """
const group = Collab();
assert.match(group.props['aria-label'], /example/i);
const cards = group.props.children.flat(Infinity);
assert.equal(cards.length, 3);
function text(node) {
  if (node == null) return '';
  if (typeof node !== 'object') return String(node);
  return [node.props.children].flat(Infinity).map(text).join('');
}
for (const card of cards) {
  const copy = text(card);
  assert.match(copy, /Illustrative example/);
  assert.match(copy, /not a live session/);
  assert.doesNotMatch(copy, /rev 42|pushing/);
}
"""
        result = subprocess.run(
            [node, "--input-type=module"], input=drive, cwd=ROOT,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class KnowledgebaseImportTests(unittest.TestCase):
    def test_local_imports_use_project_access_checks(self) -> None:
        import io
        from email.message import Message
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio
        from ocdcircuit.kb import KB

        with tempfile.TemporaryDirectory() as root:
            kb = KB(root)
            source = os.path.join(root, "notes.md")
            with open(source, "w", encoding="utf-8") as f:
                f.write("project notes\n")
            with open(os.path.join(root, ".ocd-users"), "w", encoding="utf-8") as f:
                f.write("account fixture\n")
            with patch.multiple(studio, ROOT=root, BASE=root, START_DIR=root,
                                SRC=os.path.join(root, "board.ocd")), \
                    patch.object(studio, "_authed", return_value="tester"), \
                    patch.object(studio, "_kb", return_value=kb):
                for path, allowed in ((source, True), ("notes.md", True),
                                      (os.path.join(root, ".ocd-users"), False),
                                      (".ocd-users", False)):
                    with self.subTest(path=path):
                        handler = studio.H.__new__(studio.H)
                        handler.path = "/kb/add"
                        body = json.dumps({"src": path}).encode()
                        handler.headers = Message()
                        handler.headers["Content-Length"] = str(len(body))
                        handler.rfile = io.BytesIO(body)
                        with patch.object(handler, "_send") as send:
                            handler.do_POST()
                        response = send.call_args.args[0]
                        if allowed:
                            self.assertNotIn("error", response)
                            self.assertEqual(response["added"], "notes.md")
                            self.assertEqual(kb.text(response["added"]),
                                             "project notes\n")
                        else:
                            self.assertIn("outside the project root",
                                          response["error"])
                self.assertEqual(len(kb.docs()), 1)


class RevisionAccessTests(unittest.TestCase):
    def test_revision_queries_are_scoped_to_authorized_board_files(self) -> None:
        import io
        from email.message import Message
        from unittest.mock import patch
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from apps import studio

        with tempfile.TemporaryDirectory() as root:
            for rel in ("board.ocd", ".users/alice/private.ocd",
                        ".users/bob/own.ocd", "literal*.ocd"):
                full = os.path.join(root, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write("board fixture 40x30\n")
            with patch.multiple(studio, ROOT=root, BASE=root, START_DIR=root,
                                SRC=os.path.join(root, "board.ocd")), \
                    patch.object(studio, "_authed", return_value="bob"), \
                    patch.object(studio, "_git_status", return_value={}), \
                    patch.object(studio, "_git", return_value="") as git:
                cases: tuple[tuple[str, dict[str, object], str | None], ...] = (
                    ("/vcs", {"path": ".users/alice/private.ocd"}, None),
                    ("/vcs", {"path": "."}, None),
                    ("/vcs", {"path": ".users"}, None),
                    ("/vcs", {"path": ".ocd-users"}, None),
                    ("/vcs", {"path": ":(glob)**"}, None),
                    ("/vcs", {}, "board.ocd"),
                    ("/vcs", {"path": ".users/bob/own.ocd"},
                     ".users/bob/own.ocd"),
                    ("/vcs", {"path": "literal*.ocd"}, "literal*.ocd"),
                    ("/vcs/diff", {"hash": "abcd1234"}, "board.ocd"),
                    ("/vcs/diff", {}, "board.ocd"),
                )
                for route, payload, target in cases:
                    with self.subTest(route=route, payload=payload):
                        git.reset_mock()
                        handler = studio.H.__new__(studio.H)
                        handler.path = route
                        body = json.dumps(payload).encode()
                        handler.headers = Message()
                        handler.headers["Content-Length"] = str(len(body))
                        handler.rfile = io.BytesIO(body)
                        with patch.object(handler, "_send") as send:
                            handler.do_POST()
                        response = send.call_args.args[0]
                        if target is None:
                            self.assertIn("error", response)
                            git.assert_not_called()
                        else:
                            self.assertNotIn("error", response)
                            args = git.call_args.args
                            self.assertEqual(args[0], "--literal-pathspecs")
                            self.assertEqual(args[-2:], ("--", target))
                            if payload.get("hash"):
                                self.assertIn("abcd1234^{commit}", args)
                                self.assertIn("--format=", args)


class UploadTests(unittest.TestCase):
    def run_handler(self, start: str, end: str, drive: str) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "apps", "web", "legacy.js")) as source:
            script = source.read().split(start, 1)[1].split(end, 1)[0]
        stub = """
import assert from 'node:assert/strict';
const elements = {};
const $ = id => elements[id] ||= {value:'', files:[]};
const ui = {state:{}, set(patch){Object.assign(this.state, patch);}};
const calls = [];
const api = async (path, body) => {
  calls.push({path, body});
  return path === '/fs/import' ? {note:'imported test.fp'} :
    {score:1, missing:0, extra:0, divs:[]};
};
const DIR = '.';
let refreshed = false;
const loadTree = () => {refreshed = true;};
const setEditor = () => {};
const push = () => {};
let readDone;
class FileReader {
  readAsDataURL(file) {
    this.result = file.url;
    readDone = Promise.resolve().then(() => this.onload());
  }
}
"""
        result = subprocess.run(
            [node, "--input-type=module"], input=stub + start + script + drive,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_import_payload(self) -> None:
        self.run_handler("if($('importfile'))", "function unpinRefs", """
const payload = Buffer.from('footprint TINY 1x1\\npad 1 0 0 0.5 0.5\\n').toString('base64');
$('importfile').files = [{name:'test.fp', url:'data:text/plain;base64,' + payload}];
$('importfile').onchange();
await readDone;
assert.deepEqual(calls, [{path:'/fs/import', body:{name:'test.fp', data:payload}}]);
assert.equal(ui.state.importStat, 'imported test.fp');
assert.equal(refreshed, true);
assert.equal($('importfile').value, '');
""")

    def test_xray_payload(self) -> None:
        self.run_handler("let xrayRaw='';", "async function hist", """
const payload = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB';
$('xrayfile').files = [{name:'scan.png', url:'data:image/png;base64,' + payload}];
$('xrayfile').onchange();
await readDone;
assert.match(ui.state.xrayStat, /scan.png ready/);
await $('xraygo').onclick();
assert.deepEqual(calls, [{path:'/xray', body:{png:payload, dx:0, dy:0, scale:1, thr:100}}]);
assert.match(ui.state.xrayStat, /score 1/);
""")


class PartFilterTests(unittest.TestCase):
    def test_hidden_parts_remain_searchable(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "apps", "web", "legacy.js")) as source:
            script = source.read()
        visibility = script.split("function partShown", 1)[1].split("function drawPCB", 1)[0]
        rows = script.split("const MAX_ROWS=", 1)[1].split("$('partlist').addEventListener", 1)[0]
        drive = """
import assert from 'node:assert/strict';
const VIS={parts:{R1:false},filter:'  r0805  '};
const S={cur:{parts:{R1:{value:'10k',fp:'R0805'},C1:{value:'100n',fp:'C0805'}}}};
const edHl=new Set();
const ui={state:{partRows:[]},set(patch){Object.assign(this.state,patch);}};
const visSave=()=>{},markDirty=()=>{};
""" + "function partShown" + visibility + "const MAX_ROWS=" + rows + """
renderParts();
assert.deepEqual(ui.state.partRows.map(r=>r.ref),['R1']);
assert.equal(ui.state.partRows[0].hidden,true);
assert.equal(partShown('R1',S.cur),false);
assert.equal(ui.state.partNote,'0/1 shown · 1 matching of 2');
VIS.parts.R1=true;paintParts();
assert.equal(ui.state.partRows[0].hidden,false);
assert.equal(partShown('R1',S.cur),true);
VIS.filter='missing';renderParts();
assert.equal(ui.state.partRows.length,0);
assert.equal(ui.state.partNote,'no matching parts; clear the filter to see all 2');
VIS.filter=' ';renderParts();
assert.equal(ui.state.partRows.length,2);
assert.equal(ui.state.partNote,'2/2 shown');
S.cur.parts=Object.fromEntries(Array.from({length:405},(_,i)=>['R'+i,{fp:'R0805'}]));
VIS.filter='R0805';renderParts();
assert.equal(ui.state.partRows.length,400);
assert.equal(ui.state.partNote,'400/400 shown · first 400 of 405 matching parts');
setAllParts(false,S.cur);
assert.equal(ui.state.partRows.length,400);
assert.equal(ui.state.partRows.every(r=>r.hidden),true);
setAllParts(true,S.cur);
assert.equal(ui.state.partRows.every(r=>r.on),true);
"""
        result = subprocess.run(
            [node, "--input-type=module"], input=drive, cwd=ROOT,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class ChatFormTests(unittest.TestCase):
    def run_chat(self, drive: str) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "apps", "web", "legacy.js")) as source:
            script = source.read()
        handler = script.split("let msgSeq=0;", 1)[1].split("function toast(", 1)[0]
        stub = """
import assert from 'node:assert/strict';
const elements = {};
const $ = id => elements[id] ||= {value:'',disabled:false,handlers:{},
  addEventListener(name,fn){this.handlers[name]=fn;}};
$('composer').querySelector = () => $('send');
const ui = {state:{msgs:[],followups:[]}, set(patch){Object.assign(this.state, patch);}};
const calls = [];
let chatResponse = {reply:'Board checked'}, acceptClear=true;
const confirm = () => acceptClear;
const api = async (path, body) => {
  calls.push({path, body});
  if(chatResponse instanceof Error)throw chatResponse;
  return chatResponse;
};
const applyState = () => {};
const loadVCS = () => {};
"""
        result = subprocess.run(
            [node, "--input-type=module"], input=stub + "let msgSeq=0;" + handler + drive,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_send_and_clear(self) -> None:
        self.run_chat("""
let prevented=false;
const event={preventDefault(){prevented=true;}};
assert.equal(typeof $('composer').onsubmit,'function');
$('ask').value='   ';
await $('composer').onsubmit(event);
assert.equal(prevented,true);
assert.equal(calls.length,0);
$('ask').value='  solve the unroutable net  ';
$('chatauto').checked=true;
await $('composer').onsubmit(event);
assert.deepEqual(calls, [{path:'/chat', body:{text:'solve the unroutable net', auto:true}}]);
assert.equal(ui.state.msgs[0].text,'solve the unroutable net');
assert.match(String(ui.state.followups.map(f=>f.label)), /Route and verify/);
assert.equal($('ask').value,'');
assert.equal($('send').disabled,false);
acceptClear=false;
await $('chatclear').onclick();
assert.equal(calls.length,1);
acceptClear=true;
await $('chatclear').onclick();
assert.deepEqual(calls[1], {path:'/chat/reset', body:{}});
assert.deepEqual(ui.state.msgs, []);
assert.deepEqual(ui.state.followups, []);
""")

    def test_failed_send_keeps_draft_and_proposals(self) -> None:
        self.run_chat("""
const event={preventDefault(){}};
const proposal={kind:'prop',prop:'p1'};
ui.state.msgs=[proposal];
for(const response of [{error:'Model unavailable'},new Error('network')]){
  chatResponse=response;
  $('ask').value='keep this draft';
  await $('composer').onsubmit(event);
  assert.equal($('ask').value,'keep this draft');
  assert.equal($('send').disabled,false);
  assert.equal($('chatclear').disabled,false);
  assert.ok(ui.state.msgs.includes(proposal));
  assert.equal(ui.state.msgs.at(-1).who,'err');
  assert.equal(ui.state.msgs.some(m=>m.text==='thinking…'),false);
}
chatResponse={error:'Reset failed'};
await $('chatclear').onclick();
assert.ok(ui.state.msgs.includes(proposal));
assert.equal($('chatclear').disabled,false);
""")

    def test_pending_send_preserves_new_draft_and_blocks_duplicates(self) -> None:
        self.run_chat("""
const event={preventDefault(){}};
let resolve;
chatResponse=new Promise(done=>resolve=done);
$('ask').value='first draft';
const sending=$('composer').onsubmit(event);
assert.equal($('send').disabled,true);
assert.equal($('chatclear').disabled,true);
await $('composer').onsubmit(event);
await $('chatclear').onclick();
assert.equal(calls.length,1);
$('ask').value='next draft';
resolve({reply:'Done'});
await sending;
assert.equal($('ask').value,'next draft');
let submitted=0;
$('composer').requestSubmit=()=>submitted++;
$('ask').handlers.keydown({key:'Enter',ctrlKey:true,preventDefault(){}});
$('ask').handlers.keydown({key:'Enter',metaKey:true,preventDefault(){}});
$('ask').handlers.keydown({key:'Enter',preventDefault(){}});
assert.equal(submitted,2);
""")


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
    frontend = unittest.TextTestRunner().run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    assert frontend.wasSuccessful(), "frontend handler tests failed"
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
    # the studio's own UI is components under apps/web/ now, so importing it
    # registers nothing: the slot registry is the plugin contract (see the
    # panels.js assertions below for the built-in panels themselves)
    assert _st_ui.SLOTS.report("view") == [], _st_ui.SLOTS.report("view")
    assert _st_ui._UI_DISPOSERS == [], _st_ui._UI_DISPOSERS
    # three contributions through the plugin path (_slot returns None: it is
    # the studio's wrapper that also keeps the disposer)
    for _i in range(3):
        _st_ui._slot("view", f"probe{_i}",
                     (lambda n: lambda s: f"<b id=probe{n}>x</b>")(_i),
                     order=float(_i))
    assert _st_ui.SLOTS.report("view") == ["probe0", "probe1", "probe2"] or \
        set(_st_ui.SLOTS.report("view")) == {"probe0", "probe1", "probe2"}
    assert "probe1" in _st_ui.SLOTS.render("view", None)
    # every _slot() keeps the disposer register() handed back and unload_ui()
    # runs them LIFO — the previous code dropped the disposer, which made each
    # row permanent module state (the inverse of importing the UI)
    assert len(_st_ui._UI_DISPOSERS) == 3, len(_st_ui._UI_DISPOSERS)
    _st_ui.unload_ui()
    assert _st_ui.SLOTS.report("view") == [] and _st_ui.SLOTS.report("toolbar") == []
    assert _st_ui._UI_DISPOSERS == []
    assert _st_ui.SLOTS.render("view", None) == ""
    print("ui slot dispose ok (plugin contributions, LIFO, once)")

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
    # chat turn lock: a second ask while busy must not reach the LLM
    _st_ui.H.chat_busy = True
    try:
        _chat_busy = _st_ui.H.ask("hello", False)
        assert "already thinking" in str(_chat_busy.get("error")), _chat_busy
    finally:
        _st_ui.H.chat_busy = False
    # deterministic sim intent: no LLM call, proposal carries expect line
    _old_src, _old_base = _st_ui.H.src_text, _st_ui.BASE
    try:
        _st_ui.H.src_text = ("board t 40x30 2L\npart R1 R0805 10k\n"
                             "part R2 R0805 4k7\nnet VIN: R1.1\n"
                             "net VO: R1.2 R2.1\nnet GND: R2.2\n"
                             "sim vcc VIN 9\n")
        _st_ui.BASE = os.path.dirname(BOARD)
        _ir = _st_ui.H.ask("VO should settle at 2.88V", False)
        assert "error" not in _ir, _ir
        assert "sim expect VO final" in str(_ir.get("reply", "")), _ir
        assert any("sim expect VO final" in str(p.get("diff", ""))
                   for p in cast(list[dict[str, object]],
                                 _ir.get("proposals", []))), _ir
        # non-intent yields no constraints (fallthrough needs no network)
        from ocdcircuit import agent as _ag2, sim as _sim2
        _ib = _ag2.loads(_st_ui.H.src_text, base=_st_ui.BASE)
        assert _sim2.intent(_ib, "route the board please") is None
    finally:
        _st_ui.H.src_text, _st_ui.BASE = _old_src, _old_base
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
    # the project panel renders folders and non-board files too: give it one of
    # each, so the tree's browse + preview paths have something to click
    os.makedirs(os.path.join(troot, "sub"), exist_ok=True)
    open(os.path.join(troot, "notes.md"), "w").write("notes for the board\n")
    open(os.path.join(troot, "sub", "inner.md"), "w").write("inner\n")
    # a knowledgebase beside the board: the kb panel lists it and opens it
    os.makedirs(os.path.join(troot, "kb"), exist_ok=True)
    open(os.path.join(troot, "kb", "NOTES.md"), "w").write(
        "# notes\nInput supply must stay between 2.7 and 5.5 volts.\n")
    # a real repo in the throwaway root: the revisions panel lists commits and
    # shows a diff, so it needs history to render
    for _g in (["init", "-q"], ["add", "-A"],
               ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "commit", "-q", "-m", "fixture: boards"]):
        subprocess.run(["git", *_g], cwd=troot, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        # ESM shell: copy renders client-side; the shell carries the
        # module script + mount point, copy lives in landing.js
        assert '<script type=module src="/web/landing.js">' in _gate, _gate[:400]
        assert "id=app" in _gate and "id=ed" not in _gate, _gate[:400]
        _land = urllib.request.urlopen(base + "/web/landing.js").read().decode()
        assert "Your whole team. One board." in _land, _land[:200]
        assert "id=herogo" in _land or "herogo" in _land, _land[:400]
        _fabs = json.loads(urllib.request.urlopen(base + "/fabs").read().decode())
        assert len(_fabs) == 11, _fabs
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
        assert '<script type=module src="/web/landing.js">' in _plain, _plain[:400]
        assert "'/fab-logo/'" in _land or '"/fab-logo/"' in _land, _land[:2000]
        # fab tiles stay on /fab-logo/*; brand favicon may be a tiny SVG data URI
        assert "data:image/png" not in _plain, _plain[:400]
        assert 'rel=icon href="data:image/svg+xml,' in _plain, _plain[:500]
        assert len(_plain) < 40_000, len(_plain)  # was ~120 KB with inlined tiles
        assert len(_gz_body) < len(_plain), (len(_gz_body), len(_plain))
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
        # /auth/profile is under the keyhole prefix but must still demand a session
        _prof_deny = post(base, "/auth/profile", {"display": "Ghost"})
        assert _prof_deny.get("login") is True, _prof_deny
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
        _nfdedge = post(base, "/auth/profile",
                        {"display": "x" * 39 + "e\u0301"})
        assert not _nfdedge.get("error"), _nfdedge
        assert _nfdedge.get("display") == "x" * 39 + "\u00e9", _nfdedge
        for _long_display in ("y" * 40 + "\u00e9",
                              "x" * 39 + "q\u0301",
                              "x" * 39 + "\U0001f1fa\U0001f1f8"):
            _toolong = post(base, "/auth/profile", {"display": _long_display})
            assert "error" in _toolong, _toolong
            assert post(base, "/auth/me", {})["display"] == "x" * 39 + "\u00e9"
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
        # the page is a shell: what matters is that it is the workshop page
        # (mount point + modules), not which panel markup it carries
        _open_priv = get(base, "/?board=private-board").decode()
        assert "id=app" in _open_priv and "id=slots" in _open_priv, _open_priv[:200]
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
        _kb_creds = post(base, "/kb/add", {"src": os.path.join(troot, ".ocd-users")})
        assert "outside the project root" in str(_kb_creds.get("error", "")), _kb_creds
        # restore tester onto the launch board for the rest of the suite
        _back = post(base, "/fs/open", {"path": "blinky_555.ocd"})
        assert "error" not in _back, _back
        _in = get(base, "/").decode()
        _JS = os.path.join(ROOT, "apps", "web")
        _panels = open(os.path.join(_JS, "panels.js")).read()
        _views = open(os.path.join(_JS, "views.js")).read()
        assert "id=edwrap" in _panels, "editor panel missing from panels.js"
        # the project filetree is a component (it re-renders), the editor shell
        # is not: each id is asserted where it now lives
        assert "id=importfile" in _views and "id=importstat" in _views, \
            "import picker missing"
        assert "id=tree" in _views, "project tree missing"
        # the frontend is three files under apps/web/: the panels ride in the
        # shell as slot markup, the chrome is a preact module, the behaviour is
        # a module, and the tokens are a stylesheet. Assert each where it lives.
        _chrome = open(os.path.join(_JS, "workshop.js")).read()
        _wjs = open(os.path.join(_JS, "legacy.js")).read()
        _wcss = open(os.path.join(_JS, "workshop.css")).read()
        # the tab table is data now (VIEWS in workshop.js), so the row is the
        # marker: one view id per tab, plus the toggle
        for frag in ("id=viewtabs", "id=themebtn", "['all',", "['pcb',",
                     "['sch',", "['t3d',", "['docs',"):
            assert frag in _chrome, f"flux work missing: {frag}"
        # …plus runtime-built pieces (created by JS)
        # and the followups CSS rule (in the stylesheet)
        for frag in ("setDark", "setView", "showCockpit", "followups", "thought",
                     "contextmenu", "rotRefs", "unpinRefs"):
            assert frag in _wjs, f"flux work missing: {frag}"
        assert "followups:empty" in _wcss, "flux work missing: followups:empty"
        # proper menus: solve stays top-level, the rest lives in named menus
        for frag in ("id=m-board", "id=m-edit", "id=m-sim",
                      "id=m-tools", "id=m-live", "id=roomnote"):
            assert frag in _chrome, f"menu missing: {frag}"
        # the Engines menu is its own component: its option lists arrive with
        # /load, after the first paint, so it renders from state
        for frag in ("id=m-engines", "id=placer", "id=router", "id=silk", "id=fab"):
            assert frag in _views, f"engine picker missing from views.js: {frag}"
        # every action keeps its id (handlers never rebind)
        for frag in ("id=stamp", "id=undo", "id=redo", "id=diffprev", "id=commit",
                      "id=chatbtn", "id=chatauto", "id=sharebtn", "id=room"):
            assert frag in _chrome, f"control id missing: {frag}"
        # the two labels that change on click are components: nothing writes
        # text into a node preact renders
        for frag in ("id=dl", "id=simbtn", "id=srcnote", "id=solve", "id=dice",
                     "id=fab_dl", "id=qgo"):
            assert frag in _views, f"label component missing: {frag}"
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
        # retry / double-click: same bytes → same board, no blinky_555-2
        _tmpl_again = post(base, "/shelf/from_template", {"name": "blinky_555.ocd"})
        assert _tmpl_again.get("name") == "blinky_555", _tmpl_again
        _shelf_names = [str(b.get("name")) for b in
                        cast(list[dict[str, object]], _tmpl_again.get("boards") or [])]
        assert "blinky_555-2.ocd" not in _shelf_names, _shelf_names
        _bad = post(base, "/shelf/from_template", {"name": "../../etc/passwd"})
        assert "error" in _bad, _bad
        import base64 as _b64
        _imp = post(base, "/fs/import", {"name": "x.exe", "data": _b64.b64encode(b"hi").decode()})
        assert "error" in _imp and "import wants" in str(_imp["error"]), _imp
        _trav = post(base, "/fs/import", {"name": "../../x.fp", "data": _b64.b64encode(b"hi").decode()})
        assert "error" in _trav, _trav  # never writes outside fp/
        _lp = urllib.request.urlopen(base + "/").read().decode()  # logged out → landing
        _lpjs = urllib.request.urlopen(base + "/web/landing.js").read().decode()
        for frag in ("id=newprojbtn", "id=newproj", "id=npsearch",
                     "id=npgrid", "id=npblank", "npcache",
                     "openShelfBoard", "fromTemplate"):
            assert frag in _lpjs, f"new-project modal missing: {frag}"
        _w = get(base, "/").decode()  # still authed: the workshop shell
        # chrome + panels are modules, behaviour is a module, and the slot JSON
        # carries plugin markup only
        assert 'src=/web/workshop.js' in _w and 'src=/web/legacy.js' in _w, _w[:400]
        assert 'href="/web/workshop.css"' in _w, _w[:400]
        assert "id=slots" in _w, "slot islands must ship with the shell"
        assert "id=app" in _w, "mount point missing from the shell"
        assert "withBusy" in _wjs, "long-action busy feedback missing"
        assert "fromTemplate" in _lpjs, "template double-click guard missing"
        assert "if(!text||(go&&go.disabled)||$('chatclear').disabled)return false;" in _wjs, "chat submit busy guard missing"
        assert "download ${key}" in _wjs or "download ${" in _wjs, "download label must stay a word"
        assert "sim ${simWhat}" in _wjs or "sim ${" in _wjs, "sim label must stay a word"
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
        assert '<script type=module src="/web/landing.js">' in _out, _out[:200]
        assert _land.count("fabcell") >= 1, _land[:400]  # strip renders client-side
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
        _blabel: list[str] = []
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
                        _blabel.append(_ln[5:].decode())
                        if len(_blabel) >= 2:
                            _done = True
                            break
                if _done:
                    break
        _er.close()
        assert any('"hello": "teammate"' in _e for _e in _blabel), _blabel
        print(f"collab ok (push {_cpdt * 1000:.0f}ms, 2 users, stale+presence+op+SSE)")

        # the photo-scan panel is a component: it must be in the module that
        # the authed page loads, and (below) in the DOM the browser builds
        assert "id=scanwrap" in _views and "id=scanfiles" in _views, \
            "scan panel not served"
        _ui = urllib.request.urlopen(urllib.request.Request(
            base + "/", headers={"Cookie": _JAR.get(base, "")})).read().decode()
        assert "id=app" in _ui, "workshop mount point missing"

        for key in ("svg", "sch", "xray"):
            r = post(base, "/render", {"key": key})
            assert not r.get("error"), (key, r.get("error"))
            assert len(cast(str, r["data"])) > 1000, (key, len(cast(str, r["data"])))
        print("render svg+sch+xray ok")
        # sch creation path: bare part + single-pin new net build clean.
        # no /state endpoint — /build with no text reuses the open board.
        _b1 = post(base, "/build", {})
        assert not _b1.get("error"), _b1.get("error")
        with open(BOARD, encoding="utf-8") as _bf:
            _curtext = _bf.read()
        _new = _curtext.rstrip() + "\npart U9 SOIC8\nN9 :: U9.1\n"
        _b2 = post(base, "/build", {"text": _new, "placer": "diffusion",
                                    "router": "lroute"})
        assert not _b2.get("error"), _b2.get("error")
        post(base, "/build", {"text": _curtext, "placer": "diffusion",
                              "router": "lroute"})
        print("sch create part+net ok")
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

        # behaviour ships as a module (apps/web/legacy.js, served at /web/):
        # syntax + the highlight wiring. node is dev-only here — skip rather
        # than fail when it is absent.
        node = shutil.which("node")
        pjs = _wjs
        if not node:
            print("no node: editor-highlight check skipped")
        else:
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
            # every frontend module must parse as ESM: the browser half sees
            # them only once authenticated, and a syntax error there is a
            # blank page (this is the gate the inline page got for free)
            for _mod in ("workshop.js", "panels.js", "views.js", "legacy.js",
                         "html.js", "api.js", "store.js", "core.js", "collab.js",
                         "kb.js", "vcs.js", "gallery.js", "scan.js"):
                _rn3 = subprocess.run([node, "--check", os.path.join(_JS, _mod)],
                                      capture_output=True, text=True, timeout=60)
                assert _rn3.returncode == 0, (_mod, _rn3.stderr[-400:])
            print("editor highlight → pcb/sch ok")
        assert pjs.count("edHl.has") >= 2, "PCB + SCH must both read edHl"
        # the chrome is a module too: menus, tabs and pills are htm now
        for _frag in ("id=m-board", "id=m-edit", "id=m-tools",
                      "id=viewtabs", "id=themebtn", "id=logoutbtn", "id=cost"):
            assert _frag in _chrome, f"chrome control missing: {_frag}"

        # gallery compare affordance ships + delta math holds on fixtures
        # the thumb's aria-label is the component's now
        assert "shift-click to compare" in _views, "gallery compare hint missing"
        _gjs = open(os.path.join(_JS, "gallery.js")).read()
        assert "function galDelta(" in _gjs, "galDelta missing from gallery.js"
        # this hint is panel markup (panels.js), not behaviour
        assert "shift-drag a trace previews the shove" in _panels, \
            "seg-drag hint missing"
        assert "function hitSeg(" in pjs, "hitSeg missing from legacy.js"
        if shutil.which("node"):
            with tempfile.TemporaryDirectory() as _td2:
                _ent2 = os.path.join(_td2, "gal.js")
                # just the function: the region after it now holds a delegated
                # listener that needs a real DOM
                import re as _re2
                _gal = _re2.search(r"function galDelta\([^)]*\)\s*\{.*?\n\}", _gjs, _re.S)
                assert _gal, "galDelta body not found"
                open(_ent2, "w").write(
                    "const $=()=>({});const S={};\n" + _gal.group(0))
                _djs = ("const a={cost:100,pos:{R1:[0,0],C1:[10,10]}};"
                        "const b={cost:95,pos:{R1:[0,0],C1:[15,10]}};"
                        "const d=galDelta(a,b);"
                        "if(d.dcost!==-5||d.moved!==1)throw new Error(JSON.stringify(d));"
                        "console.log('gal delta ok');")
                open(_ent2, "a").write(_djs)
                assert node, "node vanished mid-test"
                _rn2 = subprocess.run([node, _ent2], capture_output=True,
                                      text=True, timeout=60)
                assert _rn2.returncode == 0, _rn2.stderr[-400:]
                assert "gal delta ok" in _rn2.stdout, _rn2.stdout
            print("gallery compare ok")

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
        # ac sweep: ngspice waves + f-range, or a clean error without it
        _ac = post(base, "/simulate", {"what": "ac"})
        if _ac.get("error"):
            assert "ngspice" in str(_ac["error"]).lower() or \
                "not found" in str(_ac["error"]).lower(), _ac
            print("simulate ac ok (no ngspice here, clean error)")
        else:
            _acw = cast(dict[str, object], _ac["ac"])
            assert _acw and _ac.get("f0") == 1.0, _ac
            print(f"simulate ac ok ({len(cast(list[object], list(_acw.values())[0]))}pts)")

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
        # reroute: single net rip + maze, unknown net is a clean error.
        # (runs on the rc board the simulate test left open: VIN/VO/GND)
        _rr = post(base, "/reroute", {"net": "VO"})
        assert not _rr.get("error"), _rr
        assert cast(list[object], _rr["traces"]), "rerouted board routes"
        assert "retried" in _rr, _rr
        assert "unknown net" in str(post(base, "/reroute",
                                         {"net": "NOPE"}).get("error")), _rr
        print("reroute ok (VO rip + maze)")
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
            # and the modules actually rendered: dump the live DOM, so a hero
            # that never mounts (or a strip that never fills) fails here even
            # though the shell alone would still parse
            dom = subprocess.run(
                [chrom, "--headless=new", "--no-sandbox", "--disable-gpu",
                 "--virtual-time-budget=12000", "--dump-dom", base + "/"],
                capture_output=True, text=True, timeout=120).stdout
            assert 'id="herogo"' in dom, "hero CTA never rendered"
            assert 'class="gate"' in dom, "gate never rendered"
            assert dom.count('class="fabcell"') == 11, dom.count('class="fabcell"')
            assert 'id="npgrid"' in dom, "new-project modal never rendered"
            print("landing DOM ok (hero + gate + 11 fab tiles)")

            # The workshop is client-rendered as well, and it needs a session:
            # drive a real browser with the cookie (the shot above is logged
            # out), then assert the chrome, the slot islands and a painted PCB
            # canvas. This is the only gate that would catch a module that
            # loads but renders nothing.
            from urllib.parse import urlparse as _upw
            from tools.easyeda_live import CDP, wait_ready
            _uw = _upw(base)
            assert _uw.hostname and _uw.port
            _ckw: str | None = _JAR.get(base)
            if not _ckw:
                import http.client as _hcw
                _cw = _hcw.HTTPConnection(_uw.hostname, _uw.port, timeout=30)
                _cw.request("POST", "/auth/login",
                            json.dumps({"user": "tester",
                                        "password": "testtest12"}),
                            {"Content-Type": "application/json"})
                _rw = _cw.getresponse()
                _ckw = (_rw.getheader("Set-Cookie") or "").split(";")[0].strip()
                _rw.read()
            assert _ckw is not None and _ckw.startswith("ocd_user="), _ckw
            _dbg = free_port()
            _cdpdir = tempfile.mkdtemp(prefix="ocd-cdp-")
            _cr = subprocess.Popen(
                [chrom, "--headless=new", "--no-sandbox", "--disable-gpu",
                 f"--remote-debugging-port={_dbg}", f"--user-data-dir={_cdpdir}",
                 "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                _cdp = CDP(wait_ready(_dbg, timeout=60))
                try:
                    for _m in ("Runtime.enable", "Page.enable", "Network.enable"):
                        _cdp.call(_m)
                    _cdp.call("Network.setCookie",
                              {"name": "ocd_user",
                               "value": str(_ckw).split("=", 1)[1],
                               "domain": _uw.hostname, "path": "/"})
                    _cdp.call("Page.navigate",
                              {"url": f"http://{_uw.hostname}:{_uw.port}/"})
                    _st: dict[str, object] = {}
                    # CI runners are slow (cold build after the full suite):
                    # 90s budget, then console errors in the message
                    for _ in range(180):
                        time.sleep(0.5)
                        _raw = _cdp.eval(
                            "JSON.stringify({pcb:!!document.querySelector('#pcb'),"
                            "ed:!!document.querySelector('#ed'),"
                            "menus:document.querySelectorAll('.menubar>details.menu').length,"
                            "tabs:document.querySelectorAll('#viewtabs button').length,"
                            "panels:document.querySelectorAll('main#panels>section').length,"
                            "paint:(function(){var c=document.querySelector('#pcb');"
                            "if(!c)return -1;var g=c.getContext('2d');"
                            "var d=g.getImageData(0,0,c.width,c.height).data;var n=0;"
                            "for(var i=3;i<d.length;i+=4*97){if(d[i])n++;}return n;})()})")
                        try:
                            _st = cast(dict[str, object], json.loads(str(_raw)))
                        except ValueError:
                            _st = {}
                        if cast(int, _st.get("paint") or 0) > 0:
                            break
                    if cast(int, _st.get("paint") or 0) <= 0:
                        _con = [e for e in _cdp.events
                                if e.get("method") in ("Runtime.exceptionThrown",
                                                       "Log.entryAdded")]
                        _cost = str(_cdp.eval(
                            "document.querySelector('#cost').textContent"))
                        raise AssertionError(
                            f"workshop never painted: {_st} cost={_cost!r} "
                            f"console={json.dumps(_con[:2])[:400]}")
                    assert _st.get("pcb") and _st.get("ed"), _st
                    # every built-in panel is a component now: check the DOM
                    # the browser built, not the markup a slot lambda printed
                    _ids = ("edwrap", "pcbwrap", "schwrap", "wrap3d", "galwrap",
                            "vcswrap", "scanwrap", "kbwrap", "filetree", "chat",
                            "xraybar", "tidy", "drc", "layers", "partlist",
                            "kbask", "kbfetch", "scanbar", "vcs", "pluginpanels")
                    _raw_ids = str(_cdp.eval(
                        "JSON.stringify(" + json.dumps(list(_ids)) + ".filter("
                        "i=>!document.getElementById(i)))"))
                    assert json.loads(_raw_ids) == [], f"panels missing: {_raw_ids}"
                    assert _st.get("menus") == 6, _st   # Board/Edit/Engines/Sim/Tools/Share
                    assert _st.get("tabs") == 5, _st
                    assert cast(int, _st.get("panels") or 0) >= 6, _st
                    assert cast(int, _st.get("paint") or 0) > 0, _st
                    # the chrome is store-driven now: legacy.js pushes the
                    # numbers, preact renders them (apps/web/store.js), and a
                    # store update must not wipe what legacy put in the menus.
                    _cost = str(_cdp.eval("document.querySelector('#cost').textContent"))
                    assert _cost.startswith("cost ") and _cost != "cost ", _cost
                    assert str(_cdp.eval(
                        "document.querySelector('#feas').textContent.slice(0,19)"
                    )) == "routing feasibility", "feasibility pill never filled"
                    assert str(_cdp.eval(
                        "document.querySelector('#ocdscore').textContent.slice(0,4)"
                    )) == "OCD ", "neatness pill never filled"
                    _cdp.eval("document.querySelector('[data-v=pcb]').click()")
                    assert str(_cdp.eval("document.body.dataset.view")) == "pcb"
                    assert "on" in str(_cdp.eval(
                        "document.querySelector('[data-v=pcb]').className"))
                    assert "tabs" in str(_cdp.eval("document.body.className"))
                    _cdp.eval("document.querySelector('[data-v=all]').click()")
                    assert str(_cdp.eval("document.body.dataset.view || ''")) == ""
                    assert "on" in str(_cdp.eval(
                        "document.querySelector('[data-v=all]').className"))
                    _cdp.eval("document.querySelector('#themebtn').click()")
                    assert str(_cdp.eval(
                        "document.querySelector('#themebtn').textContent")) == "paper"
                    assert str(_cdp.eval(
                        "document.querySelector('#themebtn').getAttribute('aria-pressed')"
                    )) == "true"
                    assert "dark" in str(_cdp.eval("document.body.className"))
                    _cdp.eval("document.querySelector('#chatbtn').click()")
                    assert str(_cdp.eval(
                        "document.querySelector('#chatbtn').getAttribute('aria-pressed')"
                    )) == "true"
                    assert int(str(_cdp.eval(
                        "document.querySelector('#placer').options.length"))) > 1, \
                        "engine select lost its options to a chrome re-render"
                    # engine pickers: the option lists are components now, the
                    # selection stays native DOM state (six call sites read
                    # .value), so a change must survive the store round-trip
                    for _sel, _least in (("placer", 4), ("router", 2), ("fab", 5),
                                         ("silk", 2)):
                        _nopt = int(str(_cdp.eval(
                            f"document.querySelector('#{_sel}').options.length")))
                        assert _nopt >= _least, (_sel, _nopt)
                    _silk0 = str(_cdp.eval("document.querySelector('#silk').value"))
                    assert _silk0, "silk has no selected option"
                    assert str(_cdp.eval(
                        "document.querySelector('#silk').selectedOptions[0].textContent"
                    )) == _silk0, "silk default is not the server's pick"
                    _cdp.eval("(()=>{const s=document.querySelector('#silk');"
                              "s.selectedIndex=s.options.length-1;"
                              "s.dispatchEvent(new Event('change',{bubbles:true}));"
                              "return 1;})()")
                    time.sleep(2)
                    assert str(_cdp.eval(
                        "document.querySelector('#silk').value")) == str(_cdp.eval(
                            "document.querySelector('#silk').selectedOptions[0].textContent")), \
                        "selection and option text disagree after a re-render"
                    # project tree: component rows, legacy-delegated clicks
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#tree button.trow').length"))) >= 4, \
                        "tree rows missing"
                    _act = str(_cdp.eval(
                        "(document.querySelector('#tree .trow.active')||{dataset:{}})"
                        ".dataset.name || ''"))
                    assert _act == "blinky_555.ocd", _act
                    assert "files" in str(_cdp.eval(
                        "document.querySelector('#treenote').textContent")), \
                        "tree note missing"
                    # a text file previews through the delegated click
                    _cdp.eval("(()=>{const f=[...document.querySelectorAll('#tree .tfile')]"
                              ".find(b=>b.dataset.name==='notes.md');if(f)f.click();"
                              "return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "preview of notes.md" in str(_cdp.eval(
                                "document.querySelector('#msgs').textContent")):
                            break
                    else:
                        raise AssertionError("text file click did not preview")
                    # a folder row browses into it, and offers a way back up
                    _cdp.eval("(()=>{const d=[...document.querySelectorAll('#tree .tdir')]"
                              ".find(b=>b.dataset.name==='sub');if(d)d.click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if str(_cdp.eval(
                                "document.querySelector('#treenote').textContent"
                        )).startswith("sub"):
                            break
                    else:
                        raise AssertionError("folder click did not browse")
                    assert str(_cdp.eval(
                        "document.querySelector('#tree .tdir').dataset.name"
                    )).startswith(".."), "no way back up out of a folder"
                    # the up-row returns to the root, where the board row is
                    _cdp.eval("(()=>{const u=[...document.querySelectorAll('#tree .tdir')]"
                              ".find(b=>(b.dataset.name||'').startsWith('..'));"
                              "if(u)u.click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if bool(_cdp.eval("!!document.querySelector('#tree .tfile.active')")):
                            break
                    else:
                        raise AssertionError("up-row did not return to the root")
                    # candidates filmstrip: rows and captions are components,
                    # the frames are painted by legacy.js through a registered
                    # painter, and shift-click compares instead of adopting
                    assert str(_cdp.eval("getComputedStyle(document.querySelector"
                                         "('#galwrap')).display")) == "none", \
                        "filmstrip should start hidden"
                    _cdp.eval("(()=>{document.querySelector('#ncand').value='2';"
                              "document.querySelector('#dice').click();return 1;})()")
                    for _ in range(180):
                        time.sleep(1.0)
                        if int(str(_cdp.eval(
                                "document.querySelectorAll('#gal button.galpick').length"))) >= 2:
                            break
                    else:
                        raise AssertionError("candidates never rendered")
                    assert str(_cdp.eval("getComputedStyle(document.querySelector"
                                         "('#galwrap')).display")) != "none", \
                        "filmstrip stayed hidden after generating"
                    assert "cost" in str(_cdp.eval(
                        "document.querySelector('#gal .galcap').textContent")), \
                        "thumb caption missing"
                    _pix = int(str(_cdp.eval(
                        "(function(){var c=document.querySelector('#gal canvas');"
                        "if(!c)return -1;var d=c.getContext('2d')"
                        ".getImageData(0,0,300,220).data,n=0;"
                        "for(var i=3;i<d.length;i+=4*97){if(d[i])n++;}return n;})()")))
                    assert _pix > 0, "thumb canvas never painted"
                    _cdp.eval("(()=>{const b=document.querySelectorAll"
                              "('#gal button.galpick')[1];b.dispatchEvent(new MouseEvent"
                              "('click',{bubbles:true,shiftKey:true}));return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "base" in str(_cdp.eval(
                                "(document.querySelectorAll('#gal .galcap')[1]||{})"
                                ".textContent || ''")):
                            break
                    else:
                        raise AssertionError("shift-click did not start a compare")
                    assert "comparing from" in str(_cdp.eval(
                        "document.querySelector('#stat').textContent")), "no compare hint"
                    _cdp.eval("(()=>{const b=document.querySelectorAll"
                              "('#gal button.galpick')[1];b.dispatchEvent(new MouseEvent"
                              "('click',{bubbles:true,shiftKey:true}));return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "base" not in str(_cdp.eval(
                                "(document.querySelectorAll('#gal .galcap')[1]||{})"
                                ".textContent || ''")):
                            break
                    else:
                        raise AssertionError("compare did not toggle off")
                    # photo scan: the panel refuses an empty run, then a real
                    # photo (the board's own render) drives the pipeline and
                    # the review block, notes and analysis render from state
                    _cdp.eval("(()=>{document.querySelector('#scango').click();"
                              "return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "photos first" in str(_cdp.eval(
                                "document.querySelector('#scanstat').textContent")):
                            break
                    else:
                        raise AssertionError("scan did not refuse an empty run")
                    assert bool(_cdp.eval("document.querySelector('#scanview').hidden")), \
                        "review block must start hidden"
                    _cdp.eval("(async()=>{const r=await fetch('/render',{method:'POST',"
                              "headers:{'Content-Type':'application/json'},"
                              "body:JSON.stringify({key:'png'})}).then(x=>x.json());"
                              "const b=atob(r.data);const u=new Uint8Array(b.length);"
                              "for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);"
                              "const dt=new DataTransfer();"
                              "dt.items.add(new File([u],'top.png',{type:'image/png'}));"
                              "document.querySelector('#scanfiles').files=dt.files;"
                              "return 1;})()")
                    time.sleep(2)
                    _cdp.eval("(()=>{const originalFetch=window.fetch;"
                              "window.fetch=(url,options)=>{"
                              "if(url==='/scan'){"
                              "options={...options,body:JSON.stringify({"
                              "...JSON.parse(options.body),llm:false})};"
                              "window.fetch=originalFetch;}"
                              "return originalFetch(url,options);};"
                              "document.querySelector('#scango').click();"
                              "return 1;})()")
                    for _ in range(240):
                        time.sleep(1.0)
                        if not bool(_cdp.eval(
                                "document.querySelector('#scanview').hidden")):
                            break
                        if not bool(_cdp.eval(
                                "document.querySelector('#scango').disabled")):
                            raise AssertionError("scan failed: " + str(_cdp.eval(
                                "document.querySelector('#scanstat').textContent")))
                    else:
                        raise AssertionError("scan never produced a review: " + str(
                            _cdp.eval("document.querySelector('#scanstat').textContent")))
                    assert "registered" in str(_cdp.eval(
                        "document.querySelector('#scanstat').textContent")), \
                        "scan status missing"
                    assert not bool(_cdp.eval("document.querySelector('#scanview').hidden")), \
                        "review block stayed hidden after a scan"
                    assert str(_cdp.eval(
                        "document.querySelector('#scanout').textContent")).strip(), \
                        "analysis text missing"
                    # a long action labels the button it disabled, through the
                    # busy slice: withBusy writes no text into a preact node.
                    # Slow fetch so the label is observable for certain.
                    _cdp.eval("(()=>{window.__fetch=window.fetch;"
                              "window.fetch=(...a)=>new Promise(r=>setTimeout("
                              "()=>r(window.__fetch(...a)),1500));"
                              "document.querySelector('#qgo').click();return 1;})()")
                    _busylabel = ""
                    for _ in range(40):
                        time.sleep(0.1)
                        _busylabel = str(_cdp.eval(
                            "document.querySelector('#qgo').textContent"))
                        if _busylabel == "comparing…":
                            break
                    assert _busylabel == "comparing…", f"busy label missing ({_busylabel!r})"
                    _cdp.eval("(()=>{window.fetch=window.__fetch;return 1;})()")
                    for _ in range(80):
                        time.sleep(0.25)
                        if str(_cdp.eval(
                                "document.querySelector('#qgo').textContent")) == "compare":
                            break
                    else:
                        raise AssertionError("busy label never cleared")
                    # quote rows carry their fab's own logo tile, and the
                    # notes under them are state (no innerHTML in legacy.js)
                    _cdp.eval("(()=>{document.querySelector('#qgo').click();return 1;})()")
                    for _ in range(60):
                        time.sleep(0.5)
                        if int(str(_cdp.eval(
                                "document.querySelectorAll('#qout .qrow').length"))):
                            break
                    else:
                        raise AssertionError("quote rows never rendered")
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#qout .qrow').length"))) >= 5
                    assert "bare $" in str(_cdp.eval(
                        "document.querySelector('#qout .qrow').textContent")), "row wording"
                    assert bool(_cdp.eval("!!document.querySelector('#qout .qlogo')")), \
                        "quote row lost its fab logo"
                    assert "estimates" in str(_cdp.eval(
                        "document.querySelector('#qout').textContent")), "stamp missing"
                    # x-ray asks for a fabricator PNG before it can compare
                    _cdp.eval("(()=>{document.querySelector('#xraygo').click();return 1;})()")
                    for _ in range(10):
                        time.sleep(0.5)
                        if "fab PNG" in str(_cdp.eval(
                                "document.querySelector('#xraystat').textContent")):
                            break
                    else:
                        raise AssertionError("x-ray guard never spoke")
                    # external edit on disk: the strip appears and the click
                    # adopts the file (this needs GET /poll, not a POST)
                    with open(tboard, "a") as _f:
                        _f.write("\n# external edit from the test\n")
                    for _ in range(40):
                        time.sleep(0.5)
                        if bool(_cdp.eval("!!document.querySelector('#extbanner')")):
                            break
                    else:
                        raise AssertionError("disk-change strip never appeared")
                    _cdp.eval("(()=>{document.querySelector('#extbanner').click();"
                              "return 1;})()")
                    for _ in range(60):
                        time.sleep(0.5)
                        if not bool(_cdp.eval("!!document.querySelector('#extbanner')")):
                            break
                    else:
                        raise AssertionError("strip did not clear after reload")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "external edit from the test" in str(_cdp.eval(
                                "document.querySelector('#ed').innerText")):
                            break
                    else:
                        raise AssertionError("reload did not adopt the external edit")
                    # knowledgebase: the document list renders, a row opens
                    # the file, and the preference block starts hidden
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#kblist .kbrow').length"))) >= 1, \
                        "kb document rows missing"
                    assert "document" in str(_cdp.eval(
                        "document.querySelector('#kbnote').textContent")), "kb note missing"
                    assert str(_cdp.eval(
                        "getComputedStyle(document.querySelector('#kbprefs')).display")) \
                        == "none", "preferences block should start hidden"
                    _cdp.eval("(()=>{document.querySelector('#kblist button.kbname')"
                              ".click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "lines 1-" in str(_cdp.eval(
                                "document.querySelector('#kbview').textContent")):
                            break
                    else:
                        raise AssertionError("kb document never opened")
                    assert "2.7 and 5.5" in str(_cdp.eval(
                        "document.querySelector('#kbview').textContent")), "wrong text"
                    __kbprefs = post(base, "/kb/prefs/add",
                                     {"when": "placing connectors", "text": "board edge"})
                    assert not __kbprefs.get("error"), __kbprefs
                    _cdp.eval("(()=>{document.querySelector('#kbprefsbtn').click();"
                              "return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if bool(_cdp.eval("!!document.querySelector"
                                          "('#kbprefslist button[data-pref]')")):
                            break
                    else:
                        raise AssertionError("preference rows never rendered")
                    assert str(_cdp.eval(
                        "document.querySelector('#kbprefslist .kbkind').textContent"
                    )) == "pending", "preference should start pending"
                    _cdp.eval("(()=>{document.querySelector"
                              "('#kbprefslist button[data-pref]').click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if str(_cdp.eval("document.querySelector"
                                         "('#kbprefslist .kbkind').textContent")) == "approved":
                            break
                    else:
                        raise AssertionError("approve did not stick")
                    # revisions: the git log renders, a click asks for that
                    # commit's diff, and only one diff is ever open
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#vcs button.rev').length"))) >= 1, \
                        "revision rows missing"
                    assert str(_cdp.eval(
                        "document.querySelector('#vcsnote').textContent")).strip(), \
                        "revision note missing"
                    _cdp.eval("(()=>{document.querySelector('#vcs button.rev').click();"
                              "return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if int(str(_cdp.eval("document.querySelectorAll('#vcs pre').length"))):
                            break
                    else:
                        raise AssertionError("revision diff never opened")
                    assert str(_cdp.eval(
                        "document.querySelector('#vcs pre').textContent")).strip(), \
                        "empty diff"
                    # a second click moves the one diff, it does not add one
                    _cdp.eval("(()=>{const r=document.querySelectorAll('#vcs button.rev');"
                              "if(r.length>1)r[1].click();return 1;})()")
                    time.sleep(1.5)
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#vcs pre').length"))) == 1, \
                        "more than one diff open"
                    # the agent log and the tool readouts are components as
                    # well: the preview above must be one .msg with the agent's
                    # own name, and the calculators answer from state
                    assert "preview of notes.md" in str(_cdp.eval(
                        "document.querySelector('#msgs').textContent")), \
                        "message not in the log"
                    _who = str(_cdp.eval(
                        "(document.querySelector('#msgs .msg .who')||{}).textContent || ''"))
                    assert _who == "flux", _who
                    assert str(_cdp.eval(
                        "document.querySelector('#chatwhere').textContent")) != "", \
                        "agent panel does not name the open board"
                    _cdp.eval("(()=>{const a=document.querySelector('#ca');a.value='2';"
                              "a.dispatchEvent(new Event('input',{bubbles:true}));"
                              "return 1;})()")
                    assert "mm ext" in str(_cdp.eval(
                        "document.querySelector('#cout').textContent")), "calc silent"
                    # health checks render on first open, one line per check
                    _cdp.eval("(()=>{const d=document.querySelector('#doc');d.open=true;"
                              "d.dispatchEvent(new Event('toggle'));return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if int(str(_cdp.eval(
                                "document.querySelectorAll('#docout>div').length"))):
                            break
                    else:
                        raise AssertionError("doctor readout never rendered")
                    assert str(_cdp.eval(
                        "document.querySelector('#docout>div').className")) in ("ok", "warn")
                    # toast: opening the board from the tree shows it, and the
                    # timer clears it (the component renders, legacy owns time)
                    _cdp.eval("(()=>{const f=document.querySelector('#tree .tfile.active');"
                              "if(f)f.click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if "opened" in str(_cdp.eval(
                                "(document.querySelector('.toast')||{}).textContent || ''")):
                            break
                    else:
                        raise AssertionError("toast never appeared")
                    for _ in range(24):
                        time.sleep(0.5)
                        if not bool(_cdp.eval("!!document.querySelector('.toast')")):
                            break
                    else:
                        raise AssertionError("toast never cleared")
                    # layer and part visibility rows are components too: the
                    # row state and the note come from the store, and the
                    # change is delegated back to legacy.js (VIS + canvas)
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#cu label').length"))) >= 2, \
                        "copper layer rows missing"
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#marks label').length"))) >= 4, \
                        "mark rows missing"
                    _npart = int(str(_cdp.eval(
                        "document.querySelectorAll('#partlist label').length")))
                    assert _npart > 0, "part rows missing"
                    _note0 = str(_cdp.eval(
                        "document.querySelector('#partnote').textContent"))
                    assert _note0.endswith("shown"), _note0
                    _cdp.eval("(()=>{document.querySelector('#partlist label input')"
                              ".click();return 1;})()")
                    for _ in range(20):
                        time.sleep(0.5)
                        if str(_cdp.eval("document.querySelector('#partlist label')"
                                         ".dataset.hidden || ''")) == "1":
                            break
                    else:
                        raise AssertionError("part row did not hide")
                    assert str(_cdp.eval(
                        "document.querySelector('#partnote').textContent")) != _note0, \
                        "hidden part not counted in the note"
                    _cdp.eval("(()=>{document.querySelector('#partlist label input')"
                              ".click();return 1;})()")   # back on for the rest
                    _cdp.eval("(()=>{document.querySelector('#cu label input')"
                              ".click();return 1;})()")
                    _cdp.eval("(()=>{document.querySelector('#cu label input')"
                              ".click();return 1;})()")   # twice: state must toggle
                    assert str(_cdp.eval(
                        "document.querySelector('#partnote').textContent")) == _note0, \
                        "part note did not come back"
                    # the panel contents are components now (views.js): the
                    # DRC strip and the tidy list render from the store, so a
                    # build that fails must repaint them — no innerHTML in
                    # legacy.js for either.
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#tidy>div').length"))) > 0, \
                        "tidy list never rendered"
                    assert str(_cdp.eval(
                        "document.querySelector('#tidycov').textContent"
                    )).startswith("("), "tidy coverage note missing"
                    assert str(_cdp.eval(
                        "document.querySelector('#drc').textContent"
                    )).strip(), "DRC strip empty on a loaded board"
                    _ovl = ("board t 40x30 2L\npart R1 R0805 10k\npart R2 R0805 10k\n"
                            "fix R1 at 5 5\nfix R2 at 6 6\nN :: R1.1 R2.1\n")
                    _cdp.eval("(()=>{const ed=document.querySelector('#ed');"
                              "ed.innerText=" + json.dumps(_ovl) + ";"
                              "ed.dispatchEvent(new Event('input',{bubbles:true}));"
                              "return 1;})()")
                    _nerr = 0
                    for _ in range(24):
                        time.sleep(0.5)
                        _nerr = int(str(_cdp.eval(
                            "document.querySelectorAll('#drc .err').length")))
                        if _nerr:
                            break
                    assert _nerr > 0, "DRC strip did not show the failing build"
                    assert str(_cdp.eval(
                        "document.querySelector('#drc .err').textContent"
                    )).startswith("✗"), "error line lost its mark"
                    assert int(str(_cdp.eval(
                        "document.querySelectorAll('#tidy>div').length"))) > 0, \
                        "tidy list did not survive the re-render"
                    _badw = [e for e in _cdp.events
                             if e.get("method") == "Runtime.exceptionThrown"]
                    assert not _badw, json.dumps(_badw[:1])[:400]
                finally:
                    _cdp.close()
            finally:
                _cr.terminate()
                shutil.rmtree(_cdpdir, ignore_errors=True)
            print(f"workshop DOM ok ({_st['menus']} menus, {_st['panels']} panels, "
                  f"{_st['paint']} canvas samples, chrome state + panels ok)")
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
            assert "id=app" in page, "workshop mount point missing from the shell"
            for _frag in ("id=kbwrap", "id=kbfetch", "id=kbask", "id=kbprefsbtn",
                          "id=kblist", "id=kbprefslist", "id=kbview", "id=kbstat"):
                assert _frag in _views, f"kb control missing: {_frag}"
            for _frag in ("id=xraybar", "id=xraygo", "id=xrayfile"):
                assert _frag in _panels, f"x-ray control missing: {_frag}"
            # quote lives in the Tools menu now, so it ships in the chrome
            # module rather than in a panel slot
            assert "id=quote" in _chrome, "quote menu missing from the chrome"
            assert "id=qqty" in _chrome, "quote controls missing"
            assert "id=qgo" in _views, "quote button belongs to its component"
            _qq0 = post(kbase, "/quote", {"qty": 5, "no_parts": True})
            assert not _qq0.get("error"), _qq0.get("error")
            _q0rows = cast(list[dict[str, object]], _qq0["rows"])
            assert len(_q0rows) == 11, len(_q0rows)
            assert all(str(r.get("logo", "")).startswith("data:image/png;base64,")
                       for r in _q0rows), "every quote row wears its fab logo"
            slots = json.loads(urllib.request.urlopen(kbase + "/slots", timeout=5).read())
            # the registry lists plugin contributions only: built-ins are JS
            assert slots["view"] == [], slots
            assert set(slots) >= {"toolbar", "view", "panel-left"}, sorted(slots)
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
            if ka["method"] == "embeddings":
                assert kps and kps[0]["doc"] == "NOTES.md", ka
            # lexical fallback: substring match only — "voltage" is not in
            # "volts", so empty passages here are correct, not a failure
            kadd = post(kbase, "/kb/add", {"src": os.path.join(kd, "kb", "NOTES.md")})
            assert kadd.get("added") == "NOTES.md", kadd
            assert post(kbase, "/kb/list", {})["docs"] == kl["docs"]
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
