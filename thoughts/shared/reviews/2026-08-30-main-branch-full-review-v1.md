---
date: 2026-08-30T13:49:59-04:00
reviewer: Claude
git_commit: ce4c3300ec2792278a673a72b16878689fa8ab3a
branch: code-review
repository: AI-Agent-From-Scratch
target: "main branch (full source tree, excluding agentEnv/)"
version: 1
supersedes: null
verdict: Request Changes (superseded — all 4 criticals fixed, see below)
critical_count: 4
critical_fixed_count: 4
suggestion_count: 18
tags: [code-review, security, correctness, production-readiness, pricing, tool-executor, agent-loop]
status: complete
last_updated: 2026-08-30
last_updated_by: Claude
---

> **Update 2026-08-30**: All 4 critical issues below have been fixed per
> `thoughts/shared/plans/2026-08-30-critical-review-fixes.md` (Phases 1–4,
> all automated success criteria passing, 14/14 regression tests green).
> Suggestions #5–#22 remain out of scope / unaddressed. See the
> "Resolution" column added to the Critical Issues table.

# Code Review: `main` branch — AI-Agent-From-Scratch

**Date**: 2026-08-30T13:49:59-04:00
**Reviewer**: Claude
**Git Commit**: ce4c3300ec2792278a673a72b16878689fa8ab3a
**Branch**: code-review (identical to `main` — `git diff main..HEAD` is empty)
**Repository**: AI-Agent-From-Scratch
**Verdict**: Request Changes

*Full-tree review of the source, excluding the committed `agentEnv/` virtualenv.*

## What This Code Does

A framework-free customer-support agent for a fictional store, "MarketSphere". Each user turn runs three model calls: a Haiku intent classifier (`src/intent.py`), the Sonnet main agent streaming a reply (`src/agent.py`), and — when `retrieve` fires — a Haiku query rewriter (`src/query_rewriter.py`) that fans one query into N reformulations for `rag_core`'s multi-query retriever. Tool calls dispatch through `src/tool_executor.py` against a SQLite `MarketSphereBackend`. Every model call logs tokens/cost to JSONL; a console tracer prints nested spans per turn.

## Summary

The architecture is clean and the pedagogical goal is well served, but there are four defects that would bite in practice: a tool the model is instructed to use that has no handler, an unauthenticated order lookup that leaks other customers' email addresses, a 2× wrong Haiku price, and an uncapped tool loop with no error handling.

## Critical Issues

| # | File | Line | Issue | Severity | Resolution |
|---|------|------|-------|----------|------------|
| 1 | `src/tool_executor.py` | 11–56 | No handler for `search_products`, but `tools.py:35` declares it and `system_prompt.md` explicitly tells the model to use it ("Use this to find candidates, then follow up with lookup_product"). Every call returns `{"error": "Unknown tool: search_products"}`. Conversely `get_product_details` (line 16) is dead — no tool declares it. | 🔴 Critical | ✅ **Fixed** — `search_products` handler added, dead `get_product_details` branch removed. Regression: `test_search_products_tool_dispatches`, `test_every_declared_tool_has_handler`. |
| 2 | `src/tool_executor.py` | 12–14 | `lookup_order` returns the full row — including `customer_email` — for any order ID, with no identity verification. IDs are sequential (`ORD-100001`…`ORD-100008`), so "what's the status of ORD-100002?" hands the user another customer's PII. Classic IDOR. | 🔴 Critical | ✅ **Fixed** — new `users` table (`db/migrate_add_users.py`), session identified by email at REPL start, `get_order(order_id, customer_email)` scopes the query and strips `customer_email` from the result; wrong-owner and nonexistent orders are indistinguishable to the caller. Regression: `test_get_order_correct_owner`, `test_get_order_wrong_owner_is_none`, `test_get_order_missing_is_none`, `test_wrong_owner_indistinguishable_from_missing`, `test_lookup_order_no_session_email`. |
| 3 | `src/pricing.py` | 6–9 | Haiku 4.5 is priced at $2/$10 per MTok; the actual rate is **$1/$5**. Verified against the current pricing table (Sonnet 4.6 at $3/$15 is correct). Two of every three model calls are Haiku, so reported session cost is inflated — and the 11 committed JSONL logs are wrong too (`cost: 0.001544` for 272in/100out confirms the bad rate). | 🔴 Critical | ✅ **Fixed** — rate table corrected to $1/$5. Historical committed `logs/*.jsonl` intentionally left as-is (not rewritten). Regression: `test_haiku_pricing`, `test_sonnet_pricing`. |
| 4 | `src/agent.py` | 78–93 | `while response.stop_reason == "tool_use"` has no iteration cap, no `try/except`, and `conversation_history` is never trimmed. Combined with #1 — the model is told to call a tool that always errors — this loops, re-sending a growing history each pass, spending real money until you `kill` it. Any `APIError` mid-turn also takes the whole REPL down. | 🔴 Critical | ✅ **Fixed** — `MAX_TOOL_ITERATIONS = 10` cap (repairs history with an error `tool_result` on limit so the next turn stays valid), `try/except anthropic.APIError` + generic `Exception` around the turn, `tracer.end_turn()` moved to `finally`. History trimming explicitly out of scope (bounds spend per turn, not context growth). |

## Suggestions

| # | File | Line | Suggestion | Category |
|---|------|------|------------|----------|
| 5 | `src/agent.py` | 66–94 | `agent = Agent()` and the REPL run at **import time**. Wrap in `if __name__ == "__main__":` — otherwise any future `import agent` spawns a REPL and instantiates API clients. | Correctness |
| 6 | `src/pricing.py` | 13–15 | `PRICING.get(model)` returns `None` for an unknown model → `TypeError` *after* the paid API call succeeded. Raise a clear error or return 0.0 with a warning. | Correctness |
| 7 | `agent.py:23`, `intent.py:36`, `query_rewriter.py:44` | — | No explicit timeouts. SDK default is 10 min × 2 retries ≈ 30 min worst case, three times per turn. Pass `timeout=30.0, max_retries=2`. `sqlite3.connect` also has no `timeout=`. | Production |
| 8 | `src/agent.py` | 38 | `max_tokens=1000` on the main agent. If the cap is hit mid-tool-use, `stop_reason == "max_tokens"`, the loop exits and the user gets a truncated answer with no signal. Raise it and branch on `max_tokens` explicitly. | Correctness |
| 9 | `src/intent.py` | 81, 96 | `message.content[0].text` assumes block 0 is text. `message` is also read at line 96 *outside* the retry loop — the first attempt's tokens are silently never billed to `session_cost`, and an exception on attempt 2 leaves `message` stale. | Correctness |
| 10 | `src/tool_executor.py` | 44–52 | Dead loop: computes `chunk_id`/`text` for every chunk, assigns and discards both, then calls bare `print()`. Leftover debug scaffolding — delete it. | Maintainability |
| 11 | `src/backend.py` | 22–28 | `search_products` has no `LIMIT`; the whole result set goes into the model's context. `%`/`_` from an LLM-supplied query act as LIKE wildcards — `search_products("%")` returns all 56 rows. Add `LIMIT 20` and escape wildcards. | Performance |
| 12 | `src/backend.py` | 7 | `check_same_thread=False` disables sqlite's thread guard without adding a lock. Safe today (single-threaded REPL), latent the moment anything concurrent lands. | Correctness |
| 13 | `src/agent.py` | 70–94 | No SIGTERM/`KeyboardInterrupt` handling; `backend.close()` is never called. Ctrl-C dumps a traceback mid-turn. | Production |
| 14 | `src/intent.py` | 31 | `session_cost = 0.0` is a **class** attribute here while `Agent` and `QueryRewriter` use instance attributes. `self.session_cost +=` shadows it on first write, so it works — but it's inconsistent and fragile. | Maintainability |
| 15 | `src/logger.py` | 8 | Three independent `SessionLogger`s each stamp their own `datetime.now()` → three uncorrelated JSONL files per session. The tracer already has `_turn_id` (`tracer.py:11`) but it never reaches the logs. Threading `turn_id` into `log_turn` would make cost attributable per turn. | Observability |
| 16 | `tests/backend_test.py` | 1–34 | Module-level asserts rather than `test_*` functions (pytest passes only because collection executes the module); unused `import sys`; depends on the committed DB. Nothing covers `tool_executor`, `pricing`, or intent parsing — one test on `execute_tool("search_products", …)` would have caught #1. | Test coverage |
| 17 | `.vscode/settings.json` | 2 | `python.pythonPath` points at `.venv/bin/python`, but this repo's venv is `agentEnv/`. Also deprecated — use `python.defaultInterpreterPath`. | Style |
| 18 | `.gitignore` / `logs/` | 5 | 11 JSONL files are tracked despite `/logs` being ignored (added after they were committed). Contents are token/cost only — no PII — but `git rm --cached logs/*.jsonl`. | DevOps |
| 19 | `.gitignore` | 4 | `.txt` matches only a file literally named `.txt`. Probably meant `*.txt` — `Notes.txt` is currently neither tracked nor ignored. | DevOps |
| 20 | `requirements.txt` | 1–5 | `openai` and `numpy` are unused (and `openai` is misleading in a deliberately Anthropic-only project). All deps unpinned — `anthropic` spans a 0.x→1.x major boundary, so a fresh install can break the build. Pin them. | DevOps |
| 21 | `src/tool_executor.py` | 7 | `RAG_DB_URL` read at import with no validation. If unset, `retrieve` fails deep inside `rag_core` with an opaque error. Fail fast at startup. | Production |
| 22 | `src/backend.py` | 1 | Stale header comment `# src/compass/backend.py`. | Style |

## Stanford Best Practices Checklist

- [x] No hardcoded secrets or credentials — grep clean, key from `ANTHROPIC_API_KEY`, `.env` untracked
- [x] No debug/test flags left enabled — though #10 is leftover debug *code*
- [ ] Environment variables have safe defaults — `RAG_DB_URL` unvalidated (#21)
- [x] Docker layers minimized — N/A, no Dockerfile
- [x] Sensitive files isolated/excluded — `.env` gitignored; but `db/marketsphere.db` with `customer_email` is committed (mock data, acceptable here)
- [ ] AI-generated code manually verified — #1 (declared-but-unhandled tool) and #10 (dead loop) are exactly the signature of unverified generated code
- [ ] Matches project style/conventions — #14 class-vs-instance attribute inconsistency

## Production Hardening Checklist

- [ ] Timeouts set on all network/database calls — none set anywhere (#7)
- [x] Retries idempotent, backed off, jittered — SDK default handles this; intent classifier's 2-attempt retry is idempotent
- [ ] Failures degrade gracefully — no `try/except` around any API or tool call; one error kills the REPL (#4, #13)
- [ ] New failure paths logged and alertable — tracer prints failures to stdout only; JSONL logs record no errors at all
- [ ] Resource use bounded; SIGTERM clean — unbounded history, unbounded tool loop, unbounded SQL (#4, #11, #13)
- [x] Rollout gated / rollback documented — N/A, local CLI: rollback is `git revert`. **Caveat:** `db/marketsphere.db` is a committed binary, so reverting a seed commit silently swaps data with no diff to review
- [x] Migrations backward-compatible — N/A, no migration system

## The 3am Page

Nothing here is deployed, so the realistic 3am scenario is **#4 + #1 together**: the model is instructed to call `search_products`, gets `Unknown tool` back, retries, and the loop re-sends an ever-growing history with no cap. That's an open-ended spend against your API key with no ceiling.

**Fix order**: #1 (add the handler — one branch in `tool_executor.py`), then #4 (cap iterations at ~10, wrap the turn in `try/except`), then #3 (one-line price correction), then #2 (require an email or order-lookup token before returning `customer_email`).

**Status**: All four fixed and regression-tested per `thoughts/shared/plans/2026-08-30-critical-review-fixes.md` — Phase 1 (#1, #3), Phase 2 (#4), Phase 3 (#2), Phase 4 (14 automated regression tests, including negative tests proving each fix would be caught if reverted).

**Rollback**: local CLI, no deploy — `git revert` per commit. Watch `db/marketsphere.db`: it's a committed binary, so a revert changes data with no reviewable diff.

## What Looks Good

- Clean separation — `tools.py` schemas / `tool_executor.py` handlers / `backend.py` data access is the right seam, and the query-rewriter→`rag_core` callable contract is genuinely well-factored.
- **All SQL is parameterized.** No injection anywhere.
- Prompts as versioned `.md` files rather than inline strings — with real citation discipline in `system_prompt.md` ("never confabulate", cite `[ch_XXXX]`).
- Intent classifier's retry-with-correction-hint plus Pydantic-validated safe fallback is a solid pattern.
- `query_rewriter.py:58` uses the current `output_config={"format": {...}}` structured-output shape, not the deprecated `output_format`. Model IDs `claude-sonnet-4-6` and `claude-haiku-4-5` are both valid and correctly spelled.
- `tracer.py:42-54` correctly restores `_depth` and reports elapsed time on the exception path — many hand-rolled span implementations get that wrong.

## Verdict

**Request Changes** — #1 and #4 are user-visible breakage on the happy path; #2 and #3 are correctness issues worth fixing before this is demoed to anyone.

**Resolved as of 2026-08-30**: all 4 criticals fixed and covered by regression tests (see `thoughts/shared/plans/2026-08-30-critical-review-fixes.md`). Suggestions #5–#22 remain open and out of scope for that plan.
