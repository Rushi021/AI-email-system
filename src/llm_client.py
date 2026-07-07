"""Single pluggable LLM interface used by both the generator and every judge.

Provider is selected with the LLM_PROVIDER env var:
  - "anthropic" (default) -> Claude via the official anthropic SDK
  - "openai"              -> OpenAI via the official openai SDK
  - "mock"                -> deterministic offline stub, used only to smoke-test
                             the pipeline plumbing without an API key. Not part
                             of the graded flow; results are meaningless.

Model can be overridden with LLM_MODEL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
}


def complete(system: str, user: str, max_tokens: int = 1200) -> str:
    """One-shot completion. Same signature for every provider."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    model = os.getenv("LLM_MODEL", DEFAULT_MODELS.get(provider, ""))

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI()
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    if provider == "mock":
        return _mock_complete(system, user)

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (use anthropic|openai)")


def extract_json(text: str) -> dict:
    """Robustly pull the first JSON object out of an LLM response
    (handles markdown fences and surrounding prose)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in LLM output: {text[:200]}")
    # walk to the matching closing brace
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]}")


def _mock_complete(system: str, user: str) -> str:
    """Deterministic stub for offline plumbing tests only."""
    seed = int(hashlib.sha256(user.encode()).hexdigest(), 16)
    if "JSON" in system or "json" in system:
        score = 2 + seed % 4  # 2-5, deterministic per input
        return json.dumps({
            "policy_requires": "mock remedy derived offline",
            "rule": "R0",
            "reply_offers": "mock reply summary",
            "match_score": score,
            "justification": "mock judge output (no API key configured)",
            "alignment": score,
            "alignment_justification": "mock",
            "groundedness": score,
            "tone_empathy": score,
            "clarity": score,
            "actionability": score,
            "quality_justification": "mock",
        })
    return (
        "Hi, thanks for reaching out about your order. Based on our policy I've "
        "reviewed your request and here is the outcome, along with the next steps "
        "we will take to resolve it. (mock reply generated offline without an API "
        "key; set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env for real output) "
        "— NorthPeak Support"
    )
