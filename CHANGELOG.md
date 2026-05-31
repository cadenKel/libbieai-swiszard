# Changelog

## [0.1.0] - 2026-05-31

Initial public release.

- Deterministic router: regex + TF-IDF + MiniLM embedding fallback.
- 6 handlers: file_read, file_find, file_write (base64), shell, web_search, memory.
- Memory server v2 (FastAPI): SQLite + nomic-embed-text via Ollama.
- Memory ops: recall, remember, pin/unpin, deprecate, supersede, lessons.
- Session-scoped file-read dedup for context-token savings.
- MCP entry-point via FastMCP.
