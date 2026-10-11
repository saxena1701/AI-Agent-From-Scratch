"""Static, intent-driven model selection for the main agent.

Sibling of tool_gating.py: both consume the same resolved intent list, one to
pick tools, one to pick the model. Routing is a pure function of a
classification — there is no runtime escalation within a turn. The caller
(Agent) routes every user turn and applies an upward ratchet: a session only
ever moves to a higher tier, never down, and never mid-tool-loop. This module
has no notion of "session"; is_higher_tier() is the stateless comparison the
caller's ratchet is built on.

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


def is_higher_tier(candidate: str, current: str) -> bool:
    """True if candidate sits strictly above current in MODEL_TIERS."""
    return MODEL_TIERS.index(candidate) > MODEL_TIERS.index(current)
