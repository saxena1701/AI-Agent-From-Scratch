import json

import anthropic
from anthropic.types import TextBlock
import os
from dotenv import load_dotenv
from logger import SessionLogger
from pricing import calculate_cost
from datetime import datetime
import time
from intent import IntentClassifierAgent
from tool_executor import execute_tool
from tools import TOOLS
from tool_gating import gate_tools
from model_router import (
    route_model, is_higher_tier, DEFAULT_MODEL, BASELINE_MODEL, MODEL_TIERS,
)
from backend import MarketSphereBackend
from tracer import get_tracer
from output_checks import has_fake_tool_markup, invalid_citations, strip_citations
load_dotenv()

class Agent:
    def __init__(self):
        self.conversation_history = []  # Fix 3: instance variable
        self.session_cost = 0.0
        self.baseline_cost = 0.0   # same turns, estimated at BASELINE_MODEL rates
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.classifier = IntentClassifierAgent()
        self.tracer = get_tracer()
        self.current_tools = TOOLS  # default until first classification of a turn
        self.current_model = DEFAULT_MODEL      # placeholder until the first routed turn
        self.current_route_reason = "default"
        self.model_routed = False   # False until a turn has routed; DEFAULT_MODEL is not a floor
        self.floor_reason = None    # the routing reason that set the current tier
        self.route_counts: dict[str, int] = {}  # counts user turns, not API calls
        self.retrieved_chunk_ids: set[str] = set()  # every chunk_id retrieve has returned this session
        self.last_tool_calls = []
        self.last_output_checks = []  # output-check events from the most recent turn
        with open('src/prompt/system_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def ask_question(self, question=None,print_debug:bool=True):
        start_time = time.time()
        if question is not None:
            self.conversation_history.append({"role": "user", "content": question})
            classifications = self.classifier.classify_intent(question)
            self.current_tools = gate_tools(classifications)
            routed_model, routed_reason = route_model(classifications)
            if not self.model_routed or is_higher_tier(routed_model, self.current_model):
                self.current_model = routed_model
                self.current_route_reason = self.floor_reason = routed_reason
                self.model_routed = True
                if print_debug:
                    print(f"[Model Routing] → {self.current_model} — {routed_reason}")
            else:
                # This turn alone would route at or below the session's tier; hold it.
                self.current_route_reason = (
                    f"{routed_reason}; held by floor ({self.floor_reason})"
                )
            self.route_counts[self.current_model] = (
                self.route_counts.get(self.current_model, 0) + 1
            )
            if print_debug:
                print(f"[Tool Gating] tools enabled this turn: "
                      f"{[t['name'] for t in self.current_tools] or '(none)'}")

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
                for text in stream.text_stream:
                    if print_debug:
                        print(text, end="", flush=True)
                if print_debug:
                    print()
                message = stream.get_final_message()
            span.set_result({
                "stop_reason": message.stop_reason,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            })

        latency = time.time() - start_time
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
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
            baseline_turn_cost=baseline_turn_cost, print_debug=print_debug,
        )

        # Fix 4: append the full content (list of blocks), not just .text
        self.conversation_history.append({"role": "assistant", "content": message.content})
        return message  # Fix 1: return the full message object

    def run_turn(self, user_input: str, backend: MarketSphereBackend, print_debug: bool = True):
        """Run one full user turn: classify/route, stream the model, and loop
        through any tool calls until the turn reaches end_turn (or the tool
        iteration limit), then check the final reply before returning it.
        Returns the final Message; each tool call made along the way is recorded
        in self.last_tool_calls, and each failed output check in
        self.last_output_checks."""
        self.last_tool_calls = []
        self.last_output_checks = []
        self.tracer.set_enabled(print_debug)
        self.tracer.begin_turn(user_input)
        try:
            response = self.ask_question(user_input, print_debug=print_debug)
            retries_left = OUTPUT_CHECK_RETRIES
            while True:
                response = self._run_tool_loop(response, backend, print_debug)
                if response.stop_reason == "tool_use":
                    return response  # iteration limit hit; history already patched
                problem = self._find_output_problem(response)
                if problem is None:
                    return response
                # The rejected reply is the last history entry; drop it so neither
                # the customer nor later turns build on it.
                self.conversation_history.pop()
                with self.tracer.span("output_check", problem["check"], detail=problem["detail"]) as span:
                    action = "retry" if retries_left else "fallback"
                    span.set_result({"action": action})
                self.last_output_checks.append(
                    {"check": problem["check"], "detail": problem["detail"], "action": action}
                )
                if print_debug:
                    print(f"\n[Output check] {problem['check']}: {problem['detail']} → {action}")
                if not retries_left:
                    return self._fallback_reply(response, problem, print_debug)
                retries_left -= 1
                self._append_user_note(problem["nudge"])
                response = self.ask_question(print_debug=print_debug)
        finally:
            self.tracer.end_turn()

    def _run_tool_loop(self, response, backend: MarketSphereBackend, print_debug: bool):
        """Execute tool_use blocks and re-call the model until the reply stops
        asking for tools or the iteration limit is reached."""
        iterations = 0
        while response.stop_reason == "tool_use":
            if iterations >= MAX_TOOL_ITERATIONS:
                # history ends with an unanswered assistant tool_use block;
                # answer every block with an error so history stays valid for
                # the next user turn, then stop WITHOUT re-calling the model.
                self.conversation_history.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": "Tool iteration limit reached."}),
                            "is_error": True
                        }
                        for block in response.content if block.type == "tool_use"
                    ]
                })

                if print_debug:
                    print(f"\n[!] Stopped after {MAX_TOOL_ITERATIONS} tool iterations.")
                break
            iterations += 1

            for block in response.content:
                if block.type == "tool_use":
                    with self.tracer.span("tool_call", block.name, args=block.input) as span:
                        result = execute_tool(block.name, block.input, backend)
                        span.set_result(result)
                    self.last_tool_calls.append({"name": block.name, "input": block.input, "result": result})
                    if block.name == "retrieve" and isinstance(result, dict):
                        self.retrieved_chunk_ids.update(
                            r["chunk_id"] for r in result.get("results", []) if "chunk_id" in r
                        )
                    self.conversation_history.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        }]
                    })

            response = self.ask_question(print_debug=print_debug)
        return response

    def _find_output_problem(self, response):
        """Return the first failed output check on a final reply, or None if it passes."""
        text = "".join(b.text for b in response.content if b.type == "text")
        if has_fake_tool_markup(text):
            return {
                "check": "fake_tool_markup",
                "detail": "reply wrote a tool call as plain text",
                "nudge": (
                    "Your last reply wrote a tool call out as text. That did not run "
                    "anything, and the customer cannot be shown it. If you need a tool "
                    "you have this turn, call it through the tool interface. If you "
                    "don't have the tool you need, tell the customer you can't check "
                    "that right now. Don't claim to have looked anything up."
                ),
            }
        bad = invalid_citations(text, self.retrieved_chunk_ids)
        if bad:
            valid = ", ".join(sorted(self.retrieved_chunk_ids)) or "none"
            return {
                "check": "invalid_citations",
                "detail": f"cited IDs not in any retrieve result: {bad}",
                "bad_ids": bad,
                "nudge": (
                    f"Your last reply cited {bad}, which are not chunk_ids from any "
                    "knowledge-base result in this conversation. Rewrite the reply citing "
                    "only chunk_ids that appear in those results, copied exactly, or "
                    f"without citations if none apply. Valid chunk_ids: {valid}."
                ),
            }
        return None

    def _append_user_note(self, note: str):
        """Add a correction for the model to the current user turn. It is merged
        into the last user message (the question or tool results) rather than
        sent as a second consecutive user message."""
        last = self.conversation_history[-1]
        block = {"type": "text", "text": f"[Note from the support system, not the customer] {note}"}
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}, block]
        else:
            last["content"] = [*last["content"], block]

    def _fallback_reply(self, response, problem, print_debug: bool):
        """Replace a reply that failed its check after the retry. Invalid citations
        are stripped and the rest of the reply kept; a fake tool call can't be
        salvaged, so it becomes a safe message that claims nothing."""
        if problem["check"] == "invalid_citations":
            content = [
                TextBlock(type="text", text=strip_citations(b.text, problem["bad_ids"]))
                if b.type == "text" else b
                for b in response.content
            ]
        else:
            content = [TextBlock(type="text", text=FAKE_TOOL_FALLBACK)]
        reply = response.model_copy(update={"content": content})
        self.conversation_history.append({"role": "assistant", "content": reply.content})
        if print_debug:
            print("".join(b.text for b in reply.content if b.type == "text"))
        return reply

    def routing_summary(self) -> str:
        """Per-model turn counts, the session's final tier, and the estimated cost effect.

        Routing ratchets upward per turn, so a session that escalated shows one
        row per tier it passed through.
        """
        lines = ["\n=== Routing summary ==="]
        lines.append(f"  final model: {self.current_model} — set by {self.floor_reason or 'default'}")
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


MAX_TOOL_ITERATIONS = 10
OUTPUT_CHECK_RETRIES = 1  # re-ask the model once before falling back
FAKE_TOOL_FALLBACK = (
    "I'm sorry, I wasn't able to look that up just now. Could you try again, or tell "
    "me a bit more about what you're looking for?"
)

if __name__ == "__main__":
    agent = Agent()
    backend = MarketSphereBackend('db/marketsphere.db')
    tracer = agent.tracer

    while True:
        email = input("Please enter your account email to begin (or 'exit'): ").strip().lower()
        if email in ("exit", "quit"):
            raise SystemExit("Goodbye!")
        user = backend.get_user(email)
        if user:
            backend.session_email = email
            print(f"Welcome back, {user['name']}!\n")
            break
        print("No account found for that email. Please try again.\n")

    while True:
        user_input = input("Enter your prompt: ")
        if user_input.lower() in ["exit", "quit"]:
            print(agent.routing_summary())
            print("Exiting the agent. Goodbye!")
            break

        try:
            agent.run_turn(user_input, backend)
        except anthropic.APIError as e:
            print(f"\n[!] API error, turn aborted: {e}")
        except Exception as e:
            print(f"\n[!] Turn failed: {type(e).__name__}: {e}")

