"""Single pluggable LLM interface used by generation, classification, and judges.

Provider is selected with env vars (Settings writes these into .env):
  - LLM_PROVIDER / LLM_MODEL                 → email generation (+ evaluation judges)
  - CLASSIFY_LLM_PROVIDER / CLASSIFY_LLM_MODEL → email categorization
    (falls back to LLM_* when unset)

Providers:
  - "anthropic" (default) -> Claude via the official anthropic SDK
  - "openai"              -> OpenAI via the official openai SDK
  - "mistral"             -> Mistral via its OpenAI-compatible endpoint
                             (free tier friendly: throttled to ~1 req/s)
  - "mock"                -> deterministic offline stub, used only to smoke-test
                             the pipeline plumbing without an API key. Not part
                             of the graded flow; results are meaningless.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "mistral": "mistral-small-latest",
}

_last_mistral_call = 0.0


def resolve_provider_model(purpose: str = "generate") -> tuple[str, str]:
    """Return (provider, model) for a pipeline step. Classify falls back to generate."""
    if purpose == "classify":
        provider = (os.getenv("CLASSIFY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "anthropic").lower()
        if os.getenv("CLASSIFY_LLM_MODEL"):
            model = os.environ["CLASSIFY_LLM_MODEL"]
        elif os.getenv("CLASSIFY_LLM_PROVIDER"):
            # Different provider chosen for classify — use that provider's default,
            # not a generate-model that may belong to another vendor.
            model = DEFAULT_MODELS.get(provider, "")
        else:
            model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, "")
        return provider, model

    provider = (os.getenv("LLM_PROVIDER") or "anthropic").lower()
    model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, "")
    return provider, model


def complete(
    system: str,
    user: str,
    max_tokens: int = 1200,
    *,
    purpose: str = "generate",
) -> str:
    """One-shot completion. Same signature for every provider.

    purpose="generate" (default) uses LLM_PROVIDER / LLM_MODEL.
    purpose="classify" uses CLASSIFY_LLM_PROVIDER / CLASSIFY_LLM_MODEL
    (falling back to the generate pair when unset).
    """
    provider, model = resolve_provider_model(purpose)

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

    if provider in ("openai", "mistral"):
        from openai import OpenAI

        if provider == "mistral":
            # Mistral's API is OpenAI-compatible; free tier is rate-limited to
            # ~1 request/second, so throttle and retry generously.
            global _last_mistral_call
            wait = 1.1 - (time.monotonic() - _last_mistral_call)
            if wait > 0:
                time.sleep(wait)
            _last_mistral_call = time.monotonic()
            client = OpenAI(
                base_url="https://api.mistral.ai/v1",
                api_key=os.environ["MISTRAL_API_KEY"],
                max_retries=8,
            )
        else:
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

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (use anthropic|openai|mistral)")


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
    sys_l = system.lower()
    # Classifier prompt — return a category JSON so routing self-checks work offline.
    if "categor" in sys_l and "refund" in sys_l:
        low = user.lower()
        if any(h in low for h in ("unsubscribe", "newsletter", "promotion", "sale")):
            cat = "other"
        elif any(h in low for h in ("cancel",)):
            cat = "cancellation"
        elif any(h in low for h in ("charge", "billing", "invoice", "duplicate")):
            cat = "billing"
        elif any(h in low for h in ("refund", "return", "money back")):
            cat = "refund"
        elif any(h in low for h in ("broken", "defect", "warranty", "not working", "error")):
            cat = "technical_support"
        elif any(h in low for h in ("complain", "unacceptable", "furious", "terrible", "angry")):
            cat = "complain"
        elif any(h in low for h in ("help", "question", "how do", "wondering")):
            cat = "general_inquiry"
        else:
            cat = "general_inquiry"
        return json.dumps({"category": cat})
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
            "escalate": False,
            "escalate_reason": "",
        })
    return (
        "Hi, thanks for reaching out about your order. Based on our policy I've "
        "reviewed your request and here is the outcome, along with the next steps "
        "we will take to resolve it. (mock reply generated offline without an API "
        "key; set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env for real output) "
        "— NorthPeak Support"
    )
