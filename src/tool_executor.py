from backend import MarketSphereBackend

def execute_tool(name: str, args: dict, backend: MarketSphereBackend) -> dict:
    if name == "lookup_order":
        result = backend.get_order(args["order_id"])
        return result or {"error": f"No order found with ID {args['order_id']}"}

    elif name == "get_product_details":
        result = backend.get_product(args["product_id"])
        return result or {"error": f"No product found with ID {args['product_id']}"}

    elif name == "lookup_product":
        # if you want to support either product_id or product_name
        if "product_id" in args:
            result = backend.get_product(args["product_id"])
            return result or {"error": f"No product found with ID {args['product_id']}"}
        elif "product_name" in args:
            matches = backend.search_products(args["product_name"])
            return {"results": matches} if matches else {"error": f"No products found matching {args['product_name']}"}
        else:
            return {"error": "lookup_product requires product_id or product_name"}

    else:
        return {"error": f"Unknown tool: {name}"}