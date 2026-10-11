import json
from time import time

import anthropic
import os
from dotenv import load_dotenv
from agent import Agent
from backend import MarketSphereBackend
load_dotenv()

#define claude client
evaluator = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


#load the eval dataset
def load_eval_dataset(file_path):
    with open(file_path, 'r') as f:
        eval_data = json.load(f)
    return eval_data


#get eval prompt
def get_eval_prompt():
    with open('src/prompt/evaluator_prompt.md', 'r') as f:
        eval_prompt = f.read()
    return eval_prompt


def run_agent_turn(agent, backend, query, print_debug=False):
    """Run one eval turn through the agent's own run_turn loop and shape the
    result for the evaluator: {"tool_calls": [...], "final_response": "<text>"}."""
    response = agent.run_turn(query, backend, print_debug=print_debug)
    final_response = "".join(
        block.text for block in response.content if block.type == "text"
    )
    return {"tool_calls": agent.last_tool_calls, "final_response": final_response}


#iterate through eval dataset, for each eval example
    #extract query, and entire eval example
    #Pass query to the agent client and receive response
    #Pass response and eval example to independent claude client
    #Generate a score using binary values of 0 or 1 as output, where 1 indicates that the response is correct and 0 indicates that the response is incorrect.

def run_eval(start_row=100, end_row=125):
    eval_data = load_eval_dataset('eval/eval_set.json')
    eval_prompt = get_eval_prompt()
    eval_result_file = f'eval/results_{int(time())}.jsonl'
    total_rows = len(eval_data)
    results = []

    def record(entry):
        results.append(entry)
        with open(eval_result_file, 'a') as f:
            f.write(json.dumps(entry) + "\n")

    for row_num, eval_row in enumerate(eval_data, start=1):
        if row_num < start_row:
            continue
        if row_num > end_row:
            break

        print(f"Running evaluation on {row_num}/{total_rows} examples...\n")
        query = eval_row["query"]
        user_session_email = eval_row["session_customer_email"]
        backend = MarketSphereBackend('db/marketsphere.db', session_email=user_session_email)
        user = backend.get_user(user_session_email)
        if user is None:
            record({"id": eval_row.get("id"), "query": query, "score": 0, "reason": "User not found"})
            continue

        agent = Agent()
        try:
            agent_response = run_agent_turn(agent, backend, query, print_debug=False)
        except anthropic.APIError as e:
            record({"id": eval_row.get("id"), "query": query, "score": 0, "reason": f"API error: {e}"})
            continue

        evaluation_payload = {
            "agent_response": agent_response,
            "eval_example": eval_row
        }

        evaluator_response = evaluator.messages.create(
            model="claude-sonnet-5",
            system=eval_prompt,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(evaluation_payload)
                }
            ],
            max_tokens=1000
        )

        text_block = next((b for b in evaluator_response.content if b.type == "text"), None)
        if text_block is None:
            record({"id": eval_row.get("id"), "query": query, "score": 0, "reason": "Evaluator returned no text block"})
            continue
        result_text = text_block.text.strip()
        if result_text.startswith("```"):
            result_text = result_text.strip("`")
            result_text = result_text[len("json"):] if result_text.startswith("json") else result_text
        try:
            evaluation = json.loads(result_text)
        except json.JSONDecodeError as e:
            record({"id": eval_row.get("id"), "query": query, "score": 0, "reason": f"Evaluator returned invalid JSON: {e}"})
            continue

        record({
            "id": eval_row.get("id"),
            "query": query,
            "agent_response": agent_response,
            "expected_tool_calls": eval_row.get("expected_tool_calls"),
            "expected_action": eval_row.get("notes"),
            "ground_truth": eval_row.get("ground_truth"),
            "evaluator_response": evaluation
        })

    return results


if __name__ == "__main__":
    results = run_eval()
    passed = sum(1 for r in results if r.get("evaluator_response", {}).get("verdict") == "pass")
    print(f"\n{passed}/{len(results)} passed the evaluation.")
