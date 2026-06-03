# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run (development):**
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python setup.py                  # one-time: creates dirs, DB, admin user
python -m uvicorn app:app --host 0.0.0.0 --port 7000
```

**Run (Docker — recommended):**
```bash
cp .env.example .env
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

**Tests:**
```bash
python -m pytest                        # all tests (asyncio_mode=auto via pyproject.toml)
python -m pytest tests/test_foo.py      # single test file
```

**Lint / syntax checks:**
```bash
python -m py_compile app.py routes/*.py src/*.py   # Python syntax
node --check static/js/<file>.js                    # JS syntax (no build step)
docker compose config                               # validate compose files
```

## Architecture

Odysseus is a self-hosted AI workspace built on **FastAPI** (Python 3.11+) with a **vanilla-JS PWA** frontend. There is no build step for the frontend — `static/app.js` and `static/js/` modules are served directly.

### Request flow

```
static/           → served as-is by FastAPI's StaticFiles
app.py            → registers all routes, middleware, startup/shutdown lifecycle
routes/           → thin HTTP boundary: validate input, call src/ or services/, stream response
src/              → all business logic (agents, LLM, memory, search, tools, RAG, etc.)
services/         → heavier subsystems (memory/, research/, hwfit/Cookbook, search/, tts/, stt/)
core/             → auth, database session, middleware, constants, exceptions
```

### Key data flows

**Chat / agent request:**
`routes/chat_routes.py` → `src/chat_processor.py` (context analysis, memory injection) → `src/chat_handler.py` (routing) → `src/agent_loop.py` (multi-round streaming with tool execution) → `src/llm_core.py` (LLM streaming, fallback logic) → SSE response.

**Tool execution inside agent loop:**
`src/agent_loop.py` calls `src/tool_execution.py` → dispatches to `src/tool_implementations.py` (built-in tools) or `src/agent_tools.py` (MCP servers managed by `src/mcp_manager.py`).

**RAG / memory:**
`src/embeddings.py` wraps ChromaDB (`src/chroma_client.py`) with a fastembed fallback for local ONNX embeddings. `src/rag_manager.py` / `src/rag_vector.py` handle document retrieval; `src/memory.py` / `src/memory_vector.py` handle semantic memory. `src/personal_docs.py` feeds personal documents into RAG.

**Search:**
`src/search/` is a multi-provider pipeline: `core.py` orchestrates `providers.py` (SearXNG, Brave, Google, Tavily, Serper), `content.py` fetches and extracts page content, `ranking.py` scores results, `cache.py` deduplicates. `src/deep_research.py` and `services/research/` layer multi-step synthesis on top.

### Database

SQLite by default (configurable via `DATABASE_URL`). ORM models live in `core/models.py`; the session factory is in `core/database.py`. `src/database.py` holds `src/`-layer DB helpers. Runtime data (DB, uploads, chroma, memory vectors) lives in `data/` (gitignored).

### Background jobs

`src/bg_jobs.py` and `src/bg_monitor.py` run in-process async loops (controlled by `ODYSSEUS_INPROCESS_POLLERS` / `ODYSSEUS_INPROCESS_TASKS` env vars). `src/task_scheduler.py` manages cron-style scheduled tasks.

### Auth

`core/auth.py` (AuthManager) handles sessions, bcrypt passwords, and TOTP 2FA. `core/middleware.py` adds security headers. `AUTH_ENABLED=false` / `LOCALHOST_BYPASS=true` skip auth for local dev. `.env` is loaded with `utf-8-sig` encoding to handle Windows BOM.

### Configuration

All config comes from `.env` (see `.env.example` for full reference). Key vars: `LLM_HOST`/`LLM_HOSTS`, `CHROMADB_HOST`, `SEARXNG_INSTANCE`, `AUTH_ENABLED`, `APP_BIND`/`APP_PORT`. GPU support via compose override: `COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml`.

### Companion service

`companion/` is a separate pairing/routing service (its own README) for multi-device use. It is independent of the main app.

## Windows notes

- Use Docker or WSL; native Windows is not actively tested.
- `HF_HUB_DISABLE_SYMLINKS=1` is auto-set on `nt` to avoid HuggingFace symlink failures on network shares.
- `.env` saved from Notepad may have a UTF-8 BOM — `load_dotenv(encoding="utf-8-sig")` handles this, but watch for it when debugging missing env vars.
