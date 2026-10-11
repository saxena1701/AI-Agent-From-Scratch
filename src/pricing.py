PRICING = {
    "claude-opus-5": {
        "input": 0.000005,      # $5 per 1M input tokens
        "output": 0.000025,     # $25 per 1M output tokens
    },
    "claude-sonnet-5": {
        "input": 0.000002,      # $2 per 1M input tokens
        "output": 0.000010,     # $10 per 1M output tokens
    },
    "claude-sonnet-4-6": {
        "input": 0.000003,      # $3 per 1M input tokens
        "output": 0.000015,     # $15 per 1M output tokens
    },
    "claude-haiku-4-5": {
        "input": 0.000001,      # $1 per 1M input tokens
        "output": 0.000005,     # $5 per 1M output tokens
    }
}

def calculate_cost(model, input_tokens, output_tokens):
    prices = PRICING.get(model)
    if prices is None:
        raise ValueError(
            f"No pricing entry for model {model!r}. Known models: {sorted(PRICING)}"
        )
    input_cost = input_tokens * prices["input"]
    output_cost = output_tokens * prices["output"]
    return input_cost + output_cost
