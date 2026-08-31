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
