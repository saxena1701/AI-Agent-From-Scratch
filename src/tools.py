LOOKUP_ORDER_TOOL = {
    "name": "lookup_order",
    "description": "Look up the status, tracking number, and details of a customer order by its order ID.",
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
    "description": "Look up product details, availability, pricing, and specifications by product ID or product name.",
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
    "description": "Search for products by keyword in name, SKU, or description. Returns a list of matching products.",
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
    "description": "Perform a semantic search over the knowledge base to find relevant information. Use this when the customer asks something that may be answered by documentation, FAQs, or product guides.",
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
