import anthropic
import os
from dotenv import load_dotenv
from logger import SessionLogger
from pricing import calculate_cost
from datetime import datetime
import json
import time
import re
load_dotenv()

class IntentClassifierAgent:
    session_cost = 0.0
    def __init__(self):
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        with open('src/prompt/intent_classifier_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def ask_question(self, question):
        start_time = time.time()
        with self.client.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=1000,
            system=self.system_prompt,
            messages=[{"role": "user", "content": question}]
        ) as stream:
            message = stream.get_final_message()

        response_text = message.content[0].text
        try:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip())
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            result = {"intent": "off_topic", "reasoning": "Failed to parse response"}
        
        latency = time.time() - start_time
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        turn_cost = calculate_cost("claude-haiku-4-5", input_tokens, output_tokens)
        self.session_cost += turn_cost

        self.logger.log_turn("claude-haiku-4-5", input_tokens, output_tokens, turn_cost, latency)
        self.logger.print_stats(input_tokens, output_tokens, turn_cost, self.session_cost)
        
        return result

classifierAgent = IntentClassifierAgent()

while(1):
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break
    response = classifierAgent.ask_question(user_input)
    print(response)