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

Tiered-model pipeline (main agent routed per turn across Haiku/Sonnet/Opus, ratcheting upward and never downgrading within a session; Haiku fixed for auxiliary calls) wired through a manual tool-use loop — no framework doing this implicitly.

**Per-turn flow** (`while True` loop, bottom of `src/agent.py`):
1. `IntentClassifierAgent.classify_intent()` (`src/intent.py`, Haiku) → one of 5 intents (`order_status`, `product_question`, `return_request`, `general_support`, `off_topic`) + confidence/reasoning, Pydantic-validated with retry-then-fallback (`general_support` on repeated failure). `src/tool_gating.py` maps the classification(s) to a tool subset (`INTENT_TOOL_MAP`, `CONFIDENCE_THRESHOLD = 0.5`, union across multi-intent results, safe fallback to the `general_support` set on an empty result or a validation-failure fallback), and `Agent.ask_question()` rebuilds `self.current_tools` from it every turn instead of always passing the full `TOOLS` list. `src/model_router.py` maps a resolved intent list onto a model tier (`INTENT_MODEL_MAP`, max tier across multi-intent, `MODEL_TIERS` ordered cheapest-first), and `Agent.ask_question()` calls it on every user turn, keeping the higher of the routed and current tier (`is_higher_tier()`, guarded by a `model_routed` flag so the pre-first-turn `DEFAULT_MODEL` placeholder is not treated as a floor). A session therefore escalates but never downgrades, and because the whole block sits under `if question is not None:`, the model never changes inside a turn's tool loop; a turn held at the session's tier logs `route_reason` as `"<its own intent>; held by floor (<the intent that set the tier>)"`. `resolve_intents()` in `tool_gating.py` is the shared source of truth for confidence filtering and fallback, feeding both the tool-gating and model-routing calls, so the two can't disagree about *what an intent means*.
2. `Agent.ask_question()` streams from `self.current_model` (re-routed each turn, held at the session's high-water tier) via `client.messages.stream`, printing tokens live, then pulls the final message for usage/cost.
3. If `stop_reason == "tool_use"`, each tool_use block dispatches through `execute_tool()` (`src/tool_executor.py`) against a live `MarketSphereBackend`; JSON result appended to `conversation_history` as `tool_result`. Loop re-calls the model until `stop_reason == "end_turn"`.
4. `Agent.run_turn()` checks the final reply with `src/output_checks.py` (deterministic only): tool-call markup written as text (`<function_calls>`), and bracketed citations whose `chunk_id` never came back from a `retrieve` call this session (`Agent.retrieved_chunk_ids`). A failing reply is popped from history, a correction is merged into the last user message, and the model is re-asked once (`OUTPUT_CHECK_RETRIES`); the retry runs through the tool loop again. If it fails again, fake markup becomes `FAKE_TOOL_FALLBACK` and bad citations are stripped. Events land in `Agent.last_output_checks`, which `eval.py` records per example. With `print_debug`, the rejected reply has already streamed to the console before the check runs. `streamlit_app.py` drives `ask_question()` directly, so it bypasses these checks.
5. Every model call (agent, intent classifier, query rewriter) independently logs tokens/cost via `pricing.calculate_cost()` + `SessionLogger` (`src/logger.py`) → JSONL per session in `logs/`.

**Tools** (`src/tools.py` schemas, handlers in `tool_executor.py`). Per-tool usage guidance (when to call it, how to explain each error) lives in each tool's `description`, not in `system_prompt.md`, so a tool removed by gating takes its instructions with it and the model never hears about a tool it can't call. Descriptions avoid naming other tools for the same reason; `system_prompt.md` holds only tool-agnostic rules (never write tool calls as text, never claim a lookup without a tool result, order-not-found wording, citations).
- `lookup_order` / `lookup_product` / `search_products` — direct SQLite via `MarketSphereBackend` (`src/backend.py`, wraps `db/marketsphere.db`, tables `orders`/`products`).
- `list_customer_orders` / `cancel_order` / `check_return_eligibility` / `initiate_return` / `get_user_details` — order-action and account tools added on top of the original three, also via `MarketSphereBackend`; schema for returns/cancellation added by `db/migrate_add_returns.py`.
- `retrieve` — semantic search over an external vector KB via `rag_core`, not single-query: `tool_executor.py` builds `_multi_query_retrieve = make_multi_query_retriever(rewrite)` at import time (`rewrite` from `src/query_rewriter.py`).

**Query rewriting** (`src/query_rewriter.py`, Haiku, structured JSON via `output_config.format`): one call → N reformulations (paraphrases, sub-questions, synonyms). `rewrite()` returns only rewrites, never the original, no retrieval/DB logic itself. `rag_core`'s `make_multi_query_retriever` does the merge: prepends original query, retrieves+reranks each independently, normalizes scores to `[0,1]` per query, merges by `chunk_id` keeping max score and recording every query that surfaced it — a chunk multiple rewrites agree on outranks one only the original found.

**Prompts** — versioned files in `src/prompt/*.md`, not inline strings: `system_prompt.md` (main agent; citation rule — cite retrieved chunks inline by their exact `chunk_id`, e.g. `[return_policy_c0]`, admit when retrieval has no clear answer rather than guess), `intent_classifier_prompt.md`, `query_rewriter_prompt.md` (`{n}` placeholder filled at `QueryRewriter.__init__`).

**Tracing** (`src/tracer.py`) — lightweight console tracer, not an observability lib. Module-level singleton (`get_tracer()`), global `_turn_id`/`_depth` state, not thread-safe (single conversation loop only). `agent.py` calls `begin_turn()`/`end_turn()` per user turn; `Agent`, `IntentClassifierAgent`, `QueryRewriter`, `tool_executor.py` each wrap their call in `tracer.span(kind, name, **fields)` (context manager, `span.set_result(...)` before exit). Prints indented, timestamped enter/exit lines showing nesting (e.g. `tool_call` containing `rag_call`) — complements, doesn't replace, `SessionLogger`'s JSONL cost logs.

**Cost/pricing** (`src/pricing.py`): flat per-model $/token rate table covering all four models (`claude-opus-5`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-haiku-4-5`); `calculate_cost()` raises `ValueError` on an unknown model, and `model_router` validates its registry against `PRICING` at import time so an unpriced routable model fails before any call is billed. `Agent`, `IntentClassifierAgent`, `QueryRewriter` each track their own `session_cost` and instantiate their own `SessionLogger` — no shared/global tracker. `Agent` additionally accumulates `baseline_cost` at `claude-sonnet-4-6` rates for the exit routing summary, computed per turn throughout the session, including on turns where the ratchet holds the model unchanged.

## Working in this repo

- `agentEnv/` is a committed venv (unusual — normally gitignored). Don't touch its contents; it's a dependency cache, not source.
- `rag_core` is external, not in this repo — its retrieval/reranking/merge internals are out of view, referenced only via the `rewrite`-callable contract and `make_multi_query_retriever`.
- Uses the RPI (research → plan → implement) workflow via `.claude/commands/` and `.claude/agents/` — see `.claude/RPI_QUICKSTART.md`. Prefer `/research_codebase` → `/create_plan` → `/implement_plan` for non-trivial changes, outputs under `thoughts/shared/`.
