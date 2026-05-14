import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

class Agent:
    
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": input("Ask the agent a question: ")
            }
        ],
    )
    print(message.content[0].text)