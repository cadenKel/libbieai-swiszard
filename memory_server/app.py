"""
app.py — Swiszard Memory Server v2

FastAPI HTTP server. Bind: 127.0.0.1:7437

Routes:
  POST /remember          — write a memory + triggers
  POST /recall_triggers   — proactive recall (pinned + trigger-vector match, excludes deprecated)
  POST /recall_content    — on-demand recall (content-vector match, includes deprecated for forensics)
  POST /forget            — DELETE a memory by id
  POST /deprecate         — mark a memory deprecated (excluded from proactive recall)
  POST /supersede         — write new memory and link old as superseded_by
  POST /pin               — add 'always_inject' tag
  POST /unpin             — remove 'always_inject' tag
  POST /show              — fetch full row including supersede chain
  GET  /health            — liveness probe
  GET  /status            — row counts + db path
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import (
    get_connection,
    init_db,
    insert_memory,
    insert_trigger,
    delete_memory,
    deprecate_memory,
    supersede_memory,
    get_memory_row,
    update_tags,
    get_pinned_memory_rows,
    get_active_trigger_rows,
    get_all_memory_rows,
    count_rows,
)
from .embed import embed_to_blob, top_k_rows


# ── trigger generation (no LLM — deterministic from content) ──────────────────

_STOP = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might shall to of in on at by for with "
    "and or but not".split()
)


def _fallback_triggers(content: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", content)
    key = [w.lower() for w in words if w.lower() not in _STOP][:8]
    if not key:
        return [content]
    noun_phrase = " ".join(key[:4])
    topic = key[0]
    triggers = [
        content,
        f"when working with {noun_phrase}",
        f"when asked about {topic}",
    ]
    if any(w in content.lower() for w in ("prefer", "use", "always", "never", "style", "format", "config", "setting")):
        triggers.append(f"when configuring or setting preferences for {topic}")
    return triggers


log = logging.getLogger("memory_server")
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = get_connection()
        init_db(_conn)
    return _conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("memory server v2 starting — initialising db")
    _get_conn()
    log.info("memory server ready")
    yield
    if _conn:
        _conn.close()
    log.info("memory server shut down")


app = FastAPI(title="swiszard-memory", version="2.0", lifespan=lifespan)


# ── request models ────────────────────────────────────────────────────────────

class RememberRequest(BaseModel):
    content: str
    triggers: list[str] = Field(default_factory=list)
    kind: str = "fact"
    session_id: str
    turn: int = -1
    source: str = "llm_extracted"
    tags: list[str] = Field(default_factory=list)
    ttl_seconds: int | None = None


class RecallRequest(BaseModel):
    query: str
    top_k: int = 5
    include_deprecated: bool = False  # only honored by /recall_content


class ForgetRequest(BaseModel):
    memory_id: int


class DeprecateRequest(BaseModel):
    memory_id: int
    reason: str | None = None


class SupersedeRequest(BaseModel):
    old_memory_id: int
    new_content: str
    new_triggers: list[str] = Field(default_factory=list)
    lesson: str | None = None
    session_id: str
    turn: int = -1
    source: str = "llm_extracted"
    tags: list[str] = Field(default_factory=list)


class TagRequest(BaseModel):
    memory_id: int


class ShowRequest(BaseModel):
    memory_id: int


class ListRequest(BaseModel):
    tag: str | None = None
    source: str | None = None
    include_deprecated: bool = False
    limit: int = 50
    offset: int = 0


class TagModifyRequest(BaseModel):
    memory_id: int
    tag: str


# ── helpers ───────────────────────────────────────────────────────────────────

def _provenance(row) -> dict:
    return {
        "session_id": row["session_id"],
        "turn":       row["turn"],
        "timestamp":  row["timestamp"],
    }


def _has_key(row, key: str) -> bool:
    try:
        _ = row[key]
        return True
    except (IndexError, KeyError):
        return False


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "version": "2.0"}


@app.get("/status")
def status():
    conn = _get_conn()
    return {"counts": count_rows(conn), "version": "2.0"}


@app.post("/remember")
def remember(req: RememberRequest):
    conn = _get_conn()
    content_vec = embed_to_blob(req.content)
    memory_id = insert_memory(
        conn,
        content=req.content,
        content_vec=content_vec,
        kind=req.kind,
        session_id=req.session_id,
        turn=req.turn,
        source=req.source,
        ttl_seconds=req.ttl_seconds,
        tags=req.tags,
    )
    triggers = req.triggers if req.triggers else _fallback_triggers(req.content)
    for trigger_text in triggers:
        trigger_vec = embed_to_blob(trigger_text)
        insert_trigger(conn, memory_id, trigger_text, trigger_vec)
    log.info("stored memory id=%d kind=%s triggers=%d", memory_id, req.kind, len(triggers))
    return {"memory_id": memory_id, "triggers_stored": len(triggers)}


@app.post("/recall_triggers")
def recall_triggers(req: RecallRequest):
    """Proactive recall: always-inject pins + similarity-matched (deprecated excluded)."""
    conn = _get_conn()

    # Always-inject pins (top of result, no similarity filter)
    pinned_rows = get_pinned_memory_rows(conn)
    pinned_ids = {row["id"] for row in pinned_rows}
    pinned_payload = [
        {
            "id":            row["id"],
            "content":       row["content"],
            "kind":          row["kind"],
            "trigger_score": 1.0,
            "matched_trigger": "<always_inject>",
            "tags":          json.loads(row["tags"] or "[]"),
            "provenance":    _provenance(row),
            "pinned":        True,
        }
        for row in pinned_rows
    ]

    # Similarity match against active (non-deprecated) triggers
    rows = get_active_trigger_rows(conn)
    matched_payload = []
    if rows:
        scored = top_k_rows(req.query, rows, vec_field="trigger_vec", k=req.top_k * 3)
        seen: dict[int, tuple[float, Any]] = {}
        for sim, row in scored:
            mid = row["memory_id"]
            if mid in pinned_ids:
                continue  # already in pinned section
            if mid not in seen or sim > seen[mid][0]:
                seen[mid] = (sim, row)
        top = sorted(seen.values(), key=lambda x: x[0], reverse=True)[: req.top_k]
        matched_payload = [
            {
                "id":            row["memory_id"],
                "content":       row["content"],
                "kind":          row["kind"],
                "trigger_score": round(sim, 4),
                "matched_trigger": row["trigger_text"],
                "tags":          json.loads(row["tags"] or "[]"),
                "provenance":    _provenance(row),
                "pinned":        False,
            }
            for sim, row in top
        ]

    return {"memories": pinned_payload + matched_payload}


@app.post("/recall_content")
def recall_content(req: RecallRequest):
    """On-demand recall by content vector. Includes deprecated by default for forensics."""
    conn = _get_conn()
    rows = get_all_memory_rows(conn)
    if not req.include_deprecated:
        rows = [r for r in rows if not r["deprecated"]]
    if not rows:
        return {"memories": []}

    scored = top_k_rows(req.query, rows, vec_field="content_vec", k=req.top_k)
    return {
        "memories": [
            {
                "id":            row["id"],
                "content":       row["content"],
                "kind":          row["kind"],
                "content_score": round(sim, 4),
                "tags":          json.loads(row["tags"] or "[]"),
                "provenance":    _provenance(row),
                "deprecated":    bool(row["deprecated"]),
                "superseded_by": row["superseded_by"],
                "lesson":        row["lesson"],
            }
            for sim, row in scored
        ]
    }


@app.post("/forget")
def forget(req: ForgetRequest):
    conn = _get_conn()
    if not delete_memory(conn, req.memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True}


@app.post("/deprecate")
def deprecate(req: DeprecateRequest):
    conn = _get_conn()
    if not deprecate_memory(conn, req.memory_id, req.reason):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True, "memory_id": req.memory_id, "reason": req.reason}


@app.post("/supersede")
def supersede(req: SupersedeRequest):
    conn = _get_conn()
    old_row = get_memory_row(conn, req.old_memory_id)
    if not old_row:
        raise HTTPException(status_code=404, detail="old_memory_id not found")

    # Insert new memory
    content_vec = embed_to_blob(req.new_content)
    new_id = insert_memory(
        conn,
        content=req.new_content,
        content_vec=content_vec,
        kind=old_row["kind"],
        session_id=req.session_id,
        turn=req.turn,
        source=req.source,
        ttl_seconds=None,
        tags=req.tags,
    )
    triggers = req.new_triggers if req.new_triggers else _fallback_triggers(req.new_content)
    for t in triggers:
        insert_trigger(conn, new_id, t, embed_to_blob(t))

    supersede_memory(conn, req.old_memory_id, new_id, req.lesson)
    log.info("superseded memory %d -> %d", req.old_memory_id, new_id)
    return {
        "new_memory_id": new_id,
        "old_memory_id": req.old_memory_id,
        "triggers_stored": len(triggers),
    }


def _modify_tags(memory_id: int, tag: str, add: bool) -> dict:
    conn = _get_conn()
    row = get_memory_row(conn, memory_id)
    if not row:
        raise HTTPException(status_code=404, detail="memory not found")
    tags = json.loads(row["tags"] or "[]")
    if add and tag not in tags:
        tags.append(tag)
    elif not add and tag in tags:
        tags.remove(tag)
    update_tags(conn, memory_id, tags)
    return {"ok": True, "memory_id": memory_id, "tags": tags}


@app.post("/pin")
def pin(req: TagRequest):
    return _modify_tags(req.memory_id, "always_inject", add=True)


@app.post("/unpin")
def unpin(req: TagRequest):
    return _modify_tags(req.memory_id, "always_inject", add=False)


@app.post("/show")
def show(req: ShowRequest):
    conn = _get_conn()
    row = get_memory_row(conn, req.memory_id)
    if not row:
        raise HTTPException(status_code=404, detail="memory not found")

    # Walk supersede chain forward
    chain = []
    cursor = row
    while cursor and cursor["superseded_by"]:
        chain.append(cursor["superseded_by"])
        cursor = get_memory_row(conn, cursor["superseded_by"])
        if cursor and cursor["id"] in chain[:-1]:
            break  # cycle protection

    return {
        "id":            row["id"],
        "content":       row["content"],
        "kind":          row["kind"],
        "tags":          json.loads(row["tags"] or "[]"),
        "provenance":    _provenance(row),
        "deprecated":    bool(row["deprecated"]),
        "deprecated_reason": row["deprecated_reason"],
        "superseded_by": row["superseded_by"],
        "lesson":        row["lesson"],
        "superseded_chain": chain,
    }


# ── tag/untag/list (browse without semantic recall) ──────────────────────────

@app.post("/tag")
def tag(req: TagModifyRequest):
    return _modify_tags(req.memory_id, req.tag, add=True)


@app.post("/untag")
def untag(req: TagModifyRequest):
    return _modify_tags(req.memory_id, req.tag, add=False)


@app.post("/list")
def list_memories(req: ListRequest):
    """Deterministic browse by tag/source. No embedding, no similarity."""
    import sqlite3
    conn = _get_conn()
    where = []
    params = []
    if not req.include_deprecated:
        where.append("deprecated = 0")
    if req.tag:
        where.append("tags LIKE ?")
        params.append(f"%{req.tag}%")
    if req.source:
        where.append("source = ?")
        params.append(req.source)
    sql = "SELECT id, content, kind, source, tags, deprecated, deprecated_reason, superseded_by, timestamp FROM memories"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([req.limit, req.offset])
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        out.append({
            "id":         r["id"],
            "content":    r["content"],
            "kind":       r["kind"],
            "source":     r["source"],
            "tags":       json.loads(r["tags"] or "[]"),
            "deprecated": bool(r["deprecated"]),
            "superseded_by": r["superseded_by"],
            "timestamp":  r["timestamp"],
        })
    # total count for pagination
    count_sql = "SELECT COUNT(*) FROM memories"
    if where:
        count_sql += " WHERE " + " AND ".join(where)
    total = conn.execute(count_sql, params[:-2] if where else []).fetchone()[0]
    return {"memories": out, "total": total, "returned": len(out), "offset": req.offset, "limit": req.limit}
