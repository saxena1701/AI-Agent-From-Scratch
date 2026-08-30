import pytest

from backend import MarketSphereBackend
from tool_executor import execute_tool
from tools import TOOLS


@pytest.fixture(scope="session")
def backend():
    b = MarketSphereBackend("db/marketsphere.db")
    yield b
    b.close()


def test_get_product(backend):
    # Input: known SKU 'MS-LAPTOP-001'
    # Expected: the matching product row, with sku/name populated correctly
    product = backend.get_product("MS-LAPTOP-001")
    assert product is not None, "Product should exist"
    assert product["sku"] == "MS-LAPTOP-001", "SKU should match"
    assert product["name"] == "Dell XPS 13", "Name should match"


def test_get_product_missing(backend):
    # Input: a SKU that does not exist in the products table
    # Expected: None (no row, no exception)
    missing = backend.get_product("MS-NONEXISTENT")
    assert missing is None, "Non-existent product should return None"


def test_search_products(backend):
    # Input: keyword 'LAPTOP', matched against name/sku/description via LIKE
    # Expected: at least one hit, including the known Dell XPS SKU
    results = backend.search_products("LAPTOP")
    assert len(results) > 0, "Should find laptop products"
    assert any(r["sku"] == "MS-LAPTOP-001" for r in results), "Should find Dell XPS"


# --- #1: search_products tool dispatch (previously fell through to "Unknown tool") ---

def test_search_products_tool_dispatches(backend):
    # Input: execute_tool("search_products", {"query": "LAPTOP"}, backend) —
    #   the exact call the model makes per the system prompt's search_products
    #   -> lookup_product chain
    # Expected: a {"results": [...]} dict, NOT {"error": "Unknown tool: ..."} —
    #   guards against the missing-handler regression (#1)
    result = execute_tool("search_products", {"query": "LAPTOP"}, backend)
    assert "results" in result, f"Expected results, got: {result}"
    assert "error" not in result


def test_no_dead_get_product_details(backend):
    # Input: execute_tool("get_product_details", {...}, backend) — a tool name
    #   that was removed from the schema but might still be dispatched by
    #   leftover code
    # Expected: falls to the generic "Unknown tool" branch since no schema or
    #   handler declares it
    result = execute_tool("get_product_details", {"product_id": "MS-LAPTOP-001"}, backend)
    assert result == {"error": "Unknown tool: get_product_details"}


def test_every_declared_tool_has_handler(backend):
    # Input: every tool name declared in TOOLS (the schema list actually sent
    #   to the model), called with an empty args dict
    # Expected: none of them fall through to execute_tool's "Unknown tool"
    #   branch — catches any *future* schema-without-handler drift, not just
    #   the one instance fixed in #1. (A KeyError on a required arg is fine —
    #   it proves the handler branch was reached; only "Unknown tool" is a
    #   real failure here.)
    for tool in TOOLS:
        name = tool["name"]
        try:
            result = execute_tool(name, {}, backend)
        except KeyError:
            continue
        assert result != {"error": f"Unknown tool: {name}"}, f"{name} has no handler"


# --- #3: pricing rate table ---

def test_haiku_pricing():
    # Input: 1,000,000 input tokens / 1,000,000 output tokens at the Haiku 4.5 rate
    # Expected: $1.00 for input-only, $5.00 for output-only (actual Anthropic
    #   rate is $1/$5 per MTok; was previously mis-priced at $2/$10)
    from pricing import calculate_cost

    assert calculate_cost("claude-haiku-4-5", 1_000_000, 0) == pytest.approx(1.0)
    assert calculate_cost("claude-haiku-4-5", 0, 1_000_000) == pytest.approx(5.0)


def test_sonnet_pricing():
    # Input: 1,000,000 input tokens / 1,000,000 output tokens at the Sonnet rate
    # Expected: $3.00 for input-only, $15.00 for output-only (already correct
    #   before this fix — regression guard, not a bug fix)
    from pricing import calculate_cost

    assert calculate_cost("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(3.0)
    assert calculate_cost("claude-sonnet-4-6", 0, 1_000_000) == pytest.approx(15.0)


def test_unknown_model_pricing():
    # Input: a model name absent from the rate table
    # Expected: TypeError today (documents current behavior; suggestion #6 —
    #   raising a clearer error for unknown models — is not fixed here)
    from pricing import calculate_cost

    with pytest.raises(TypeError):
        calculate_cost("claude-nonexistent-model", 1_000_000, 1_000_000)


# --- #2: session-scoped lookup_order / IDOR fix ---

def test_get_order_correct_owner(backend):
    # Input: ORD-100001, owned by customer1@example.com — the correct owner
    # Expected: the order row is returned, and customer_email is stripped
    #   before it reaches the caller (never surface PII to the model)
    order = backend.get_order("ORD-100001", "customer1@example.com")
    assert order is not None, "Order should exist for its real owner"
    assert order["order_id"] == "ORD-100001"
    assert "customer_email" not in order


def test_get_order_wrong_owner_is_none(backend):
    # Input: ORD-100002, which actually belongs to customer2@example.com,
    #   looked up as customer1@example.com
    # Expected: None — a real order, but scoped away from a non-owner (this
    #   is the actual IDOR fix: no cross-customer order access)
    order = backend.get_order("ORD-100002", "customer1@example.com")
    assert order is None


def test_get_order_missing_is_none(backend):
    # Input: ORD-999999, an order ID that doesn't exist for anyone
    # Expected: None, same as the wrong-owner case
    order = backend.get_order("ORD-999999", "customer1@example.com")
    assert order is None


def test_wrong_owner_indistinguishable_from_missing(backend):
    # Input: execute_tool("lookup_order", ...) for the SAME order_id under
    #   two backends — one where that id belongs to someone else, one where
    #   it never existed at all — compared with the id text normalized out
    # Expected: identical error dict shape/phrasing either way, so a caller
    #   (or the model) has no oracle to infer "this order belongs to someone
    #   else" vs. "this order doesn't exist"
    wrong_owner = execute_tool(
        "lookup_order", {"order_id": "ORD-100002"},
        MarketSphereBackend("db/marketsphere.db", session_email="customer1@example.com"),
    )
    missing = execute_tool(
        "lookup_order", {"order_id": "ORD-999999"},
        MarketSphereBackend("db/marketsphere.db", session_email="customer1@example.com"),
    )
    normalize = lambda d: d["error"].replace("ORD-100002", "<ID>").replace("ORD-999999", "<ID>")
    assert normalize(wrong_owner) == normalize(missing)


def test_lookup_order_no_session_email():
    # Input: execute_tool("lookup_order", ...) against a backend with no
    #   session_email set (session identity never established)
    # Expected: an explicit "no session" error rather than a DB query with
    #   customer_email=None, which could otherwise match orders with a NULL
    #   customer_email if any existed
    b = MarketSphereBackend("db/marketsphere.db")
    result = execute_tool("lookup_order", {"order_id": "ORD-100001"}, b)
    assert result == {"error": "No customer session established."}
