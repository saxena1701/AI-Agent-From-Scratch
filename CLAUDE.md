# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A ground-up, framework-free implementation of an AI agent (customer support assistant for a fictional e-commerce platform, "MarketSphere"), built to expose every mechanic that agent frameworks normally hide: the conversation loop, streaming, intent classification, tool dispatch, RAG retrieval with query rewriting, and per-turn cost tracking. Every component talks to the Anthropic API directly via the `anthropic` SDK — there is no LangChain/etc.

## Setup & Commands

```bash
# Create/activate venv (already present as agentEnv/, but recreate if needed)
python3 -m venv agentEnv
source agentEnv/bin/activate
pip install -r requirements.txt

# rag_core is an external sibling project providing vector retrieval — required for the `retrieve` tool.
# Clone it separately and install editable into the same venv:
pip install -e <path/to/rag_core>
```

Required `.env` values: `ANTHROPIC_API_KEY`, `RAG_DB_URL` (sqlite URL pointing at rag_core's `rag.db`).

**Run the agent** (must run from repo root — it opens `db/marketsphere.db` and `src/prompt/*.md` via relative paths, and `src/` files import each other with bare module names, e.g. `from logger import SessionLogger`, not `src.logger`):
```bash
python src/agent.py
```

**Run tests** (test file imports via `src.backend`, i.e. run from repo root as a module, not with a bare interpreter path):
```bash
python -m pytest tests/
# or directly:
python -m tests.backend_test
```
Note the inconsistency: `src/*.py` files import each other with bare names (`from backend import ...`), while `tests/backend_test.py` imports `from src.backend import ...`. This is why `agent.py` is invoked as `python src/agent.py` (puts `src/` on `sys.path`) but tests are invoked as a module from the root instead.

## Architecture

The system is a two-model pipeline (Sonnet for the main agent, Haiku for cheap auxiliary calls) wired together through a manual tool-use loop — there is no agent framework doing this implicitly.

**Request flow, per user turn** (see the `while True` loop at the bottom of `src/agent.py`):
1. User input → `IntentClassifierAgent.classify_intent()` (`src/intent.py`, Haiku) classifies into one of 5 intents (`order_status`, `product_question`, `return_request`, `general_support`, `off_topic`) with confidence + reasoning, validated against a Pydantic schema with a retry-then-fallback loop (`general_support` on repeated failure). This is currently called for its side-effect logging; intent-based tool gating is the intended design (see README §6) but `agent.py` currently always passes the full `TOOLS` list.
2. `Agent.ask_question()` streams a response from `claude-sonnet-4-6` via `client.messages.stream`, printing tokens live, then retrieves the final message for usage/cost accounting.
3. If `response.stop_reason == "tool_use"`, each tool_use block is dispatched through `execute_tool()` (`src/tool_executor.py`) against a live `MarketSphereBackend`, and the JSON result is appended to `conversation_history` as a `tool_result` block. The loop re-calls the model until `stop_reason` is `end_turn`.
4. Every model call (main agent, intent classifier, query rewriter) independently logs input/output tokens and cost through `pricing.calculate_cost()` and `SessionLogger` (`src/logger.py`), which writes JSONL per session to `logs/`.

**Tools** (`src/tools.py`, Anthropic tool-use schema) and their handlers in `tool_executor.py`:
- `lookup_order` / `get_product_details` / `lookup_product` — direct SQLite lookups via `MarketSphereBackend` (`src/backend.py`, wraps `db/marketsphere.db`, tables `orders` and `products`).
- `retrieve` — semantic search over an external vector KB via `rag_core`. Not simple single-query RAG: `tool_executor.py` builds `_multi_query_retrieve = make_multi_query_retriever(rewrite)` at import time, where `rewrite` comes from `src/query_rewriter.py`.

**Query rewriting fan-out** (`src/query_rewriter.py`, Haiku, structured JSON output via `output_config.format`): a single call turns one query into N reformulations (paraphrases, decomposed sub-questions, synonym variants). `rewrite()` returns only the rewrites, never the original, and has no retrieval/DB logic itself — `rag_core`'s `make_multi_query_retriever` owns the merge: it prepends the original query, retrieves+reranks each query independently, normalizes scores to `[0,1]` per query, then merges results by `chunk_id`, keeping the max score and recording every query that surfaced each chunk. This means a chunk multiple rewrites agree on outranks one only the original query found.

**Prompts** live in `src/prompt/*.md` as versioned files, not inline strings — `system_prompt.md` (main agent, includes citation instructions: retrieved chunks must be cited inline as `[ch_XXXX]`, and the agent must admit when retrieval has no clear answer rather than guess), `intent_classifier_prompt.md`, `query_rewriter_prompt.md` (has an `{n}` placeholder substituted at `QueryRewriter.__init__` time).

**Cost/pricing model** (`src/pricing.py`): a flat per-model input/output $/token rate table (`claude-sonnet-4-6`, `claude-haiku-4-5`). Every class that makes a model call (`Agent`, `IntentClassifierAgent`, `QueryRewriter`) independently tracks its own `session_cost` and instantiates its own `SessionLogger` — there is no shared/global session or cost tracker across the three.

## Working in this repo

- `agentEnv/` is a committed virtualenv directory (unusual — normally gitignored). Don't touch its contents; treat it as a local dependency cache, not source.
- `rag_core` is an external dependency, not part of this repo — its retrieval/reranking/merge behavior is out of view here, referenced only through the `rewrite`-callable contract and `make_multi_query_retriever`.
- This repo uses the RPI (research → plan → implement) workflow via `.claude/commands/` and `.claude/agents/` — see `.claude/RPI_QUICKSTART.md`. Prefer `/research_codebase` → `/create_plan` → `/implement_plan` for non-trivial changes, with outputs saved under `thoughts/shared/`.
