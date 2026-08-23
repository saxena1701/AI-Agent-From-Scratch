from typing import List, Literal

import anthropic
import os
from dotenv import load_dotenv
from logger import SessionLogger
from pricing import calculate_cost
from datetime import datetime
import json
import time
from pydantic import BaseModel, Field, ValidationError
import re
from tracer import get_tracer
load_dotenv()




class IntentClassification(BaseModel):
    intent: Literal[
        "order_status",
        "product_question",
        "return_request",
        "general_support",
        "off_topic",
    ]
    confidence: float = Field(ge=0.0, le=1.0, description="How confident the classification is")
    reasoning: str = Field(description="One sentence explaining the chosen intent")

class IntentClassifierAgent:
    session_cost = 0.0

    def __init__(self):
        self.session_start = datetime.now()
        self.logger = SessionLogger(self.session_start)
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.tracer = get_tracer()
        with open('src/prompt/intent_classifier_prompt.md', 'r') as f:
            self.system_prompt = f.read()

    def _parse_response(self, response_text: str) -> List[IntentClassification]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip())
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            parsed = [parsed]

        return [IntentClassification.model_validate(item) for item in parsed]

    def _safe_fallback(self) -> List[IntentClassification]:
        return [
            IntentClassification(
                intent="general_support",
                confidence=0.0,
                reasoning="Safe fallback due to repeated validation failure."
            )
        ]

    def classify_intent(self, question: str) -> List[IntentClassification]:
        start_time = time.time()
        parser_error = None
        response_text = ""
        results: List[IntentClassification] = []

        with self.tracer.span("llm_call", "intent-classifier") as span:
            for attempt in range(2):
                prompt = question
                if parser_error:
                    prompt += (
                        "\n\nCorrection hint: The previous response failed validation with error: "
                        f"{parser_error}. Please return only valid JSON that matches the intent schema."
                    )

                with self.client.messages.stream(
                    model="claude-haiku-4-5",
                    max_tokens=1000,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}]
                ) as stream:
                    message = stream.get_final_message()

                response_text = message.content[0].text
                try:
                    results = self._parse_response(response_text)
                    break
                except (json.JSONDecodeError, ValidationError) as exc:
                    parser_error = str(exc)
                    if attempt == 1:
                        results = self._safe_fallback()

            span.set_result([
                {"intent": r.intent, "confidence": r.confidence, "reasoning": r.reasoning}
                for r in results
            ])

        latency = time.time() - start_time
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        turn_cost = calculate_cost("claude-haiku-4-5", input_tokens, output_tokens)
        self.session_cost += turn_cost

        self.logger.log_turn("claude-haiku-4-5", input_tokens, output_tokens, turn_cost, latency)
        self.logger.print_stats(input_tokens, output_tokens, turn_cost, self.session_cost)
        print("\n[Intent Classification]")
        for r in results:
            print(f"  {r.intent} ({r.confidence:.0%}) — {r.reasoning}")
        print()
        return results
