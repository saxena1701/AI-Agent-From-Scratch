import json
import os
import time
from datetime import datetime
from typing import List

import anthropic
from dotenv import load_dotenv

from logger import SessionLogger
from pricing import calculate_cost
from tracer import get_tracer

load_dotenv()

MODEL = "claude-haiku-4-5"

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Diverse reformulations of the original query, excluding the original itself",
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


class QueryRewriter:
    """Single-LLM-call query rewriter: one query in, list[str] rewrites out.

    No retrieval logic and no DB — this is step 1 of the multi-query fan-out
    (rewrite -> retrieve+rerank per query -> merge), owned by the RAG side.
    """

    def __init__(self, n: int = 3):
        self.n = n
        self.session_cost = 0.0
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.tracer = get_tracer()
        with open('src/prompt/query_rewriter_prompt.md', 'r') as f:
            self.system_prompt = f.read().replace("{n}", str(self.n))

    def rewrite(self, query: str) -> List[str]:
        start_time = time.time()

        with self.tracer.span("llm_call", "query-rewriter") as span:
            message = self.client.messages.create(
                model=MODEL,
                max_tokens=1000,
                system=self.system_prompt,
                messages=[{"role": "user", "content": query}],
                output_config={"format": {"type": "json_schema", "schema": REWRITE_SCHEMA}},
            )
            text = next(block.text for block in message.content if block.type == "text")
            rewrites = json.loads(text)["queries"]
            span.set_result({"rewrites": rewrites})

        latency = time.time() - start_time
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        turn_cost = calculate_cost(MODEL, input_tokens, output_tokens)
        self.session_cost += turn_cost
        self.logger.log_turn(MODEL, input_tokens, output_tokens, turn_cost, latency)
        self.logger.print_stats(input_tokens, output_tokens, turn_cost, self.session_cost)

        return rewrites


_default_rewriter = None


def rewrite(query: str) -> List[str]:
    """Public entry point: rewrite(query) -> list[str] of rewrites (original excluded).

    Matches the `rewriter: Callable[[str], list[str]]` shape rag_core's
    make_multi_query_retriever expects, so it can be passed straight through:
    `make_multi_query_retriever(rewrite)`.
    """
    global _default_rewriter
    if _default_rewriter is None:
        _default_rewriter = QueryRewriter()
    return _default_rewriter.rewrite(query)
