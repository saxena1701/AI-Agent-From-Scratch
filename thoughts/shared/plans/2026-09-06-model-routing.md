# Intent-Based Model Routing Implementation Plan

> **Amended 2026-10-11:** routing cadence changed from session-pinned to per-turn
> with an upward ratchet. See `2026-10-11-per-turn-model-routing-ratchet.md`.
> Anything below describing first-turn-only routing, `model_locked`, or
> "session-pinned" is superseded and kept as the record of what was decided then.

## Overview

Today the main agent is hardcoded to `claude-sonnet-4-6` (`src/agent.py:42,61,63`) regardless of what the turn actually requires — a "where's my order" lookup and a return-cancellation both pay Sonnet rates. This plan adds `src/model_router.py`, a pure function that maps the intent classification the agent *already computes every turn* onto a model tier, exactly mirroring how `src/tool_gating.py` maps that same classification onto a tool subset.

Routing is **static** (a function of the classification, no runtime escalation) and **pinned per session** (the first user turn's classification decides the model once, and every subsequent turn — and every tool-loop iteration within every turn — reuses that decision, even if a later turn's intent would route differently on its own). Alongside it, the cost logger gains a counterfactual: what each turn *would* have cost on the old fixed `claude-sonnet-4-6` baseline, so routing's effect is measurable rather than asserted.

## Current State Analysis

**The three LLM call sites, and which one is routable:**

| Call site | Work | Route it? |
|---|---|---|
| `src/intent.py:74` — classifier | Picks 1 of 5 labels, returns JSON | **No.** Routing the router is circular — you'd have to classify in order to decide how to classify. Stays fixed Haiku. |
| `src/query_rewriter.py:53` — rewriter | Emits N paraphrases under a JSON schema | **No.** Mechanical, same difficulty every call. Stays fixed Haiku. |
| `src/agent.py:42` — main agent | Reasons, dispatches tools, synthesizes the answer | **Yes.** The only call whose difficulty varies per turn, and the expensive one. |

**Facts that shape the design:**

- `Agent.ask_question()` is re-entered with `question=None` inside the tool loop (`src/agent.py:133`). Classification happens **once** per user turn (`src/agent.py:33-38`, guarded by `if question is not None`), but the `model=` argument is supplied on **every** API call. The chosen model therefore has to live on `self`, or a turn's later tool iterations would silently fall back to whatever the default is.
- Tool gating stays per-turn — `gate_tools()` re-runs on every user turn's classification, since the tool subset offered legitimately should track what the current turn is about. Model routing is the one thing pinned for the whole session: it consumes the **first** turn's classification only and is never recomputed after that, so `self.current_model` needs a one-shot guard (e.g. a `model_locked` flag) distinct from the tools/reasoning state that's rebuilt every turn.
- `src/pricing.py:13-16` — `calculate_cost` does `PRICING.get(model)` and then `prices["input"]`. An unpriced model raises `TypeError: 'NoneType' object is not subscriptable`, **after** the API call has already been billed. `tests/backend_test.py:100-107` currently pins that `TypeError` as documented behavior.
- `src/tool_gating.py:35-53` is the structural precedent to mirror: pure function, intent→resource map, `CONFIDENCE_THRESHOLD = 0.5`, safe-fallback detection, union across multi-intent. `tests/tool_gating_test.py` covers all 7 branches with zero API calls.
- `gate_tools` contains non-obvious fallback logic (safe-fallback → `general_support`; all-below-threshold → `general_support` *unless* `off_topic` is present → no tools). A second function re-deriving that from scratch would drift, producing turns where the agent gets `return_request` tools but an `order_status` model.
- `src/logger.py:10` — `log_turn(model, input_tokens, output_tokens, cost, latency)` is called positionally by `intent.py:101` and `query_rewriter.py:69`. New fields must be optional keyword arguments so those two files stay untouched.
- `README.md:78` lists Haiku 4.5 at **$2 / $10 per MTok**. The real rate is **$1 / $5**, and `src/pricing.py:7-8` already has it right — the README is stale (previously flagged in `thoughts/shared/reviews/2026-09-03-main-branch-full-review-v4.md:138`).
- `claude-sonnet-5` is **$2 / $10** against `claude-sonnet-4-6`'s **$3 / $15** — the current default is both older and more expensive than its successor.

## Desired End State

`src/model_router.py` exists and owns the main agent's model selection. The **first** user turn prints its routing decision once; every turn after that — and every tool-loop iteration within every turn — runs on that same session-pinned model. Each turn logs both its real cost and its estimated cost under the old fixed-Sonnet baseline. Exiting the session prints a routing summary.

**Verification:** `python -m pytest tests/` passes, including a new `tests/model_router_test.py`; `tests/tool_gating_test.py` passes **unchanged** (proving the Phase 2 refactor is behavior-preserving); and a live session shows a single `[Model Routing]` line (only on turn one) whose model matches the `model` field of every corresponding `logs/session_*.jsonl` entry for that session, including turns whose own intent would have routed differently.

### Key Discoveries:
- `resolve_intents()` extracted from `gate_tools` (Phase 2) is the mechanism that makes tool gating and model routing structurally incapable of disagreeing about *which intent drove a turn* — both consume one resolved intent list. Routing only ever consumes it once, on turn one; gating consumes it every turn.
- Registry validation at **import** time (Phase 3) converts "unpriced model" from a mid-turn `TypeError` after billing into a startup `RuntimeError`.
- The first turn's intent, if multi-intent, takes the **max** tier, mirroring how `gate_tools` takes the **union** of tools on every turn — both err toward capability. Only the first turn's tier calculation matters for routing; later turns' intents don't affect the model.

## What We're NOT Doing

- **No runtime escalation.** No mid-turn model switching on tool-loop depth, tool errors, or low confidence. The model is a pure function of the **first** turn's classification and is pinned for the whole session.
- **No re-routing on later turns.** Even if turn three's intent would route to a different (cheaper or more expensive) tier than turn one's, the session stays on the model chosen at turn one. Tool gating is unaffected by this — it still re-derives the tool subset every turn from that turn's own classification.
- **No routing of the classifier or the rewriter.** Both stay on hardcoded `claude-haiku-4-5`; `src/intent.py` and `src/query_rewriter.py` are not modified by this plan.
- **No shared model registry across the project.** `model_router.py` owns only the main agent's model IDs plus the pricing baseline. The `"claude-haiku-4-5"` literals in `intent.py` and `query_rewriter.py` stay where they are.
- **No prompt caching, no thinking/effort configuration, no `max_tokens` changes per tier.** Routing changes which model runs, nothing else about the request.
- **No user-facing model override** (no `/model` command, no env var pin).
- **No A/B harness or eval set.** The counterfactual in Phase 5 is an arithmetic estimate, not a measured comparison.

## Implementation Approach

Six phases, each independently verifiable. Phases 1–3 are pure and fully unit-testable with no API calls; Phase 4 is the single wiring change; Phases 5–6 are observability and docs.

The ordering matters: pricing rows must exist (Phase 1) before the router's import-time validation can pass (Phase 3), and `resolve_intents` must exist (Phase 2) before the router can consume it.

**Tier assignment rationale** — two properties decide the tier: does the turn require synthesis, and what does a wrong answer cost. Since routing is session-pinned, this table effectively answers "what should the *session's opening* turn cost" — a session that starts with `order_status` and later drifts into a return request stays on Haiku for that return request too (see Phase 4).

| Intent | Tools it receives | Model | Why |
|---|---|---|---|
| `off_topic` | none | `claude-haiku-4-5` | Decline politely. No tools, no reasoning. |
| `order_status` | `lookup_order`, `list_customer_orders` | `claude-haiku-4-5` | Extract an ID → one call → format a row. Errors are visible and self-correcting. |
| `product_question` | `lookup_product`, `search_products`, `retrieve` | `claude-sonnet-5` | Multi-query RAG fan-out, synthesis across merged chunks, `[ch_XXXX]` citation discipline that `src/prompt/system_prompt.md` explicitly guards against fabricating. |
| `general_support` | `get_user_details`, `lookup_order`, `list_customer_orders`, `retrieve` | `claude-sonnet-5` | Open-ended, and the fallback bucket for everything the classifier couldn't place — highest ambiguity. |
| `return_request` | + `cancel_order`, `initiate_return`, `check_return_eligibility` | `claude-opus-5` | Policy/eligibility reasoning, and the only intent whose tools **mutate the database** irreversibly. Highest cost of error in the system. |

---

## Phase 1: Price the Routable Models

### Overview
Add `claude-sonnet-5` and `claude-opus-5` rates, and make an unpriced model fail with a message that names the problem instead of a `NoneType` subscript error. `claude-sonnet-4-6` stays in the table — it's the counterfactual baseline in Phase 5.

### Changes Required:

#### 1. Rate table and error handling
**File**: `src/pricing.py`
**Changes**: Add two rows; raise `ValueError` on an unknown model.

```python
PRICING = {
    "claude-opus-5": {
        "input": 0.000005,      # $5 per 1M input tokens
        "output": 0.000025,     # $25 per 1M output tokens
    },
    "claude-sonnet-5": {
        "input": 0.000002,      # $2 per 1M input tokens
        "output": 0.000010,     # $10 per 1M output tokens
    },
    "claude-sonnet-4-6": {
        "input": 0.000003,      # $3 per 1M input tokens
        "output": 0.000015,     # $15 per 1M output tokens
    },
    "claude-haiku-4-5": {
        "input": 0.000001,      # $1 per 1M input tokens
        "output": 0.000005,     # $5 per 1M output tokens
    }
}

def calculate_cost(model, input_tokens, output_tokens):
    prices = PRICING.get(model)
    if prices is None:
        raise ValueError(
            f"No pricing entry for model {model!r}. Known models: {sorted(PRICING)}"
        )
    input_cost = input_tokens * prices["input"]
    output_cost = output_tokens * prices["output"]
    return input_cost + output_cost
```

#### 2. Update the test that pinned the old failure mode
**File**: `tests/backend_test.py`
**Changes**: `test_unknown_model_pricing` (lines 100-107) currently asserts `TypeError` and its comment says the clearer-error suggestion "is not fixed here". It is fixed here.

```python
def test_unknown_model_pricing():
    # Input: a model name absent from the rate table
    # Expected: ValueError naming the offending model — routing can introduce new
    #   model IDs, and a NoneType subscript error gives no clue which one is missing.
    from pricing import calculate_cost

    with pytest.raises(ValueError, match="claude-nonexistent-model"):
        calculate_cost("claude-nonexistent-model", 1_000_000, 1_000_000)
```

#### 3. Rates for the two new tiers
**File**: `tests/backend_test.py`
**Changes**: Add alongside the existing `test_haiku_pricing` / `test_sonnet_pricing`.

```python
def test_sonnet_5_pricing():
    # Input: 1,000,000 input / 1,000,000 output tokens at the Sonnet 5 rate
    # Expected: $2.00 input-only, $10.00 output-only — cheaper than sonnet-4-6's $3/$15
    from pricing import calculate_cost

    assert calculate_cost("claude-sonnet-5", 1_000_000, 0) == pytest.approx(2.0)
    assert calculate_cost("claude-sonnet-5", 0, 1_000_000) == pytest.approx(10.0)


def test_opus_5_pricing():
    # Input: 1,000,000 input / 1,000,000 output tokens at the Opus 5 rate
    # Expected: $5.00 input-only, $25.00 output-only
    from pricing import calculate_cost

    assert calculate_cost("claude-opus-5", 1_000_000, 0) == pytest.approx(5.0)
    assert calculate_cost("claude-opus-5", 0, 1_000_000) == pytest.approx(25.0)
```

### Success Criteria:

#### Automated Verification:
- [x] Pricing tests pass: `python -m pytest tests/backend_test.py -k pricing -v`
- [x] Full suite still passes: `python -m pytest tests/`
- [x] All four models priced: `python -c "import sys;sys.path.insert(0,'src');from pricing import PRICING;assert set(PRICING)=={'claude-opus-5','claude-sonnet-5','claude-sonnet-4-6','claude-haiku-4-5'};print('ok')"`

#### Manual Verification:
- [ ] Rates match the current published Anthropic table (Haiku 4.5 $1/$5, Sonnet 5 $2/$10, Sonnet 4.6 $3/$15, Opus 5 $5/$25).

---

## Phase 2: Extract `resolve_intents()` — One Source of Truth for the Turn's Intent

### Overview
`gate_tools` currently fuses two concerns: *which intents actually drive this turn* (threshold filtering, safe-fallback detection, the `off_topic` special case) and *which tools those intents map to*. Model routing needs the first half. Extracting it means tool gating and model routing consume the same resolved list and cannot disagree.

This refactor is **behavior-preserving by design**: `tests/tool_gating_test.py` must pass with **zero edits**, which is the proof.

### Changes Required:

#### 1. Extract the intent-resolution half
**File**: `src/tool_gating.py`
**Changes**: Add `resolve_intents()`; reduce `gate_tools()` to a map-and-union over its result. `CONFIDENCE_THRESHOLD`, `INTENT_TOOL_MAP`, `_FALLBACK_REASONING`, and `_is_safe_fallback` are unchanged.

```python
def resolve_intents(classifications: list[IntentClassification]) -> list[str]:
    """Reduce raw classifier output to the intent list that actually drives this turn.

    Single source of truth for confidence filtering and fallback selection, so
    tool gating and model routing can never disagree about what the turn is.
    Always returns at least one intent.
    """
    if _is_safe_fallback(classifications):
        return ["general_support"]

    resolved: list[str] = []
    seen: set[str] = set()
    for c in classifications:
        if c.confidence >= CONFIDENCE_THRESHOLD and c.intent not in seen:
            resolved.append(c.intent)
            seen.add(c.intent)

    if resolved:
        return resolved
    # Nothing cleared the bar. An off_topic signal at any confidence still means
    # "offer nothing"; otherwise fall back to the general_support bucket.
    if "off_topic" in {c.intent for c in classifications}:
        return ["off_topic"]
    return ["general_support"]


def gate_tools(classifications: list[IntentClassification]) -> list[dict]:
    """Return the tool schema subset the main agent should be offered this turn."""
    gated: list[dict] = []
    seen_names: set[str] = set()
    for intent in resolve_intents(classifications):
        for tool in INTENT_TOOL_MAP.get(intent, []):
            if tool["name"] not in seen_names:
                gated.append(tool)
                seen_names.add(tool["name"])
    return gated
```

Equivalence check against every branch the existing tests cover:

| Input | Old `gate_tools` | `resolve_intents` → union | Match |
|---|---|---|---|
| single intent ≥ 0.5 | that intent's tools | `[intent]` → same | ✓ |
| two intents ≥ 0.5 | union | `[a, b]` → union | ✓ |
| one ≥ 0.5, one < 0.5 | kept intent's tools | `[kept]` → same | ✓ |
| `off_topic` alone ≥ 0.5 | `[]` (gated empty, but `off_topic` present → no fallback) | `["off_topic"]` → `[]` | ✓ |
| safe fallback | `general_support` set | `["general_support"]` → same | ✓ |
| all < 0.5, no `off_topic` | `general_support` set | `["general_support"]` → same | ✓ |
| `off_topic` ≥ 0.5 + other < 0.5 | `[]` | `["off_topic"]` → `[]` | ✓ |

#### 2. Direct tests for the extracted function
**File**: `tests/tool_gating_test.py`
**Changes**: Append; do not modify any existing test.

```python
from src.tool_gating import resolve_intents


def test_resolve_intents_filters_by_confidence():
    assert resolve_intents([
        IntentClassification(intent="order_status", confidence=0.9, reasoning="r"),
        IntentClassification(intent="product_question", confidence=0.2, reasoning="r"),
    ]) == ["order_status"]


def test_resolve_intents_dedupes_repeats():
    assert resolve_intents([
        IntentClassification(intent="order_status", confidence=0.9, reasoning="r"),
        IntentClassification(intent="order_status", confidence=0.7, reasoning="r"),
    ]) == ["order_status"]


def test_resolve_intents_safe_fallback():
    assert resolve_intents([
        IntentClassification(
            intent="general_support", confidence=0.0,
            reasoning="Safe fallback due to repeated validation failure.",
        )
    ]) == ["general_support"]


def test_resolve_intents_all_below_threshold_falls_back():
    assert resolve_intents([
        IntentClassification(intent="order_status", confidence=0.3, reasoning="r"),
    ]) == ["general_support"]


def test_resolve_intents_off_topic_wins_when_nothing_clears_threshold():
    assert resolve_intents([
        IntentClassification(intent="off_topic", confidence=0.4, reasoning="r"),
        IntentClassification(intent="order_status", confidence=0.3, reasoning="r"),
    ]) == ["off_topic"]


def test_resolve_intents_never_returns_empty():
    assert resolve_intents([]) == ["general_support"]
```

### Success Criteria:

#### Automated Verification:
- [x] **The seven pre-existing gating tests pass with no edits**: `python -m pytest tests/tool_gating_test.py -v`
- [x] New `resolve_intents` tests pass: `python -m pytest tests/tool_gating_test.py -k resolve_intents -v`
- [x] Full suite passes: `python -m pytest tests/`
- [x] `git diff tests/tool_gating_test.py` shows only additions, no modified lines.

#### Manual Verification:
- [ ] A live session's `[Tool Gating] tools enabled this turn:` line is identical to pre-refactor behavior for an order query, a product question, and a return request.

**Implementation Note**: After this phase's automated verification passes, pause for manual confirmation before proceeding.

---

## Phase 3: `src/model_router.py`

### Overview
The routing decision itself: a pure function from classifications to `(model_id, reason)`, with import-time validation so a misconfigured registry fails at startup rather than mid-turn.

### Changes Required:

#### 1. The router
**File**: `src/model_router.py` (new)

```python
"""Static, intent-driven model selection for the main agent.

Sibling of tool_gating.py: both consume the same resolved intent list, one to
pick tools, one to pick the model. Routing is a pure function of a
classification — there is no runtime escalation, and the caller (Agent) invokes
it exactly once per session, on the first turn's classification, then pins the
result on self for every later turn and every tool-loop iteration within every
turn. This module has no notion of "session" itself; it just routes whatever
classification it's given. The one-shot behavior lives in the caller.

Only the main agent is routed. The intent classifier and the query rewriter are
fixed on Haiku and own their own model IDs.
"""

from intent import IntentClassification
from pricing import PRICING
from tool_gating import resolve_intents

# Cheapest -> most capable. A model's index here is its tier ordinal, used to
# resolve multi-intent turns (take the max, mirroring how gate_tools takes the
# union of tools — both err toward capability).
MODEL_TIERS = [
    "claude-haiku-4-5",   # $1 / $5   per MTok
    "claude-sonnet-5",    # $2 / $10  per MTok
    "claude-opus-5",      # $5 / $25  per MTok
]

# Tier per intent. Two properties decide it: does the turn require synthesis,
# and what does a wrong answer cost.
INTENT_MODEL_MAP = {
    # No tools, no reasoning — just decline politely.
    "off_topic": "claude-haiku-4-5",
    # Extract an ID, call one tool, format a row. Errors are self-correcting.
    "order_status": "claude-haiku-4-5",
    # Multi-query RAG fan-out, synthesis over merged chunks, citation discipline.
    "product_question": "claude-sonnet-5",
    # Open-ended, and the fallback bucket for anything unclassifiable.
    "general_support": "claude-sonnet-5",
    # Policy reasoning, and the only intent whose tools mutate the DB (cancel_order,
    # initiate_return) irreversibly. Highest cost of error in the system.
    "return_request": "claude-opus-5",
}

# Used when the intent map has no entry (defensive; resolve_intents only ever
# returns the five known intents).
DEFAULT_MODEL = "claude-sonnet-5"

# What the agent ran on before routing existed. Every routed turn is also priced
# at this model's rates so the session can report routing's effect.
BASELINE_MODEL = "claude-sonnet-4-6"


def _validate_registry() -> None:
    """Fail at import, not mid-turn.

    An unpriced routable model would otherwise blow up in calculate_cost()
    *after* the API call had already been billed.
    """
    unpriced = sorted({*MODEL_TIERS, BASELINE_MODEL, DEFAULT_MODEL} - set(PRICING))
    if unpriced:
        raise RuntimeError(f"Models missing from pricing.PRICING: {unpriced}")

    untiered = sorted(
        {*INTENT_MODEL_MAP.values(), DEFAULT_MODEL} - set(MODEL_TIERS)
    )
    if untiered:
        raise RuntimeError(f"Models not listed in MODEL_TIERS: {untiered}")


_validate_registry()


def route_model(classifications: list[IntentClassification]) -> tuple[str, str]:
    """Pick the model for this turn.

    Returns (model_id, reason). Multi-intent turns take the most capable tier
    across the resolved intents.
    """
    intents = resolve_intents(classifications)

    chosen = DEFAULT_MODEL
    driver = "default"
    best_rank = -1
    for intent in intents:
        model = INTENT_MODEL_MAP.get(intent, DEFAULT_MODEL)
        rank = MODEL_TIERS.index(model)
        if rank > best_rank:
            best_rank, chosen, driver = rank, model, intent

    reason = driver if len(intents) == 1 else f"{driver} (max of {','.join(intents)})"
    return chosen, reason
```

#### 2. Router tests
**File**: `tests/model_router_test.py` (new) — mirrors the shape of `tests/tool_gating_test.py`.

```python
import pytest

from src.intent import IntentClassification
from src.model_router import (
    BASELINE_MODEL, DEFAULT_MODEL, INTENT_MODEL_MAP, MODEL_TIERS, route_model,
)
from src.pricing import PRICING
from src.tool_gating import INTENT_TOOL_MAP, gate_tools


def _c(intent, confidence=0.9):
    return IntentClassification(intent=intent, confidence=confidence, reasoning="r")


@pytest.mark.parametrize("intent,expected", [
    ("off_topic", "claude-haiku-4-5"),
    ("order_status", "claude-haiku-4-5"),
    ("product_question", "claude-sonnet-5"),
    ("general_support", "claude-sonnet-5"),
    ("return_request", "claude-opus-5"),
])
def test_each_intent_routes_to_its_tier(intent, expected):
    model, _ = route_model([_c(intent)])
    assert model == expected


def test_multi_intent_takes_the_most_capable_tier():
    # Input: a cheap intent and an expensive one, both above threshold
    # Expected: opus — mirrors gate_tools taking the union, erring toward capability
    model, reason = route_model([_c("order_status"), _c("return_request")])
    assert model == "claude-opus-5"
    assert "max of" in reason


def test_low_confidence_intent_does_not_escalate_the_tier():
    # Input: order_status at 0.9, return_request at 0.2 (below CONFIDENCE_THRESHOLD)
    # Expected: haiku — the sub-threshold expensive intent is filtered out first
    model, _ = route_model([_c("order_status"), _c("return_request", 0.2)])
    assert model == "claude-haiku-4-5"


def test_safe_fallback_routes_to_general_support_tier():
    # Input: the classifier's safe fallback (validation failed twice)
    # Expected: sonnet — same bucket gate_tools falls back to
    model, _ = route_model([
        IntentClassification(
            intent="general_support", confidence=0.0,
            reasoning="Safe fallback due to repeated validation failure.",
        )
    ])
    assert model == INTENT_MODEL_MAP["general_support"]


def test_all_below_threshold_routes_to_general_support_tier():
    model, _ = route_model([_c("order_status", 0.3), _c("product_question", 0.1)])
    assert model == INTENT_MODEL_MAP["general_support"]


def test_off_topic_with_low_confidence_others_stays_cheap():
    model, _ = route_model([_c("off_topic", 0.95), _c("return_request", 0.2)])
    assert model == "claude-haiku-4-5"


# --- registry invariants ---

def test_tiers_are_ordered_cheapest_first():
    # Guards against a MODEL_TIERS list that lies about cost order, which would
    # silently make "max tier" mean "cheapest model".
    output_rates = [PRICING[m]["output"] for m in MODEL_TIERS]
    assert output_rates == sorted(output_rates)


def test_every_routable_model_is_priced():
    for model in {*MODEL_TIERS, BASELINE_MODEL, DEFAULT_MODEL}:
        assert model in PRICING


def test_every_intent_has_a_tier():
    assert set(INTENT_MODEL_MAP) == set(INTENT_TOOL_MAP)


# --- the invariant that keeps routing and gating in step ---

@pytest.mark.parametrize("classifications", [
    [_c("order_status")],
    [_c("return_request")],
    [_c("off_topic")],
    [_c("order_status"), _c("product_question")],
    [_c("order_status", 0.3)],
    [_c("off_topic"), _c("return_request", 0.2)],
])
def test_routing_and_gating_agree_on_the_turn(classifications):
    # A turn offered the return tools must not be routed as an order lookup.
    # Both derive from resolve_intents, so this asserts they stay coupled.
    model, _ = route_model(classifications)
    tool_names = {t["name"] for t in gate_tools(classifications)}
    mutating = {"cancel_order", "initiate_return"}
    if tool_names & mutating:
        assert model == INTENT_MODEL_MAP["return_request"]
```

### Success Criteria:

#### Automated Verification:
- [x] Router tests pass: `python -m pytest tests/model_router_test.py -v`
- [x] Full suite passes: `python -m pytest tests/`
- [x] Import-time validation actually fires: `python -c "import sys;sys.path.insert(0,'src');import model_router as m;m.MODEL_TIERS.append('claude-not-real');
try: m._validate_registry(); raise AssertionError('should have raised')
except RuntimeError as e: print('ok:', e)"`
- [x] Module imports standalone under the repo's bare-import convention: `python -c "import sys;sys.path.insert(0,'src');from model_router import route_model;print('ok')"`

#### Manual Verification:
- [ ] The tier map in `INTENT_MODEL_MAP` reads as defensible against the rationale table above.

**Implementation Note**: After this phase's automated verification passes, pause for manual confirmation before proceeding.

---

## Phase 4: Wire the Router into `Agent`

### Overview
Select the model once per **session** — on the first user turn only — store it on `self`, and use it for every API call and cost calculation for the rest of the session. This is the phase where the three hardcoded `"claude-sonnet-4-6"` literals disappear.

### Changes Required:

#### 1. Import and initialize
**File**: `src/agent.py`
**Changes**: Add the import alongside the existing `from tool_gating import gate_tools` (line 13).

```python
from model_router import route_model, DEFAULT_MODEL
```

In `Agent.__init__`, next to `self.current_tools = TOOLS` (line 27):

```python
        self.current_tools = TOOLS  # default until first classification of a turn
        self.current_model = DEFAULT_MODEL      # ditto — pinned for the whole session once routed
        self.current_route_reason = "default"
        self.model_locked = False   # True once the first turn's routing decision is made
        self.route_counts: dict[str, int] = {}  # counts user turns, not API calls
```

#### 2. Route once, at the first turn's classification
**File**: `src/agent.py`
**Changes**: Extend the `if question is not None` block (lines 33-38). This block runs **once per user turn** — the tool loop re-enters `ask_question()` with `question=None` and therefore inherits both `self.current_tools` and `self.current_model` unchanged. Tool gating still re-derives `self.current_tools` from every turn's own classification, same as before. Model routing is different: it only runs when `self.model_locked` is still `False`, i.e. on turn one. Every later turn's classification still feeds `gate_tools`, but is never passed to `route_model` — the session's model was already decided.

```python
        if question is not None:
            self.conversation_history.append({"role": "user", "content": question})
            classifications = self.classifier.classify_intent(question)
            self.current_tools = gate_tools(classifications)
            if not self.model_locked:
                self.current_model, self.current_route_reason = route_model(classifications)
                self.model_locked = True
                print(f"[Model Routing] {self.current_model} — "
                      f"{self.current_route_reason} (session-pinned)")
            self.route_counts[self.current_model] = (
                self.route_counts.get(self.current_model, 0) + 1
            )
            print(f"[Tool Gating] tools enabled this turn: "
                  f"{[t['name'] for t in self.current_tools] or '(none)'}")
```

`route_counts` still increments every turn, not just turn one — since the model never changes after `model_locked` is set, that dict's single entry ends up equal to the session's total turn count, which is exactly the number Phase 5's summary wants.

#### 3. Use the routed model for the call and the cost
**File**: `src/agent.py`
**Changes**: Replace the three `"claude-sonnet-4-6"` literals at lines 42, 61, 63, and surface the model on the tracer span (line 40).

```python
        with self.tracer.span(
            "llm_call", "agent-main",
            model=self.current_model, route=self.current_route_reason,
        ) as span:
            with self.client.messages.stream(
                model=self.current_model,
                max_tokens=1000,
                system=self.system_prompt,
                messages=self.conversation_history,
                tools=self.current_tools
            ) as stream:
                ...
```

```python
        turn_cost = calculate_cost(self.current_model, input_tokens, output_tokens)
        self.session_cost += turn_cost
        self.logger.log_turn(
            self.current_model, input_tokens, output_tokens, turn_cost, latency
        )
```

(The `print_stats` call is left alone here; Phase 5 extends it.)

### Success Criteria:

#### Automated Verification:
- [x] No hardcoded agent model remains: `! grep -n 'claude-sonnet-4-6' src/agent.py`
- [x] Full suite passes: `python -m pytest tests/`
- [x] Agent module imports cleanly: `python -c "import sys;sys.path.insert(0,'src');import agent" 2>&1 | head -1` — expect only the interactive-prompt block, not an ImportError.

#### Manual Verification:
> **Superseded 2026-10-11** by Phase 2 of `2026-10-11-per-turn-model-routing-ratchet.md`.
> Routing is per-turn with an upward ratchet, so the first-turn-only expectations
> below no longer hold. The tool-loop item (same `model=` on every iteration
> *within* a turn) does still hold and was re-verified there.

- [ ] `python src/agent.py`, then "where is my order ORD-100001" as the **first** turn → `[Model Routing] claude-haiku-4-5 — order_status (session-pinned)`, printed once, and the answer is correct.
- [ ] Same session, follow up with "I want to return my headphones" → **no** new `[Model Routing]` line prints, and the turn still runs on `claude-haiku-4-5` (not `claude-opus-5`), proving the session stayed pinned instead of re-routing to the more expensive tier.
- [ ] Fresh session opened with "what's the battery life on the AeroPulse headphones" as the first turn → `[Model Routing] claude-sonnet-5 — product_question (session-pinned)`, with `[ch_XXXX]` citations intact.
- [ ] Fresh session opened with "what's the weather" as the first turn → `[Model Routing] claude-haiku-4-5 — off_topic (session-pinned)`, and the agent declines.
- [ ] On a turn that runs several tool iterations, the tracer's `agent-main` span reports the **same** `model=` on every iteration.
- [ ] The `model` field in every `logs/session_*.jsonl` row for the main agent — across every turn of the session, not just the first — matches the single printed routing decision.

**Implementation Note**: After this phase's automated verification passes, pause for manual confirmation before proceeding.

---

## Phase 5: Counterfactual Cost & Session Routing Summary

### Overview
Routing's whole justification is cost, so make it measurable. Every routed turn is additionally priced at `BASELINE_MODEL` rates using the same token counts, and the session prints a summary on exit.

**Stated honestly, this is an estimate, not a measurement.** It reprices the actual token counts the routed model produced. It does not account for a different model emitting a different-length response, or needing a different number of tool-loop iterations. The output labels it as estimated for exactly this reason.

### Changes Required:

#### 1. Optional routing fields on the logger
**File**: `src/logger.py`
**Changes**: New parameters are keyword-only with defaults, so the positional calls in `intent.py:101` and `query_rewriter.py:69` keep working untouched.

```python
    def log_turn(self, model, input_tokens, output_tokens, cost, latency,
                 route_reason=None, baseline_model=None, baseline_cost=None):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "latency": latency
        }
        if route_reason is not None:
            log_entry["route_reason"] = route_reason
        if baseline_cost is not None:
            # Estimated: the routed model's token counts repriced at baseline rates.
            log_entry["baseline_model"] = baseline_model
            log_entry["baseline_cost"] = baseline_cost
            log_entry["estimated_saving"] = baseline_cost - cost
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def print_stats(self, input_tokens, output_tokens, turn_cost, session_cost,
                    baseline_turn_cost=None):
        line = (f"[in: {input_tokens} tok | out: {output_tokens} tok | "
                f"turn: ${turn_cost:.4f} | session: ${session_cost:.4f}")
        if baseline_turn_cost is not None:
            line += (f" | baseline: ${baseline_turn_cost:.4f} "
                     f"(est. {baseline_turn_cost - turn_cost:+.4f})")
        print(line + "]")
```

#### 2. Track baseline cost and emit the summary
**File**: `src/agent.py`
**Changes**: Extend the import, add the accumulator, pass the extra fields, and add a summary method.

```python
from model_router import route_model, DEFAULT_MODEL, BASELINE_MODEL, MODEL_TIERS
```

In `__init__`, next to `self.session_cost = 0.0` (line 21):

```python
        self.session_cost = 0.0
        self.baseline_cost = 0.0   # same turns, estimated at BASELINE_MODEL rates
```

In `ask_question`, replacing the cost block:

```python
        turn_cost = calculate_cost(self.current_model, input_tokens, output_tokens)
        baseline_turn_cost = calculate_cost(BASELINE_MODEL, input_tokens, output_tokens)
        self.session_cost += turn_cost
        self.baseline_cost += baseline_turn_cost
        self.logger.log_turn(
            self.current_model, input_tokens, output_tokens, turn_cost, latency,
            route_reason=self.current_route_reason,
            baseline_model=BASELINE_MODEL, baseline_cost=baseline_turn_cost,
        )
        self.logger.print_stats(
            input_tokens, output_tokens, turn_cost, self.session_cost,
            baseline_turn_cost=baseline_turn_cost,
        )
```

New method on `Agent`:

```python
    def routing_summary(self) -> str:
        """The session's pinned model, its turn count, and the estimated cost effect.

        Since routing is session-pinned (Phase 4), MODEL_TIERS will show exactly
        one non-zero row here — this loop is defensive, not evidence of per-turn
        switching.
        """
        lines = ["\n=== Routing summary ==="]
        lines.append(f"  session model: {self.current_model} — {self.current_route_reason}")
        for model in MODEL_TIERS:
            turns = self.route_counts.get(model, 0)
            if turns:
                lines.append(f"  {model:<20} {turns} turn(s)")
        lines.append(f"  actual:   ${self.session_cost:.4f}")
        lines.append(f"  baseline: ${self.baseline_cost:.4f}  (all-{BASELINE_MODEL}, estimated)")
        delta = self.baseline_cost - self.session_cost
        pct = (delta / self.baseline_cost * 100) if self.baseline_cost else 0.0
        lines.append(f"  delta:    ${delta:+.4f} ({pct:+.1f}%)")
        lines.append("  (estimate: routed token counts repriced at baseline rates)")
        return "\n".join(lines)
```

#### 3. Print it on exit
**File**: `src/agent.py`
**Changes**: In the main loop's exit branch (lines 90-92).

```python
    if user_input.lower() in ["exit", "quit"]:
        print(agent.routing_summary())
        print("Exiting the agent. Goodbye!")
        break
```

### Success Criteria:

#### Automated Verification:
- [x] Full suite passes: `python -m pytest tests/`
- [x] Old positional callers unaffected: `python -c "import sys;sys.path.insert(0,'src');from datetime import datetime;from logger import SessionLogger;l=SessionLogger(datetime.now());l.log_turn('claude-haiku-4-5',1,1,0.1,0.1);l.print_stats(1,1,0.1,0.1);print('ok')"`
- [x] New fields are optional and omitted when absent — the command above writes a JSONL row with no `route_reason` / `baseline_cost` keys.

#### Manual Verification:
> **Superseded 2026-10-11** (cadence only): deltas are now driven by the
> session's high-water tier, not its opening turn. The baseline/estimate
> mechanics below are unchanged.

- [ ] A multi-turn session that **opens** with an order lookup (routes Haiku) and later mixes in a product question and a return request shows a non-zero **positive** delta — every turn priced at Haiku rates, including the return, because the session stayed pinned to the first turn's tier.
- [ ] `logs/session_*.jsonl` rows from the main agent carry `route_reason`, `baseline_model`, `baseline_cost`, `estimated_saving`; rows from the classifier and rewriter carry none of them.
- [ ] A session that **opens** with a return request (routes Opus) shows a **negative** delta even if later turns in the same session are simple order lookups (Opus costs more than the Sonnet 4.6 baseline for every turn) — the summary must not present routing as unconditionally cheaper.
- [ ] The summary's per-model turn-count loop shows one row per tier the session passed through, and those counts sum to the session's total turn count.

**Implementation Note**: After this phase's automated verification passes, pause for manual confirmation before proceeding.

---

## Phase 6: Documentation

### Overview
Update both docs to describe routing, and fix the stale Haiku rate in the README while in the same table.

### Changes Required:

#### 1. README rate table and new build step
**File**: `README.md`
**Changes**:
- Lines 76-78 — replace the rate table (**note: the existing Haiku row says $2/$10, which is wrong; `src/pricing.py` has the correct $1/$5**):

```markdown
| Model | Input | Output |
|---|---|---|
| claude-opus-5 | $5 / 1M tokens | $25 / 1M tokens |
| claude-sonnet-5 | $2 / 1M tokens | $10 / 1M tokens |
| claude-sonnet-4-6 | $3 / 1M tokens | $15 / 1M tokens |
| claude-haiku-4-5 | $1 / 1M tokens | $5 / 1M tokens |
```

- After §11 Tracing (line 141-145), add §12, and add its entry to the Table of Contents (lines 7-17):

```markdown
### 12. Intent-Based Model Routing
`src/model_router.py` is the sibling of `tool_gating.py`: both consume the
resolved intent list, one to pick the tool subset, the other to pick the model.
Routing is static — a pure function of a classification, with no runtime
escalation — and runs **per turn, ratcheting upward**: every user turn is routed
from its own classification, and the session moves to that model only if it is a
*higher* tier than the current one. A session never downgrades, and the model
never changes inside a turn's tool loop (`is_higher_tier()` plus a
`model_routed` flag in `Agent.ask_question()`). A turn held at the session's
tier records why in its log row, e.g.
`route_reason: "order_status; held by floor (return_request)"`.

| Intent (of the turn) | Model | Why |
|---|---|---|
| `off_topic` | `claude-haiku-4-5` | No tools, no reasoning — just decline |
| `order_status` | `claude-haiku-4-5` | One lookup, format a row; errors self-correct |
| `product_question` | `claude-sonnet-5` | RAG synthesis across merged chunks, citation discipline |
| `general_support` | `claude-sonnet-5` | Open-ended, and the catch-all for unclassifiable turns |
| `return_request` | `claude-opus-5` | Policy reasoning, and the only intent whose tools mutate the DB |

A multi-intent turn takes the most capable tier, mirroring how tool gating takes
the union of tools; the ratchet applies that same "err toward capability" rule
across turns instead of only within one. Only the main agent is routed —
routing the intent classifier would be circular, and the query rewriter's work
is the same difficulty every call.

Every turn is also priced at the pre-routing `claude-sonnet-4-6` baseline, and
`exit` prints a routing summary with one row per tier the session passed
through. That baseline is an **estimate**: it reprices each turn's actual token
counts, so it does not capture a different model producing a different-length
answer or taking a different number of tool iterations. One `return_request`
escalates the rest of the session to Opus and can turn the delta negative —
the deliberate price of never downgrading, shown rather than hidden.
```

- Lines 188-192 — Models Used table:

```markdown
| Component | Model | Reason |
|---|---|---|
| Main agent | routed per intent (`claude-haiku-4-5` / `claude-sonnet-5` / `claude-opus-5`) | Tier matched to the turn's reasoning demand and cost of error — see §12 |
| Intent classifier | `claude-haiku-4-5` | Fast and cheap for classification tasks |
| Query rewriter | `claude-haiku-4-5` | Cheap, fast, high-volume structured task |
```

#### 2. CLAUDE.md architecture section
**File**: `CLAUDE.md`
**Changes**:
- Line 31 — the two-model claim ("Two-model pipeline (Sonnet main agent, Haiku for cheap auxiliary calls)") becomes: "Tiered-model pipeline (main agent routed per turn across Haiku/Sonnet/Opus, ratcheting upward and never downgrading within a session; Haiku fixed for auxiliary calls)".
- Step 1 of the per-turn flow (lines 34-35) — after the tool-gating sentence, add: "`src/model_router.py` maps a resolved intent list onto a model tier (`INTENT_MODEL_MAP`, max tier across multi-intent, `MODEL_TIERS` ordered cheapest-first), and `Agent.ask_question()` calls it on every user turn, keeping the higher of the routed and current tier (`is_higher_tier()`, guarded by a `model_routed` flag so the pre-first-turn `DEFAULT_MODEL` placeholder is not treated as a floor). A session therefore escalates but never downgrades, and because the whole block sits under `if question is not None:`, the model never changes inside a turn's tool loop; a turn held at the session's tier logs `route_reason` as `\"<its own intent>; held by floor (<the intent that set the tier>)\"`. `resolve_intents()` in `tool_gating.py` is the shared source of truth for confidence filtering and fallback, feeding both the tool-gating and model-routing calls, so the two can't disagree about *what an intent means*."
- Step 2 (line 36) — "streams from `claude-sonnet-4-6`" becomes "streams from `self.current_model` (re-routed each turn, held at the session's high-water tier)".
- The Cost/pricing section (line 51) — note the table now covers four models, that `calculate_cost` raises `ValueError` on an unknown model, that `model_router` validates the registry at import time, and that the main agent additionally accumulates `baseline_cost` at `claude-sonnet-4-6` rates for the exit summary — computed per turn throughout the session, including on turns where the ratchet holds the model unchanged.

### Success Criteria:

#### Automated Verification:
- [x] No stale Haiku rate remains — criterion corrected: `$2 / 1M` now legitimately matches the `claude-sonnet-5` row, so the check that actually expresses the intent is `! grep -n 'claude-haiku-4-5 | \$2' README.md`. All four README rates cross-checked against `src/pricing.py`.
- [x] Routing is documented in both files: `grep -c 'model_router' CLAUDE.md README.md` — CLAUDE.md:2, README.md:1

#### Manual Verification:
- [ ] README §12 matches the behavior actually implemented in `src/model_router.py`.
- [ ] The Table of Contents links resolve.

---

## Testing Strategy

### Unit Tests:
- **`tests/model_router_test.py`** (new) — each intent's tier; multi-intent max-tier; sub-threshold intents not escalating the tier; safe fallback → `general_support` tier; all-below-threshold fallback; `off_topic` plus low-confidence others. `route_model()` itself stays a pure, stateless function of one classification list — it has no idea at what cadence it is called. **Amended 2026-10-11:** the cadence is Agent's upward ratchet, and `tests/model_router_test.py` now also covers `is_higher_tier()` and a simulated three-turn ratchet sequence; the `Agent`-level wiring remains integration-level and covered manually.
- **Registry invariants** — `MODEL_TIERS` ordered by ascending output price (catches a tier list that lies about cost order, which would make "max tier" silently mean "cheapest model"); every routable model priced; every intent has a tier.
- **Routing/gating coupling** — parametrized: any turn offered `cancel_order` or `initiate_return` must be routed to the `return_request` tier. This is the test that catches drift between the two consumers of `resolve_intents`.
- **`tests/tool_gating_test.py`** — the seven existing tests must pass **unedited** after the Phase 2 refactor; new `resolve_intents` tests appended.
- **`tests/backend_test.py`** — rates for the two new models; `test_unknown_model_pricing` updated from `TypeError` to `ValueError`.

All of the above are pure functions — no API key, no network, no DB.

### Integration Tests:
None automated. The wiring in Phases 4-5 requires a live API call and is covered by manual steps.

### Manual Testing Steps:
1. `python src/agent.py`, log in with a seeded email.
2. **Session A** — first turn "where is my order ORD-100001" → routes `claude-haiku-4-5` (printed once), correct order returned.
3. Same session, second turn "I want to return order ORD-100001" → **no** new `[Model Routing]` line; still runs on `claude-haiku-4-5`, not `claude-opus-5`, even though `return_request` alone would route to Opus. Confirm the return tools are still offered (tool gating is unaffected) and eligibility check runs before any mutation.
4. Same session, third turn "what's the weather in Delhi" → still `claude-haiku-4-5`, agent declines, no tools offered.
5. `exit` → routing summary prints one model row (`claude-haiku-4-5`), turn count equal to 3.
6. **Session B** (fresh) — first turn "what's the battery life on the AeroPulse headphones" → routes `claude-sonnet-5`, answer carries `[ch_XXXX]` citations.
7. **Session C** (fresh) — first turn "I want to return order ORD-100001" → routes `claude-opus-5`. On this turn's multi-tool-iteration exchange, confirm every `agent-main` tracer span reports the same `model=`.
8. Same session, follow up with a plain order lookup → still `claude-opus-5` — confirm the session doesn't downgrade just because a later turn is cheap.
9. `exit` on Session C → summary reports a **negative** delta (Opus costs more than the Sonnet 4.6 baseline for every turn in that session, including the cheap follow-up).
10. **Session D** (fresh) — first turn "where's my order and can I return it" (multi-intent) → routes `claude-opus-5` (max tier of the two intents), and the tool list includes both the lookup and the return tools.
11. Inspect the newest `logs/session_*.jsonl` files for sessions A and C: every main-agent row within a session carries the same `model`, plus `route_reason` / `baseline_cost`; classifier and rewriter rows carry none of them.

## Performance Considerations

- **No new API calls.** `classify_intent()` was already being called on every turn (tool gating needs it regardless); routing reuses that same classification. The decision itself is a dict lookup plus two `MODEL_TIERS.index()` calls, once per user turn.
- **Latency tracks the session's high-water tier.** A session that only ever sees `order_status` / `off_topic` stays on Haiku throughout, materially faster than Sonnet.
- **Escalation is sticky, by design.** One `return_request` keeps the rest of the session on Opus 5 ($5/$25 vs the $3/$15 baseline) even for later turns that would route cheaper alone. That is the deliberate price of never downgrading mid-conversation, and Phase 5's summary shows the resulting negative delta rather than hiding it.
- **Prompt caching is not in use**, so today the ratchet costs nothing in cache invalidation. If caching is added, note that caches are model-scoped: each escalation re-pays full input cost on the accumulated history. Under the ratchet that happens at most twice per session (Haiku→Sonnet→Opus), but measure it before assuming it is negligible — this is the one condition under which session-pinning would be the better design.
- **No thinking blocks are in play.** If extended thinking is added, verify how the API handles earlier assistant turns' thinking blocks when the model changes mid-conversation before enabling both together.

## Migration Notes

No data migration and no schema change. `conversation_history` is model-agnostic — nothing persisted encodes which model produced a turn, so existing sessions and logs remain readable.

Rollback is a single-line revert: setting every entry in `INTENT_MODEL_MAP` to `claude-sonnet-4-6` restores exactly the pre-routing behavior while leaving the plumbing in place. Narrower cadence rollbacks are one-liners in `Agent.ask_question()`: for session-pinning, change the ratchet's condition to `if not self.model_routed:` and drop its `else:` branch; for pure per-turn routing with no floor, delete the condition and the `else:` branch so every turn reassigns `self.current_model`.

Existing `logs/*.jsonl` files predate the new fields. Anything reading them must treat `route_reason` / `baseline_model` / `baseline_cost` / `estimated_saving` as optional keys — which is why Phase 5 omits them entirely rather than writing nulls.

## References

- Sibling implementation to mirror: `src/tool_gating.py:35-53`, tested in `tests/tool_gating_test.py`
- Tool-gating plan (same shape, prior step): `thoughts/shared/plans/2026-08-30-intent-tool-gating.md`
- Unpriced-model `TypeError` first documented: `thoughts/shared/research/2026-08-22-end-to-end-tracing-system.md:87`
- Stale README Haiku rate flagged: `thoughts/shared/reviews/2026-09-03-main-branch-full-review-v4.md:138`
- Model routing listed as planned work: `Notes.txt`
- Call sites replaced: `src/agent.py:42,61,63`
