# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Overview

Ground-up, framework-free AI agent (customer support for fictional e-commerce "MarketSphere"), exposing every mechanic frameworks normally hide: conversation loop, streaming, intent classification, tool dispatch, RAG with query rewriting, per-turn cost tracking, tracing. All calls go directly through the `anthropic` SDK — no LangChain/etc.

## Setup & Commands

```bash
python3 -m venv agentEnv && source agentEnv/bin/activate
pip install -r requirements.txt
# rag_core is an external sibling project (vector retrieval, required for the `retrieve` tool) — clone separately:
pip install -e <path/to/rag_core>
```
Required `.env`: `ANTHROPIC_API_KEY`, `RAG_DB_URL` (sqlite URL to rag_core's `rag.db`).

**Run** (from repo root — opens `db/marketsphere.db` and `src/prompt/*.md` via relative paths; `src/*.py` import each other with bare names, e.g. `from logger import SessionLogger`):
```bash
python src/agent.py
```

**Test** (imports via `src.backend`, so run as a module from root, not a bare interpreter path):
```bash
python -m pytest tests/        # or: python -m tests.backend_test
```
Inconsistency to remember: `src/*.py` use bare imports (`from backend import ...`), `tests/backend_test.py` uses `from src.backend import ...`. Hence `agent.py` runs as `python src/agent.py` (puts `src/` on `sys.path`) while tests run as `-m` from root.

## Architecture

Two-model pipeline (Sonnet main agent, Haiku for cheap auxiliary calls) wired through a manual tool-use loop — no framework doing this implicitly.

**Per-turn flow** (`while True` loop, bottom of `src/agent.py`):
1. `IntentClassifierAgent.classify_intent()` (`src/intent.py`, Haiku) → one of 5 intents (`order_status`, `product_question`, `return_request`, `general_support`, `off_topic`) + confidence/reasoning, Pydantic-validated with retry-then-fallback (`general_support` on repeated failure). `src/tool_gating.py` maps the classification(s) to a tool subset (`INTENT_TOOL_MAP`, `CONFIDENCE_THRESHOLD = 0.5`, union across multi-intent results, safe fallback to the `general_support` set on an empty result or a validation-failure fallback), and `Agent.ask_question()` rebuilds `self.current_tools` from it every turn instead of always passing the full `TOOLS` list.
2. `Agent.ask_question()` streams from `claude-sonnet-4-6` via `client.messages.stream`, printing tokens live, then pulls the final message for usage/cost.
3. If `stop_reason == "tool_use"`, each tool_use block dispatches through `execute_tool()` (`src/tool_executor.py`) against a live `MarketSphereBackend`; JSON result appended to `conversation_history` as `tool_result`. Loop re-calls the model until `stop_reason == "end_turn"`.
4. Every model call (agent, intent classifier, query rewriter) independently logs tokens/cost via `pricing.calculate_cost()` + `SessionLogger` (`src/logger.py`) → JSONL per session in `logs/`.

**Tools** (`src/tools.py` schemas, handlers in `tool_executor.py`):
- `lookup_order` / `lookup_product` / `search_products` — direct SQLite via `MarketSphereBackend` (`src/backend.py`, wraps `db/marketsphere.db`, tables `orders`/`products`).
- `list_customer_orders` / `cancel_order` / `check_return_eligibility` / `initiate_return` / `get_user_details` — order-action and account tools added on top of the original three, also via `MarketSphereBackend`; schema for returns/cancellation added by `db/migrate_add_returns.py`.
- `retrieve` — semantic search over an external vector KB via `rag_core`, not single-query: `tool_executor.py` builds `_multi_query_retrieve = make_multi_query_retriever(rewrite)` at import time (`rewrite` from `src/query_rewriter.py`).

**Query rewriting** (`src/query_rewriter.py`, Haiku, structured JSON via `output_config.format`): one call → N reformulations (paraphrases, sub-questions, synonyms). `rewrite()` returns only rewrites, never the original, no retrieval/DB logic itself. `rag_core`'s `make_multi_query_retriever` does the merge: prepends original query, retrieves+reranks each independently, normalizes scores to `[0,1]` per query, merges by `chunk_id` keeping max score and recording every query that surfaced it — a chunk multiple rewrites agree on outranks one only the original found.

**Prompts** — versioned files in `src/prompt/*.md`, not inline strings: `system_prompt.md` (main agent; citation rule — cite retrieved chunks inline as `[ch_XXXX]`, admit when retrieval has no clear answer rather than guess), `intent_classifier_prompt.md`, `query_rewriter_prompt.md` (`{n}` placeholder filled at `QueryRewriter.__init__`).

**Tracing** (`src/tracer.py`) — lightweight console tracer, not an observability lib. Module-level singleton (`get_tracer()`), global `_turn_id`/`_depth` state, not thread-safe (single conversation loop only). `agent.py` calls `begin_turn()`/`end_turn()` per user turn; `Agent`, `IntentClassifierAgent`, `QueryRewriter`, `tool_executor.py` each wrap their call in `tracer.span(kind, name, **fields)` (context manager, `span.set_result(...)` before exit). Prints indented, timestamped enter/exit lines showing nesting (e.g. `tool_call` containing `rag_call`) — complements, doesn't replace, `SessionLogger`'s JSONL cost logs.

**Cost/pricing** (`src/pricing.py`): flat per-model $/token rate table (`claude-sonnet-4-6`, `claude-haiku-4-5`). `Agent`, `IntentClassifierAgent`, `QueryRewriter` each track their own `session_cost` and instantiate their own `SessionLogger` — no shared/global tracker.

## Working in this repo

- `agentEnv/` is a committed venv (unusual — normally gitignored). Don't touch its contents; it's a dependency cache, not source.
- `rag_core` is external, not in this repo — its retrieval/reranking/merge internals are out of view, referenced only via the `rewrite`-callable contract and `make_multi_query_retriever`.
- Uses the RPI (research → plan → implement) workflow via `.claude/commands/` and `.claude/agents/` — see `.claude/RPI_QUICKSTART.md`. Prefer `/research_codebase` → `/create_plan` → `/implement_plan` for non-trivial changes, outputs under `thoughts/shared/`.
