import os
from backend import MarketSphereBackend
from query_rewriter import rewrite
from rag_core import make_multi_query_retriever
from tracer import get_tracer

RAG_DB_URL = os.getenv("RAG_DB_URL")
_multi_query_retrieve = make_multi_query_retriever(rewrite)
_tracer = get_tracer()

def execute_tool(name: str, args: dict, backend: MarketSphereBackend) -> dict:
    if name == "lookup_order":
        if not backend.session_email:
            return {"error": "No customer session established."}
        result = backend.get_order(args["order_id"], backend.session_email)
        return result or {"error": f"No order found with ID {args['order_id']}"}

    elif name == "search_products":
        matches = backend.search_products(args["query"])
        return {"results": matches} if matches else {"error": f"No products found matching {args['query']}"}

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

    elif name == "retrieve":
        with _tracer.span("rag_call", "retrieve", query=args["query"]) as span:
            raw = _multi_query_retrieve(
                args["query"],
                db_url=RAG_DB_URL,
                top_k=args.get("top_k", 5)
            )
            chunks = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
            span.set_result([
                {"chunk_id": c.get("chunk_id"), "score": round(c.get("score", 0), 3)}
                for c in chunks[:5] if isinstance(c, dict)
            ])

        if chunks:
            for i, chunk in enumerate(chunks):
                if isinstance(chunk, dict):
                    chunk_id = chunk.get("chunk_id", f"chunk_{i}")
                    text = chunk.get("text", chunk.get("content", str(chunk)))
                else:
                    chunk_id = f"chunk_{i}"
                    text = str(chunk)
            print()
        return {"results": chunks} if chunks else {"error": "No relevant results found."}

    else:
        return {"error": f"Unknown tool: {name}"}