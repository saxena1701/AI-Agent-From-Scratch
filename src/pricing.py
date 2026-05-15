PRICING = {
    "claude-sonnet-4-6": {
        "input": 0.000003,      # $3 per 1M input tokens
        "output": 0.000015,     # $15 per 1M output tokens
    }
}

def calculate_cost(model, input_tokens, output_tokens):
    prices = PRICING.get(model)
    input_cost = input_tokens * prices["input"]
    output_cost = output_tokens * prices["output"]
    return input_cost + output_cost