# Expand Agent Tool Set Implementation Plan

## Overview

Add five new tools to the MarketSphere support agent: `list_customer_orders`, `cancel_order`, `check_return_eligibility`, `initiate_return`, and `get_user_details`. This closes a real capability gap — the system prompt already promises "Process return/refund requests" (`src/prompt/system_prompt.md:17`) but no return path exists anywhere in the codebase — and adds the first mutating tools (`cancel_order`, `initiate_return`) on top of what has been a read-only backend.

## Current State Analysis

- `src/tools.py` declares four tools; `src/tool_executor.py` dispatches them against `MarketSphereBackend` (`src/backend.py`), a thin SQLite wrapper over `db/marketsphere.db` (`orders`, `products`, `users`).
- All customer-specific access is scoped through `backend.session_email`, set once at REPL login (`src/agent.py:76-80`). `get_order` (`src/backend.py:17-28`) is the load-bearing security pattern: a wrong-owner order and a nonexistent order both return `None`, so nothing gives the model (or a malicious customer) an oracle to distinguish "not yours" from "doesn't exist." `tests/backend_test.py:139-155` (`test_wrong_owner_indistinguishable_from_missing`) pins this behavior.
- There is no `returns` table. `orders.status` is unconstrained `TEXT`; current seed data only contains `processing`, `shipped`, `delivered` (no `cancelled` yet).
- Every backend method today is a `SELECT`; there is no established write path or transaction-commit pattern in `MarketSphereBackend`.
- `db/migrate_add_users.py` is the one precedent for schema change: a one-shot, idempotent script — checks whether the target table already exists, backs up the `.db` file, runs the DDL/DML in a single `executescript` transaction, prints a verification summary. New schema work should follow this exact shape.
- `tests/backend_test.py` opens the **real** `db/marketsphere.db` file (session-scoped fixture, `tests/backend_test.py:8-12`), not a temp copy. Existing tests are all read-only, so this has never mattered; new mutating tests must not permanently alter seed data or the suite becomes non-reproducible on rerun.

## Desired End State

The agent can, within a single support session:
- List all of the session customer's past orders without needing an order ID (`list_customer_orders`).
- Cancel an order it owns, but only while the order is still `processing` (`cancel_order`).
- Check whether a delivered order still qualifies for return, and separately file a return request with an optional reason (`check_return_eligibility`, `initiate_return`).
- Retrieve the logged-in customer's own profile (name, email, account-created date) without asking for it (`get_user_details`).

All five tools follow the existing session-scoping and no-oracle conventions: unauthenticated session → explicit `{"error": "No customer session established."}`; wrong-owner vs. nonexistent order → identical error shape.

### Key Discoveries:
- `src/backend.py:17-28` — the get_order scoping/no-oracle pattern every new order-scoped method must replicate.
- `src/tool_executor.py:12-16` — the `if not backend.session_email` guard used per-tool; new tools repeat this rather than centralizing it (matches existing style, not introducing a new abstraction mid-plan).
- `db/migrate_add_users.py:1-58` — migration script shape to copy for the `returns` table.
- Current wall-clock date is 2026-08-30; every seeded `delivered` order's `order_date` is already **more than 30 days old** (`ORD-100006`, the most recent, is 2026-07-15 — 46 days ago). This means a 30-day return-eligibility window will show *no* seeded order as currently eligible. Tests must not depend on seeded rows for the "eligible" branch — see Testing Strategy.

## What We're NOT Doing

- Not implementing intent-based tool gating (the classifier result is still computed and discarded — a pre-existing, separately-tracked gap per `CLAUDE.md` and the 2026-08-30 review).
- Not building any ops-side workflow to approve/reject/complete a return once `initiate_return` files it — `returns.status` starts and stays `"requested"` in this plan; moving it forward is future scope.
- Not adding a `cancelled` value to a CHECK constraint — `orders.status` is unconstrained `TEXT` today and stays that way; `cancel_order` just writes the string `"cancelled"`.
- Not allowing cancellation of `shipped` or `delivered` orders (no recall/refuse-delivery flow).
- Not adding a way to look up another customer's user details or orders by email — `get_user_details` and `list_customer_orders` always resolve against `backend.session_email`, never a model-supplied identifier.
- Not touching `src/query_rewriter.py`, `src/intent.py`, or the RAG/`retrieve` path.

## Implementation Approach

Follow the grain of the existing code exactly: one `MarketSphereBackend` method per operation, one tool schema per operation, one `elif` branch per operation in `tool_executor.py`, and a matching section in `system_prompt.md`. Introduce one small new internal helper (`_get_owned_order`) in `MarketSphereBackend` so the no-oracle scoping logic is written once and reused by `get_order`, `cancel_order`, `check_return_eligibility`, and `initiate_return` instead of being copy-pasted four times — this is a refactor of `get_order` itself (kept behavior-identical, covered by existing tests) plus three new callers.

## Phase 1: `returns` Table Migration

### Overview
Add the `returns` table via a one-shot idempotent script mirroring `db/migrate_add_users.py`.

### Changes Required:

#### 1. New migration script
**File**: `db/migrate_add_returns.py`
**Changes**: New file.

```python
"""One-shot: add returns table (order_id/customer_email FK'd to existing tables).
Idempotent. Run from repo root: python db/migrate_add_returns.py
"""
import sqlite3, shutil, datetime, sys

DB = "db/marketsphere.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

have = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "returns" in have:
    print("returns table already present — nothing to do")
    sys.exit(0)

shutil.copy(DB, f"{DB}.pre-returns-{datetime.datetime.now():%Y%m%d%H%M%S}")

conn.executescript("""
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE returns (
    return_id       TEXT PRIMARY KEY,
    order_id        TEXT,
    customer_email  TEXT,
    sku             TEXT,
    reason          TEXT,
    status          TEXT,
    created_at      TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (customer_email) REFERENCES users(email)
);

COMMIT;
PRAGMA foreign_keys=ON;
""")
conn.commit()

print("returns:", conn.execute("SELECT count(*) FROM returns").fetchone()[0])
print("fk violations:", conn.execute("PRAGMA foreign_key_check").fetchall())
conn.close()
```

### Success Criteria:

#### Automated Verification:
- [x] Migration runs cleanly: `python db/migrate_add_returns.py`
- [x] Rerunning is a no-op: `python db/migrate_add_returns.py` prints "already present" and exits 0
- [x] Schema check: `sqlite3 db/marketsphere.db ".schema returns"` shows the table
- [x] No FK violations: script's own `PRAGMA foreign_key_check` prints an empty list

#### Manual Verification:
- [x] `.db.pre-returns-*` backup file exists after running

---

## Phase 2: Backend Methods

### Overview
Add the query/mutation logic to `MarketSphereBackend`, refactoring `get_order` to share scoping logic with the three new order-scoped methods.

### Changes Required:

#### 1. Shared ownership-scoped lookup + new methods
**File**: `src/backend.py`
**Changes**: Add `_get_owned_order` helper, refactor `get_order` to use it, add `list_orders`, `cancel_order`, `check_return_eligibility`, `initiate_return`.

```python
from datetime import datetime, timedelta

CANCELLABLE_STATUSES = {"processing"}
RETURN_WINDOW_DAYS = 30

class MarketSphereBackend:
    # ... existing __init__, get_user ...

    def _get_owned_order(self, order_id: str, customer_email: str) -> dict | None:
        """Scoped lookup shared by every order-touching method. Wrong owner and
        nonexistent both return None — callers must not be able to distinguish
        the two (no enumeration oracle). customer_email is NOT stripped here;
        callers that surface results to the model must strip it themselves."""
        row = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND customer_email = ?",
            (order_id, customer_email),
        ).fetchone()
        return dict(row) if row else None

    def get_order(self, order_id: str, customer_email: str) -> dict | None:
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        order.pop("customer_email", None)   # never surface PII to the model
        return order

    def list_orders(self, customer_email: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE customer_email = ? ORDER BY order_date DESC",
            (customer_email,),
        ).fetchall()
        orders = [dict(r) for r in rows]
        for o in orders:
            o.pop("customer_email", None)
        return orders

    def cancel_order(self, order_id: str, customer_email: str) -> dict | None:
        """None = not found or not owned (indistinguishable, see _get_owned_order).
        {"error": "not_cancellable", "status": ...} = found, owned, wrong status.
        Otherwise: the updated order dict, status now "cancelled"."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        if order["status"] not in CANCELLABLE_STATUSES:
            return {"error": "not_cancellable", "status": order["status"]}
        self.conn.execute(
            "UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,)
        )
        self.conn.commit()
        order["status"] = "cancelled"
        order.pop("customer_email", None)
        return order

    def check_return_eligibility(self, order_id: str, customer_email: str) -> dict | None:
        """None = not found or not owned. Otherwise {"eligible": bool, ...detail}."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        if order["status"] != "delivered":
            return {"eligible": False, "reason": "not_delivered", "status": order["status"]}
        order_date = datetime.fromisoformat(order["order_date"])
        days_since = (datetime.now() - order_date).days
        if days_since > RETURN_WINDOW_DAYS:
            return {"eligible": False, "reason": "window_expired", "days_since_order": days_since}
        return {"eligible": True, "days_since_order": days_since}

    def initiate_return(self, order_id: str, customer_email: str, reason: str | None = None) -> dict | None:
        """None = not found or not owned. {"error": "not_eligible", ...} = ineligible
        (same shape as check_return_eligibility's ineligible detail). Otherwise the
        created return record."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        eligibility = self.check_return_eligibility(order_id, customer_email)
        if not eligibility["eligible"]:
            return {"error": "not_eligible", **{k: v for k, v in eligibility.items() if k != "eligible"}}

        count = self.conn.execute("SELECT COUNT(*) FROM returns").fetchone()[0]
        return_id = f"RET-{100001 + count}"
        created_at = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO returns (return_id, order_id, customer_email, sku, reason, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'requested', ?)",
            (return_id, order_id, customer_email, order["sku"], reason, created_at),
        )
        self.conn.commit()
        return {
            "return_id": return_id,
            "order_id": order_id,
            "sku": order["sku"],
            "reason": reason,
            "status": "requested",
            "created_at": created_at,
        }
```

`get_user_details` needs no new backend method — dispatch calls the existing `get_user(backend.session_email)`.

### Success Criteria:

#### Automated Verification:
- [x] All existing backend tests still pass unmodified: `python -m pytest tests/ -k "get_order or search_products or get_product"`
- [x] Type checking / import sanity: `python -c "from src.backend import MarketSphereBackend"` run as `python -m pytest tests/` succeeds (imports resolve)

#### Manual Verification:
- [ ] None — covered by Phase 5 automated tests

---

## Phase 3: Tool Schemas & Dispatch

### Overview
Declare the five tool schemas and wire dispatch, matching the session-guard pattern already used by `lookup_order`.

### Changes Required:

#### 1. Tool schemas
**File**: `src/tools.py`
**Changes**: Add five schemas, append to `TOOLS`.

```python
LIST_CUSTOMER_ORDERS_TOOL = {
    "name": "list_customer_orders",
    "description": "List all orders placed by the currently logged-in customer, most recent first. Use when the customer asks about their order history or doesn't have a specific order ID.",
    "input_schema": {"type": "object", "properties": {}},
}

CANCEL_ORDER_TOOL = {
    "name": "cancel_order",
    "description": "Cancel a customer's order. Only orders that have not yet shipped (status 'processing') can be cancelled.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "The order ID, format ORD-XXXXXX"}
        },
        "required": ["order_id"],
    },
}

CHECK_RETURN_ELIGIBILITY_TOOL = {
    "name": "check_return_eligibility",
    "description": "Check whether a delivered order is still eligible to be returned (within the return window). Use before initiate_return, or when a customer just asks if they can return something.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "The order ID, format ORD-XXXXXX"}
        },
        "required": ["order_id"],
    },
}

INITIATE_RETURN_TOOL = {
    "name": "initiate_return",
    "description": "File a return request for a delivered, still-eligible order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "The order ID, format ORD-XXXXXX"},
            "reason": {"type": "string", "description": "Optional reason the customer is returning the item"},
        },
        "required": ["order_id"],
    },
}

GET_USER_DETAILS_TOOL = {
    "name": "get_user_details",
    "description": "Get the logged-in customer's own account profile (name, email, member-since date).",
    "input_schema": {"type": "object", "properties": {}},
}

TOOLS = [
    LOOKUP_ORDER_TOOL, LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL,
    LIST_CUSTOMER_ORDERS_TOOL, CANCEL_ORDER_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
    INITIATE_RETURN_TOOL, GET_USER_DETAILS_TOOL,
]
```

#### 2. Dispatch
**File**: `src/tool_executor.py`
**Changes**: Add branches before the final `else`.

```python
    elif name == "list_customer_orders":
        if not backend.session_email:
            return {"error": "No customer session established."}
        orders = backend.list_orders(backend.session_email)
        return {"results": orders} if orders else {"error": "No orders found on this account."}

    elif name == "cancel_order":
        if not backend.session_email:
            return {"error": "No customer session established."}
        result = backend.cancel_order(args["order_id"], backend.session_email)
        return result if result else {"error": f"No order found with ID {args['order_id']}"}

    elif name == "check_return_eligibility":
        if not backend.session_email:
            return {"error": "No customer session established."}
        result = backend.check_return_eligibility(args["order_id"], backend.session_email)
        return result if result else {"error": f"No order found with ID {args['order_id']}"}

    elif name == "initiate_return":
        if not backend.session_email:
            return {"error": "No customer session established."}
        result = backend.initiate_return(args["order_id"], backend.session_email, args.get("reason"))
        return result if result else {"error": f"No order found with ID {args['order_id']}"}

    elif name == "get_user_details":
        if not backend.session_email:
            return {"error": "No customer session established."}
        user = backend.get_user(backend.session_email)
        return user if user else {"error": "No account found for this session."}
```

Note: `cancel_order`'s `{"error": "not_cancellable", ...}` and `check_return_eligibility`/`initiate_return`'s `{"eligible": False, ...}` / `{"error": "not_eligible", ...}` results are returned as-is (truthy dicts) — only the `None` (not-found-or-not-owned) case gets the generic "No order found" text, preserving the no-oracle property: a wrong-owner order and a nonexistent order produce the exact same error string.

### Success Criteria:

#### Automated Verification:
- [x] `test_every_declared_tool_has_handler` (existing test, `tests/backend_test.py:62-76`) still passes with the five new tools added to `TOOLS` — proves no schema-without-handler drift
- [x] `python -m pytest tests/` passes in full

#### Manual Verification:
- [ ] None — covered by automated tests

---

## Phase 4: System Prompt

### Overview
Document the five new tools and their business rules so the agent uses them correctly and explains rejections without implying an oracle.

### Changes Required:

#### 1. New tool sections
**File**: `src/prompt/system_prompt.md`
**Changes**: Insert after the existing `### retrieve` section (before "### General tool-use guidelines"), matching the existing style.

```markdown
### list_customer_orders
List all orders for the logged-in customer.
- Use when: the customer asks about their order history, or wants to find an order but doesn't have the ID handy
- Requires: nothing — always scoped to the current session automatically
- Never ask the customer for their email to use this tool

### cancel_order
Cancel an order that has not yet shipped.
- Use when: the customer asks to cancel an order
- Requires: an order ID in the format ORD-XXXXXX; ask for it if not provided
- Only orders with status "processing" can be cancelled. If the tool reports the
  order isn't cancellable, tell the customer the order has already moved past the
  point where it can be cancelled (e.g. it has shipped) and offer other options
  (such as a return once delivered)
- A "no order found" result may mean the ID does not exist **or** does not belong
  to this customer — same rule as lookup_order: don't speculate about which

### check_return_eligibility
Check whether a delivered order can still be returned.
- Use when: the customer asks if they can return an item, or before calling initiate_return
- Requires: an order ID
- Only delivered orders within the return window are eligible; explain ineligibility
  in plain terms (not yet delivered, or outside the return window) without
  guessing at exact policy wording beyond what the tool reports

### initiate_return
File a return request for an eligible order.
- Use when: the customer wants to return an item and the order is return-eligible
- Requires: an order ID; a reason is optional but helpful — ask if the customer
  wants to provide one, don't require it
- If the tool reports the order isn't eligible, explain why using the detail it
  returned rather than guessing

### get_user_details
Retrieve the logged-in customer's own account profile.
- Use when: the customer asks about their account details (name, member-since date, etc.)
- Requires: nothing — always the current session's own account
- Never accept or use this tool to look up another customer's details
```

### Success Criteria:

#### Automated Verification:
- [x] File still loads: `python -c "open('src/prompt/system_prompt.md').read()"` (run from repo root)

#### Manual Verification:
- [ ] Run `python src/agent.py`, log in, ask "what orders have I placed?" → agent calls `list_customer_orders` without asking for an email
- [ ] Ask to cancel a `processing` order (e.g. ORD-100005 or ORD-100008 per current seed data) → succeeds, agent confirms cancellation
- [ ] Ask to cancel a `shipped`/`delivered` order → agent explains it can't be cancelled, doesn't imply it's missing
- [ ] Ask "can I return ORD-100001?" → agent explains it's outside the return window (delivered 2026-05-17, well past 30 days as of today)
- [ ] Ask about account details → agent calls `get_user_details` without asking for email

---

## Phase 5: Tests & Docs

### Overview
Add regression tests for the new backend methods and tool dispatch, following the existing IDOR-style testing pattern. Mutating tests use their own scratch rows (inserted and deleted within the test) rather than the seeded fixture rows, since `tests/backend_test.py` runs against the real `db/marketsphere.db` file and must stay reproducible across runs. Update the README tool table to match.

### Changes Required:

#### 1. Backend/tool tests
**File**: `tests/backend_test.py`
**Changes**: Add tests. Key cases:

```python
import uuid
from datetime import datetime, timedelta


def _make_scratch_order(backend, status, order_date, customer_email="customer1@example.com"):
    """Insert a throwaway order row for a mutating test; caller must delete it."""
    order_id = f"ORD-TEST-{uuid.uuid4().hex[:8]}"
    backend.conn.execute(
        "INSERT INTO orders (order_id, customer_email, sku, quantity, order_date, status, tracking_number) "
        "VALUES (?, ?, 'MS-LAPTOP-001', 1, ?, ?, NULL)",
        (order_id, customer_email, order_date, status),
    )
    backend.conn.commit()
    return order_id


def _delete_order(backend, order_id):
    backend.conn.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))
    backend.conn.execute("DELETE FROM returns WHERE order_id = ?", (order_id,))
    backend.conn.commit()


# --- list_customer_orders ---

def test_list_orders_scoped_to_customer(backend):
    orders = backend.list_orders("customer1@example.com")
    assert all("customer_email" not in o for o in orders)
    assert len(orders) > 0


def test_list_orders_no_cross_customer_leak(backend):
    orders = backend.list_orders("customer1@example.com")
    # every returned order must actually belong to customer1 in the DB
    for o in orders:
        row = backend.conn.execute(
            "SELECT customer_email FROM orders WHERE order_id = ?", (o["order_id"],)
        ).fetchone()
        assert row["customer_email"] == "customer1@example.com"


# --- cancel_order ---

def test_cancel_order_success(backend):
    order_id = _make_scratch_order(backend, "processing", "2026-08-25 00:00:00")
    try:
        result = backend.cancel_order(order_id, "customer1@example.com")
        assert result["status"] == "cancelled"
        assert "customer_email" not in result
    finally:
        _delete_order(backend, order_id)


def test_cancel_order_not_cancellable(backend):
    order_id = _make_scratch_order(backend, "shipped", "2026-08-01 00:00:00")
    try:
        result = backend.cancel_order(order_id, "customer1@example.com")
        assert result == {"error": "not_cancellable", "status": "shipped"}
    finally:
        _delete_order(backend, order_id)


def test_cancel_order_wrong_owner_indistinguishable_from_missing(backend):
    order_id = _make_scratch_order(backend, "processing", "2026-08-25 00:00:00", "customer2@example.com")
    try:
        wrong_owner = backend.cancel_order(order_id, "customer1@example.com")
        missing = backend.cancel_order("ORD-TEST-nonexistent", "customer1@example.com")
        assert wrong_owner is None and missing is None
    finally:
        _delete_order(backend, order_id)


# --- check_return_eligibility / initiate_return ---

def test_return_eligible_within_window(backend):
    recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    order_id = _make_scratch_order(backend, "delivered", recent)
    try:
        elig = backend.check_return_eligibility(order_id, "customer1@example.com")
        assert elig["eligible"] is True
    finally:
        _delete_order(backend, order_id)


def test_return_ineligible_outside_window(backend):
    old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
    order_id = _make_scratch_order(backend, "delivered", old)
    try:
        elig = backend.check_return_eligibility(order_id, "customer1@example.com")
        assert elig == {"eligible": False, "reason": "window_expired", "days_since_order": 45}
    finally:
        _delete_order(backend, order_id)


def test_return_ineligible_not_delivered(backend):
    order_id = _make_scratch_order(backend, "shipped", "2026-08-20 00:00:00")
    try:
        elig = backend.check_return_eligibility(order_id, "customer1@example.com")
        assert elig == {"eligible": False, "reason": "not_delivered", "status": "shipped"}
    finally:
        _delete_order(backend, order_id)


def test_initiate_return_success(backend):
    recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    order_id = _make_scratch_order(backend, "delivered", recent)
    try:
        result = backend.initiate_return(order_id, "customer1@example.com", reason="wrong size")
        assert result["status"] == "requested"
        assert result["order_id"] == order_id
        row = backend.conn.execute(
            "SELECT * FROM returns WHERE return_id = ?", (result["return_id"],)
        ).fetchone()
        assert row is not None and row["reason"] == "wrong size"
    finally:
        _delete_order(backend, order_id)


def test_initiate_return_ineligible(backend):
    old = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
    order_id = _make_scratch_order(backend, "delivered", old)
    try:
        result = backend.initiate_return(order_id, "customer1@example.com")
        assert result["error"] == "not_eligible"
        assert result["reason"] == "window_expired"
    finally:
        _delete_order(backend, order_id)


# --- get_user_details ---

def test_get_user_details_via_tool(backend):
    b = MarketSphereBackend("db/marketsphere.db", session_email="customer1@example.com")
    result = execute_tool("get_user_details", {}, b)
    assert result["email"] == "customer1@example.com"


def test_get_user_details_no_session():
    b = MarketSphereBackend("db/marketsphere.db")
    result = execute_tool("get_user_details", {}, b)
    assert result == {"error": "No customer session established."}
```

Also add the five new tool names to the existing `test_every_declared_tool_has_handler` coverage implicitly (it already iterates `TOOLS`, no change needed there — just verify it passes).

#### 2. README tool table
**File**: `README.md`
**Changes**: Update the tool table (around line 93) to list all nine tools instead of four, and remove/update the now-inaccurate framing (if any) implying returns aren't supported.

### Success Criteria:

#### Automated Verification:
- [ ] Full suite passes: `python -m pytest tests/`
- [ ] Suite is rerunnable without manual DB cleanup: `python -m pytest tests/ && python -m pytest tests/` both pass (proves scratch rows are cleaned up)
- [ ] `sqlite3 db/marketsphere.db "SELECT count(*) FROM orders"` reports the same row count before and after running the test suite (8, per current seed data)
- [ ] `sqlite3 db/marketsphere.db "SELECT count(*) FROM returns"` reports 0 after running the test suite

#### Manual Verification:
- [ ] README tool table reviewed for accuracy against `src/tools.py`

---

## Testing Strategy

### Unit Tests:
- No-oracle scoping for `cancel_order`, `check_return_eligibility`/`initiate_return` (wrong owner vs. missing → identical result), mirroring the existing `get_order` tests.
- Status-gating logic for `cancel_order` (only `processing` succeeds) and the 30-day window boundary for returns, using freshly-inserted scratch orders with controlled dates rather than seeded rows (seeded delivered orders are all >30 days old as of the current date and will only get older).
- Tool-dispatch session-guard (`No customer session established.`) for each new tool.

### Integration Tests:
- `execute_tool("initiate_return", ...)` end-to-end: creates a `returns` row, tool result matches what's persisted.
- `test_every_declared_tool_has_handler` continues to guard against schema/handler drift for the whole `TOOLS` list.

### Manual Testing Steps:
1. `python db/migrate_add_returns.py`, confirm `returns` table exists and old DB backup was created.
2. `python src/agent.py`, log in as an existing seeded customer.
3. Ask for order history → `list_customer_orders` fires, no email requested.
4. Cancel a `processing` order → succeeds; try again on the same order → now `shipped`/`cancelled`, rejected with a clear (non-oracle) explanation.
5. Ask about returning an old delivered order → ineligible (window expired), explained without guessing.
6. Ask for account details → `get_user_details` fires, no email requested.

## Performance Considerations

None significant — all new queries are single-row/small-table SQLite lookups, consistent with existing tool cost.

## Migration Notes

`db/migrate_add_returns.py` is additive-only (new table, no changes to `orders`/`products`/`users` schema) and idempotent, following `db/migrate_add_users.py`'s established pattern. Safe to run repeatedly; backs up the `.db` file before the first real run.

## References

- Prior migration precedent: `db/migrate_add_users.py`
- IDOR-safe scoping pattern: `src/backend.py:17-28` (`get_order`)
- Related review noting the persona/capability gap this plan closes: `thoughts/shared/reviews/2026-08-30-main-branch-full-review-v3.md`
