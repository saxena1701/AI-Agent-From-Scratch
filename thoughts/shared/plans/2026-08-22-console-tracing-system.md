# Console Tracing System Implementation Plan

## Overview

Add a small `Tracer` that prints an indented trace tree to the console for every user turn — intent classification, the main model call, each tool call, and (nested inside `retrieve`) the RAG query-rewrite + retrieval detail. Console-only, no new log files, no new dependencies.

## Current State Analysis

Per `thoughts/shared/research/2026-08-22-end-to-end-tracing-system.md`: nothing today links a user query to what happened to answer it. `SessionLogger` (`src/logger.py`) only records `{model, input_tokens, output_tokens, cost, latency}` per model call, instantiated separately by `Agent`, `IntentClassifierAgent`, and `QueryRewriter` with no shared id. The only console output today is streamed assistant text, a standalone intent-classification print, and two bare `print()`s of chunk ids inside `retrieve` (`tool_executor.py:37-45`). There's no way to see, in order, what a turn actually did.

## Desired End State

Running `python src/agent.py` and asking something that triggers intent classification + a `retrieve` tool call prints:

```
=== [TURN a1b2c3] 'What's your return policy on electronics?' ===
  -> [llm_call] intent-classifier
  <- [llm_call] intent-classifier (610ms) {'intent': 'product_question', 'confidence': 0.82, ...}
  -> [llm_call] agent-main
  <- [llm_call] agent-main (1840ms) {'stop_reason': 'tool_use', ...}
  -> [tool_call] retrieve args={'query': 'return policy electronics', 'top_k': 5}
    -> [rag_call] retrieve query='return policy electronics'
      -> [llm_call] query-rewriter
      <- [llm_call] query-rewriter (540ms) {'rewrites': [...]}
    <- [rag_call] retrieve (890ms) [{'chunk_id': 'ch_0231', 'score': 0.91}, ...]
  <- [tool_call] retrieve (912ms) {...}
  -> [llm_call] agent-main
  <- [llm_call] agent-main (2210ms) {'stop_reason': 'end_turn', ...}
=== [TURN a1b2c3] done in 5490ms ===
```

### Key Discoveries
- A single module-level `Tracer` instance (mirroring the existing `_default_rewriter` singleton in `src/query_rewriter.py:70-83`) lets every class call `get_tracer()` with no constructor/threading changes.
- This is a single-threaded synchronous CLI — no concurrency — so plain module-level variables for "current turn" and "current indent depth" work fine; no need for `contextvars` or any locking.
- Nesting is free: wrapping `execute_tool` in `agent.py` and `_multi_query_retrieve` in `tool_executor.py` means the RAG/query-rewriter spans automatically nest inside the tool-call span, since Python's call stack already nests them.

## What We're NOT Doing

- No new log file / JSONL persistence — console output only, matching the stated goal.
- No changes to `SessionLogger`/cost accounting.
- No changes to the external `rag_core` package — only the final merged `{chunk_id, score, queries}` view is traced.
- No exception handling changes — tool-call failures still propagate as they do today (add a `try/except` separately if you want that later; it's a one-line addition around the `execute_tool` call once tracing is in).
- No extended-thinking / reasoning-mode support — not used by this codebase today; out of scope.
- Not fixing the two pre-existing, unrelated tool-schema bugs found during research (`search_products` has no dispatch branch; `get_product_details` has no schema).

## Implementation Approach

One new file, `src/tracer.py`, exposing `get_tracer()` and a `span(kind, name, **fields)` context manager. Wire it into the 4 existing call sites (main Sonnet call, intent classifier, tool dispatch, RAG retrieval + query rewriter) with no change to their business logic.

---

## Phase 1: Tracer Module

**File**: `src/tracer.py` (new)

```python
import time
import uuid

_turn_id = None
_depth = 0


class Tracer:
    def begin_turn(self, query: str) -> None:
        global _turn_id, _depth
        _turn_id = uuid.uuid4().hex[:6]
        _depth = 0
        self._start = time.time()
        print(f"\n=== [TURN {_turn_id}] {query!r} ===")

    def end_turn(self) -> None:
        elapsed_ms = (time.time() - self._start) * 1000
        print(f"=== [TURN {_turn_id}] done in {elapsed_ms:.0f}ms ===\n")

    def span(self, kind: str, name: str, **fields):
        return _Span(kind, name, fields)


class _Span:
    def __init__(self, kind, name, fields):
        self.kind, self.name, self.fields = kind, name, fields
        self.result = None

    def set_result(self, result) -> None:
        self.result = result

    def __enter__(self):
        global _depth
        self.depth = _depth
        _depth += 1
        indent = "  " * self.depth
        extra = " ".join(f"{k}={v!r}" for k, v in self.fields.items())
        print(f"{indent}-> [{self.kind}] {self.name} {extra}".rstrip())
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _depth
        _depth = self.depth
        elapsed_ms = (time.time() - self.start) * 1000
        indent = "  " * self.depth
        if exc_val is not None:
            print(f"{indent}x [{self.kind}] {self.name} FAILED ({elapsed_ms:.0f}ms): {exc_val}")
            return False
        result_str = str(self.result) if self.result is not None else ""
        if len(result_str) > 150:
            result_str = result_str[:150] + "...(truncated)"
        print(f"{indent}<- [{self.kind}] {self.name} ({elapsed_ms:.0f}ms) {result_str}".rstrip())
        return False


_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer
```

### Success Criteria

#### Automated Verification
- [ ] `python -m pytest tests/` passes unchanged

#### Manual Verification
- [ ] `cd src && python -c "from tracer import get_tracer; t=get_tracer(); t.begin_turn('hi'); \nwith t.span('llm_call','x') as s: s.set_result({'ok':True})\nt.end_turn()"` prints a nested, indented tree

---

## Phase 2: Wire Into the Main Agent Loop

**File**: `src/agent.py`
**Changes**: import `get_tracer`; wrap the Sonnet call; wrap turn begin/end in the outer loop; wrap each tool call.

```python
from tracer import get_tracer
```

In `Agent.__init__`: `self.tracer = get_tracer()`.

In `ask_question`, wrap the streaming call:
```python
with self.tracer.span("llm_call", "agent-main") as span:
    with self.client.messages.stream(...) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        print()
        message = stream.get_final_message()
    span.set_result({
        "stop_reason": message.stop_reason,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    })
```

In the outer `while True` loop:
```python
tracer = agent.tracer
...
tracer.begin_turn(user_input)
response = agent.ask_question(user_input, tools=TOOLS)
while response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            with tracer.span("tool_call", block.name, args=block.input) as span:
                result = execute_tool(block.name, block.input, backend)
                span.set_result(result)
            agent.conversation_history.append({...})  # unchanged
    response = agent.ask_question(tools=TOOLS)
tracer.end_turn()
```

### Success Criteria

#### Automated Verification
- [ ] `python -m pytest tests/` passes
- [ ] `python src/agent.py` starts and exits cleanly via `quit`

#### Manual Verification
- [ ] A no-tool question prints a turn banner, one `agent-main` span, and a done-footer
- [ ] A tool-triggering question (e.g. order lookup) shows a nested `tool_call` span between two `agent-main` spans

---

## Phase 3: Wire Into Intent Classification, Query Rewriting, and RAG Retrieval

**File**: `src/intent.py` — wrap the Haiku call in `classify_intent`:
```python
from tracer import get_tracer
# in __init__: self.tracer = get_tracer()

with self.tracer.span("llm_call", "intent-classifier") as span:
    for attempt in range(2):
        ...  # unchanged
    span.set_result([{"intent": r.intent, "confidence": r.confidence, "reasoning": r.reasoning} for r in results])
```

**File**: `src/query_rewriter.py` — wrap the `.create()` call in `rewrite`:
```python
from tracer import get_tracer
# in __init__: self.tracer = get_tracer()

with self.tracer.span("llm_call", "query-rewriter") as span:
    message = self.client.messages.create(...)  # unchanged
    text = next(block.text for block in message.content if block.type == "text")
    rewrites = json.loads(text)["queries"]
    span.set_result({"rewrites": rewrites})
```

**File**: `src/tool_executor.py` — wrap the `_multi_query_retrieve` call:
```python
from tracer import get_tracer
_tracer = get_tracer()

elif name == "retrieve":
    with _tracer.span("rag_call", "retrieve", query=args["query"]) as span:
        raw = _multi_query_retrieve(args["query"], db_url=RAG_DB_URL, top_k=args.get("top_k", 5))
        chunks = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
        span.set_result([
            {"chunk_id": c.get("chunk_id"), "score": round(c.get("score", 0), 3)}
            for c in chunks[:5] if isinstance(c, dict)
        ])
    ...  # unchanged print/return
```

### Success Criteria

#### Automated Verification
- [ ] `python -m pytest tests/` passes

#### Manual Verification
- [ ] A knowledge-base question shows `tool_call retrieve` → `rag_call retrieve` → `llm_call query-rewriter`, correctly nested
- [ ] The `rag_call` span's result shows real `chunk_id`/`score` values
- [ ] A full session (plain question, order lookup, product lookup, KB question) renders a correct tree for each turn with no crashes

---

## Testing Strategy

No new automated tests — this is console output, verified manually per phase above. `python -m pytest tests/` (unaffected, since `MarketSphereBackend` isn't touched) is run after each phase as a regression check.

## Performance Considerations

Each span adds one `time.time()` pair and a `print()` — negligible next to network-bound LLM/DB/RAG calls.

## Migration Notes

Purely additive; no existing behavior changes.

## Recommendations: External Tracing Systems

If this outgrows console debugging (persistent UI, session comparison, team sharing), in order of fit:

1. **Langfuse** — open source, self-hostable, LLM-trace-first UI with prompt/token/cost capture; its span API maps closely onto `Tracer.span()` here.
2. **Arize Phoenix** — similar, but fully local (`pip install arize-phoenix`), no account needed.
3. **Helicone** — a proxy in front of the Anthropic client; near-zero code change, but no custom tool/RAG span granularity.
4. **OpenTelemetry** — vendor-neutral, more setup (collector + backend), worth it only if feeding an existing ops stack (Grafana/Datadog/etc.).

Migration is contained: only `_Span.__enter__`/`__exit__` in `tracer.py` would need to also emit to one of these; call sites in `agent.py`/`intent.py`/`query_rewriter.py`/`tool_executor.py` wouldn't change.

## References

- Research: `thoughts/shared/research/2026-08-22-end-to-end-tracing-system.md`
- `src/agent.py:17-81`, `src/intent.py:56-99`, `src/tool_executor.py:9-50`, `src/query_rewriter.py:47-67`
- `src/query_rewriter.py:70-83` — existing singleton pattern this plan's `get_tracer()` mirrors
