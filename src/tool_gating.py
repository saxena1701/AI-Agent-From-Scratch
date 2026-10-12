from intent import IntentClassification
from tools import (
    LOOKUP_ORDER_TOOL, LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL,
    LIST_CUSTOMER_ORDERS_TOOL, CANCEL_ORDER_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
    INITIATE_RETURN_TOOL, GET_USER_DETAILS_TOOL,
)

CONFIDENCE_THRESHOLD = 0.5

INTENT_TOOL_MAP = {
    # retrieve is included for order_status and return_request so policy/how-to
    # questions in those buckets ("how do I track my order?", "how long do
    # refunds take?") can still be answered from the knowledge base.
    "order_status": [LOOKUP_ORDER_TOOL, LIST_CUSTOMER_ORDERS_TOOL, RETRIEVE_TOOL],
    "product_question": [LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL],
    "return_request": [
        LOOKUP_ORDER_TOOL, LIST_CUSTOMER_ORDERS_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
        INITIATE_RETURN_TOOL, CANCEL_ORDER_TOOL, RETRIEVE_TOOL,
    ],
    "general_support": [
        GET_USER_DETAILS_TOOL, LOOKUP_ORDER_TOOL, LIST_CUSTOMER_ORDERS_TOOL, RETRIEVE_TOOL,
    ],
    "off_topic": [],
}

_FALLBACK_REASONING = "Safe fallback due to repeated validation failure."


def _is_safe_fallback(classifications: list[IntentClassification]) -> bool:
    return (
        len(classifications) == 1
        and classifications[0].intent == "general_support"
        and classifications[0].confidence == 0.0
        and classifications[0].reasoning == _FALLBACK_REASONING
    )


def resolve_intents(classifications: list[IntentClassification]) -> list[str]:
    """Reduce raw classifier output to the intent list that actually drives this turn.

    Single source of truth for confidence filtering and fallback selection, so
    tool gating and model routing can never disagree about what the turn is.
    Always returns at least one intent.
    """
    if _is_safe_fallback(classifications):
        return ["general_support"]

    resolved: list[str] = []
    seen: set[str] = set()
    for c in classifications:
        if c.confidence >= CONFIDENCE_THRESHOLD and c.intent not in seen:
            resolved.append(c.intent)
            seen.add(c.intent)

    if resolved:
        return resolved
    # Nothing cleared the bar. An off_topic signal at any confidence still means
    # "offer nothing"; otherwise fall back to the general_support bucket.
    if "off_topic" in {c.intent for c in classifications}:
        return ["off_topic"]
    return ["general_support"]


def gate_tools(classifications: list[IntentClassification]) -> list[dict]:
    """Return the tool schema subset the main agent should be offered this turn."""
    gated: list[dict] = []
    seen_names: set[str] = set()
    for intent in resolve_intents(classifications):
        for tool in INTENT_TOOL_MAP.get(intent, []):
            if tool["name"] not in seen_names:
                gated.append(tool)
                seen_names.add(tool["name"])
    return gated
