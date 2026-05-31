# libbieai-swiszard

**Deterministic tool routing + local semantic memory for LLM agents.**
No LLM in the hot path. Pattern-match dispatch, local SQLite, local embeddings.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Why

Most agent frameworks route every action through the LLM. That wastes tokens, adds latency, and makes the agent feel sluggish. swiszard flips it: the LLM *chooses what to do*, and a deterministic router *does it*.

- **One MCP tool slot** instead of 10+ for the common ops (file read, find, grep, shell, web, memory).
- **Zero LLM calls** at dispatch time. Regex + TF-IDF + a tiny on-disk embedding bank.
- **Local-first memory**: SQLite + nomic-embed-text (Ollama), all on your box.
- **Token-thrifty**: session-scoped file dedup, recall-result truncation, preview-on-repeat-read.

If you are running a local LLM on consumer hardware and watching every token, this is the kind of plumbing that pays for itself in the first 50 turns.

## What is in the box

| Component        | What it does                                                  |
|------------------|---------------------------------------------------------------|
| swiszard/        | The router. One MCP tool, ~6 deterministic handlers.          |
| memory_server/   | FastAPI daemon at 127.0.0.1:7437. SQLite + nomic-embed-text.  |
| server.py        | The MCP server entry-point (FastMCP).                         |

## Install

    pip install libbieai-swiszard[all]
    # or just the bits you need:
    pip install libbieai-swiszard[mcp]      # router only
    pip install libbieai-swiszard[memory]   # memory server only

For semantic memory you will also want [Ollama](https://ollama.com) with the embedding model:

    ollama pull nomic-embed-text

## Run

Memory server (background):

    swiszard-memory          # binds 127.0.0.1:7437

MCP server (typically launched by your agent host):

    swiszard-mcp             # stdio MCP

## The DSL (cheat sheet)

    read /abs/path                          # full file
    find *.py in /abs/path                  # glob
    grep TEXT in /abs/path                  # content search
    write_b64 /abs/path BASE64              # write/overwrite, quoting-proof

    run COMMAND   (command must be wrapped in backticks)
    search the web for QUERY                # SearxNG (assumes local instance)

    memory recall QUERY                     # semantic search
    memory remember FACT                    # store fact
    memory show ID                          # full row
    memory pin ID | memory unpin ID         # always-inject toggle
    memory status                           # counts

    help | route: T | safety: T | chain: a | b

## Architecture

    LLM/Agent --swiszard_do(task)--> router.py
                                      1. regex rules
                                      2. TF-IDF cosine
                                      3. MiniLM embeddings (last resort)
                                      |
                                      v
                  handler_shell | handler_file_* | handler_memory
                                                      |
                                                      v
                                      HTTP 127.0.0.1:7437
                                                      |
                                                      v
                                      memory_server (SQLite + Ollama)

**No fallbacks.** If a handler cannot do its job it fails loudly. No silent LLM completion, no "best guess." Wrong is louder than slow.

## Status

v0.1.0 — **beta**. API may shift. Used in production by [LibbieAI](https://liljarv.dev).

## License

MIT. See LICENSE.

## Commercial

Free forever, MIT. If you want **first-party deterministic handler packs** (git ops, docker, k8s, AWS CLI, GitHub API, etc.) that drop in alongside the core, those live at https://liljarv.dev with a 7-day money-back guarantee.

This repo will always be free.
