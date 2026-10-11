# Per-Turn Model Routing with an Upward Ratchet — Implementation Plan

## Overview

Change main-agent model routing from **session-pinned** (first turn decides the model forever) to **per-turn with an upward ratchet**: every user turn is routed from its own classification, and the session moves to that model only if it is a *higher* tier than the current one. Never downgrade within a session.

This amends `2026-09-06-model-routing.md`, which is otherwise unchanged: same tiers, same `route_model()`, same pricing and counterfactual.

## Why

- **Safety gap.** A session that opens on `order_status` (Haiku) and then asks for a return runs `cancel_order` / `initiate_return`, the only irreversible DB writes, on Haiku. `INTENT_MODEL_MAP` puts `return_request` on Opus precisely because of that cost of error. Today the tier map promises Opus for mutations and the caller doesn't honor it.
- **Arbitrary anchor.** The first turn isn't special. Pinning makes the whole session's cost and capability depend on whichever intent arrived first, in both directions.
- **Consistency with the router's own principle.** `model_router.py:19-21` says multi-intent turns take the max tier and "err toward capability." The ratchet applies the same max across turns.
- **Why ratchet, not pure per-turn:** both close the gap, but pure per-turn lets classifier noise downgrade a session mid-conversation after context was built at a higher tier.

## Current State

- `src/agent.py:32,43-48`: the `model_locked` flag makes `route_model()` run only on the first classified turn.
- `src/agent.py:42`: tool gating already runs per turn. It doesn't change.
- `src/agent.py:39`: everything sits under `if question is not None:`, so tool-loop re-entries (`ask_question()` with no argument, `agent.py:149`) never re-route. **That stays the same**, so the model can never change mid-tool-loop.
- `src/agent.py:154-162`: `routing_summary()` docstring and its "session model" line assume a single model.
- `src/model_router.py:5-9`: the module docstring describes one-shot calling.
- `tests/model_router_test.py`: covers `route_model()` only. Nothing tests the pinning, so no test breaks.
- `src/logger.py`, `src/pricing.py`, `src/tool_gating.py`, `src/eval.py`: **no changes.** The logger already writes per call. Eval builds a fresh `Agent()` per single-turn example, so it behaves the same under either policy.
- `streamlit_app.py`: drives `ask_question()` directly, so it inherits the new behavior. **No change.**
- `README.md` / `CLAUDE.md`: routing isn't documented yet (old plan Phase 6 never ran).

## Desired End State

Each user turn computes `route_model(classifications)`. If no model has been routed yet in the session, or the routed model is a strictly higher tier than `self.current_model`, the session switches to it and prints `[Model Routing] → <model> — <reason>`. Otherwise the model holds and the turn's logged `route_reason` records both its own intent and the floor holding it. Tool-loop iterations never re-route.

### Key edge case

The pre-first-turn placeholder is `DEFAULT_MODEL` (Sonnet). The ratchet must **not** treat that as a floor, or an opening `order_status` turn could never drop to Haiku. A `model_routed` flag (replacing `model_locked`) handles this: the first routed turn always takes its own tier.

## What We're NOT Doing

- No downgrades within a session, and no time- or turn-based decay of the floor.
- No mid-tool-loop routing, and no escalation triggered by tool errors, loop depth or low confidence.
- No changes to `INTENT_MODEL_MAP`, `MODEL_TIERS`, pricing, the classifier or the rewriter.
- No refactor of `streamlit_app.py`'s duplicated tool loop. It's a separate cleanup.
- No prompt caching or extended thinking. Both are reasons to revisit this design (see Risks).

---

## Phase 1: Pure Ratchet Helper and Tests

### Changes

**File**: `src/model_router.py`

Rewrite the module docstring's second paragraph (lines 5-9):

```python
"""...
Routing is a pure function of a classification — there is no runtime escalation
within a turn. The caller (Agent) routes every user turn and applies an upward
ratchet: a session only ever moves to a higher tier, never down, and never
mid-tool-loop. This module has no notion of "session"; is_higher_tier() is the
stateless comparison the caller's ratchet is built on.
..."""
```

Append:

```python
def is_higher_tier(candidate: str, current: str) -> bool:
    """True if candidate sits strictly above current in MODEL_TIERS."""
    return MODEL_TIERS.index(candidate) > MODEL_TIERS.index(current)
```

**File**: `tests/model_router_test.py`. Add the following, and add `is_higher_tier` to the existing import.

```python
# --- the session ratchet's comparison ---

@pytest.mark.parametrize("candidate,current,expected", [
    ("claude-opus-5", "claude-haiku-4-5", True),
    ("claude-sonnet-5", "claude-haiku-4-5", True),
    ("claude-haiku-4-5", "claude-opus-5", False),   # never downgrade
    ("claude-sonnet-5", "claude-sonnet-5", False),  # same tier is not higher
])
def test_is_higher_tier(candidate, current, expected):
    assert is_higher_tier(candidate, current) is expected


def test_ratchet_sequence_order_then_return_then_order():
    # Simulates Agent's ratchet over three turns with no API calls.
    # Expected: haiku → opus (escalate on the return) → opus (held, no downgrade)
    current, routed_yet, seen = DEFAULT_MODEL, False, []
    for intent in ["order_status", "return_request", "order_status"]:
        model, _ = route_model([_c(intent)])
        if not routed_yet or is_higher_tier(model, current):
            current, routed_yet = model, True
        seen.append(current)
    assert seen == ["claude-haiku-4-5", "claude-opus-5", "claude-opus-5"]


def test_first_turn_may_route_below_the_default_placeholder():
    # DEFAULT_MODEL (sonnet) is a placeholder, not a floor: an opening
    # order_status turn must land on haiku.
    model, _ = route_model([_c("order_status")])
    assert is_higher_tier(model, DEFAULT_MODEL) is False
    assert model == "claude-haiku-4-5"   # Agent takes it because model_routed is False
```

### Success Criteria

#### Automated
- [x] `python -m pytest tests/model_router_test.py -v` passes — 25 passed (19 existing + 6 new)
- [x] `python -m pytest tests/` passes (48 existing + 6 new) — 54 passed

---

## Phase 2: Wire the Ratchet into `Agent`

### Changes

**File**: `src/agent.py`

1. Imports (line 14): add `is_higher_tier`. `MODEL_TIERS` stays because `routing_summary` uses it.

2. `__init__` (lines 30-32): replace the pinning state.
   ```python
   self.current_model = DEFAULT_MODEL      # placeholder until the first routed turn
   self.current_route_reason = "default"
   self.model_routed = False   # False until a turn has routed; DEFAULT_MODEL is not a floor
   self.floor_reason = None    # the routing reason that set the current tier
   ```

3. `ask_question` (lines 43-48): replace the `if not self.model_locked:` block. It stays inside `if question is not None:`, so it's still once per user turn and never per tool iteration.
   ```python
   routed_model, routed_reason = route_model(classifications)
   if not self.model_routed or is_higher_tier(routed_model, self.current_model):
       self.current_model = routed_model
       self.current_route_reason = self.floor_reason = routed_reason
       self.model_routed = True
       if print_debug:
           print(f"[Model Routing] → {self.current_model} — {routed_reason}")
   else:
       # This turn alone would route at or below the session's tier; hold it.
       self.current_route_reason = f"{routed_reason}; held by floor ({self.floor_reason})"
   ```
   `route_counts` (lines 49-51) is unchanged. It now gets more than one row whenever a session escalates, as originally intended.

4. `routing_summary` (lines 154-162): fix the docstring and the header line.
   ```python
   """Per-model turn counts, the session's final tier, and the estimated cost effect.

   Routing ratchets upward per turn, so a session that escalated shows one row
   per tier it passed through.
   """
   ...
   lines.append(f"  final model: {self.current_model} — set by {self.floor_reason or 'default'}")
   ```

### Success Criteria

#### Automated
- [x] `python -m pytest tests/` passes — 54 passed
- [x] No pinning remnants: `! grep -n -E "model_locked|session-pinned" src/`
- [x] Agent imports cleanly: `python -c "import sys;sys.path.insert(0,'src');import agent"` — no output, no ImportError (the interactive block is behind `if __name__ == "__main__":`, so it does not run on import)

#### Manual (`python src/agent.py`)
- [x] **Escalation:** turn 1 "where is my order ORD-100001" → `[Model Routing] → claude-haiku-4-5 — order_status`. Turn 2 "I want to return it" → `[Model Routing] → claude-opus-5 — return_request`, and the return tools appear in `[Tool Gating]`.
- [x] **No downgrade:** turn 3 "where's my other order" → no `[Model Routing]` line. That turn's `logs/session_*.jsonl` row has `"model": "claude-opus-5"` and `route_reason` `"order_status; held by floor (return_request)"`.
- [x] **Placeholder isn't a floor:** a fresh session opening with "what's the weather" routes to `claude-haiku-4-5` (not stuck on the Sonnet placeholder) and declines.
- [x] **No mid-loop switch:** on a multi-tool-iteration turn, every `agent-main` tracer span reports the same `model=`.
- [x] **Summary:** after the escalation session, `exit` prints both a `claude-haiku-4-5 1 turn(s)` row and a `claude-opus-5 2 turn(s)` row, and `final model: claude-opus-5 — set by return_request`.
- [x] **Streamlit:** `streamlit run streamlit_app.py`. The sidebar "Model" changes from Haiku to Opus after a return-request turn.

**Pause here for manual confirmation before Phase 3.**

---

## Phase 3: Reconcile the Original Plan

### Changes

**File**: `thoughts/shared/plans/2026-09-06-model-routing.md`

1. Add a banner under the title:
   > **Amended 2026-10-11:** routing cadence changed from session-pinned to per-turn with an upward ratchet. See `2026-10-11-per-turn-model-routing-ratchet.md`. Anything below describing first-turn-only routing, `model_locked`, or "session-pinned" is superseded.
2. Mark the pinning-specific manual criteria as superseded by this plan's Phase 2 criteria: lines 599-602 (Phase 4) and line 731 (Phase 5's "every turn priced at Haiku rates, including the return"). Rewrite line 734, "exactly one non-zero row," to say one row per tier the session passed through.
3. Rewrite Phase 6's draft doc text so it describes the ratchet before anyone executes it:
   - README §12 (lines ~768-794): routing is "per turn, ratcheting upward, never mid-tool-loop," and the table header becomes "Intent (of the turn)." Replace the "session-pinning" consequence paragraph with: one return request escalates the rest of the session to Opus; that's the deliberate price of never downgrading.
   - CLAUDE.md step 1 and step 2 text (lines 812-813): replace "only calls it once, on the session's first turn … `model_locked` flag" with "routes every user turn and keeps the higher of the routed and current tier (`is_higher_tier`, `model_routed` flag); the model never changes within a turn's tool loop."
4. Performance Considerations (lines 859-861) and Rollback (line 867): replace with the Risks section below and the rollback note below.

### Success Criteria

#### Automated
- [x] The banner exists: `grep -n "Amended 2026-10-11" thoughts/shared/plans/2026-09-06-model-routing.md`
- [x] No stale guard in the Phase 6 draft text — checked across the whole Phase 6 section rather than a line range (the line numbers shifted when the banner was added): `! sed -n '/^## Phase 6/,/^## Testing Strategy/p' thoughts/shared/plans/2026-09-06-model-routing.md | grep -q model_locked`

#### Manual
- [ ] Reading old plan Phase 6 on its own produces README and CLAUDE.md text that matches the implemented behavior.

---

## Risks and Revisit Triggers

- **Cost:** a single `return_request` keeps the rest of the session on Opus ($5/$25 against the $3/$15 baseline). This is visible in the `routing_summary` delta and isn't hidden.
- **Prompt caching (not used today):** caches are scoped to one model, so each escalation re-pays full input cost on the accumulated history. Under the ratchet that happens at most twice per session (Haiku→Sonnet→Opus). If caching is added, measure it before assuming it's negligible.
- **Extended thinking (not used today):** if added, check the API's handling of earlier assistant turns' thinking blocks when the model changes mid-conversation, before enabling both together.
- **Classifier false positive:** a misclassified `return_request` escalates the session for good. This is the deliberate trade, the same "err toward capability" as multi-intent max.

## Rollback

- **Back to session-pinned:** in `ask_question`, change the condition to `if not self.model_routed:` and drop the `else:` branch.
- **Pure per-turn instead:** delete the condition and the `else:` branch so every turn assigns `routed_model` unconditionally.

## References

- Original plan: `thoughts/shared/plans/2026-09-06-model-routing.md`
- Routing guard: `src/agent.py:43-48`
- Tool-loop re-entry (stays unrouted): `src/agent.py:149`
- "Err toward capability" principle: `src/model_router.py:19-21`
- Gating/routing coupling test: `tests/model_router_test.py:84-99`
