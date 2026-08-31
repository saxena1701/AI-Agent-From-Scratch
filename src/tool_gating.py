from intent import IntentClassification
from tools import (
    LOOKUP_ORDER_TOOL, LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL,
    LIST_CUSTOMER_ORDERS_TOOL, CANCEL_ORDER_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
    INITIATE_RETURN_TOOL, GET_USER_DETAILS_TOOL,
)

CONFIDENCE_THRESHOLD = 0.5

INTENT_TOOL_MAP = {
    "order_status": [LOOKUP_ORDER_TOOL, LIST_CUSTOMER_ORDERS_TOOL],
    "product_question": [LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL],
    "return_request": [
        LOOKUP_ORDER_TOOL, LIST_CUSTOMER_ORDERS_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
        INITIATE_RETURN_TOOL, CANCEL_ORDER_TOOL,
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


def gate_tools(classifications: list[IntentClassification]) -> list[dict]:
    """Return the tool schema subset the main agent should be offered this turn."""
    if _is_safe_fallback(classifications):
        return list(INTENT_TOOL_MAP["general_support"])

    kept = [c for c in classifications if c.confidence >= CONFIDENCE_THRESHOLD]

    gated: list[dict] = []
    seen_names: set[str] = set()
    for c in kept:
        for tool in INTENT_TOOL_MAP.get(c.intent, []):
            if tool["name"] not in seen_names:
                gated.append(tool)
                seen_names.add(tool["name"])

    if not gated and "off_topic" not in {c.intent for c in classifications}:
        return list(INTENT_TOOL_MAP["general_support"])

    return gated
