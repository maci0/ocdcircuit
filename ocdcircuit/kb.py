"""Board knowledgebase: notes + datasheets, next to the board, for humans and agents.

A board project keeps its documentation in `kb/` beside the `.ocd`:

    boards/mitox/kb/NOTES.md          dropped in by hand, or `ocd kb add`
    boards/mitox/kb/errata.txt        any text file the walk finds
    boards/mitox/kb/datasheets/       `ocd kb fetch` writes here, one per part
    boards/mitox/kb/sources.tsv       name<TAB>url — where a file came from
    boards/mitox/kb/.cache/           pdftotext output (derived, gitignored)

No index: the directory *is* the index, so nothing goes stale when you drop a
file in with the file manager. Text is extracted on demand — PDFs via
`pdftotext`, cached against the source's mtime — and searched with plain
substring scoring, because exact terms are what part numbers, register names
and pin tables need, and the caller can read the hit for context.

`fetch(board)` gets datasheets without guessing: a part's `datasheet=<url>`
attr wins (a URL you pinned in the `.ocd`), else `lcsc=C1234` is resolved
through LCSC's public product endpoint. Anything else is left to the human.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:  # engine import stays light (ARCHITECTURE: imports inside run)
    from .circuit import Board

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
LCSC_RE = re.compile(r"^C\d+$")
TEXT_EXT = {".md", ".txt", ".rst", ".csv", ".tsv", ".json", ".log", ".toml",
            ".fp", ".ocd", ".srv", ".cfg", ".ini"}
MAX_BYTES = 64 * 1024 * 1024
LCSC_API = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode="


def _clean(name: str, fallback: str = "download") -> str:
    """One path component, safe to join: no separators, no `.`/`..`, bounded."""
    base = os.path.basename(name.strip())
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    return base[:80] or fallback


def _download(url: str, dest: str, timeout: float = 60.0) -> int:
    """Fetch url → dest. Refuses non-https, oversized, and .pdf URLs that
    aren't PDFs (a login page saved as a datasheet is worse than nothing)."""
    if urlparse(url).scheme != "https":
        raise ValueError(f"refusing non-https url {url!r}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{url}: larger than {MAX_BYTES // 1024 // 1024}MB")
    if dest.lower().endswith(".pdf") and not data.startswith(b"%PDF"):
        raise ValueError(f"{url}: not a PDF (got {data[:24]!r})")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def lcsc_pdf(lcsc: str, timeout: float = 30.0) -> str:
    """Datasheet URL for an LCSC part code, from LCSC's product endpoint."""
    if not LCSC_RE.match(lcsc):
        raise ValueError(f"{lcsc!r} is not an LCSC code (want C1234)")
    req = urllib.request.Request(LCSC_API + lcsc, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        doc = json.loads(r.read().decode("utf-8", "replace"))
    res = doc.get("result") if isinstance(doc, dict) else None
    url = cast(dict[str, object], res).get("pdfUrl") if isinstance(res, dict) else None
    host = urlparse(url).hostname if isinstance(url, str) else None
    if not (isinstance(url, str) and urlparse(url).scheme == "https"
            and (host or "").endswith("lcsc.com")):
        raise ValueError(f"no datasheet on lcsc for {lcsc}")
    return url


class KB:
    """One board's knowledgebase. `base` = the board's project dir."""

    def __init__(self, base: str | os.PathLike[str],
                 board: Board | None = None) -> None:
        self.dir = os.path.join(os.path.abspath(base), "kb")
        self.ds = os.path.join(self.dir, "datasheets")
        self.cache = os.path.join(self.dir, ".cache")
        self.sources = os.path.join(self.dir, "sources.tsv")
        self.board = board

    # ---------- traversal ----------

    def _path(self, name: str) -> str:
        """Resolve a doc name, refusing anything outside kb/."""
        full = os.path.abspath(os.path.join(self.dir, name))
        if full != self.dir and not full.startswith(self.dir + os.sep):
            raise ValueError(f"{name!r} is outside the knowledgebase")
        if not os.path.isfile(full):
            raise ValueError(f"no such doc {name!r} (kb list shows what's there)")
        return full

    def _files(self) -> list[str]:
        if not os.path.isdir(self.dir):
            return []
        out: list[str] = []
        for root, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                if n.startswith(".") or n == "sources.tsv":
                    continue
                out.append(os.path.relpath(os.path.join(root, n), self.dir))
        return out

    def _origins(self) -> dict[str, str]:
        """name → url, from the append-only sources.tsv log."""
        out: dict[str, str] = {}
        if os.path.isfile(self.sources):
            with open(self.sources, encoding="utf-8", errors="replace") as f:
                for line in f:
                    bits = line.rstrip("\n").split("\t")
                    if len(bits) >= 2:
                        out[bits[0]] = bits[1]
        return out

    def parts_for(self, name: str) -> list[str]:
        """Board refs this doc belongs to: lcsc code or ref/MPN as a filename
        token (`C1525_100n.pdf`, `U3_sensor.md`) — the part→datasheet join."""
        if self.board is None:
            return []
        toks = set(re.split(r"[^A-Za-z0-9]+", os.path.basename(name)))
        hits = []
        for ref, p in sorted(self.board.parts.items()):
            lcsc = str(p.attrs.get("lcsc", ""))
            mpn = str(p.attrs.get("mpn", ""))
            if lcsc and lcsc in name:
                hits.append(ref)
            elif ref in toks or (mpn and mpn in toks):
                hits.append(ref)
        return hits

    def _title(self, name: str) -> str:
        try:
            with open(self._path(name), encoding="utf-8", errors="replace") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        return line.lstrip("# ")[:80]
        except (OSError, ValueError):
            pass
        return name

    def docs(self) -> list[dict[str, object]]:
        """Every file in kb/, cheapest useful metadata, nothing precomputed."""
        org = self._origins()
        out: list[dict[str, object]] = []
        for name in self._files():
            ext = os.path.splitext(name)[1].lower()
            kind = "pdf" if ext == ".pdf" else ("doc" if ext in TEXT_EXT else "other")
            d: dict[str, object] = {
                "name": name, "kind": kind,
                "bytes": os.path.getsize(os.path.join(self.dir, name)),
                "parts": self.parts_for(name)}
            if kind == "doc":
                d["title"] = self._title(name)
            if name in org:
                d["source"] = org[name]
            out.append(d)
        return out

    def read(self, name: str, start: int = 1, lines: int = 200) -> dict[str, object]:
        """A window of a doc's text — page a datasheet instead of dumping 5k lines."""
        if start < 1 or lines < 1:
            raise ValueError("start/lines are 1-based and positive")
        body = self.text(name).splitlines()
        i0 = min(start - 1, len(body))
        i1 = min(len(body), i0 + lines)
        return {"name": name, "start": i0 + 1, "end": i1,
                "total_lines": len(body), "text": "\n".join(body[i0:i1])}

    def search(self, q: str, limit: int = 20, per_doc: int = 3) -> dict[str, object]:
        """Substring scoring over every doc's text. `per_doc` keeps one huge
        datasheet from eating the whole result set."""
        terms = [t for t in re.split(r"[^0-9a-z]+", q.lower()) if len(t) > 1]
        if not terms:
            raise ValueError(f"query {q!r} has no searchable term (2+ chars)")
        phrase = q.lower().strip()
        hits: list[dict[str, object]] = []
        skipped: list[str] = []
        searched = 0
        for d in self.docs():
            name = str(d["name"])
            if d["kind"] == "other":
                skipped.append(f"{name}: no text extractor")
                continue
            try:
                body = self.text(name)
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                skipped.append(f"{name}: {e}")
                continue
            searched += 1
            found = 0
            for i, line in enumerate(body.splitlines(), 1):
                low = line.lower()
                score = sum(low.count(t) for t in terms)
                if not score:
                    continue
                if len(terms) > 1 and phrase in low:
                    score += len(terms)  # phrase beats scattered words
                found += 1
                if found > per_doc:
                    break
                hits.append({"doc": name, "line": i, "score": score,
                             "text": line.strip()[:300]})
        hits.sort(key=lambda h: -cast(int, h["score"]))
        return {"q": q, "hits": hits[:limit], "docs_searched": searched,
                "skipped": skipped,
                "note": "read the doc at/around a hit line for context"}

    def text(self, name: str) -> str:
        """Doc text. PDFs go through pdftotext and are cached against mtime."""
        p = self._path(name)
        ext = os.path.splitext(p)[1].lower()
        if ext == ".pdf":
            return self._pdf_text(name, p)
        if ext in TEXT_EXT:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        raise ValueError(f"no text extractor for {name!r} ({ext or 'no extension'})")

    def _pdf_text(self, name: str, p: str) -> str:
        exe = shutil.which("pdftotext")
        if exe is None:
            raise ValueError("pdftotext not on PATH: install poppler-utils to read PDFs")
        cpath = os.path.join(self.cache, name.replace(os.sep, "__") + ".txt")
        if os.path.isfile(cpath) and os.path.getmtime(cpath) >= os.path.getmtime(p):
            with open(cpath, encoding="utf-8", errors="replace") as f:
                return f.read()
        r = subprocess.run([exe, "-layout", p, "-"], capture_output=True, timeout=120)
        if r.returncode != 0:
            raise ValueError(f"pdftotext failed on {name!r}: "
                             f"{r.stderr.decode(errors='replace')[:200]}")
        body = r.stdout.decode("utf-8", errors="replace")
        os.makedirs(self.cache, exist_ok=True)
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(body)
        return body

    # ---------- ingest ----------

    def _target(self, name: str, dest: str | None = None) -> str:
        """Free filename: never overwrite different bytes (notes are not
        regenerable), so a clash becomes `name-2.ext`."""
        n = _clean(name)
        d = dest or (self.ds if n.lower().endswith(".pdf") else self.dir)
        stem, ext = os.path.splitext(n)
        cand, i = n, 1
        while os.path.exists(os.path.join(d, cand)):
            i += 1
            cand = f"{stem}-{i}{ext}"
        return os.path.join(d, cand)

    def add(self, src: str | None = None, name: str | None = None,
            text: str | None = None) -> dict[str, object]:
        """Bring a doc in: a URL (`https://…/ds.pdf`), a local path, or text
        straight from the caller. URL/paths keep a sources.tsv line."""
        if text is not None:
            dest = self._target(name or "note.md")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
            return {"added": os.path.relpath(dest, self.dir), "bytes": len(text)}
        if not src:
            raise ValueError("add needs a path, a url, or text")
        u = urlparse(src)
        if u.scheme in ("http", "https"):
            nm = name or os.path.basename(u.path) or "download"
            if not os.path.splitext(nm)[1]:
                nm += ".html" if u.scheme == "https" else ".txt"
            dest = self._target(nm)
            size = _download(src, dest)
            with open(self.sources, "a", encoding="utf-8") as f:
                f.write(f"{os.path.relpath(dest, self.dir)}\t{src}\t{int(time.time())}\n")
            return {"added": os.path.relpath(dest, self.dir), "bytes": size, "url": src}
        if not os.path.isfile(src):
            raise ValueError(f"no such file {src!r} (or pass an http(s) url)")
        dest = self._target(name or src)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        return {"added": os.path.relpath(dest, self.dir),
                "bytes": os.path.getsize(dest), "from": os.path.abspath(src)}

    def fetch(self, board: Board, refs: list[str] | None = None,
              timeout: float = 60.0) -> dict[str, object]:
        """Download datasheets for the board's parts: `datasheet=` attr first,
        else the `lcsc=` code. Already-present files are skipped, not refetched."""
        saved: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        failed: list[dict[str, object]] = []
        origins = self._origins()
        have = [f for f in self._files() if f.startswith("datasheets" + os.sep)]
        for ref, p in sorted(board.parts.items()):
            if refs is not None and ref not in refs:
                continue
            lcsc = str(p.attrs.get("lcsc", ""))
            url = str(p.attrs.get("datasheet", "")) or None
            if url is None and not lcsc:
                skipped.append({"part": ref, "why": "no datasheet= or lcsc= attr"})
                continue
            if url is not None and url in origins.values():
                skipped.append({"part": ref, "why": f"already have {url}"})
                continue
            if url is None and any(os.path.basename(f).startswith(lcsc + "_")
                                   or os.path.basename(f) == lcsc + ".pdf" for f in have):
                skipped.append({"part": ref, "why": f"already have a datasheet for {lcsc}"})
                continue
            label = _clean(str(p.attrs.get("mpn", "")) or p.value or ref, "part")
            try:
                if url is None:
                    url = lcsc_pdf(lcsc, timeout=timeout)
                dest = self._target(f"{lcsc or ref}_{label}.pdf")
                size = _download(url, dest, timeout=timeout)
            except (ValueError, OSError, KeyError) as e:
                failed.append({"part": ref, "lcsc": lcsc, "error": str(e)[:200]})
                continue
            name = os.path.relpath(dest, self.dir)
            with open(self.sources, "a", encoding="utf-8") as f:
                f.write(f"{name}\t{url}\t{int(time.time())}\n")
            have.append(name)
            saved.append({"part": ref, "name": name, "bytes": size, "url": url})
        return {"saved": saved, "skipped": skipped, "failed": failed,
                "dir": self.ds}


if __name__ == "__main__":  # self-check: ingest → traverse → search → paging
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "t.ocd"), "w").write("board t 10x10\n")
        k = KB(td)
        k.add(text="# Notes\nQ1 gate needs 10k pulldown\n", name="NOTES.md")
        note = os.path.join(td, "errata.txt")
        open(note, "w").write("rev B: R7 must be 0R\n")
        assert k.add(note)["added"] == "errata.txt"
        names = [str(d["name"]) for d in k.docs()]
        assert names == ["NOTES.md", "errata.txt"], names
        assert len(cast(list[object], k.search("pulldown")["hits"])) == 1
        assert cast(list[object], k.search("zzz")["hits"]) == []
        r = k.read("NOTES.md", start=2, lines=1)
        assert r["text"] == "Q1 gate needs 10k pulldown" and r["total_lines"] == 2, r
        # a second file with the same name must not clobber the first
        assert k.add(note)["added"] == "errata-2.txt"
        try:
            k.read("../../etc/passwd")
            raise AssertionError("path escape allowed")
        except ValueError:
            pass
        assert k.search("pulldown")["docs_searched"] == 3  # + the errata-2 copy
    print("kb ok")
