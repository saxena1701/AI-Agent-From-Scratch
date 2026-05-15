import json
import os
from datetime import datetime

class SessionLogger:
    def __init__(self, session_start):
        os.makedirs('logs', exist_ok=True)
        self.log_file = f"logs/session_{session_start.strftime('%Y%m%d_%H%M%S')}.jsonl"
    
    def log_turn(self, model, input_tokens, output_tokens, cost, latency):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "latency": latency
        }
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    
    def print_stats(self, input_tokens, output_tokens, turn_cost, session_cost):
        print(f"[in: {input_tokens} tok | out: {output_tokens} tok | turn: ${turn_cost:.4f} | session: ${session_cost:.4f}]")