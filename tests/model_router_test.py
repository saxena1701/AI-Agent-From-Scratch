import pytest

from src.intent import IntentClassification
from src.model_router import (
    BASELINE_MODEL, DEFAULT_MODEL, INTENT_MODEL_MAP, MODEL_TIERS,
    is_higher_tier, route_model,
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
