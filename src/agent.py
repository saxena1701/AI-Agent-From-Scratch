import anthropic
import os
from dotenv import load_dotenv
from logger import SessionLogger
from pricing import calculate_cost
from datetime import datetime
import time
load_dotenv()

class Agent:
    conversation_history = []
    session_cost = 0.0
    def __init__(self):
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        with open('src/prompt/system_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def ask_question(self, question):
        start_time = time.time()
        self.conversation_history.append({"role": "user", "content": question})
        with self.client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=self.system_prompt,
            messages=self.conversation_history
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
        
        self.conversation_history.append({"role": "assistant", "content": message.content[0].text})
        return message.content[0].text



agent = Agent()

while(1):
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break
    response = agent.ask_question(user_input)



