# Eval set

`eval_set.json` is a 125-example eval set built directly against this agent's own tools and
intents (`src/tools.py`, `src/tool_gating.py`, `src/backend.py`), grounded against two real data
sources:

- `db/marketsphere.db` (`orders`, `products`, `returns`, `users`) for every DB-backed tool.
- The RAG knowledge base corpus at
  `/Users/akshatsaxena/Projects/Building-RAG-from-scratch/data/knowledge_base/marketsphere_kb`
  (an external sibling project — see root `CLAUDE.md` re: `rag_core`) for every `retrieve`-tool
  example. That corpus is real policy/FAQ/runbook markdown plus product-manual PDFs, and it ships
  with a `marketsphere_kb_meta/FLAWS.md` documenting four *deliberate* data-quality flaws. Several
  eval examples are built specifically to probe those.

## Coverage

| Intent | Tools exercised | Count |
|---|---|---|
| `order_status` | `lookup_order`, `list_customer_orders` | 37 |
| `product_question` | `lookup_product`, `search_products`, `retrieve` | 32 |
| `return_request` | `check_return_eligibility`, `initiate_return`, `cancel_order`, `list_customer_orders`, `retrieve` | 40 |
| `general_support` | `get_user_details`, `retrieve`, or no tool at all | 8 |
| `off_topic` | none (`INTENT_TOOL_MAP["off_topic"] == []`) | 8 |

Each example has: `id`, `agent_intent` (which of the 5 buckets in
`src/prompt/intent_classifier_prompt.md` it should hit), `session_customer_email` (every tool
here is scoped to "the logged-in customer," see `MarketSphereBackend._get_owned_order`),
`query`, `expected_tool_calls`, and `ground_truth` (`null` where there's genuinely nothing to
assert — off-topic turns, no-tool escalations).

## DB fixtures — `db/seed_eval_fixtures.py`

The original seed data had no delivered order inside the 30-day return window and no
out-of-stock product, so several scenarios below weren't constructible. `db/seed_eval_fixtures.py`
is a one-shot, idempotent script (same pattern as `db/migrate_add_returns.py` /
`db/migrate_add_users.py`, now with two independently-guarded batches) that adds:

- **Batch 1** (guarded on `customer7@example.com`): 6 customers, 8 orders (`ORD-200001`–`200008`),
  one out-of-stock product (`MS-HEAD-1004`).
- **Batch 2** (guarded on `customer13@example.com`): 8 more customers, 12 more orders
  (`ORD-300001`–`300012`), a second out-of-stock product (`MS-OUT-5001`).

Together with the original 8 orders / 6 customers, the DB now has **28 orders across 20
customers**, spanning every return/cancel state this agent's tools can produce: freshly-delivered
(eligible-for-return), boundary-eligible (29 days) and boundary-expired (31 days), long-expired,
still-processing (cancellable), shipped (neither cancellable nor returnable), and already-cancelled
— several customers have 2 orders each, to exercise `list_customer_orders`. Run it once from the
repo root: `python db/seed_eval_fixtures.py`. It no-ops per batch if that batch's marker customer
already exists.

## What's grounded against the DB vs. the knowledge base

**DB-backed** (`lookup_order`, `list_customer_orders`, `cancel_order`, `check_return_eligibility`,
`initiate_return`, `lookup_product`, `search_products`, `get_user_details`): every `ground_truth`
is a literal fact pulled from a live query against `db/marketsphere.db` at the time this set was
written (order status/tracking/sku, product price/stock, eligibility math against
`RETURN_WINDOW_DAYS = 30` in `backend.py`), not invented.

**KB-backed** (`retrieve`): `ground_truth` gives `expected_source_docs` (the KB corpus's
`document_id`s, per `marketsphere_kb_meta/manifest.jsonl`) and an `answer_gist` or specific facts
pulled directly from those documents. This agent's own `retrieve` runs against `rag_core`'s
indexed/reranked/multi-query-rewritten view of that corpus (see `src/query_rewriter.py`,
`src/tool_executor.py`), not the raw files — so treat `expected_source_docs` as "what a good
retrieval pipeline should surface," not a guarantee of this agent's exact chunk IDs.

## Deliberate edge cases

- **Not found**: nonexistent order IDs / product names / search terms.
- **Cross-owner / enumeration**: a real order ID belonging to a *different* customer than the
  session (`order_status_enumeration_*`, `return_request_cancel_enumeration_*`). `lookup_order`/
  `cancel_order` must say "not found," never leak or act on someone else's order —
  `_get_owned_order`'s "not found and wrong-owner are indistinguishable" contract.
- **Return-window boundary**: eligibility checks at 29 days (eligible) and 31 days (expired)
  straddle `RETURN_WINDOW_DAYS = 30`.
- **Cancel vs. return confusion**: asking to return an order that was never delivered
  (`return_request_cancel_vs_return_confusion_01`), to check the agent doesn't conflate the flows.
- **`search_products` substring quirk** (`product_question_search_quirk_01`):
  `backend.search_products` does one `LIKE '%query%'` match against the literal query string, not
  a per-word/tokenized search. `"wireless earbuds"` returns **zero** results even though
  `MS-HEAD-1003` ("AeroPulse Mini Earbuds", "True wireless, IPX4") is a clear match — the exact
  phrase never appears contiguously. `product_question_search_01` is the same intent with a single
  keyword (`"earbuds"`) that does match — good regression check for whether the agent retries with
  a narrower term after an empty result.
- **KB Flaw 1 — contradiction** (`product_question_rag_return_policy_electronics`):
  `runbook_electronics_returns_legacy.md` (2023, internal-only) says 14 days / 20% restocking fee;
  current customer-facing policy (`return_policy.md` + `electronics_return_addendum.md`, both
  2024-11-15) says 30 days / 15%. Checks the agent prefers the current, customer-facing answer.
- **KB Flaw 2 — staleness** (`product_question_rag_free_shipping_threshold`):
  `shipping_policy_archive_2023.md` (explicitly headed as historical) says $35; current
  `shipping_policy.md` (2024-10-01) says $50.
- **KB Flaw 3 — near-duplicate** (`product_question_rag_track_order_dedup`): `faq_track_order.md`
  and `faq_track_order_alt.md` answer the same question in different words — checks retrieval
  doesn't waste its whole top-k budget on both.
- **Vocabulary mismatch** (`general_support_rag_vocab_mismatch_reimbursement`): customer says
  "reimbursement," the policy text says "refund" (`return_policy.md`'s Refund Timing section).
- **Cross-format** (`product_question_rag_cross_format_manual_policy`): one query needs both a
  product-manual PDF (`ms-head-1004.pdf` troubleshooting) and a policy markdown doc
  (`electronics_return_addendum.md`) to answer fully.
- **No tool AND no KB coverage** (`general_support_no_tool_billing_charge`,
  `general_support_no_tool_password_reset`): real-time billing disputes and credential resets have
  no tool and no matching KB document — correct behavior is escalation, not a fabricated fix.
  Contrast with `general_support_rag_cancellation_fee` and `general_support_rag_damaged_item`,
  which have no *tool* but ARE answerable correctly via `retrieve`.
- **Off-topic / prompt-injection probes** (`off_topic_*`): confirm the agent declines and makes
  zero tool calls — `off_topic` gates the agent to an empty tool set (`tool_gating.py`), so any
  tool call at all here is a bug.

## Known limitations

- **Return/cancel-window fixtures are time-relative.** `check_return_eligibility` compares
  `order_date` against `datetime.now()`, so the "eligible" vs. "expired" fixtures in
  `db/seed_eval_fixtures.py` only stay correct relative to *when that script was run*, not a fixed
  calendar date. Re-run it (or bump the seeded `order_date` values) if this eval set is used long
  after the fixtures were seeded and the boundary cases have drifted.
- **4 examples mutate the DB** (tagged in their `notes` with "MUTATES"): 2 `cancel_order` happy
  paths flip a `processing` order to `cancelled`, and 2 `initiate_return` happy paths insert a row
  into `returns`. Running the suite twice against the same DB will make the two cancel examples
  report `not_cancellable` on the second pass. Restore `db/marketsphere.db` from git, or run
  against a throwaway copy, between full eval runs.
- **`retrieve`/RAG ground truth reflects the source documents, not this agent's live retrieval
  pipeline.** `rag_core`'s indexing, multi-query rewriting, and reranking (external to this repo)
  determine what chunks actually come back; a good pipeline should surface the documents listed in
  `expected_source_docs`, but exact chunk IDs/scores aren't predictable from here.

## Format

```json
{
  "id": "...",
  "agent_intent": "...",                 // which of the 5 intents in intent_classifier_prompt.md this should hit
  "session_customer_email": "...",       // the logged-in customer for this turn
  "query": "...",
  "expected_tool_calls": [{"name": "...", "input": {...}}, ...],  // [] means no tool should be called
  "ground_truth": {...} | null,          // expected facts from the DB, the KB corpus, or backend.py's return contract; null where ungradeable (off-topic, no-tool escalation)
  "notes": "..."
}
```
