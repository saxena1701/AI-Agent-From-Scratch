"""Deterministic checks on the agent's final reply, run before it is returned.

Only failures with an exact, recognizable signature are checked here; anything
that needs judgment belongs in the eval harness, not on the serving path.
"""
import re

# Tool-call markup written as plain text: nothing ran, and the customer would see it.
_FAKE_TOOL_MARKERS = ("<function_calls>", "<invoke name=")

# A bracketed span that isn't a markdown link, e.g. "[return_policy_c0]" or
# "[ch_0042/return_policy, electronics_return_addendum]".
_BRACKET = re.compile(r"\[([^\[\]\n]{1,200})\](?!\()")
_TOKEN_SPLIT = re.compile(r"[\s,/;]+")
# Every chunk_id contains an underscore; order IDs, checkboxes, and prose don't.
_ID_LIKE = re.compile(r"^[A-Za-z][\w.\-]*_[\w.\-]+$")


def has_fake_tool_markup(text: str) -> bool:
    return any(marker in text for marker in _FAKE_TOOL_MARKERS)


def cited_ids(text: str) -> list[str]:
    """Every chunk-ID-like token inside square brackets, in order of appearance."""
    ids = []
    for m in _BRACKET.finditer(text):
        ids.extend(t for t in _TOKEN_SPLIT.split(m.group(1)) if _ID_LIKE.match(t))
    return ids


def invalid_citations(text: str, valid_ids: set[str]) -> list[str]:
    """Cited chunk IDs that never appeared in a retrieve result, sorted and de-duplicated."""
    return sorted({c for c in cited_ids(text) if c not in valid_ids})


def strip_citations(text: str, bad_ids) -> str:
    """Remove the given IDs from bracketed citations, dropping brackets left empty."""
    bad = set(bad_ids)

    def fix(m):
        tokens = [t for t in _TOKEN_SPLIT.split(m.group(1)) if t]
        if not any(t in bad for t in tokens):
            return m.group(0)
        keep = [t for t in tokens if t not in bad and _ID_LIKE.match(t)]
        return f"[{', '.join(keep)}]" if keep else ""

    out = _BRACKET.sub(fix, text)
    # "a sentence [ch_0042]." -> "a sentence ." -> "a sentence."
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", out)
