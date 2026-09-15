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
from .util import numpy as _numpy
import base64
import json
import math
from array import array
import os
import re
import shutil
import subprocess
import time
import urllib.request
from itertools import islice
from operator import mul
from typing import TYPE_CHECKING, Any, Callable, cast
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
CHUNK_CHARS = 1200   # passage size for embeddings: a datasheet table fits in
                     # one, a whole datasheet never does
EmbedFn = Callable[[list[str]], list[list[float]]]


def chunks_of(body: str, size: int = CHUNK_CHARS) -> list[dict[str, object]]:
    """Line-aligned passages of ~size chars carrying line numbers, so a
    recalled passage can be read back with KB.read()."""
    out: list[dict[str, object]] = []
    buf: list[str] = []
    start, n = 1, 0
    for i, line in enumerate(body.splitlines(), 1):
        if not buf:
            start = i
        buf.append(line)
        n += len(line) + 1
        if n >= size:
            out.append({"start": start, "end": i, "text": "\n".join(buf)})
            buf, n = [], 0
    if buf:
        out.append({"start": start, "end": start + len(buf) - 1,
                    "text": "\n".join(buf)})
    return out


def parts_map(text: str) -> dict[str, dict[str, str]]:
    """`ref -> attrs` straight from .ocd source, without building a Board.

    The kb only needs ref/lcsc/mpn to map a document back to a part, and
    building a real Board for a 5420-part design costs ~1.5s (Context
    journals every part and net — `core.emit` is 60% of it). A panel open or
    `ocd kb list` must not pay that. Ceiling: `block` bodies contribute their
    template refs, and quoted values containing spaces are not unquoted —
    neither carries an lcsc/mpn the mapping needs."""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("part "):
            continue
        toks = line.split(None, 3)
        if len(toks) < 3:
            continue
        attrs: dict[str, str] = {}
        if len(toks) > 3:
            for t in toks[3].split():
                k, eq, v = t.partition("=")
                if eq and k not in ("x", "y"):
                    attrs[k] = v
        out[toks[1]] = attrs
    return out


_np = None  # lazy: imported on the first vectorized ranking, not at load


def _unit(vec: array[float], dim: int) -> array[float]:
    """Scale each `dim`-long chunk to unit length (store once, rank with a dot
    product: cosine without recomputing either norm per query)."""
    np = _numpy()
    if np is not None:
        m = np.frombuffer(memoryview(vec), dtype=np.float32)
        m = m.reshape(len(vec) // dim, dim)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        np.divide(m, norms, out=m, where=norms > 0)
        return vec
    out: array[float] = array("f")
    for i in range(0, len(vec), dim):
        chunk = vec[i:i + dim]
        n = math.sqrt(sum(x * x for x in chunk))
        out.extend([x / n for x in chunk] if n else chunk)
    return out


def _dot_scores(q: list[float], vec: array[float], count: int, dim: int) -> list[float]:
    """Dot of the unit query with every stored unit vector."""
    np = _numpy()
    if np is not None and count:
        m = np.frombuffer(memoryview(vec), dtype=np.float32).reshape(count, dim)
        return [float(x) for x in (m @ np.asarray(q, dtype=np.float32))]
    it = iter(vec)
    return [sum(map(mul, q, islice(it, dim))) for _ in range(count)]


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
                 board: Board | None = None,
                 embed: EmbedFn | None = None,
                 parts: dict[str, dict[str, str]] | None = None) -> None:
        self.dir = os.path.join(os.path.abspath(base), "kb")
        self.ds = os.path.join(self.dir, "datasheets")
        self.cache = os.path.join(self.dir, ".cache")
        self.sources = os.path.join(self.dir, "sources.tsv")
        self.board = board
        self.embed = embed  # injected in tests; llm.embed otherwise
        # two ways to know the board, same mapping: a Board the caller already
        # has (CLI fetch, MCP), or a cheap parts map (panel, `kb list`).
        self.parts: dict[str, dict[str, str]] | None = (
            parts if parts is not None
            else ({ref: dict(p.attrs) for ref, p in board.parts.items()}
                  if board is not None else None))

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

    def count(self) -> int:
        """Documents in kb/ without stat-ing or titling any of them (a bounded
        listing still reports the true total)."""
        return len(self._files())

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

    def _part_index(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """(lcsc -> refs, token -> refs), built once per call. docs() asks per
        document, so walking 5420 parts per doc cost 6ms per list; walking them
        once costs 0.03ms and returns the identical mapping."""
        by_lcsc: dict[str, list[str]] = {}
        by_token: dict[str, list[str]] = {}
        for ref, attrs in sorted((self.parts or {}).items()):
            lcsc = str(attrs.get("lcsc", ""))
            mpn = str(attrs.get("mpn", ""))
            if lcsc:
                by_lcsc.setdefault(lcsc, []).append(ref)
            by_token.setdefault(ref, []).append(ref)
            if mpn:
                by_token.setdefault(mpn, []).append(ref)
        return by_lcsc, by_token

    def _parts_of(self, name: str,
                  index: tuple[dict[str, list[str]], dict[str, list[str]]]
                  ) -> list[str]:
        by_lcsc, by_token = index
        hits: list[str] = []
        for lcsc, refs in by_lcsc.items():
            if lcsc in name:
                hits.extend(refs)
        toks = set(re.split(r"[^A-Za-z0-9]+", os.path.basename(name)))
        for tok in toks:
            for ref in by_token.get(tok, ()):
                if ref not in hits:
                    hits.append(ref)
        return sorted(hits)  # the single-pass version listed refs in ref order

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

    def docs(self, limit: int | None = None) -> list[dict[str, object]]:
        """Every file in kb/, cheapest useful metadata, nothing precomputed.
        `limit` bounds a UI listing (the panel renders every row); search and
        recall always walk the whole thing."""
        org = self._origins()
        index = self._part_index()
        out: list[dict[str, object]] = []
        for name in self._files():
            if limit is not None and len(out) >= limit:
                break
            ext = os.path.splitext(name)[1].lower()
            kind = "pdf" if ext == ".pdf" else ("doc" if ext in TEXT_EXT else "other")
            d: dict[str, object] = {
                "name": name, "kind": kind,
                "bytes": os.path.getsize(os.path.join(self.dir, name)),
                "parts": self._parts_of(name, index)}
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

    # ---------- recall (embeddings, with a lexical floor) ----------

    def _embed_fn(self) -> EmbedFn:
        if self.embed is not None:
            return self.embed
        from . import llm
        return llm.embed

    def _embed_name(self) -> str:
        if self.embed is not None:
            return getattr(self.embed, "__name__", "injected")
        from . import llm
        return llm.embed_model()

    def _vec_path(self, name: str) -> str:
        return os.path.join(self.cache, "vec__" + name.replace(os.sep, "__") + ".json")

    def _stamp(self, name: str) -> dict[str, object]:
        p = self._path(name)
        return {"mtime": os.path.getmtime(p), "size": os.path.getsize(p),
                "model": self._embed_name()}

    def _load_vectors(self, name: str) -> tuple[array[float], list[tuple[int, int]], int]:
        """(flat float32 vectors, [(start, end)] line ranges, dim) for one doc,
        or empty when stale/missing (mtime + size + embedder must match: an
        edited note or a new model re-embeds). A cache written by an older
        layout reads as stale, so `index()` rebuilds it.

        Packed, not JSON float lists: a 30-datasheet kb is 2610 passages ≈ 2M
        floats, and parsing those as JSON cost 273ms on every load (that WAS
        recall's latency). Same numbers — float32 is what the models emit and
        what cosine ranking needs. Passage text is not stored either: it is the
        document's own lines, so recall re-reads the top k by line range.
        `unit` marks float32 vectors already normalized; a cache without it is
        re-normalized in place (a raw-vector dot product would rank wrongly,
        silently). Anything else unknown reads as stale and gets re-embedded."""
        p = self._vec_path(name)
        if not os.path.isfile(p):
            return array("f"), [], 0
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("stamp") != self._stamp(name):
                return array("f"), [], 0
            chunks = doc.get("chunks", [])
            assert isinstance(chunks, list)
            ranges = [(int(a), int(b)) for a, b in cast(list[list[int]], chunks)]
            dim = int(doc.get("dim", 0))
            vec: array[float] = array("f")
            blob = doc.get("vec32")
            if isinstance(blob, str) and dim:
                vec.frombytes(base64.b64decode(blob))
                if doc.get("unit") is not True:
                    vec = _unit(vec, dim)
                    self._write_vectors(name, vec, ranges, dim)
                return vec, ranges, dim
            return array("f"), [], 0  # no usable vector payload: treat as stale
        except (OSError, ValueError, AssertionError, TypeError):
            return array("f"), [], 0

    def _write_vectors(self, name: str, vec: array[float], ranges: list[tuple[int, int]],
                       dim: int) -> None:
        assert dim > 0
        os.makedirs(self.cache, exist_ok=True)
        with open(self._vec_path(name), "w", encoding="utf-8") as f:
            json.dump({"stamp": self._stamp(name), "dim": dim, "chunks": ranges,
                       "unit": True,
                       "vec32": base64.b64encode(vec.tobytes()).decode("ascii")}, f)

    def index(self, force: bool = False) -> dict[str, object]:
        """Embed every readable doc's passages into kb/.cache/vec__*.json.
        Incremental: only docs whose mtime/size/model changed are re-embedded."""
        embed = self._embed_fn()
        made, reused = 0, 0
        failed: list[str] = []
        for d in self.docs():
            name = str(d["name"])
            if d["kind"] == "other":
                continue
            if not force and self._load_vectors(name)[1]:
                reused += 1
                continue
            try:
                body = self.text(name)
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                failed.append(f"{name}: {e}")
                continue
            ch = chunks_of(body)
            if not ch:
                continue
            try:
                vecs = embed([str(c["text"]) for c in ch])
            except Exception as e:  # noqa: BLE001 — transport/library, reported
                failed.append(f"{name}: {e}")
                continue
            flat: array[float] = array("f")
            for v in vecs:
                flat.extend(v)
            ranges = [(cast(int, c["start"]), cast(int, c["end"])) for c in ch]
            self._write_vectors(name, _unit(flat, len(vecs[0])), ranges, len(vecs[0]))
            made += 1
        return {"indexed": made, "reused": reused, "failed": failed,
                "model": self._embed_name(), "passages": self.passage_count()}

    def _all_vectors(self) -> list[tuple[str, array[float], list[tuple[int, int]], int]]:
        """Every doc's cached vectors, each file read exactly once (recall and
        passage_count both need the whole set — two passes parsed it twice)."""
        out: list[tuple[str, array[float], list[tuple[int, int]], int]] = []
        for d in self.docs():
            name = str(d["name"])
            vec, ranges, dim = self._load_vectors(name)
            if ranges:
                out.append((name, vec, ranges, dim))
        return out

    def passage_count(self) -> int:
        return sum(len(r) for _n, _v, r, _d in self._all_vectors())

    def _lexical_passages(self, q: str, k: int) -> list[dict[str, object]]:
        """No embedder (or nothing indexed): rank with search() and expand each
        hit into the passage around it — the same shape recall() returns."""
        out: list[dict[str, object]] = []
        for h in cast(list[dict[str, object]], self.search(q, limit=k)["hits"]):
            name, line = str(h["doc"]), cast(int, h["line"])
            start = max(1, line - 3)
            r = self.read(name, start=start, lines=14)
            out.append({"doc": name, "start": r["start"], "end": r["end"],
                        "score": h["score"], "text": r["text"]})
        return out

    def recall(self, q: str, k: int = 6, rebuild: bool = False) -> dict[str, object]:
        """Question → the passages most likely to answer it, from everywhere in
        kb/ including 5k-line datasheet text. Embeddings when the endpoint
        answers (index built/refreshed on demand); lexical hits otherwise, so a
        machine with no local model still gets an answer, just a worse one."""
        try:
            # probe the embedder with the question itself before indexing
            # anything: a 30-datasheet cold index is ~26s of model compute, and
            # it used to be attempted even when the endpoint was unreachable
            # (per document, so a hanging endpoint meant minutes).
            qv = self._embed_fn()([q])[0]
            qn = math.sqrt(sum(x * x for x in qv))
            if not qn:
                raise ValueError("the embedder returned a zero vector")
            qu = [x / qn for x in qv]
            rows = self._all_vectors()
            if rebuild or not rows:
                self.index()
                rows = self._all_vectors()
        except Exception as e:  # noqa: BLE001 — any embedder failure degrades
            return {"q": q, "method": "lexical", "model": None,
                    "passages": self._lexical_passages(q, k),
                    "note": f"embeddings unavailable ({e}); ranked by term match"}
        if not rows:
            return {"q": q, "method": "lexical", "model": None,
                    "passages": self._lexical_passages(q, k),
                    "note": "nothing indexed; ranked by term match"}
        scored: list[tuple[float, str, int, int]] = []
        for name, vec, ranges, dim in rows:
            sims = _dot_scores(qu, vec, len(ranges), dim)
            for (start, end), sim in zip(ranges, sims):
                scored.append((sim, name, start, end))
        scored.sort(key=lambda t: -t[0])
        out: list[dict[str, object]] = []
        for score, name, start, end in scored[:k]:
            r = self.read(name, start=start, lines=max(1, end - start + 1))
            out.append({"doc": name, "start": start, "end": end,
                        "score": round(score, 4), "text": r["text"]})
        return {"q": q, "method": "embeddings", "model": self._embed_name(),
                "passages": out,
                "note": "passages only — cite doc:start-end and read them "
                        "before answering"}

    def ask(self, q: str, k: int = 6, answer: bool = False,
            rebuild: bool = False) -> dict[str, object]:
        """recall(), plus (optionally) a written answer from the local model —
        grounded in the passages, told to say when they don't contain it."""
        r = self.recall(q, k=k, rebuild=rebuild)
        if not answer:
            return r
        ps = cast(list[dict[str, object]], r["passages"])
        if not ps:
            r["answer_error"] = "nothing in kb/ matched — nothing to answer from"
            return r
        ctx = "\n\n".join(f"[{p['doc']}:{p['start']}-{p['end']}]\n{p['text']}"
                          for p in ps)
        from . import llm
        try:
            ans = llm.chat([
                {"role": "system", "content": (
                    "Answer only from the passages. Cite each claim as "
                    "doc:line. If the passages do not answer the question, say "
                    "exactly that — do not use outside knowledge.")},
                {"role": "user", "content": f"question: {q}\n\npassages:\n{ctx}"}])
            # a thinking model can stream its reasoning elsewhere and return
            # empty content — say that, don't print an empty answer section
            if ans.strip():
                r["answer"] = ans
            else:
                r["answer_error"] = ("the model returned an empty answer "
                                     "(reasoning-only model? see OCD_LLM_MODEL)")
        except llm.LLMError as e:
            r["answer_error"] = str(e)
        return r

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
        else the `lcsc=` code. Already-present files are skipped, not refetched.

        A real Board, not the cheap parts map: `block`/`instance` members are
        separate refs with their own attrs, and only the parser expands them
        (1974 of this repo's 5420-part test board are instanced). Callers that
        already have a Board pass it; the panel builds one in its worker."""
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
