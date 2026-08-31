# From Zero to Agent: An Iterative Build

A ground-up implementation of a production-style AI agent, built iteratively to understand the core mechanics behind agentic systems. The agent is scoped as a customer support assistant for an e-commerce platform (MarketSphere), backed by a real SQLite database.

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Build Steps](#build-steps)
- [Setup](#setup)
- [How It Works](#how-it-works)
- [Models Used](#models-used)

---

## Overview

Rather than using a high-level agent framework, every component here is implemented explicitly — the conversation loop, streaming, intent classification, intent-based tool gating, tool dispatch, RAG with query rewriting, per-turn cost tracking, and tracing. The goal is full visibility into what an agent is actually doing at each step.

---

## Project Structure

```
AI-Agent-From-Scratch/
├── src/
│   ├── agent.py              # Main agent class + conversation loop
│   ├── intent.py             # Intent classifier agent (separate Claude client)
│   ├── tool_gating.py        # Maps classified intent(s) -> the tool subset offered this turn
│   ├── tools.py              # Tool definitions (Anthropic tool-use schema)
│   ├── tool_executor.py      # Tool dispatcher — routes agent requests to backend
│   ├── query_rewriter.py     # Query rewriter (multi-query fan-out, separate Claude client)
│   ├── backend.py            # MarketSphere SQLite backend (orders, products, returns)
│   ├── pricing.py            # Per-turn cost calculator
│   ├── logger.py             # Session logger (JSON output)
│   ├── tracer.py             # Lightweight console tracer (nested spans per turn)
│   └── prompt/
│       ├── system_prompt.md          # Main agent system prompt
│       ├── intent_classifier_prompt.md  # Intent classifier system prompt
│       └── query_rewriter_prompt.md     # Query rewriter system prompt
├── db/
│   ├── marketsphere.db       # SQLite mock database
│   └── migrate_add_returns.py  # One-off migration adding returns/cancellation support
├── tests/                    # pytest suite (backend, tool gating)
├── logs/                     # Per-session JSONL cost/usage logs
├── thoughts/                 # RPI workflow output (research, plans, reviews, handoffs)
├── requirements.txt
└── README.md
```

---

## Build Steps

### 1. Claude API Client + Conversation Loop
The `Agent` class wraps the Anthropic SDK and maintains a `conversation_history` list that grows with each turn. Every user message and assistant response is appended, giving the agent full context across the session. The loop runs until the user types `exit` or `quit`.

### 2. Multi-Turn Conversation History
Conversation state is persisted across turns within a session. The full message history is passed to the API on every call, enabling the agent to reference earlier messages and maintain coherent multi-turn dialogue.

### 3. System Prompts & Prompt Versioning
The agent's behaviour is defined by a system prompt loaded from `src/prompt/system_prompt.md`. Storing prompts as versioned files in a dedicated directory treats them with the same discipline as code — changes are tracked, reviewable, and reversible.

The main agent is scoped as an e-commerce support assistant with tool-use guidance baked into the prompt. A separate prompt file drives the intent classifier.

### 4. Response Streaming
Agent responses are streamed token-by-token using `client.messages.stream`, printing output as it arrives rather than waiting for the full response. This significantly reduces perceived latency. After streaming completes, `stream.get_final_message()` retrieves the full message object including usage stats.

### 5. Cost Tracking & Session Pricing
Every API call is metered in `pricing.py`, which calculates cost from input/output token counts against a per-model rate table. Costs accumulate across turns and are logged per-session to `logs/` as JSONL files via `SessionLogger`. Per-turn stats are also printed to the terminal.

**Rates tracked:**
| Model | Input | Output |
|---|---|---|
| claude-sonnet-4-6 | $3 / 1M tokens | $15 / 1M tokens |
| claude-haiku-4-5 | $2 / 1M tokens | $10 / 1M tokens |

### 6. Intent Classification
Before the main agent processes a query, a lightweight `IntentClassifierAgent` (backed by `claude-haiku-4-5`) categorises the user's intent into one of five classes:

| Intent | Description |
|---|---|
| `order_status` | Customer asking about an order |
| `product_question` | Product details or availability |
| `return_request` | Return or refund |
| `general_support` | General help |
| `off_topic` | Outside the agent's scope |

Each classification includes a `confidence` score and a `reasoning` field. Output is validated against a Pydantic schema (`IntentClassification`) with a retry loop and safe fallback — a misclassified or malformed output falls back to `general_support` rather than propagating downstream errors.

### 7. Intent-Based Tool Gating
`src/tool_gating.py` maps each classified intent to the subset of tools relevant to it (`INTENT_TOOL_MAP`), and `Agent.ask_question()` (`src/agent.py`) rebuilds `self.current_tools` from the classifier's output at the start of every turn instead of always passing the full `TOOLS` list. This keeps the tool schema out of the model's context when it's irrelevant (e.g. no return/cancel tools offered for a `product_question`), reducing prompt size and the chance of the wrong tool being called.

Rules:
- Only classifications at or above `CONFIDENCE_THRESHOLD` (0.5) contribute tools; low-confidence classifications are dropped.
- Tools from multiple qualifying intents (the classifier can return more than one) are unioned, deduplicated by tool name.
- `off_topic` alone yields no tools.
- If gating would otherwise produce an empty set (and `off_topic` isn't present) or the classifier fell back after repeated validation failure, gating falls back to a safe `general_support` tool set rather than leaving the agent unable to act.

Covered by `tests/tool_gating_test.py`.

### 8. Expanded Tool Set — Order Actions & Returns
Beyond the original three, the agent now has tools for account- and order-scoped actions, all served by `MarketSphereBackend` (`src/backend.py`) against `db/marketsphere.db` (schema extended via `db/migrate_add_returns.py`):

| Tool | Purpose |
|---|---|
| `lookup_order` | Fetch order status and details by order ID |
| `lookup_product` | Look up a product by ID or name |
| `search_products` | Keyword search across products |
| `list_customer_orders` | List the logged-in customer's orders, most recent first |
| `cancel_order` | Cancel an order still in `processing` status |
| `check_return_eligibility` | Check whether a delivered order is within the return window |
| `initiate_return` | File a return request for an eligible, delivered order |
| `get_user_details` | Fetch the logged-in customer's own account profile |
| `retrieve` | Semantic search over the knowledge base (see below) |

The main loop checks `response.stop_reason` after each turn:
- **`tool_use`** — the agent wants to call a tool. The tool name and arguments are extracted from the response content, dispatched through `execute_tool()`, and the result is fed back as a `tool_result` message. The loop continues.
- **`end_turn`** — the agent has everything it needs and is ready to respond to the user.

`tool_executor.py` routes each tool call to the appropriate `MarketSphereBackend` method, which queries a local SQLite database (`db/marketsphere.db`).

### 9. Retrieval-Augmented Generation (RAG)
The `retrieve` tool extends the agent beyond structured database lookups into unstructured knowledge. It performs a semantic search over a vector knowledge base (via `rag_core`) containing product guides, policies, and FAQs.

When the agent calls `retrieve`, `tool_executor.py`:
1. Calls `rag_core.retrieve(query, db_url, top_k)` to fetch the most relevant document chunks
2. Prints the retrieved chunks to the terminal with their chunk IDs before the agent responds
3. Returns the chunks as a `tool_result` so the agent can cite them inline

The system prompt instructs the agent to cite retrieved chunks inline as `[ch_XXXX]` after each supported sentence, and to explicitly acknowledge when retrieval doesn't contain a clear answer rather than guessing. This eliminates confabulation on policy and documentation questions.

### 10. Query Rewriter (Multi-Query Fan-Out)

Before retrieval, `src/query_rewriter.py` fans a single query out into several diverse reformulations via a single Claude Haiku 4.5 call — paraphrases, decompositions of multi-part questions, and vocabulary/synonym variants — using structured outputs (`output_config.format`) so the response is a guaranteed JSON array of strings, no text parsing required.

This is a standalone step: `rewrite(query) -> list[str]` returns only the rewrites (never the original) and holds no retrieval logic or DB access. `tool_executor.py` wires it into the `retrieve` tool via `rag_core.make_multi_query_retriever(rewrite)`, which prepends the original query, retrieves and reranks each query independently, normalizes each query's scores to `[0, 1]`, and merges by `chunk_id` (keeping the max score and recording every surfacing query as provenance). A chunk that multiple rewrites converge on ranks higher than one only the original query happened to find.

### 11. Tracing
`src/tracer.py` is a lightweight console tracer (not a full observability library) that prints nested, timestamped enter/exit lines for each unit of work in a turn — e.g. the `agent-main` LLM call, a `tool_call` span, and a `rag_call` span nested inside it when `retrieve` fans out to `rag_core`. `agent.py` brackets each user turn with `begin_turn()`/`end_turn()`, and `Agent`, `IntentClassifierAgent`, `QueryRewriter`, and `tool_executor.py` each wrap their own work in `tracer.span(kind, name, **fields)`. It's a module-level singleton with global turn/depth state, intentionally single-threaded (one conversation loop), and complements — rather than replaces — the per-session JSONL cost logs written by `SessionLogger`.

---

## Setup

**1. Create and activate a virtual environment:**
```bash
python3 -m venv agentEnv
source agentEnv/bin/activate
```

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Install `rag_core` as a local editable package:**

`rag_core` is a separate project that provides the vector retrieval backend. Clone it and install it into the same virtual environment:

```bash
git clone https://github.com/saxena1701/AI-Agent-From-Scratch <path/to/rag_core>
pip install -e <path/to/rag_core>
```

Then set the database URL in your `.env`:
```bash
RAG_DB_URL=sqlite:///<path/to/rag_core>/rag.db
```

**5. Add your API key:**
```bash
# Create a .env file in the project root
ANTHROPIC_API_KEY=your_key_here
```

**6. Run the agent:**
```bash
python src/agent.py
```

---

## Models Used

| Component | Model | Reason |
|---|---|---|
| Main agent | `claude-sonnet-4-6` | Strong reasoning and tool-use |
| Intent classifier | `claude-haiku-4-5` | Fast and cheap for classification tasks |
| Query rewriter | `claude-haiku-4-5` | Cheap, fast, high-volume structured task |
