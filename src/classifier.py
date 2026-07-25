"""Classification stage: turn parsed email text into one structured signal —
category, noise/spam gate, frustration — before context lookup and generation
ever run. Cheap and deterministic (keyword/regex only, zero LLM calls) so the
noise gate can drop non-support mail before spending a single model call.

This used to be three private helpers inline in src.router. Pulled out here so
it's a distinct, independently testable pipeline stage — matching the
Classify step in the architecture — rather than routing logic and signal
detection living in the same function. src.router now just calls classify().

Upgrade path: swap the keyword heuristics below for a small/cheap model
(README §8 roadmap item 2) without router.py or anything downstream noticing —
they only depend on the ClassificationResult shape.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

_CATEGORY_HINTS = {
    "return": ["return", "refund", "send back", "money back"],
    "shipping": ["ship", "deliver", "package", "tracking", "arrive", "lost", "missing"],
    "warranty": ["warranty", "defect", "broke", "broken", "stopped working", "faulty"],
    "billing": ["charge", "charged", "billing", "invoice", "duplicate", "double"],
    "cancellation": ["cancel", "cancellation"],
}
_NOISE_HINTS = ["unsubscribe", "newsletter", "promotion", "no-reply", "noreply", "view in browser"]
_SUPPORT_HINTS = sum(_CATEGORY_HINTS.values(), []) + ["order", "help", "issue", "problem", "return"]
_FRUSTRATED_RE = re.compile(
    r"!!!|unacceptable|furious|ridiculous|outrage|terrible|angry|worst|asap|immediately|right now",
    re.IGNORECASE,
)


class ClassificationResult(BaseModel):
    category: str
    is_noise: bool
    frustrated: bool


def classify_category(text: str) -> str:
    low = text.lower()
    for cat, hints in _CATEGORY_HINTS.items():
        if any(h in low for h in hints):
            return cat
    return "other"


def is_noise(text: str, has_order: bool) -> bool:
    """Cheap noise gate so we don't spend LLM calls on non-support mail."""
    if has_order:
        return False
    low = text.lower()
    if any(h in low for h in _NOISE_HINTS):
        return True
    return not any(h in low for h in _SUPPORT_HINTS)


def is_frustrated(text: str) -> bool:
    return bool(_FRUSTRATED_RE.search(text))


def classify(text: str, has_order: bool) -> ClassificationResult:
    return ClassificationResult(
        category=classify_category(text),
        is_noise=is_noise(text, has_order),
        frustrated=is_frustrated(text),
    )


def _demo() -> None:
    """Offline self-check — no LLM calls (moved from src.router's self-check)."""
    assert classify_category("I want a refund for my order") == "return"
    assert classify_category("where is my package") == "shipping"
    assert is_noise("Big summer sale! unsubscribe here", has_order=False)
    assert not is_noise("please help with my return, order broke", has_order=False)
    assert not is_noise("hi", has_order=True)  # a known order is always actionable
    assert is_frustrated("this is UNACCEPTABLE, I need this fixed ASAP")
    assert not is_frustrated("could you help when you get a chance")

    r = classify("I want a refund for my order, this is ridiculous", has_order=True)
    assert r.category == "return" and not r.is_noise and r.frustrated
    print("classifier self-check OK")


if __name__ == "__main__":
    _demo()
