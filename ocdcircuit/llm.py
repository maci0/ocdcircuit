"""LLM bridge (stdlib only): OpenAI-compatible chat + a small tool loop.

The agent reads and writes .ocd with three tools, written as fenced blocks
so any model that can print markdown can drive them (no function-calling
support required, works with OpenAI, DeepSeek, Ollama, LM Studio):

    ```fs.list: boards
    ```fs.read: boards/psu.ocd
    ```write: boards/psu.ocd      (the block body is the new full text)
    ```replace: boards/psu.ocd    (body: two @@-separated halves, old then new)

Nothing is written before it is validated: the studio applies a proposal
through /build (parse + place + route + DRC) and only then commits it.
"""
from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, cast

MAX_STEPS = 6  # tool rounds before we stop and hand back what we have
TOOL_NAMES = ("fs.list", "fs.read", "write", "replace")

SYSTEM = """You are the circuit agent inside OCD Studio, a .ocd board editor.

Language (one fact per line):
  board NAME WxH LAYERS          part REF FOOTPRINT [VALUE] [x=.. y=..]
  use path.ocd as PREFIX         net NAME :: REF.PIN <--> REF.PIN
  fix REF at x y                 keep REF near OTHER MM
  route NET on LAYER             power NET...   silk LEVEL
Comments start with #. Every net lists every pin it touches.

Tools: to inspect or change a project, print ONE fenced block per tool call
and stop; the caller runs them and sends the results back:
  ```fs.list: RELPATH
  ```fs.read: RELPATH
  ```write: RELPATH      (body = the complete new file text; creates the file)
  ```replace: RELPATH    (body = old text, a line with only @@, new text)
Paths are relative to the project root. Read a file before you edit it, and
prefer `replace` — `write` must carry the whole file or the caller refuses it.
Several files in one turn is fine: one block per file. Propose a change only
when the user asked for one, and state what you changed in one sentence before
the block. Keep every line the parser accepts; do not invent footprints or
pins."""


class LLMError(RuntimeError):
    """Configuration or transport failure, worded for the operator."""


def cfg() -> dict[str, str]:
    """Endpoint settings from the environment. Empty key is allowed: local
    servers (Ollama, LM Studio) usually need none."""
    return {
        "base": os.environ.get("OCD_LLM_BASE", "http://127.0.0.1:11434/v1").rstrip("/"),
        "key": os.environ.get("OCD_LLM_KEY", ""),
        "model": os.environ.get("OCD_LLM_MODEL", "qwen3.5:latest"),
    }


def models(timeout: float = 10.0) -> list[str]:
    """Ids the endpoint serves — named in the error when the configured model
    is not one of them (a 404 that lists nothing is a wasted round trip)."""
    c = cfg()
    req = urllib.request.Request(c["base"] + "/models", headers=(
        {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read())
        return [str(m["id"]) for m in doc["data"]]
    except Exception:
        return []


def chat(messages: list[dict[str, str]], *, temperature: float = 0.2,
         timeout: float = 180.0) -> str:
    """One completion. Raises LLMError with the endpoint's own words."""
    c = cfg()
    body = json.dumps({"model": c["model"], "messages": messages,
                       "temperature": temperature}).encode()
    req = urllib.request.Request(
        c["base"] + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {c['key']}"} if c["key"] else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf8", "replace")
        hint = ""
        if e.code == 404:  # usually a model id the server does not serve
            have = models()
            hint = f" available: {', '.join(have)}" if have else ""
        raise LLMError(f"{c['base']} said {e.code} for model {c['model']!r}: "
                       f"{detail}{hint}. Set OCD_LLM_MODEL.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"cannot reach {c['base']} ({e}). Set OCD_LLM_BASE, "
                       "OCD_LLM_MODEL, OCD_LLM_KEY.") from e
    try:
        doc = json.loads(raw)
        return str(doc["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected reply from {c['base']}: {raw[:200]!r}") from e


_BLOCK = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.S)


def parse_calls(text: str) -> list[dict[str, str]]:
    """Fenced tool blocks → [{tool, path, body}]. Junk is skipped, not fatal:
    a model that only talks returns no calls and stays a chat message."""
    out: list[dict[str, str]] = []
    for head, body in _BLOCK.findall(text):
        head = head.strip()
        if ":" not in head:
            continue
        tool, _, path = head.partition(":")
        tool, path = tool.strip().lower(), path.strip()
        if tool not in TOOL_NAMES or not path:
            continue
        out.append({"tool": tool, "path": path, "body": body})
    return out


def apply_replace(src: str, body: str) -> str:
    """replace tool body = old @@ new. Old must match once, or we refuse —
    a fuzzy match silently rewrites the wrong line."""
    old, sep, new = body.partition("\n@@\n")
    if not sep:
        old, sep, new = body.partition("\n@@")
    if not sep:
        raise ValueError("replace body needs `old` then a line with @@ then `new`")
    if new.endswith("\n"):
        new = new[:-1]
    if src.count(old) != 1:
        raise ValueError(f"the old block matches {src.count(old)} times (need exactly 1)")
    return src.replace(old, new, 1)


def _current(path: str, tools: dict[str, Callable[[str, str], str]]) -> tuple[str, bool]:
    """(text, exists). A missing file is empty text and not an error — that is
    how `write` creates a file; `replace` checks `exists` itself."""
    try:
        return tools["fs.read"](path, ""), True
    except Exception as e:
        msg = str(e).lower()
        if "no such file" in msg or "not found" in msg or "no such" in msg:
            return "", False
        raise  # a refusal for another reason (outside the root, binary, huge)


# A model that rewrites a file from memory drops content it did not re-read.
# Below this fraction of the original we refuse and make it re-read instead.
SHRINK_LIMIT = 0.6


def _no_truncate(path: str, old: str, new: str) -> None:
    if len(old) >= 200 and len(new) < len(old) * SHRINK_LIMIT:
        raise ValueError(
            f"{path}: `write` would drop {100 - round(100 * len(new) / len(old))}% "
            f"of the file ({len(old)} → {len(new)} bytes). Use `replace` for a "
            "targeted edit, or `write` the complete file text (read it first).")


def run(messages: list[dict[str, str]], tools: dict[str, Callable[[str, str], str]],
        *, chat_fn: Callable[[list[dict[str, str]]], str] | None = None,
        max_steps: int = MAX_STEPS) -> dict[str, object]:
    """Chat until the model stops asking for tools. Returns
    {reply, files:{path: text}, log:[str], steps:int} — files are proposals,
    the caller validates and applies them."""
    if chat_fn is None:
        chat_fn = chat
    msgs = [{"role": "system", "content": SYSTEM}] + messages
    files: dict[str, str] = {}
    log: list[str] = []
    reply = ""
    for _ in range(max_steps):
        reply = chat_fn(msgs)
        calls = parse_calls(reply)
        if not calls:
            break
        results: list[str] = []
        for c in calls:
            tool, path, body = c["tool"], c["path"], c["body"]
            name = f"{tool}" if tool.startswith("fs.") else f"{tool} {path}"
            try:
                if tool in ("write", "replace"):
                    text = files.get(path)
                    if text is None:
                        text, known = _current(path, tools)
                        if tool == "replace" and not known:
                            raise ValueError(
                                f"{path}: no such file — `replace` edits an "
                                "existing file, `write` creates one")
                    if tool == "replace":
                        files[path] = apply_replace(text, body)
                    else:
                        new = body if body.endswith("\n") else body + "\n"
                        if known:
                            _no_truncate(path, text, new)  # raises before staging
                        files[path] = new
                    results.append(f"{name}: staged ({len(files[path])} bytes)")
                else:
                    results.append(f"{name}: ok\n{tools[tool](path, body)}")
                log.append(f"{name}: ok")
            except Exception as e:  # tool refusal is information for the model
                results.append(f"{name}: FAILED — {e}")
                log.append(f"{name}: failed — {e}")
        msgs.append({"role": "assistant", "content": reply})
        msgs.append({"role": "user", "content": "tool results:\n" + "\n".join(results)})
    words = _BLOCK.sub("", reply).strip()  # the prose around the calls
    return {"reply": words or "(no comment)", "files": files, "log": log}


def _selfcheck() -> None:
    """One runnable check for the parser and the replace guard (no network)."""
    calls = parse_calls("here you go\n```fs.read: a/b.ocd\nx\n```\n"
                        "```write: a/b.ocd\nboard x 10x10 2L\n```")
    assert [c["tool"] for c in calls] == ["fs.read", "write"], calls
    assert calls[1]["path"] == "a/b.ocd" and calls[1]["body"] == "board x 10x10 2L\n"
    assert parse_calls("no tools here, just prose") == []
    assert parse_calls("```python\nprint(1)\n```") == []  # unknown tool ignored
    assert apply_replace("a\nb\nc\n", "b\n@@\nBB\n") == "a\nBB\nc\n"
    for bad, body in (("a\nb\n", "zz\n@@\nq"), ("a\na\n", "a\n@@\nb")):
        try:
            apply_replace(bad, body)
            raise AssertionError(f"replace should refuse {body!r}")
        except ValueError:
            pass
    # the loop: model asks for a tool, gets results, then only talks
    seen: list[list[dict[str, str]]] = []

    def fake(msgs: list[dict[str, str]]) -> str:
        seen.append(msgs)
        if len(seen) == 1:
            return "reading it\n```fs.read: b.ocd\n```"
        return "done: renamed the net"
    out = run([{"role": "user", "content": "rename net N to OUT"}],
              {"fs.read": lambda p, _b: "board b 10x10 2L\n",
               "fs.list": lambda p, _b: "b.ocd"}, chat_fn=fake)
    assert out["reply"] == "done: renamed the net", out
    assert out["files"] == {} and len(seen) == 2, out
    assert "board b 10x10 2L" in seen[1][-1]["content"], seen[1]

    # the loop creates a file it could not read, and stages a second file in
    # the same turn; a truncating `write` is refused with a reason
    here = {"b.ocd": "board b 10x10 2L\npart U1 X\n" + "part R1 R0805 1k\n" * 30}

    def rd(path: str, _b: str) -> str:
        if path not in here:
            raise ValueError(f"{path}: no such file")
        return here[path]

    def fake2(msgs: list[dict[str, str]]) -> str:
        if not any("tool results" in m["content"] for m in msgs):
            return ("adding notes\n```write: NOTES.md\nhello\n```\n"
                    "and trimming\n```replace: b.ocd\npart U1 X\n@@\n"
                    "part U1 Y\n```")
        return "both staged"
    out2 = run([{"role": "user", "content": "go"}],
               {"fs.read": rd, "fs.list": lambda p, _b: "b.ocd"}, chat_fn=fake2)
    files2 = cast(dict[str, str], out2["files"])
    assert files2["NOTES.md"] == "hello\n", files2  # created, not an error
    assert "part U1 Y" in files2["b.ocd"], files2
    assert len(files2["b.ocd"]) > len(here["b.ocd"]) / 2, "replace kept the rest"

    def fake3(msgs: list[dict[str, str]]) -> str:
        if not any("tool results" in m["content"] for m in msgs):
            return "rewriting\n```write: b.ocd\nboard b 10x10 2L\n```"
        return "refused it"
    out3 = run([{"role": "user", "content": "go"}],
               {"fs.read": rd, "fs.list": lambda p, _b: "b.ocd"}, chat_fn=fake3)
    assert out3["files"] == {}, out3  # truncating write never staged
    assert any("would drop" in ln for ln in cast(list[str], out3["log"])), out3

    def fake4(msgs: list[dict[str, str]]) -> str:
        if not any("tool results" in m["content"] for m in msgs):
            return "editing\n```replace: missing.ocd\nx\n@@\ny\n```"
        return "ok"
    out4 = run([{"role": "user", "content": "go"}],
               {"fs.read": rd, "fs.list": lambda p, _b: "b.ocd"}, chat_fn=fake4)
    assert out4["files"] == {}, out4  # replace on a missing file is refused


if __name__ == "__main__":
    _selfcheck()
    print("llm self-check ok")
