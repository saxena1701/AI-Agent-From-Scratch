import json
import os
import shutil
import tempfile
from collections import Counter
from time import time

import anthropic
from dotenv import load_dotenv
from agent import Agent
from backend import MarketSphereBackend
load_dotenv()

BASELINE_DB = 'db/marketsphere.db'
EVALUATOR_MODEL = "claude-sonnet-5"
EVALUATOR_MAX_TOKENS = 4000
EVALUATOR_ATTEMPTS = 2  # first call + one retry
VERDICTS = {"pass", "partial", "fail"}

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


def fresh_db_copy(workdir):
    """Copy the baseline DB into workdir so every example starts from the same state.
    The tracked DB is never opened directly, so cancel/return writes can't leak between examples."""
    path = os.path.join(workdir, "marketsphere.db")
    shutil.copyfile(BASELINE_DB, path)
    return path


def run_agent_turn(agent, backend, query, print_debug=False):
    """Run one eval turn through the agent's own run_turn loop and shape the
    result for the evaluator: {"tool_calls": [...], "final_response": "<text>"}."""
    response = agent.run_turn(query, backend, print_debug=print_debug)
    final_response = "".join(
        block.text for block in response.content if block.type == "text"
    )
    return {"tool_calls": agent.last_tool_calls, "final_response": final_response}


def parse_evaluation(text):
    """Pull the verdict object out of the evaluator's reply. Tolerates a code fence or stray
    prose around the JSON; raises ValueError if no object with a valid verdict is found."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    evaluation = json.loads(text[start:end + 1])
    if evaluation.get("verdict") not in VERDICTS:
        raise ValueError(f"unexpected verdict {evaluation.get('verdict')!r}")
    return evaluation


def grade(eval_prompt, evaluation_payload):
    """Ask the evaluator for a verdict, retrying once on API errors, empty replies, or
    unparseable JSON. Returns (evaluation, None) on success or (None, reason) on failure."""
    reason = None
    for _ in range(EVALUATOR_ATTEMPTS):
        try:
            response = evaluator.messages.create(
                model=EVALUATOR_MODEL,
                system=eval_prompt,
                messages=[{"role": "user", "content": json.dumps(evaluation_payload)}],
                max_tokens=EVALUATOR_MAX_TOKENS,
            )
        except anthropic.APIError as e:
            reason = f"evaluator API error: {e}"
            continue
        text_block = next((b for b in response.content if b.type == "text"), None)
        if text_block is None:
            reason = f"evaluator returned no text block (stop_reason={response.stop_reason})"
            continue
        try:
            return parse_evaluation(text_block.text), None
        except ValueError as e:
            reason = f"evaluator reply unparseable: {e} (stop_reason={response.stop_reason})"
    return None, reason


#iterate through eval dataset, for each eval example
    #extract query, and entire eval example
    #Pass query to the agent client and receive response
    #Pass response and eval example to independent claude client
    #Record a verdict of pass / partial / fail, or an error if the example could not be graded

def run_example(eval_row, eval_prompt):
    """Run one example against a fresh DB copy and return its result record."""
    base = {"id": eval_row.get("id"), "query": eval_row["query"]}
    user_session_email = eval_row["session_customer_email"]

    with tempfile.TemporaryDirectory() as workdir:
        backend = MarketSphereBackend(fresh_db_copy(workdir), session_email=user_session_email)
        try:
            if backend.get_user(user_session_email) is None:
                return {**base, "status": "error", "reason": "user not found in DB"}

            agent = Agent()
            try:
                agent_response = run_agent_turn(agent, backend, eval_row["query"], print_debug=False)
            except Exception as e:
                return {**base, "status": "error", "reason": f"agent error ({type(e).__name__}): {e}"}

            evaluation, reason = grade(eval_prompt, {
                "agent_response": agent_response,
                "eval_example": eval_row
            })
            # Kept out of the evaluator payload so the judge grades the reply itself,
            # but recorded so runs can track how often the output checks fire.
            output_checks = agent.last_output_checks
            if evaluation is None:
                return {**base, "status": "error", "reason": reason,
                        "agent_response": agent_response, "output_checks": output_checks}

            return {
                **base,
                "status": "graded",
                "agent_response": agent_response,
                "output_checks": output_checks,
                "expected_tool_calls": eval_row.get("expected_tool_calls"),
                "expected_action": eval_row.get("notes"),
                "ground_truth": eval_row.get("ground_truth"),
                "evaluator_response": evaluation
            }
        finally:
            backend.conn.close()


def run_eval(start_row=1, end_row=None):
    eval_data = load_eval_dataset('eval/eval_set.json')
    eval_prompt = get_eval_prompt()
    eval_result_file = f'eval/results_{int(time())}.jsonl'
    total_rows = len(eval_data)
    end_row = end_row or total_rows
    results = []

    for row_num, eval_row in enumerate(eval_data, start=1):
        if row_num < start_row:
            continue
        if row_num > end_row:
            break

        print(f"Running evaluation on {row_num}/{total_rows} examples...\n")
        entry = run_example(eval_row, eval_prompt)
        results.append(entry)
        with open(eval_result_file, 'a') as f:
            f.write(json.dumps(entry) + "\n")

    return results


def summarize(results):
    counts = Counter(
        r["evaluator_response"]["verdict"] if r["status"] == "graded" else "error"
        for r in results
    )
    print(f"\nTotal: {len(results)}  pass: {counts['pass']}  partial: {counts['partial']}  "
          f"fail: {counts['fail']}  error: {counts['error']}")
    for r in results:
        if r["status"] == "error":
            print(f"  error {r['id']}: {r['reason']}")


if __name__ == "__main__":
    results = run_eval()
    summarize(results)
