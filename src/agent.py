import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

class Agent:
    conversation_history = []
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def ask_question(self, question):
        self.conversation_history.append({"role": "user", "content": question})
        message = self.client.messages.create(
            model="claude-opus-4-7",
            max_tokens=1000,
            messages=self.conversation_history
        )
        agent_response = message.content[0].text
        self.conversation_history.append({"role": "assistant", "content": agent_response})   
        return agent_response


agent = Agent()

while(1):
    user_input = input("Enter your prompt: ")
    if user_input.lower() in ["exit", "quit"]:
        print("Exiting the agent. Goodbye!")
        break
    response = agent.ask_question(user_input)
    print("Agent's response:", response)



