"""
handlers.py — Deterministic handlers for swiszard routing.

Each handler takes a task string and returns a result string.
Handlers do not call the LLM.
"""
from __future__ import annotations

import re
import subprocess
import urllib.parse
import urllib.request
import json
from pathlib import Path

from .narrate import narrate


# ── session-scoped file dedup ─────────────────────────────────────────────────
# Module-level: lives for the lifetime of the swiszard MCP process, which maps
# to one Hermes session. Prevents the same file being injected at full size
# into context multiple times in one conversation.
_SESSION_FILES_SEEN: dict[str, int] = {}  # path → read count this session
_SESSION_FILE_PREVIEW_LINES = 30          # lines to show on repeat reads


# ── helper: extract path from task text ──────────────────────────────────────

_PATH_RE = re.compile(r"(/(?:[^\s,\"']+))")


def _extract_path(task: str) -> str | None:
    """Extract the first absolute path from a task string."""
    m = _PATH_RE.search(task)
    return m.group(1) if m else None


# ── handler_file_read ─────────────────────────────────────────────────────────

def handler_file_read(task: str) -> str:
    path = _extract_path(task)
    if not path:
        return "handler_file_read: could not extract a file path from the task."
    p = Path(path)
    if not p.exists():
        return f"handler_file_read: path does not exist: {path}"
    if not p.is_file():
        return f"handler_file_read: path is not a regular file: {path}"
    try:
        contents = p.read_text(errors="replace")
    except PermissionError:
        return f"handler_file_read: permission denied: {path}"

    count = _SESSION_FILES_SEEN.get(path, 0)
    _SESSION_FILES_SEEN[path] = count + 1

    if count == 0:
        # First read this session: full content
        return f"=== {path} ===\n{contents}"
    else:
        # Repeat read: first N lines + truncation notice to save context budget
        lines = contents.splitlines()
        preview = "\n".join(lines[:_SESSION_FILE_PREVIEW_LINES])
        omitted = max(0, len(lines) - _SESSION_FILE_PREVIEW_LINES)
        notice = (
            f"[already read this session — showing first {_SESSION_FILE_PREVIEW_LINES} lines"
            + (f", {omitted} more omitted" if omitted else "")
            + ". Use 'read lines X-Y of /path' for a specific range.]"
        )
        return f"=== {path} ({count + 1}x this session) ===\n{preview}\n{notice}"


# ── handler_file_find ─────────────────────────────────────────────────────────

def handler_file_find(task: str) -> str:
    """
    Extract either:
      - a glob/filename pattern (words like '*.py', '*.log', 'config.yaml'), and
        a search root (first absolute path, defaulting to /home if none found).
      - OR a grep pattern via 'containing|grep|for' keywords.
    """
    # Check for grep-style ("grep for X in /path" or "files containing X")
    grep_match = re.search(
        r"(?:grep(?:\s+for)?|containing(?:\s+the\s+(?:word|string|text))?)\s+['\"]?([^'\"]+?)['\"]?\s+(?:in|under|inside|at)\s+(/\S+)",
        task, re.IGNORECASE
    )
    if grep_match:
        pattern, root = grep_match.group(1).strip(), grep_match.group(2)
        cmd = ["grep", "-r", "--include=*", "-l", pattern, root]
        narrate(f"running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout.strip() or "(no matches)"
        if result.stderr:
            output += f"\n[stderr]: {result.stderr.strip()}"
        return output

    # find-style: extract file pattern and search root
    # Look for file extension patterns or quoted names
    quoted = re.search(r"['\"]([^'\"]+)['\"]", task)
    name_match = re.search(
        r"(?:named?|called?|matching|with\s+extension)\s+['\"]?([^\s'\"]+)['\"]?",
        task, re.IGNORECASE,
    )
    all_ext = re.search(r"all\s+(\w+)\s+files?", task, re.IGNORECASE)

    path = _extract_path(task)
    root = path or "/home"

    if all_ext:
        pattern = f"*.{all_ext.group(1).lower()}"
    elif name_match:
        pattern = name_match.group(1)
    elif quoted:
        q = quoted.group(1)
        pattern = q if any(c in q for c in "*?[") else f"*{q}*"
    else:
        words = task.split()
        pattern = next((w for w in words if "." in w and "/" not in w), None)
        if not pattern:
            return (
                "handler_file_find: could not extract a name pattern. "
                "Use: find *.py in /path | find files matching FOO in /path | grep TEXT in /path"
            )

    cmd = ["find", root, "-iname", pattern, "-type", "f"]
    narrate(f"running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "handler_file_find: search timed out after 30 seconds."
    output = result.stdout.strip() or "(no matches)"
    if result.stderr:
        output += f"\n[stderr]: {result.stderr.strip()}"
    return output


# ── handler_shell ─────────────────────────────────────────────────────────────

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def handler_shell(task: str) -> str:
    """Extract a command from backticks in the task and run it."""
    m = _BACKTICK_RE.search(task)
    if not m:
        return (
            "handler_shell: could not find a command in backticks. "
            "Wrap the command like: run `ls -la /tmp`"
        )
    command = m.group(1).strip()
    narrate(f"running shell command: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        parts = []
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout.rstrip()}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr.rstrip()}")
        parts.append(f"exit code: {result.returncode}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"handler_shell: command timed out after 60 seconds: {command}"


# ── handler_web_search ────────────────────────────────────────────────────────

SEARXNG_URL = "http://127.0.0.1:8888/search"


def handler_web_search(task: str) -> str:
    """Search SearxNG at localhost; fall back to a friendly error if unavailable."""
    # Strip common prefixes to get the actual query
    query = re.sub(
        r"^(?:search(?:\s+the\s+web)?\s+for|web\s+search:|look\s+up|find\s+online|google)\s*:?\s*",
        "",
        task,
        flags=re.IGNORECASE,
    ).strip()

    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{SEARXNG_URL}?{params}"
    narrate(f"querying SearxNG: {url[:100]}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "swiszard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", [])[:5]
        if not results:
            return "handler_web_search: no results found."
        lines = [f"Top {len(results)} results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '(no title)')}")
            lines.append(f"   {r.get('url', '')}")
            snippet = r.get("content", "")
            if snippet:
                lines.append(f"   {snippet[:200]}")
            lines.append("")
        return "\n".join(lines)
    except OSError as e:
        return (
            f"handler_web_search: SearxNG not reachable at {SEARXNG_URL}: {e}. "
            "Is the search instance running?"
        )
    except Exception as e:
        return f"handler_web_search: unexpected error: {e}"


# ── handler_memory ────────────────────────────────────────────────────────────

_MEMORY_SERVER = "http://127.0.0.1:7437"


def _mem_post(path: str, payload: dict) -> dict | None:
    url = _MEMORY_SERVER + path
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def _mem_get(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(_MEMORY_SERVER + path, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def handler_memory(task: str) -> str:
    """
    Interface to the swiszard memory server (v2).

    Task prefixes:
      memory recall <query>            — content-vector search (excludes deprecated)
      memory recall+history <query>    — content-vector search (INCLUDES deprecated)
      memory remember <content>        — store a fact
      memory forget <id>               — DELETE memory by id (permanent)
      memory deprecate <id> [: reason] — mark deprecated (excluded from proactive recall, kept for forensics)
      memory supersede <id> with: <new content> [| lesson: <lesson>]
                                       — replace memory with new version, preserving chain
      memory pin <id>                  — add 'always_inject' tag (always returned by proactive recall)
      memory unpin <id>                — remove 'always_inject' tag
      memory show <id>                 — full row including supersede chain
      memory status                    — row counts
    """
    text = task.strip()
    lower = text.lower()

    # ── status ────────────────────────────────────────────────────────────────
    if re.search(r"\bstatus\b", lower):
        result = _mem_get("/status")
        if result and "error" not in result:
            c = result.get("counts", {})
            return (
                f"Memory server status:\n"
                f"  memories:            {c.get('memories', '?')}\n"
                f"  memories_active:     {c.get('memories_active', '?')}\n"
                f"  memories_deprecated: {c.get('memories_deprecated', '?')}\n"
                f"  memory_triggers:     {c.get('memory_triggers', '?')}\n"
                f"  repo_files:          {c.get('repo_files', '?')}"
            )
        return f"memory status error: {result}"

    # ── show ──────────────────────────────────────────────────────────────────
    show_m = re.match(r"(?:memory\s+)?show\s+(\d+)", lower)
    if show_m:
        mid = int(show_m.group(1))
        result = _mem_post("/show", {"memory_id": mid})
        if not result or "error" in result:
            return f"memory show failed: {result}"
        if result.get("detail"):
            return f"memory show: {result['detail']}"
        p = result["provenance"]
        chain = result.get("superseded_chain") or []
        lines = [
            f"memory:{result['id']} ({result['kind']})",
            f"  content:    {result['content']}",
            f"  tags:       {result['tags']}",
            f"  provenance: session {p['session_id'][:12]}, turn {p['turn']}, ts {p['timestamp']}",
            f"  deprecated: {result['deprecated']}" + (f" — {result['deprecated_reason']}" if result['deprecated_reason'] else ""),
        ]
        if result.get("superseded_by"):
            lines.append(f"  superseded_by: {result['superseded_by']}")
        if result.get("lesson"):
            lines.append(f"  lesson:     {result['lesson']}")
        if chain:
            lines.append(f"  chain:      {' -> '.join(str(x) for x in chain)}")
        return "\n".join(lines)

    # ── pin / unpin ───────────────────────────────────────────────────────────
    pin_m = re.match(r"(?:memory\s+)?pin\s+(\d+)", lower)
    if pin_m:
        mid = int(pin_m.group(1))
        result = _mem_post("/pin", {"memory_id": mid})
        if result and result.get("ok"):
            return f"Pinned memory id={mid} (always_inject). Tags: {result['tags']}"
        return f"memory pin failed: {result}"

    unpin_m = re.match(r"(?:memory\s+)?unpin\s+(\d+)", lower)
    if unpin_m:
        mid = int(unpin_m.group(1))
        result = _mem_post("/unpin", {"memory_id": mid})
        if result and result.get("ok"):
            return f"Unpinned memory id={mid}. Tags: {result['tags']}"
        return f"memory unpin failed: {result}"

    # ── deprecate ─────────────────────────────────────────────────────────────
    dep_m = re.match(r"(?:memory\s+)?deprecate\s+(\d+)\s*[:\-]?\s*(.*)?", text, re.IGNORECASE)
    if dep_m:
        mid = int(dep_m.group(1))
        reason = (dep_m.group(2) or "").strip() or None
        result = _mem_post("/deprecate", {"memory_id": mid, "reason": reason})
        if result and result.get("ok"):
            return f"Deprecated memory id={mid}" + (f" (reason: {reason})" if reason else "")
        return f"memory deprecate failed: {result}"

    # ── supersede ─────────────────────────────────────────────────────────────
    sup_m = re.match(r"(?:memory\s+)?supersede\s+(\d+)\s+with\s*[:]?\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if sup_m:
        old_id = int(sup_m.group(1))
        rest = sup_m.group(2).strip()
        # Optional "| lesson: ..." separator
        lesson = None
        if "| lesson:" in rest.lower():
            content_part, _, lesson_part = rest.partition("|")
            new_content = content_part.strip()
            lesson = lesson_part.split(":", 1)[1].strip() if ":" in lesson_part else None
        else:
            new_content = rest
        result = _mem_post("/supersede", {
            "old_memory_id": old_id,
            "new_content":   new_content,
            "new_triggers":  [],
            "lesson":        lesson,
            "session_id":    "user_explicit",
            "turn":          -1,
            "source":        "user_explicit",
            "tags":          [],
        })
        if result and result.get("new_memory_id"):
            return (
                f"Superseded memory {old_id} -> {result['new_memory_id']}. "
                f"{result.get('triggers_stored', 0)} triggers on new memory."
            )
        return f"memory supersede failed: {result}"

    # ── forget ────────────────────────────────────────────────────────────────
    forget_m = re.match(r"(?:memory\s+)?forget\s+(\d+)", lower)
    if forget_m:
        mid = int(forget_m.group(1))
        result = _mem_post("/forget", {"memory_id": mid})
        if result and result.get("ok"):
            return f"Deleted memory id={mid}."
        return f"memory forget failed: {result}"

    # ── remember ──────────────────────────────────────────────────────────────
    remember_m = re.match(r"(?:memory\s+)?remember\s+(.+)", text, re.IGNORECASE | re.DOTALL)
    if remember_m:
        content = remember_m.group(1).strip()
        result = _mem_post("/remember", {
            "content":     content,
            "triggers":    [],
            "kind":        "fact",
            "session_id":  "user_explicit",
            "turn":        -1,
            "source":      "user_explicit",
            "tags":        ["self"],
            "ttl_seconds": None,
        })
        if result and "memory_id" in result:
            return f"Stored memory id={result['memory_id']}: {content[:80]}"
        return f"memory remember failed: {result}"

    # ── list (deterministic browse by tag/source) ───────────────────────────
    list_m = re.match(r"(?:memory\s+)?list(?:\s+(.*))?$", text, re.IGNORECASE)
    if list_m:
        rest = (list_m.group(1) or "").strip()
        # parse: --tag X, --source X, --limit N, --include-deprecated
        tag = None
        source = None
        limit = 20
        include_dep = False
        m_tag = re.search(r"--tag\s+(\S+)", rest)
        if m_tag: tag = m_tag.group(1)
        m_src = re.search(r"--source\s+(\S+)", rest)
        if m_src: source = m_src.group(1)
        m_lim = re.search(r"--limit\s+(\d+)", rest)
        if m_lim: limit = int(m_lim.group(1))
        if "--include-deprecated" in rest or "--all" in rest:
            include_dep = True
        if not (tag or source):
            tag = "self"  # default browse target = my own notes
        result = _mem_post("/list", {
            "tag": tag, "source": source,
            "include_deprecated": include_dep,
            "limit": limit, "offset": 0,
        })
        if not result or "error" in result:
            return f"memory list failed: {result}"
        rows = result.get("memories", [])
        if not rows:
            filt = f"tag={tag} " if tag else ""
            filt += f"source={source}" if source else ""
            return f"No memories matching: {filt.strip()}"
        lines = [f"Memories ({result['returned']}/{result['total']}, tag={tag}, source={source}):"]
        for r in rows:
            flag = " [DEP]" if r["deprecated"] else ""
            preview = r["content"][:120].replace("\n", " ")
            if len(r["content"]) > 120:
                preview += "…"
            lines.append(f"  {r['id']:>4}{flag} [{','.join(r['tags']) or '-'}] {preview}")
        return "\n".join(lines)

    # ── tag / untag (curate without supersede) ───────────────────────────────
    tag_m = re.match(r"(?:memory\s+)?tag\s+(\d+)\s+(\S+)", text, re.IGNORECASE)
    if tag_m:
        mid, t = int(tag_m.group(1)), tag_m.group(2)
        result = _mem_post("/tag", {"memory_id": mid, "tag": t})
        if result and result.get("ok"):
            return f"Tagged memory {mid} with '{t}'. Tags now: {result['tags']}"
        return f"memory tag failed: {result}"

    untag_m = re.match(r"(?:memory\s+)?untag\s+(\d+)\s+(\S+)", text, re.IGNORECASE)
    if untag_m:
        mid, t = int(untag_m.group(1)), untag_m.group(2)
        result = _mem_post("/untag", {"memory_id": mid, "tag": t})
        if result and result.get("ok"):
            return f"Removed tag '{t}' from memory {mid}. Tags now: {result['tags']}"
        return f"memory untag failed: {result}"

    # ── recall+history (includes deprecated for forensic search) ─────────────
    if re.search(r"\brecall\+history\b|\brecall\s+with\s+history\b", lower):
        query = re.sub(r"^.*?recall(?:\+history|\s+with\s+history)\s*", "", text, flags=re.IGNORECASE).strip()
        if not query:
            return "handler_memory: provide a query after 'recall+history'"
        result = _mem_post("/recall_content", {"query": query, "top_k": 8, "include_deprecated": True})
        return _format_recall(result, include_deprecated=True)

    # ── recall (default, excludes deprecated) ────────────────────────────────
    recall_m = re.match(r"(?:memory\s+)?recall\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    query = recall_m.group(1).strip() if recall_m else text.strip()
    if not query:
        return "handler_memory: provide a query after 'recall'"
    result = _mem_post("/recall_content", {"query": query, "top_k": 5, "include_deprecated": False})
    return _format_recall(result, include_deprecated=False)


_RECALL_PREVIEW_CHARS = 240


def _format_recall(result, include_deprecated: bool) -> str:
    if not result or "error" in result:
        return f"memory recall failed: {result}"
    memories = result.get("memories", [])
    if not memories:
        return "No memories found matching that query."
    lines = []
    for m in memories:
        score = m.get("content_score", 0)
        flag = ""
        if m.get("deprecated"):
            sb = m.get("superseded_by")
            flag = " [DEPRECATED" + (f" -> {sb}" if sb else "") + "]"
        full = m["content"]
        if len(full) > _RECALL_PREVIEW_CHARS:
            tail = f"... [+{len(full) - _RECALL_PREVIEW_CHARS} chars; full: memory show {m['id']}]"
            body = full[:_RECALL_PREVIEW_CHARS].rstrip() + tail
        else:
            body = full
        if m.get("lesson"):
            body += f"\n    lesson: {m['lesson']}"
        lines.append(f"[memory:{m['id']} score={score:.2f}{flag}]\n  {body}")
    return "Recalled memories:\n" + "\n".join(lines)


# ── handler_file_write ────────────────────────────────────────────────────────

import base64 as _b64

def handler_file_write(task: str) -> str:
    """
    Deterministic file write that survives any LLM-tool quoting hell.

    Format:  write_b64 /absolute/path <BASE64_OF_CONTENT>

    Why base64: every other write format eventually hits a backtick or quote
    that the upstream MCP transport mangles. Base64 has no special chars.
    """
    m = re.match(r"^write_b64\s+(/\S+)\s+(\S+)\s*$", task.strip())
    if not m:
        return "handler_file_write: expected 'write_b64 /absolute/path <base64>'"
    path, b64 = m.group(1), m.group(2)
    try:
        data = _b64.b64decode(b64, validate=True)
    except Exception as exc:
        return f"handler_file_write: invalid base64: {exc}"
    p = Path(path)
    if not p.parent.exists():
        return f"handler_file_write: parent does not exist: {p.parent}"
    try:
        p.write_bytes(data)
    except PermissionError:
        return f"handler_file_write: permission denied: {path}"
    narrate(f"wrote {len(data)} bytes to {path}")
    return f"handler_file_write: wrote {len(data)} bytes to {path}"
