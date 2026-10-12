# Per-tool usage guidance lives in each description, not in system_prompt.md, so a
# tool that tool_gating removes for a turn takes its instructions with it and the
# model never hears about a tool it can't call.

LOOKUP_ORDER_TOOL = {
    "name": "lookup_order",
    "description": (
        "Look up the status, tracking number, and details of one of the logged-in "
        "customer's orders by its order ID. Use when the customer asks about an order's "
        "status, shipping, or delivery. If the customer hasn't given an order ID, ask for "
        "it before calling this tool. The customer's identity is already established for "
        "this session: never ask for their email address, and never accept one as an order "
        "identifier. Handle a 'no order found' result as described under 'Order not found' "
        "in your instructions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The order ID, format ORD-XXXXXX",
            }
        },
        "required": ["order_id"],
    },
}

LOOKUP_PRODUCT_TOOL = {
    "name": "lookup_product",
    "description": (
        "Look up product details, availability, pricing, and specifications by product ID "
        "or product name. Use when the customer asks about a specific product. Prefer "
        "product_id when you have it, otherwise use product_name; at least one is required."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "string",
                "description": "The product ID or SKU, e.g. PROD-XXXXXX. Provide this OR product_name.",
            },
            "product_name": {
                "type": "string",
                "description": "The name of the product to search for. Provide this OR product_id.",
            },
        }

    },
}

SEARCH_PRODUCTS_TOOL = {
    "name": "search_products",
    "description": (
        "Search for products by keyword in name, SKU, or description. Returns a list of "
        "matching products. Use when the customer describes what they want but doesn't know "
        "the exact product name or ID. Use the results as candidates, then look up full "
        "details for the ones that fit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term to query products by (name, SKU, or description)",
            }
        },
        "required": ["query"],
    },
}



RETRIEVE_TOOL = {
    "name": "retrieve",
    "description": (
        "Perform a semantic search over the knowledge base (policies, FAQs, product guides, "
        "and support articles). Use this when the customer asks something that may be "
        "answered by documentation, including general how-to and policy questions about "
        "orders and returns (e.g. 'how do I track my order?', 'how long do refunds take?', "
        "'my package arrived damaged, what do I do?'). Answer those from the knowledge base "
        "first; only ask for an order ID if the customer wants something done to a specific "
        "order. Each result has a chunk_id; cite results as described under 'Using "
        "retrieved knowledge' in your instructions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or topic to search for in the knowledge base.",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return. Defaults to 5.",
            },
        },
        "required": ["query"],
    },
}

LIST_CUSTOMER_ORDERS_TOOL = {
    "name": "list_customer_orders",
    "description": (
        "List all orders placed by the currently logged-in customer, most recent first. Use "
        "when the customer asks about their order history or doesn't have a specific order "
        "ID. Always scoped to the current session; never ask the customer for their email "
        "to use it."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

CANCEL_ORDER_TOOL = {
    "name": "cancel_order",
    "description": (
        "Cancel one of the logged-in customer's orders by order ID; ask for the ID if it "
        "wasn't given. When the customer asks to cancel, call this directly without looking "
        "the order up first: it checks and reports the status itself. Only orders with "
        "status 'processing' can be cancelled. If it returns 'not_cancellable', explain why "
        "using the 'status' it returned, never an assumed reason: 'cancelled' means the "
        "order is already cancelled and nothing more is needed; 'shipped' means it can no "
        "longer be cancelled, so offer a return once it is delivered; 'delivered' means "
        "offer to check return eligibility; for any other status, tell the customer the "
        "status as returned without guessing a reason. A 'no order found' error is "
        "different from 'not_cancellable': handle it as described under 'Order not found' "
        "in your instructions."
    ),
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
    "description": (
        "Check whether a delivered order is still eligible to be returned (within the "
        "return window). Use when the customer asks if they can return something, and "
        "before filing a return. Only delivered orders within the return window are "
        "eligible. Explain ineligibility in plain terms (not yet delivered, or outside the "
        "return window) using what the tool reports, without guessing at policy wording "
        "beyond it."
    ),
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
    "description": (
        "File a return request for a delivered, still-eligible order. A reason is optional "
        "but helpful: ask if the customer wants to give one, but don't require it. If the "
        "tool reports the order isn't eligible, explain why using the detail it returned "
        "rather than guessing."
    ),
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
    "description": (
        "Get the logged-in customer's own account profile (name, email, member-since date). "
        "Use when the customer asks about their account details. Always the current "
        "session's own account; never use it to look up another customer."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

TOOLS = [
    LOOKUP_ORDER_TOOL, LOOKUP_PRODUCT_TOOL, SEARCH_PRODUCTS_TOOL, RETRIEVE_TOOL,
    LIST_CUSTOMER_ORDERS_TOOL, CANCEL_ORDER_TOOL, CHECK_RETURN_ELIGIBILITY_TOOL,
    INITIATE_RETURN_TOOL, GET_USER_DETAILS_TOOL,
]
