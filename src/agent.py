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
from backend import MarketSphereBackend
load_dotenv()

class Agent:
    def __init__(self):
        self.conversation_history = []  # Fix 3: instance variable
        self.session_cost = 0.0
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.classifier = IntentClassifierAgent()
        with open('src/prompt/system_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def ask_question(self, question=None, tools=None):
        start_time = time.time()
        if question is not None:
            self.conversation_history.append({"role": "user", "content": question})
            self.classifier.classify_intent(question)

        with self.client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=self.system_prompt,
            messages=self.conversation_history,
            tools=tools
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            print()
            message = stream.get_final_message()

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


agent = Agent()
backend = MarketSphereBackend('db/marketsphere.db')

while True:
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break

    response = agent.ask_question(user_input, tools=TOOLS)
    print(response)
    while response.stop_reason == "tool_use":  # Fix 1: now checking the actual message
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, backend)
                agent.conversation_history.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    }]
                })

        response = agent.ask_question(tools=TOOLS)

