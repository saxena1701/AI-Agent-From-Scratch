import json
import os
from datetime import datetime

class SessionLogger:
    def __init__(self, session_start):
        os.makedirs('logs', exist_ok=True)
        self.log_file = f"logs/session_{session_start.strftime('%Y%m%d_%H%M%S')}.jsonl"
    
    def log_turn(self, model, input_tokens, output_tokens, cost, latency,
                 route_reason=None, baseline_model=None, baseline_cost=None):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "latency": latency
        }
        if route_reason is not None:
            log_entry["route_reason"] = route_reason
        if baseline_cost is not None:
            # Estimated: the routed model's token counts repriced at baseline rates.
            log_entry["baseline_model"] = baseline_model
            log_entry["baseline_cost"] = baseline_cost
            log_entry["estimated_saving"] = baseline_cost - cost
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def print_stats(self, input_tokens, output_tokens, turn_cost, session_cost,
                    baseline_turn_cost=None, print_debug=True):
        if not print_debug:
            return
        line = (f"[in: {input_tokens} tok | out: {output_tokens} tok | "
                f"turn: ${turn_cost:.4f} | session: ${session_cost:.4f}")
        if baseline_turn_cost is not None:
            line += (f" | baseline: ${baseline_turn_cost:.4f} "
                     f"(est. {baseline_turn_cost - turn_cost:+.4f})")
        print(line + "]")