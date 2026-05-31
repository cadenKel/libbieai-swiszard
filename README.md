# libbieai-swiszard

**Ergonomic handles for your LLM. Deterministic tool routing + local semantic memory.**
Free and MIT. No LLM in the hot path. Local SQLite, local embeddings, zero phone-home.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/status-beta-yellow.svg)](.)
[![AI-generated](https://img.shields.io/badge/code-AI--generated_human--orchestrated-orange.svg)](#a-note-on-how-this-was-built)
[![tests](https://img.shields.io/badge/tests-14%2F14_passing-brightgreen)](.)

> Most agent tooling treats the LLM as infinitely capable (cram more context, more tools, more retries) and treats you as someone who can be trained to live with bad UX. swiszard flips both: the LLM gets ergonomic handles (one tool slot, deterministic dispatch, proactive memory), and so do you (one install, plain-English commands, no DSL to learn).

---

## What it does

- **One MCP tool slot** instead of 10+ for the common ops. Drops into Claude Desktop, Cursor, Continue, Cline, Hermes, or anything else that speaks MCP.
- **Zero LLM calls at dispatch.** Regex + TF-IDF + a tiny on-disk embedding bank decide where your task goes.
- **Local-first memory.** SQLite + nomic-embed-text (Ollama), all on your machine.
- **Proactive recall.** Memories auto-inject when the current situation embedding matches a stored trigger -- no query needed.
- **Pinnable facts.** Pin a memory and it injects every turn, like a sticky note on the LLM monitor.

## Benchmarks (honest)

On read-heavy git inspection operations against a real Flask checkout (tiktoken cl100k_base):

| Scenario              | Raw git tokens | swiszard tokens | Savings |
|-----------------------|---------------:|----------------:|--------:|
| log inspection (-30)  | ~4,000         | ~880            | **78%** |
| diff + blame combo    | ~2,500         | ~720            | **71%** |
| status on dirty tree  | ~1,200         | ~590            | **51%** |
| repo overview         | ~260           | ~390            | **net loss** (raw is already tiny) |

Average across the four: **~68% smaller payloads on the scenarios where it matters; a small loss on trivial calls.** The real win for your wallet comes when these compressed payloads also eliminate retry loops and turn-count, not just per-call bytes. See [BENCHMARKS.md](BENCHMARKS.md) for the script -- reproduce in 60 seconds.

---

## A note on how this was built

**All code in this repository is AI-generated and human-orchestrated by a biologist, not a career software engineer.** The architecture, the product decisions, the engineering taste, and every line of review come from a human; the typing comes from an LLM agent. We think this is a feature -- the result is unusually composable and explicit -- but it also means we genuinely need your eyes on it.

**If you find a security issue, dependency vulnerability, malware-like behavior, supply-chain concern, or anything sketchy:** open an issue with the security label, or email security@libbie.ai. We will respond fast and credit you in the changelog. Please be loud about it. Quiet bugs are how people get hurt and we are committed to never being the reason that happens.

We are also actively soliciting:

- Code review on the router (swiszard/handlers.py) and the memory server (memory_server/app.py)
- Have-you-tried-X feedback from people running this against real agent workloads
- PRs that make installs safer, the threat model clearer, or the tests harsher

If you want to help and you are not sure where to start, open a discussion. We are happy to point you at something useful.

---

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
