import json

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from src.output_checks import (
    cited_ids, has_fake_tool_markup, invalid_citations, strip_citations,
)
import agent as agent_module
from agent import Agent
from tracer import get_tracer


# --- pure checks ---------------------------------------------------------------

def test_fake_tool_markup_detected():
    text = 'Let me search.\n<function_calls>\n<invoke name="search_products">'
    assert has_fake_tool_markup(text)


def test_plain_reply_has_no_fake_tool_markup():
    assert not has_fake_tool_markup("We carry the FlexBand Pro for $24.99.")


def test_cited_ids_reads_single_and_grouped_brackets():
    text = "Thirty days [return_policy_c0]. Fee applies [ch_0042/return_policy, electronics_return_addendum]."
    assert cited_ids(text) == [
        "return_policy_c0", "ch_0042", "return_policy", "electronics_return_addendum",
    ]


def test_cited_ids_ignores_links_order_ids_and_checkboxes():
    text = "See [our policy](https://example.com/p_1), order [ORD-123456], [x] done."
    assert cited_ids(text) == []


def test_invalid_citations_are_ids_not_in_results():
    text = "Window is 30 days [return_policy_c0] with a fee [ch_0042]."
    assert invalid_citations(text, {"return_policy_c0"}) == ["ch_0042"]


def test_valid_citations_pass():
    assert invalid_citations("Thirty days [return_policy_c0].", {"return_policy_c0"}) == []


def test_strip_citations_removes_bad_ids_and_tidies_punctuation():
    text = "Thirty days [return_policy_c0, ch_0042]. Fee is 15% [ch_0042]."
    assert strip_citations(text, ["ch_0042"]) == "Thirty days [return_policy_c0]. Fee is 15%."


# --- run_turn retry and fallback -----------------------------------------------

def _message(*blocks, stop_reason="end_turn"):
    return Message(
        id="msg", type="message", role="assistant", model="test-model",
        content=list(blocks), stop_reason=stop_reason, stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _text(text):
    return TextBlock(type="text", text=text)


FAKE = _text('Let me check.\n<function_calls>\n<invoke name="search_products">\n</invoke>\n</function_calls>')


def _agent(scripted_replies):
    """An Agent whose model calls return scripted replies, without touching the API."""
    agent = Agent.__new__(Agent)
    agent.conversation_history = []
    agent.retrieved_chunk_ids = set()
    agent.tracer = get_tracer()
    replies = iter(scripted_replies)

    def fake_ask(question=None, print_debug=False):
        if question is not None:
            agent.conversation_history.append({"role": "user", "content": question})
        reply = next(replies)
        agent.conversation_history.append({"role": "assistant", "content": reply.content})
        return reply

    agent.ask_question = fake_ask
    return agent


def test_fake_tool_call_is_retried_and_dropped_from_history():
    agent = _agent([_message(FAKE), _message(_text("We have the FlexBand Pro."))])
    reply = agent.run_turn("any resistance bands?", backend=None, print_debug=False)

    assert reply.content[0].text == "We have the FlexBand Pro."
    assert [c["action"] for c in agent.last_output_checks] == ["retry"]
    history_text = json.dumps(agent.conversation_history, default=str)
    assert "<function_calls>" not in history_text
    # the correction is merged into the user turn, not sent as a second user message
    roles = [m["role"] for m in agent.conversation_history]
    assert roles == ["user", "assistant"]


def test_fake_tool_call_twice_falls_back_to_safe_message():
    agent = _agent([_message(FAKE), _message(FAKE)])
    reply = agent.run_turn("any resistance bands?", backend=None, print_debug=False)

    assert reply.content[0].text == agent_module.FAKE_TOOL_FALLBACK
    assert [c["action"] for c in agent.last_output_checks] == ["retry", "fallback"]
    assert agent.conversation_history[-1]["content"][0].text == agent_module.FAKE_TOOL_FALLBACK


def test_retry_can_make_real_tool_calls(monkeypatch):
    monkeypatch.setattr(agent_module, "execute_tool", lambda name, args, backend: {
        "results": [{"chunk_id": "return_policy_c0", "text": "30 days"}],
    })
    tool_use = ToolUseBlock(type="tool_use", id="t1", name="retrieve", input={"query": "returns"})
    agent = _agent([
        _message(FAKE),
        _message(tool_use, stop_reason="tool_use"),
        _message(_text("You have 30 days [return_policy_c0].")),
    ])
    reply = agent.run_turn("return window?", backend=None, print_debug=False)

    assert reply.content[0].text == "You have 30 days [return_policy_c0]."
    assert [c["name"] for c in agent.last_tool_calls] == ["retrieve"]
    assert agent.retrieved_chunk_ids == {"return_policy_c0"}


def test_invalid_citation_twice_is_stripped_not_replaced():
    agent = _agent([
        _message(_text("You have 30 days [ch_0042].")),
        _message(_text("You have 30 days [ch_0042].")),
    ])
    reply = agent.run_turn("return window?", backend=None, print_debug=False)

    assert reply.content[0].text == "You have 30 days."
    assert [c["check"] for c in agent.last_output_checks] == ["invalid_citations"] * 2


def test_clean_reply_passes_untouched():
    agent = _agent([_message(_text("Your order shipped yesterday."))])
    reply = agent.run_turn("where is my order?", backend=None, print_debug=False)

    assert reply.content[0].text == "Your order shipped yesterday."
    assert agent.last_output_checks == []
