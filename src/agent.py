import json

import anthropic
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
from backend import MarketSphereBackend
from tracer import get_tracer
load_dotenv()

class Agent:
    def __init__(self):
        self.conversation_history = []  # Fix 3: instance variable
        self.session_cost = 0.0
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.classifier = IntentClassifierAgent()
        self.tracer = get_tracer()
        self.current_tools = TOOLS  # default until first classification of a turn
        with open('src/prompt/system_prompt.md', 'r') as f:
            self.system_prompt = f.read()

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
                tools=self.current_tools
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
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
        turn_cost = calculate_cost("claude-sonnet-4-6", input_tokens, output_tokens)
        self.session_cost += turn_cost
        self.logger.log_turn("claude-sonnet-4-6", input_tokens, output_tokens, turn_cost, latency)
        self.logger.print_stats(input_tokens, output_tokens, turn_cost, self.session_cost)

        # Fix 4: append the full content (list of blocks), not just .text
        self.conversation_history.append({"role": "assistant", "content": message.content})
        return message  # Fix 1: return the full message object


MAX_TOOL_ITERATIONS = 10

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
        print("Exiting the agent. Goodbye!")
        break

    tracer.begin_turn(user_input)
    try:
        response = agent.ask_question(user_input)
        iterations = 0
        while response.stop_reason == "tool_use":
            if iterations >= MAX_TOOL_ITERATIONS:
                # history ends with an unanswered assistant tool_use block;
                # answer every block with an error so history stays valid for
                # the next user turn, then stop WITHOUT re-calling the model.
                agent.conversation_history.append({
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
                print(f"\n[!] Stopped after {MAX_TOOL_ITERATIONS} tool iterations.")
                break
            iterations += 1

            for block in response.content:
                if block.type == "tool_use":
                    with tracer.span("tool_call", block.name, args=block.input) as span:
                        result = execute_tool(block.name, block.input, backend)
                        span.set_result(result)
                    agent.conversation_history.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        }]
                    })

            response = agent.ask_question()
    except anthropic.APIError as e:
        print(f"\n[!] API error, turn aborted: {e}")
    except Exception as e:
        print(f"\n[!] Turn failed: {type(e).__name__}: {e}")
    finally:
        tracer.end_turn()

