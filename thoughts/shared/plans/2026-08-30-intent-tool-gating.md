# Intent-Based Tool Gating Implementation Plan

## Overview

Wire the existing `IntentClassifierAgent` output into the tool list passed to the main agent, per turn. Today `agent.py` calls `classify_intent()` for logging only (`src/agent.py:33`) and always passes the full `TOOLS` list (`src/agent.py:91,128`) to `Agent.ask_question()`. This plan makes the classified intent(s) actually gate which tool schemas are sent to `claude-sonnet-4-6`, per README §6's stated (but unimplemented) design: "The classifier also gates which tools are passed to the main agent — only the subset relevant to the detected intent is included."

## Current State Analysis

- `IntentClassifierAgent.classify_intent(question)` (`src/intent.py:58-107`) returns `List[IntentClassification]` — the classifier is multi-label (can return >1 intent per message) and always includes `confidence` + `reasoning`. On repeated validation failure it returns a single-element **safe-fallback** list: `intent="general_support", confidence=0.0, reasoning="Safe fallback due to repeated validation failure."` (`src/intent.py:49-56`).
- `Agent.ask_question()` (`src/agent.py:29-63`) calls `classify_intent()` at line 33 but discards the return value — no gating happens.
- The main loop (`src/agent.py:83-134`) always passes the module-level `TOOLS` constant (`src/tools.py:120-124`, 9 tool schemas) to every `ask_question()` call — both the first call of a turn (line 91) and every follow-up call inside the `tool_use` iteration loop (line 128).
- `tests/backend_test.py:62-76` (`test_every_declared_tool_has_handler`) asserts every schema in `TOOLS` has a working `execute_tool` handler — this must keep passing; the gating map must only ever reference tools that already exist in `TOOLS`.

## Desired End State

Each user turn: the classifier's result determines a gated tool subset, computed once, and that same subset is reused for every `ask_question()` call within that turn (including tool-result follow-ups) — not recomputed or reset to the full `TOOLS` list mid-turn.

Verify by: running the agent, asking an order-status-only question, and confirming (via the new `[Tool Gating]` print line and `tracer` output) that only order-related tools were offered to the model that turn; asking an off-topic question and confirming zero tools were offered; asking a mixed order+return question and confirming the union of both subsets was offered.

### Key Discoveries:
- Confirmed with user: gating unions tool subsets across **all** classified intents (not just the top one) — multi-intent messages need the union.
- Confirmed with user: a confidence threshold (0.5) excludes low-confidence intents from contributing tools.
- Confirmed with user: `off_topic` grants zero tools; the safe-fallback classification (0.0 confidence, single `general_support` result) grants the `general_support` subset — which means the fallback case must **bypass** the 0.5 threshold check (0.0 < 0.5), handled as an explicit special case, not just "apply threshold to everything."
- Confirmed with user: mapping/gating logic lives in its own module, not bolted onto `intent.py` or `agent.py` inline.

## What We're NOT Doing

- Not changing the classifier's prompt, schema, or retry/fallback behavior in `src/intent.py`.
- Not adding per-tool gating within a single intent (gating is at the intent→tool-subset granularity, not finer).
- Not persisting or caching gating decisions across turns — every turn reclassifies and regates from scratch.
- Not changing `tool_executor.py` dispatch logic — gating only affects which schemas are *offered* to the model, not how a called tool is executed (a gated-out tool simply can't be called by the model that turn).
- Not adding a config/env override for the confidence threshold — `0.5` is a constant in the new module.

## Implementation Approach

1. Add `src/tool_gating.py`: an `INTENT_TOOL_MAP` (intent → list of tool schema dicts from `src/tools.py`), a `CONFIDENCE_THRESHOLD = 0.5`, and a `gate_tools(classifications: list[IntentClassification]) -> list[dict]` function encoding the union/threshold/fallback/off_topic rules above.
2. Change `Agent` (`src/agent.py`) to compute gated tools once per turn (when `question is not None`, i.e. the first call of a turn) and store them on `self.current_tools`, reusing that for all calls within the turn. Drop the external `tools=` parameter from call sites — `Agent` owns tool selection internally now.
3. Update the main loop's two `ask_question()` call sites to stop passing `tools=TOOLS` explicitly.
4. Add a `[Tool Gating]` print (parallel to the existing `[Intent Classification]` print in `intent.py`) so the gated set is visible per turn, consistent with how classification results are already surfaced.
5. Add unit tests for `gate_tools()` covering: single intent, multi-intent union, below-threshold exclusion, off_topic → empty, safe-fallback → general_support subset, all-below-threshold-with-no-off_topic → general_support safety net.

### Full intent → tool mapping

| Intent | Tools granted |
|---|---|
| `order_status` | `lookup_order`, `list_customer_orders` |
| `product_question` | `lookup_product`, `search_products`, `retrieve` |
| `return_request` | `lookup_order`, `list_customer_orders`, `check_return_eligibility`, `initiate_return`, `cancel_order` |
| `general_support` | `get_user_details`, `lookup_order`, `list_customer_orders`, `retrieve` |
| `off_topic` | *(none)* |

Rationale: `order_status`/`return_request` both need `lookup_order`/`list_customer_orders` since a customer typically doesn't have the order ID memorized. `cancel_order` is grouped under `return_request` (not `order_status`) since cancellation is a return-adjacent action, not a status check. `general_support` gets a broad-but-not-full set (account info + order lookups + KB retrieval) since it's the catch-all bucket. `off_topic` gets nothing — the model should decline/redirect, not fetch backend data for an unrelated request.

### Gating algorithm (`gate_tools`)

```
1. If classifications == the safe-fallback sentinel
   (len == 1, intent == "general_support", confidence == 0.0,
    reasoning == "Safe fallback due to repeated validation failure."):
     return INTENT_TOOL_MAP["general_support"]

2. kept = [c for c in classifications if c.confidence >= CONFIDENCE_THRESHOLD]

3. gated = union of INTENT_TOOL_MAP[c.intent] for c in kept, de-duplicated by tool name,
   order-stable (first-seen wins)

4. If gated is empty AND "off_topic" not in {c.intent for c in classifications}:
     # every intent was under-confident; don't strand the agent with zero tools
     return INTENT_TOOL_MAP["general_support"]

5. Return gated  (this correctly returns [] when off_topic was confidently classified,
   whether alone or alongside other intents that all fell below threshold)
```

## Phase 1: Add the gating module

### Overview
Introduce `src/tool_gating.py` with the mapping table and `gate_tools()` function, independently testable without touching `agent.py`.

### Changes Required:

#### 1. New file: gating module
**File**: `src/tool_gating.py`
**Changes**: New file.

```python
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
```

### Success Criteria:

#### Automated Verification:
- [x] Module imports cleanly: `cd src && python -c "import tool_gating"`
- [x] Existing tests still pass: `python -m pytest tests/`

#### Manual Verification:
- N/A (pure function, covered by automated tests in Phase 3)

---

## Phase 2: Wire gating into the agent loop

### Overview
Make `Agent` compute and reuse gated tools per turn instead of always sending the full `TOOLS` list.

### Changes Required:

#### 1. `Agent.ask_question` — compute and reuse gated tools
**File**: `src/agent.py`
**Changes**: Import `gate_tools`; drop the `tools` parameter; add `self.current_tools`, initialized to full `TOOLS` in `__init__` (so a turn can't start with zero tools before any classification has run); recompute it from the classifier result whenever `question is not None`; use `self.current_tools` in the `messages.stream` call; print the gated set for visibility.

```python
from tool_gating import gate_tools
from tools import TOOLS
...
class Agent:
    def __init__(self):
        ...
        self.current_tools = TOOLS  # default until first classification of a turn

    def ask_question(self, question=None):
        start_time = time.time()
        if question is not None:
            self.conversation_history.append({"role": "user", "content": question})
            classifications = self.classifier.classify_intent(question)
            self.current_tools = gate_tools(classifications)
            print(f"[Tool Gating] tools enabled this turn: "
                  f"{[t['name'] for t in self.current_tools] or '(none)'}")

        with self.tracer.span("llm_call", "agent-main") as span:
            with self.client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                system=self.system_prompt,
                messages=self.conversation_history,
                tools=self.current_tools,
            ) as stream:
                ...
```

#### 2. Main loop — stop passing `tools=TOOLS` explicitly
**File**: `src/agent.py`
**Changes**: Both call sites now rely on `Agent` internally tracking `self.current_tools`.

```python
response = agent.ask_question(user_input)   # was: agent.ask_question(user_input, tools=TOOLS)
...
response = agent.ask_question()             # was: agent.ask_question(tools=TOOLS)
```

Note: the follow-up call inside the `tool_use` loop (previously line 128) must **not** re-widen to the full `TOOLS` list — it now correctly reuses `self.current_tools` from the turn's initial classification, since `question` stays `None` on that call.

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/` passes
- [x] No circular import between `agent.py`, `tool_gating.py`, `intent.py`, `tools.py` (verified via `tests/tool_gating_test.py` importing `src.intent`/`src.tool_gating`, and `tool_gating.py` importing `intent`/`tools` cleanly; `agent.py`'s own module-level code requires being run from repo root with a live API key/stdin, same as before this change, so `from agent import Agent` alone can't be exercised standalone — this is pre-existing, not a regression)

#### Manual Verification:
- [x] Run `python src/agent.py`, log in, ask an order-status-only question (e.g. "where is my order ORD-000123") — `[Tool Gating]` line shows only `lookup_order`/`list_customer_orders`, and the agent can still successfully complete the lookup via the tool loop.
- [x] Ask an off-topic question (e.g. "what's the weather today") — `[Tool Gating]` line shows `(none)`, agent responds without attempting a tool call.
- [x] Ask a mixed question touching both order status and a return (e.g. "where's my order ORD-000123 and can I return it") — `[Tool Gating]` line shows the union of both subsets, and the tool-use follow-up call in the same turn keeps using that same gated set (not the full `TOOLS` list).
- [x] Ask a vague/general question likely to yield low-confidence classifications across the board — confirm the general_support safety-net subset is granted rather than an empty list.

---

## Phase 3: Tests for the gating function

### Overview
Unit-test `gate_tools()` directly against `IntentClassification` fixtures — no API calls needed, pure function.

### Changes Required:

#### 1. New test module
**File**: `tests/tool_gating_test.py`
**Changes**: New file, following the existing `tests/backend_test.py` style (plain `pytest`, `from src.X import Y` per the module's documented `-m pytest` convention).

```python
from src.intent import IntentClassification
from src.tool_gating import gate_tools, INTENT_TOOL_MAP


def _names(tools):
    return {t["name"] for t in tools}


def test_single_intent_above_threshold():
    result = gate_tools([IntentClassification(intent="order_status", confidence=0.9, reasoning="r")])
    assert _names(result) == _names(INTENT_TOOL_MAP["order_status"])


def test_multi_intent_union():
    result = gate_tools([
        IntentClassification(intent="order_status", confidence=0.9, reasoning="r"),
        IntentClassification(intent="return_request", confidence=0.8, reasoning="r"),
    ])
    expected = _names(INTENT_TOOL_MAP["order_status"]) | _names(INTENT_TOOL_MAP["return_request"])
    assert _names(result) == expected


def test_below_threshold_excluded():
    result = gate_tools([
        IntentClassification(intent="order_status", confidence=0.9, reasoning="r"),
        IntentClassification(intent="product_question", confidence=0.2, reasoning="r"),
    ])
    assert _names(result) == _names(INTENT_TOOL_MAP["order_status"])


def test_off_topic_alone_yields_no_tools():
    result = gate_tools([IntentClassification(intent="off_topic", confidence=0.95, reasoning="r")])
    assert result == []


def test_safe_fallback_yields_general_support():
    result = gate_tools([
        IntentClassification(
            intent="general_support", confidence=0.0,
            reasoning="Safe fallback due to repeated validation failure.",
        )
    ])
    assert _names(result) == _names(INTENT_TOOL_MAP["general_support"])


def test_all_below_threshold_no_off_topic_falls_back_to_general_support():
    result = gate_tools([
        IntentClassification(intent="order_status", confidence=0.3, reasoning="r"),
        IntentClassification(intent="product_question", confidence=0.1, reasoning="r"),
    ])
    assert _names(result) == _names(INTENT_TOOL_MAP["general_support"])


def test_off_topic_plus_low_confidence_others_still_yields_no_tools():
    result = gate_tools([
        IntentClassification(intent="off_topic", confidence=0.95, reasoning="r"),
        IntentClassification(intent="order_status", confidence=0.2, reasoning="r"),
    ])
    assert result == []
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/tool_gating_test.py -v` — all new tests pass
- [x] `python -m pytest tests/` — full suite passes, including pre-existing `test_every_declared_tool_has_handler` (guards that every tool referenced in `INTENT_TOOL_MAP` still has a live handler in `tool_executor.py`, since the map only pulls from `tools.py`'s existing constants)

#### Manual Verification:
- N/A — fully covered by automated tests.

---

## Testing Strategy

### Unit Tests:
- `tests/tool_gating_test.py` (Phase 3) covers the gating function's branches directly, no network calls.

### Integration Tests:
- None added — the existing `tests/backend_test.py::test_every_declared_tool_has_handler` already provides the integration guarantee that gating never references a dangling tool name.

### Manual Testing Steps:
1. Order-status-only question → confirm narrow tool set + successful lookup.
2. Off-topic question → confirm zero tools offered.
3. Mixed-intent question → confirm union of subsets, consistently reused across the turn's tool-use follow-up calls.
4. Low-confidence/vague question → confirm general_support safety-net fallback, not an empty list.

## Performance Considerations

Gating reduces the token footprint of the `tools` parameter sent to `claude-sonnet-4-6` on turns where fewer than all 9 tools are relevant — this is the whole point (README §6: "keeping the token window lean and reducing per-call cost"). No new API calls are introduced; `classify_intent()` was already being called every turn, just previously ignored.

## Migration Notes

No data migration. Purely a behavior change in which tool schemas are sent per turn; `logs/` JSONL format is unaffected (gating doesn't touch `SessionLogger`).

## References

- README §6 (Intent Classification) — states the intended-but-unimplemented gating behavior this plan implements.
- `src/intent.py:58-107` — `classify_intent()`, the source of per-turn classifications.
- `src/agent.py:29-63,83-134` — call sites being modified.
- `src/tools.py:120-124` — `TOOLS`, the full schema list gating subsets from.
- `tests/backend_test.py:62-76` — existing invariant (`test_every_declared_tool_has_handler`) that gating must not violate.
</content>
