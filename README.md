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

Rather than using a high-level agent framework, every component here is implemented explicitly — the conversation loop, streaming, intent classification, tool dispatch, and cost tracking. The goal is full visibility into what an agent is actually doing at each step.

---

## Project Structure

```
AI-Agent-From-Scratch/
├── src/
│   ├── agent.py              # Main agent class + conversation loop
│   ├── intent.py             # Intent classifier agent (separate Claude client)
│   ├── tools.py              # Tool definitions (Anthropic tool-use schema)
│   ├── tool_executor.py      # Tool dispatcher — routes agent requests to backend
│   ├── backend.py            # MarketSphere SQLite backend (orders, products)
│   ├── pricing.py            # Per-turn cost calculator
│   ├── logger.py             # Session logger (JSON output)
│   └── prompt/
│       ├── system_prompt.md          # Main agent system prompt
│       └── intent_classifier_prompt.md  # Intent classifier system prompt
├── db/
│   └── marketsphere.db       # SQLite mock database
├── logs/                     # Per-session JSONL cost/usage logs
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

The classifier also gates which tools are passed to the main agent — only the subset relevant to the detected intent is included, keeping the token window lean and reducing per-call cost.

### 7. Tools, Tool Dispatcher & Agent-Tool Execution Loop
The agent has access to structured tools defined in `tools.py` using Anthropic's tool-use schema:

| Tool | Purpose |
|---|---|
| `lookup_order` | Fetch order status and details by order ID |
| `lookup_product` | Look up a product by ID or name |
| `search_products` | Keyword search across products |

The main loop checks `response.stop_reason` after each turn:
- **`tool_use`** — the agent wants to call a tool. The tool name and arguments are extracted from the response content, dispatched through `execute_tool()`, and the result is fed back as a `tool_result` message. The loop continues.
- **`end_turn`** — the agent has everything it needs and is ready to respond to the user.

`tool_executor.py` routes each tool call to the appropriate `MarketSphereBackend` method, which queries a local SQLite database (`db/marketsphere.db`).

### 8. Retrieval-Augmented Generation (RAG)
A fourth tool — `retrieve` — extends the agent beyond structured database lookups into unstructured knowledge. It performs a semantic search over a vector knowledge base (via `rag_core`) containing product guides, policies, and FAQs.

When the agent calls `retrieve`, `tool_executor.py`:
1. Calls `rag_core.retrieve(query, db_url, top_k)` to fetch the most relevant document chunks
2. Prints the retrieved chunks to the terminal with their chunk IDs before the agent responds
3. Returns the chunks as a `tool_result` so the agent can cite them inline

The system prompt instructs the agent to cite retrieved chunks inline as `[ch_XXXX]` after each supported sentence, and to explicitly acknowledge when retrieval doesn't contain a clear answer rather than guessing. This eliminates confabulation on policy and documentation questions.

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
