import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

class Agent:
    conversation_history = []
    def __init__(self):
        
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        with open('src/prompt/system_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def ask_question(self, question):
        self.conversation_history.append({"role": "user", "content": question})
        with self.client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=1000,
            system=self.system_prompt,
            messages=self.conversation_history
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            print()
            message = stream.get_final_message()

        self.conversation_history.append({"role": "assistant", "content": message.content[0].text})
        return message.content[0].text



agent = Agent()

while(1):
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break
    response = agent.ask_question(user_input)



