import time
import uuid

_turn_id = None
_depth = 0


class Tracer:
    def begin_turn(self, query: str) -> None:
        global _turn_id, _depth
        _turn_id = uuid.uuid4().hex[:6]
        _depth = 0
        self._start = time.time()
        print(f"\n=== [TURN {_turn_id}] {query!r} ===")

    def end_turn(self) -> None:
        elapsed_ms = (time.time() - self._start) * 1000
        print(f"=== [TURN {_turn_id}] done in {elapsed_ms:.0f}ms ===\n")

    def span(self, kind: str, name: str, **fields):
        return _Span(kind, name, fields)


class _Span:
    def __init__(self, kind, name, fields):
        self.kind, self.name, self.fields = kind, name, fields
        self.result = None

    def set_result(self, result) -> None:
        self.result = result

    def __enter__(self):
        global _depth
        self.depth = _depth
        _depth += 1
        indent = "  " * self.depth
        extra = " ".join(f"{k}={v!r}" for k, v in self.fields.items())
        print(f"{indent}-> [{self.kind}] {self.name} {extra}".rstrip())
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _depth
        _depth = self.depth
        elapsed_ms = (time.time() - self.start) * 1000
        indent = "  " * self.depth
        if exc_val is not None:
            print(f"{indent}x [{self.kind}] {self.name} FAILED ({elapsed_ms:.0f}ms): {exc_val}")
            return False
        result_str = str(self.result) if self.result is not None else ""
        if len(result_str) > 150:
            result_str = result_str[:150] + "...(truncated)"
        print(f"{indent}<- [{self.kind}] {self.name} ({elapsed_ms:.0f}ms) {result_str}".rstrip())
        return False


_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer
