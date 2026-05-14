import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

class Agent:

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def ask_question(self, question):
        message = self.client.messages.create(
            model="claude-opus-4-7",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": question
                }
        ],
    )
        return message.content[0].text  


agent = Agent()
answer = agent.ask_question(input("Ask the agent a question: "));
print("Agent response: ", answer)



