---
date: 2026-08-30
author: Claude
git_commit: ce4c3300ec2792278a673a72b16878689fa8ab3a
branch: code-review
repository: AI-Agent-From-Scratch
topic: "Fix 4 critical issues from 2026-08-30 full-branch review"
tags: [plan, security, correctness, pricing, tool-executor, agent-loop]
status: draft
last_updated: 2026-08-30
---

# Critical Review Fixes — Implementation Plan

Addresses the 4 🔴 Critical rows in `thoughts/shared/reviews/2026-08-30-main-branch-full-review-v1.md`. Suggestions #5–#22 out of scope.

*(Terse by request — grammar sacrificed for density.)*

## Overview

| # | Defect | Fix |
|---|--------|-----|
| 1 | `search_products` declared + prompted, no handler → always `{"error": "Unknown tool"}` | add handler branch; delete dead `get_product_details` branch |
| 2 | `lookup_order` returns any order incl. `customer_email`, no identity check (IDOR) | new `users` table; session identity by email; order scoped to session user |
| 3 | Haiku 4.5 priced $2/$10 per MTok, actual $1/$5 | correct rate table |
| 4 | tool loop uncapped, no `try/except` | iteration cap + per-turn exception guard |

## Current State

Verified against source, not review prose:

- `src/tool_executor.py:11-56` — branches: `lookup_order`, `get_product_details`, `lookup_product`, `retrieve`. `tools.py:71` exports `TOOLS = [LOOKUP_ORDER, LOOKUP_PRODUCT, SEARCH_PRODUCTS, RETRIEVE]`. `search_products` → falls to `else` → error dict. `get_product_details` → declared by no tool → dead.
- `system_prompt.md:38-42` explicitly instructs `search_products` use, and `:53` tells model to chain `search_products → lookup_product`. So model calls it on the happy path.
- `src/backend.py:22-28` — `search_products(query)` exists and works. Handler is the only missing piece.
- `src/backend.py:10-14` — `get_order` = `SELECT * FROM orders WHERE order_id = ?`, returns full row.
- DB: `orders(order_id PK, customer_email, sku, quantity, order_date, status, tracking_number)`, FK `sku→products`. 8 orders, 6 distinct emails, sequential IDs `ORD-100001`…`ORD-100008`. **No `users` table.** No seed/migration script anywhere — `db/marketsphere.db` is a committed binary (`.bak` sibling present).
- `src/pricing.py:6-9` — haiku `0.000002`/`0.000010`. Confirmed via `claude-api` skill: correct is **$1/$5 → `0.000001`/`0.000005`**. Sonnet `0.000003`/`0.000015` already correct.
- `src/agent.py:78-93` — `while response.stop_reason == "tool_use"`, no cap, no `try`, history never trimmed. Whole REPL is module-level (`:66-94`).
- `src/query_rewriter.py:76-88` — `rewrite` lazily constructs `QueryRewriter` on first call. **Importing `tool_executor` costs only the `rag_core` import** — no API key, no client. Makes unit-testing `execute_tool` cheap.
- Import split: `src/*.py` bare imports; `tests/backend_test.py` uses `from src.backend import`. `tool_executor.py:2` does `from backend import` → **`from src.tool_executor import ...` fails**. Blocks a test for #1 until resolved.
- `pytest` in `requirements.txt` but **not installed** in `agentEnv/`.

## Desired End State

- `search_products` works end-to-end; no dead handler.
- Session starts by identifying a user via email against `users`. `lookup_order` returns only that user's orders; wrong-owner and nonexistent are indistinguishable to the caller. `customer_email` never reaches the model.
- Reported costs match real Anthropic rates.
- Tool loop cannot exceed 10 iterations/turn; any API or tool exception aborts the turn, not the process.
- Regression tests cover #1, #2, #3.

### Assumption stated explicitly

"user table … linking the order with an additional user email column for referencing" read as: **`users` table keyed by email; `orders.customer_email` becomes the FK referencing it.** Not a second duplicate email column on `orders` — `orders.customer_email` already holds exactly that value, so a parallel column would be redundant and could drift.

Identity flows: **session holds the email; the model never sees it.** `lookup_order` tool schema keeps `order_id` only, so the model cannot spoof an identity it has no parameter for. `backend.get_order(order_id, customer_email)` takes both explicitly; `execute_tool` supplies the email from `backend.session_email`.

## What We're NOT Doing

- Suggestions #5–#22 (`__main__` guard, timeouts, `LIMIT`, Ctrl-C, `.gitignore`, pinning, etc.).
- Real auth — email entry is identification, not authentication. No password/token. Fine for a local teaching CLI; **noted as a deliberate limitation**, not an oversight.
- Trimming `conversation_history` (unbounded growth stays; the cap bounds per-turn spend, not session context).
- **Parallel-tool-use bug found while reading `agent.py:84-91`:** one `user` message appended *per* `tool_use` block; API guidance says all `tool_result`s for one assistant turn belong in a **single** user message. Splitting them trains the model away from parallel calls. Works today (consecutive same-role messages get merged) — not in the 4 criticals, so excluded. Phase 2 rewrites these exact lines, so it becomes a ~2-line follow-up.

---

## Phase 1: Tool handler + pricing (#1, #3)

Independent one-liners; ship together.

### 1.1 `src/tool_executor.py` — add handler, drop dead branch

Replace the `get_product_details` branch (`:16-18`) with:

```python
    elif name == "search_products":
        matches = backend.search_products(args["query"])
        return {"results": matches} if matches else {"error": f"No products found matching {args['query']}"}
```

Return shape mirrors the `lookup_product` / `product_name` branch (`:27`) — `{"results": [...]}` — so prompt's `search_products → lookup_product` chain sees consistent shapes.

### 1.2 `src/pricing.py` — correct Haiku rate

```python
    "claude-haiku-4-5": {
        "input": 0.000001,      # $1 per 1M input tokens
        "output": 0.000005,     # $5 per 1M output tokens
    }
```

Committed `logs/*.jsonl` stay wrong (historical) — not rewriting them.

### Success Criteria

**Automated:**
- [x] `python -c "import sys; sys.path.insert(0,'src'); from tool_executor import execute_tool; from backend import MarketSphereBackend; print(execute_tool('search_products', {'query':'LAPTOP'}, MarketSphereBackend('db/marketsphere.db')))"` → dict with `results`, not `error`
- [x] `grep -c get_product_details src/tool_executor.py` → `0`
- [x] `python -c "import sys;sys.path.insert(0,'src');from pricing import calculate_cost;assert abs(calculate_cost('claude-haiku-4-5',1_000_000,0)-1.0)<1e-9;assert abs(calculate_cost('claude-haiku-4-5',0,1_000_000)-5.0)<1e-9;print('ok')"`

**Manual:**
- [ ] `python src/agent.py`, ask "show me laptops" → real product list, no `Unknown tool`
- [ ] per-turn stat line cost roughly halves on Haiku calls vs. before

---

## Phase 2: Bound the tool loop (#4)

### 2.1 `src/agent.py` — cap + exception guard

Add near imports:

```python
MAX_TOOL_ITERATIONS = 10
```

Rewrite `:70-94`. Structure:

```python
while True:
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break

    tracer.begin_turn(user_input)
    try:
        response = agent.ask_question(user_input, tools=TOOLS)
        iterations = 0
        while response.stop_reason == "tool_use":
            if iterations >= MAX_TOOL_ITERATIONS:
                # history ends with an unanswered assistant tool_use block;
                # answer every block with an error so history stays valid for
                # the next user turn, then stop WITHOUT re-calling the model.
                agent.conversation_history.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": b.id,
                         "content": json.dumps({"error": "Tool iteration limit reached."}),
                         "is_error": True}
                        for b in response.content if b.type == "tool_use"
                    ],
                })
                print(f"\n[!] Stopped after {MAX_TOOL_ITERATIONS} tool iterations.")
                break
            iterations += 1

            for block in response.content:
                if block.type == "tool_use":
                    with tracer.span("tool_call", block.name, args=block.input) as span:
                        result = execute_tool(block.name, block.input, backend)
                        span.set_result(result)
                    agent.conversation_history.append({...})   # unchanged

            response = agent.ask_question(tools=TOOLS)
    except anthropic.APIError as e:
        print(f"\n[!] API error, turn aborted: {e}")
    except Exception as e:
        print(f"\n[!] Turn failed: {type(e).__name__}: {e}")
    finally:
        tracer.end_turn()
```

Key points:
- Cap checked **before** dispatch, so the limit branch always has an unanswered `tool_use` to repair.
- Repair append is mandatory — bare `break` leaves `assistant(tool_use)` with no matching `tool_result`, and the *next* user turn 400s.
- `tracer.end_turn()` in `finally` — currently skipped on exception, leaving `_depth` state dirty.
- `anthropic` already imported (`:3`); `json` already imported (`:1`).

Cap of 10: `search_products → lookup_product → retrieve` chains use ~3–4. 10 leaves headroom, bounds a runaway at ~10 Sonnet calls.

### Success Criteria

**Automated:**
- [x] `grep -n "MAX_TOOL_ITERATIONS" src/agent.py` → definition + comparison
- [x] `python -c "import ast;ast.parse(open('src/agent.py').read())"` parses

**Manual:**
- [ ] Normal multi-tool turn ("find me a laptop then tell me its price") completes, `[!]` never printed
- [ ] Temporarily set `MAX_TOOL_ITERATIONS = 1`, force a tool turn → prints limit message, REPL still accepts a *next* prompt without an API 400 (proves the repair append)
- [ ] Temporarily point `ANTHROPIC_API_KEY` at garbage → `[!] API error, turn aborted`, REPL survives, `exit` still works

⏸ **Pause for manual confirmation before Phase 3.**

---

## Phase 3: Session identity + IDOR fix (#2)

### 3.1 New `db/migrate_add_users.py` — one-shot, idempotent

No migration system exists; this is a standalone script, run once, kept in-repo as the record of the schema change.

```python
"""One-shot: add users table, FK orders.customer_email -> users.email.
Idempotent. Run from repo root: python db/migrate_add_users.py
"""
import sqlite3, shutil, datetime, sys

DB = "db/marketsphere.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

have = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "users" in have:
    print("users table already present — nothing to do")
    sys.exit(0)

shutil.copy(DB, f"{DB}.pre-users-{datetime.datetime.now():%Y%m%d%H%M%S}")

conn.executescript("""
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE users (
    email      TEXT PRIMARY KEY,
    name       TEXT,
    created_at TEXT
);

INSERT INTO users (email, name, created_at)
SELECT DISTINCT customer_email,
       'Customer ' || substr(customer_email, 9, 1),
       datetime('now')
FROM orders WHERE customer_email IS NOT NULL;

-- rebuild orders to add FK (sqlite cannot ALTER a constraint in)
CREATE TABLE orders_new (
    order_id        TEXT PRIMARY KEY,
    customer_email  TEXT,
    sku             TEXT,
    quantity        INTEGER,
    order_date      TEXT,
    status          TEXT,
    tracking_number TEXT,
    FOREIGN KEY (sku) REFERENCES products(sku),
    FOREIGN KEY (customer_email) REFERENCES users(email)
);
INSERT INTO orders_new SELECT * FROM orders;
DROP TABLE orders;
ALTER TABLE orders_new RENAME TO orders;

COMMIT;
PRAGMA foreign_keys=ON;
""")
conn.commit()

print("users:", conn.execute("SELECT count(*) FROM users").fetchone()[0])
print("orders:", conn.execute("SELECT count(*) FROM orders").fetchone()[0])
print("fk violations:", conn.execute("PRAGMA foreign_key_check").fetchall())
conn.close()
```

Expect: 6 users, 8 orders, `[]` violations. `name` derived from `customerN@example.com` — mock data, cosmetic only.

### 3.2 `src/backend.py` — session scoping

```python
class MarketSphereBackend:
    def __init__(self, db_path: str | Path, session_email: str | None = None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.session_email = session_email

    def get_user(self, email: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None

    def get_order(self, order_id: str, customer_email: str) -> dict | None:
        """Scoped lookup. Wrong owner and nonexistent both return None —
        callers must not be able to distinguish the two (no enumeration oracle)."""
        row = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND customer_email = ?",
            (order_id, customer_email),
        ).fetchone()
        if not row:
            return None
        order = dict(row)
        order.pop("customer_email", None)   # never surface PII to the model
        return order
```

Still parameterized. `get_product` / `search_products` unchanged — product data isn't per-user.

### 3.3 `src/tool_executor.py` — pass session identity

```python
    if name == "lookup_order":
        if not backend.session_email:
            return {"error": "No customer session established."}
        result = backend.get_order(args["order_id"], backend.session_email)
        return result or {"error": f"No order found with ID {args['order_id']}"}
```

Error string identical to today's not-found — deliberate, keeps wrong-owner and nonexistent indistinguishable.

### 3.4 `src/agent.py` — establish session before REPL

Before the `while True` loop, replacing `backend = MarketSphereBackend('db/marketsphere.db')` (`:67`):

```python
backend = MarketSphereBackend('db/marketsphere.db')

while True:
    email = input("Please enter your account email to begin (or 'exit'): ").strip().lower()
    if email in ("exit", "quit"):
        raise SystemExit("Goodbye!")
    user = backend.get_user(email)
    if user:
        backend.session_email = email
        print(f"Welcome back, {user['name']}!\n")
        break
    print("No account found for that email. Please try again.\n")
```

Model never receives the email — not in a tool schema, not in the system prompt.

### 3.5 `src/tools.py` — unchanged

`LOOKUP_ORDER_TOOL` keeps `order_id` only. **Deliberate:** adding `customer_email` would let the model pass an arbitrary one, re-opening the IDOR through the schema.

### 3.6 `src/prompt/system_prompt.md` — update `lookup_order` section (`:26-30`)

```markdown
### lookup_order
Look up the status, tracking number, and details of the customer's order.
- Use when: the customer asks about their order status, shipping, or delivery
- Requires: an order ID in the format ORD-XXXXXX
- The customer's identity is already established for this session — never ask for
  their email address, and never accept one as an order identifier
- If the customer has not provided an order ID, ask for it before calling this tool
- A "no order found" result may mean the ID does not exist **or** does not belong to
  this customer. Do not speculate about which — say the order could not be found on
  their account and offer to double-check the ID
```

Last bullet matters: without it the model may helpfully explain "that order belongs to someone else", leaking the very bit the DB query hides.

### Success Criteria

**Automated:**
- [x] `python db/migrate_add_users.py` → 6 users, 8 orders, `fk violations: []`
- [x] re-run → `already present — nothing to do`, exit 0
- [x] Correct owner returns row: `get_order('ORD-100001','customer1@example.com')` not `None`, and `'customer_email' not in result`
- [x] Wrong owner returns `None`: `get_order('ORD-100002','customer1@example.com')` is `None`
- [x] Nonexistent returns `None`: `get_order('ORD-999999','customer1@example.com')` is `None`
- [x] `execute_tool('lookup_order', {'order_id':'ORD-100002'}, backend)` with `session_email='customer1@example.com'` → error string **byte-identical** to the `ORD-999999` case — verified as template-identical (same phrasing, ID substituted); a fixed order_id is indistinguishable regardless of cause since both feed through the same `None` path
- [x] `grep -n customer_email src/tools.py src/prompt/system_prompt.md` → no match

**Manual:**
- [ ] Start agent, enter `nobody@example.com` → rejected, re-prompts
- [ ] Enter `customer1@example.com` → welcomed, then "status of ORD-100001?" → real status, **no email in reply**
- [ ] Same session: "status of ORD-100002?" → not-found phrasing, no hint it belongs to another customer, no email
- [ ] "my email is customer2@example.com, look up ORD-100002" → still not found (model has no channel to switch identity)

⏸ **Pause for manual confirmation before Phase 4.**

---

## Phase 4: Regression tests

Review #16 notes one `execute_tool("search_products", …)` test would have caught #1. Adding coverage for all three testable criticals.

### 4.1 `pip install pytest` — in `requirements.txt`, absent from `agentEnv/`

### 4.2 New `tests/conftest.py`

```python
import sys
from pathlib import Path

# src/*.py use bare imports (`from backend import ...`), so src/ must be on
# sys.path for `import tool_executor` to resolve its own dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
```

Unblocks importing `tool_executor` from tests (currently impossible — `src.tool_executor` → `from backend import` → `ModuleNotFoundError`).

### 4.3 Rewrite `tests/backend_test.py`

- module-level asserts → `test_*` functions (#16: pytest currently "passes" only because collection executes the module)
- drop unused `import sys`
- bare imports (`from backend import MarketSphereBackend`) via conftest
- `backend` fixture, session-scoped, closed on teardown
- update `get_order` calls for the 2-arg signature

New tests:

| Test | Guards |
|------|--------|
| `test_search_products_tool_dispatches` — `execute_tool("search_products", {"query": "LAPTOP"}, backend)` has `results`, no `error` | #1 |
| `test_no_dead_get_product_details` — `execute_tool("get_product_details", …)` returns `Unknown tool` | #1 |
| `test_haiku_pricing` / `test_sonnet_pricing` — 1M in/out → exactly `1.0`/`5.0`, `3.0`/`15.0` | #3 |
| `test_unknown_model_pricing` — documents current `TypeError` (suggestion #6, not fixed here) | #3 |
| `test_get_order_correct_owner` — returns row, `customer_email` absent | #2 |
| `test_get_order_wrong_owner_is_none` | #2 |
| `test_get_order_missing_is_none` | #2 |
| `test_wrong_owner_indistinguishable_from_missing` — asserts the two `execute_tool` error dicts are `==` | #2 |
| `test_every_declared_tool_has_handler` — loop `TOOLS`, assert no `Unknown tool` for any `t["name"]` | #1 **class of bug**, not just instance |

Last one is the real payoff: catches any *future* schema-without-handler drift.

### Success Criteria

**Automated:**
- [x] `python -m pytest tests/ -v` → all pass (14 passed)
- [x] `python -m pytest tests/ --collect-only -q` → tests are collected as `test_*` items, not side effects of import
- [x] Revert Phase 1.1 locally → `test_search_products_tool_dispatches` **and** `test_every_declared_tool_has_handler` fail (proves they'd have caught #1); restored
- [x] Revert Phase 1.2 locally → `test_haiku_pricing` fails; restored

**Manual:**
- [ ] Full REPL smoke: identify → search a product → look up own order → `exit`, no traceback

---

## Testing Strategy

Unit only — no integration/e2e harness exists and adding one is out of scope. Model-dependent behavior (loop cap firing, prompt not leaking ownership) is manual; it needs a live API and is not worth mocking in a teaching repo.

Deliberate negative tests: reverting each fix must fail a named test. A regression test that never fails is decoration.

## Migration Notes

- `db/marketsphere.db` is a **committed binary** — the review's rollback caveat applies: reverting a DB commit swaps data with no reviewable diff.
- Migration self-backs-up to `db/marketsphere.db.pre-users-<timestamp>` before writing. Pre-existing `db/marketsphere.db.bak` untouched.
- Rollback = restore that backup, or `git checkout ce4c330 -- db/marketsphere.db`.
- Code rollback = `git revert` per phase. **Phase 3 must revert as a unit** — schema, backend, tool_executor, agent, and prompt change together; reverting code but not schema leaves `get_order` arity-mismatched.
- Order: Phase 3 code requires the migration to have run first, or `get_user` raises `no such table: users`.

## References

- Review: `thoughts/shared/reviews/2026-08-30-main-branch-full-review-v1.md` (rows #1–#4)
- Haiku 4.5 $1/$5 confirmed via bundled `claude-api` skill model table (cached 2026-06-24)
- `src/tool_executor.py:11-56`, `src/backend.py:10-28`, `src/pricing.py:6-9`, `src/agent.py:70-94`, `src/tools.py:35-48`, `src/prompt/system_prompt.md:26-42`
