"""Live EasyEDA Pro renders via CDP (no extension needed).

The client's full extension API sits on window._EXTAPI_ROOT_, reachable
through Chrome DevTools Protocol. Chain per board: launch client (xvfb) →
createProject → createPcb → openDocument → setDocumentSource(Pro source)
→ screenshot canvas → kill client. Client crashes ~15min under automation,
so one fresh client per board, never reuse.

Proven (2026-09-14, client 3.2.149): CDP connect, clicks, project/pcb
create+open, primitive create, get/setDocumentSource, screenshot. Std JSON
is NOT accepted by setDocumentSource (returns True, changes nothing) —
use pro_source() below (Pro record stream, schema learned by diffing
blank vs API-drawn track).

Usage: python -m tools.easyeda_live boards/blinky_555.ocd
"""
from __future__ import annotations
import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlparse

MIL = 39.3700787  # mm → mil (Pro source units)


class CDP:
    """Minimal CDP client, stdlib only (hand-rolled WS framing).
    cordis-boundary: socket + reader thread are process resources, not
    context effects — owned explicitly via close() (render_board's
    finally), never by a fiber."""

    def __init__(self, ws_url: str, timeout: float = 90.0) -> None:
        u = urlparse(ws_url)
        assert u.hostname and u.port
        self.s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(12)).decode()
        self.s.sendall(
            (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(4096)
        self.oid = 0
        self.events: list[dict[str, object]] = []
        self.lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        """Stop the reader thread and the socket (idempotent, safe on a
        half-constructed client)."""
        self._closed = True
        s = getattr(self, "s", None)
        if s is None:
            return
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        s.close()

    def _recvn(self, n: int) -> bytes:
        d = b""
        while len(d) < n:
            c = self.s.recv(n - len(d))
            if not c:
                raise ConnectionError("closed")
            d += c
        return d

    def _loop(self) -> None:
        try:
            while not self._closed:
                hdr = self._recvn(2)
                ln = hdr[1] & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._recvn(2))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._recvn(8))[0]
                masked = bool(hdr[1] & 0x80)
                mask = self._recvn(4) if masked else None
                data = self._recvn(ln)
                if masked and mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                if hdr[0] & 0x0F == 0x8:
                    return
                if hdr[0] & 0x0F not in (0x1, 0x0):
                    continue
                try:
                    m = json.loads(data)
                except ValueError:
                    continue
                with self.lock:
                    self.events.append(m)
        except (ConnectionError, OSError):
            return

    def call(self, method: str, params: dict[str, object] | None = None,
             timeout: float = 90.0) -> dict[str, object]:
        with self.lock:
            self.oid += 1
            oid = self.oid
        data = json.dumps({"id": oid, "method": method, "params": params or {}}).encode()
        mask = b"\xab\xcd\xef\x01"
        n = len(data)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                for i, m in enumerate(self.events):
                    if m.get("id") == oid:
                        self.events.pop(i)
                        if "error" in m:
                            raise RuntimeError(str(m["error"])[:500])
                        res = m.get("result", {})
                        assert isinstance(res, dict)
                        return res
            time.sleep(0.2)
        raise TimeoutError(method)

    def eval(self, expr: str, timeout: float = 90.0) -> object:
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                           "returnByValue": True}, timeout)
        res = r["result"]
        assert isinstance(res, dict)
        if res.get("type") == "undefined":
            return None
        if "value" in res:
            return res["value"]
        raise RuntimeError(str(r)[:500])

    def shot(self, path: str) -> str:
        r = self.call("Page.captureScreenshot", {"format": "png"})
        data = r.get("data", "")
        assert isinstance(data, str)
        open(path, "wb").write(base64.b64decode(data))
        return path


def page_ws(port: int = 9223) -> str:
    targets = json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json/list", timeout=10))
    page = next(t for t in targets if t.get("type") == "page")
    url = page["webSocketDebuggerUrl"]
    assert isinstance(url, str)
    return url


def launch_client(port: int = 9223) -> tuple[subprocess.Popen[bytes], str]:
    """Fresh client under xvfb with CDP. Returns (proc, profile dir):
    caller must proc.terminate() + shutil.rmtree(home) — the profile
    dir is ~100MB, don't leak it per render.
    cordis-boundary: child process + temp dir are outside-context
    emissions; render_board compensates in finally (terminate + rmtree)."""
    home = tempfile.mkdtemp(prefix="ezlive")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "/opt/easyeda-pro/easyeda-pro",
         f"--remote-debugging-port={port}", f"--user-data-dir={home}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, home


def wait_ready(port: int = 9223, timeout: float = 120.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return page_ws(port)
        except (OSError, ValueError, KeyError, StopIteration):
            time.sleep(2)
    raise TimeoutError("easyeda client did not come up")


def _hex16() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(16))


def pro_source(board: object, blank: str, ticket0: int = 200) -> str:
    """Blank Pro source + our copper as LINE records. Only LINE (track) and
    NET/PRIMITIVE records are proven; VIA/PAD/COMPONENT schemas are not
    (learn one by drawing it through the API and diffing the source, then
    extend here). Callers must not assume vias survive the push."""
    from ocdcircuit.circuit import Board
    assert isinstance(board, Board)
    t = ticket0
    recs: list[str] = []
    nets = sorted(board.nets)
    for n in nets:
        recs.append(json.dumps({"type": "NET", "ticket": t, "id": f'["NET","{n}"]'}))
        t += 1
    recs.append(json.dumps({"type": "PRIMITIVE", "ticket": t, "id": '["PRIMITIVE","TRACK"]'}))
    t += 1
    for s in board.traces:
        if s.via:
            continue
        layer = s.layer + 1  # our 0-indexed → Pro 1=Top
        recs.append(json.dumps({"type": "LINE", "ticket": t, "id": _hex16()}))
        recs.append(json.dumps({
            "partitionId": "", "groupId": 0, "netName": s.net, "layerId": layer,
            "startX": round(s.x1 * MIL), "startY": round(s.y1 * MIL),
            "endX": round(s.x2 * MIL), "endY": round(s.y2 * MIL),
            "width": round(s.width * MIL), "locked": False, "zIndex": -1}))
        t += 1
    return blank.rstrip("|") + "||" + "||".join(recs) + "|"


def render_board(ocd_path: str, out_png: str, port: int = 9223) -> str:
    """One fresh client per board (they crash ~15min under automation)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ocdcircuit import agent
    text = open(ocd_path).read()
    base = os.path.dirname(os.path.abspath(ocd_path))
    proc, home = launch_client(port)
    try:
        cdp = CDP(wait_ready(port))
    except TimeoutError:
        proc.terminate()
        shutil.rmtree(home, ignore_errors=True)
        raise RuntimeError("easyeda-pro not found (need local install)")
    try:
        pre = ("(async()=>{const R=window._EXTAPI_ROOT_;return " , ";})()")
        team = "/home/maci/Documents/EasyEDA-Pro/projects"
        name = os.path.basename(ocd_path).replace(".ocd", "")
        proj = cdp.eval(pre[0] + "JSON.stringify(await R.dmt_Project.createProject("
                        f"'{name}','{name}','{team}',null,'ocd render',1)){pre[1]}")
        assert isinstance(proj, str)
        uuid = json.loads(proj).get("projectUuid", json.loads(proj).get("uuid", ""))
        cdp.eval(pre[0] + f"await R.dmt_Project.openProject('{uuid}'){pre[1]}")
        # The individual primitives are proven (see module docstring), but the
        # full createPcb→openDocument→setDocumentSource(pro_source(...))→shot
        # chain is NOT: under automation the client dies before the source
        # lands, and a half-pushed document is worse than an honest failure.
        # Implemented only when a client build survives the chain end to end.
        raise RuntimeError("pro_source push untested (client kept crashing); "
                           "see module docstring for the proven chain")
    finally:
        cdp.close()
        proc.terminate()
        shutil.rmtree(home, ignore_errors=True)
    return out_png


if __name__ == "__main__":
    print(render_board(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/tmp/ez.png"))
