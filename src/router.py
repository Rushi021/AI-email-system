"""Routing engine: turn one incoming email into a decision.

Reuses the existing RAG generator and 3-layer evaluator unchanged, then routes:

  AUTO      confident + clean + unambiguous, and policy does NOT mandate a human
  REVIEW    mid-confidence -> queue a suggested reply for a human to approve/edit
  ESCALATE  policy mandates a human (judge's escalate flag) OR confidence too low
  IGNORE    no order id and the message isn't actionable (newsletter / noise)

Company-agnostic: the escalate decision comes from the compliance judge reading
the policy (src/evaluator + prompts), never from a hardcoded rule id here.
"""

from __future__ import annotations

import re

from src.config import DEFAULTS
from src.evaluator import evaluate_reply
from src.generator import generate_reply
from src.policy_store import PolicyStore
from src.queue_store import DECISION_WEIGHT
from src.retriever import TicketRetriever
from src.schema import IncomingEmail, Ticket, detect_order_id, placeholder_transaction

# cheap, generic category hints (display + future per-category thresholds)
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


def _classify(text: str) -> str:
    low = text.lower()
    for cat, hints in _CATEGORY_HINTS.items():
        if any(h in low for h in hints):
            return cat
    return "other"


def _is_noise(text: str, has_order: bool) -> bool:
    """Cheap noise gate so we don't spend LLM calls on non-support mail."""
    if has_order:
        return False
    low = text.lower()
    if any(h in low for h in _NOISE_HINTS):
        return True
    return not any(h in low for h in _SUPPORT_HINTS)


def _decide(
    confidence: float,
    flags: list[str],
    cited_rule: str,
    escalate: bool,
    t1: float,
    t2: float,
) -> str:
    """Pure decision boundary — tested directly in _demo(), no LLM involved."""
    if escalate or confidence < t2:
        return "escalate"
    if confidence >= t1 and not flags and cited_rule.strip():
        return "auto"
    return "review"


def _priority(decision: str, price: float, frustrated: bool) -> float:
    # decision dominates (escalate > review > auto); value and frustration
    # only re-order within a tier, so the band gap (100) is never crossed.
    value = min(price / 10.0, 30.0)
    return round(DECISION_WEIGHT[decision] + value + (20.0 if frustrated else 0.0), 1)


def route_email(
    email: IncomingEmail,
    transactions: dict,
    policy_store: PolicyStore,
    retriever: TicketRetriever,
    config: dict | None = None,
) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    t1, t2 = float(cfg["t1"]), float(cfg["t2"])
    text = f"{email.subject}\n{email.body}".strip()

    order_id = detect_order_id(text, transactions)
    txn = transactions[order_id] if order_id else placeholder_transaction()
    category = _classify(text)
    frustrated = bool(_FRUSTRATED_RE.search(text))

    base = {
        "email_id": email.id,
        "thread_id": email.thread_id,
        "from_addr": email.from_addr,
        "subject": email.subject,
        "body": email.body,
        "order_id": order_id or "",
        "category": category,
    }

    if _is_noise(text, has_order=bool(order_id)):
        return {**base, "decision": "ignore", "status": "dismissed", "confidence": 0.0,
                "priority": _priority("ignore", txn.price, frustrated),
                "suggested_reply": "", "judge": {}, "flags": []}

    gen = generate_reply(text, txn, policy_store, retriever)
    live = Ticket(
        ticket_id=email.id,
        order_id=txn.order_id,
        category=category,
        split="holdout",
        sentiment="frustrated" if frustrated else "neutral",
        incoming_email=text,
        actual_reply=gen.reply,  # no human reply for live mail; alignment is ignored below
    )
    ev = evaluate_reply(live, txn, gen.reply, policy_store, "generated")

    # Live confidence drops alignment (no human ground truth exists) and
    # renormalizes over policy compliance + quality, minus deterministic penalty.
    confidence = round(
        max(0.0, min(100.0, 0.6 * ev.policy_score + 0.4 * ev.quality_score - ev.deterministic_penalty)),
        1,
    )
    decision = _decide(confidence, ev.flags, ev.judge_cited_rule, ev.escalate, t1, t2)

    return {
        **base,
        "decision": decision,
        "status": "pending",
        "confidence": confidence,
        "priority": _priority(decision, txn.price, frustrated),
        "suggested_reply": gen.reply,
        "judge": {
            "policy_requires": ev.judge_policy_requires,
            "cited_rule": ev.judge_cited_rule,
            "reply_offers": ev.judge_reply_offers,
            "compliance_score": ev.compliance_match_score,
            "escalate": ev.escalate,
            "escalate_reason": ev.escalate_reason,
            "quality": {
                "groundedness": ev.groundedness,
                "tone_empathy": ev.tone_empathy,
                "clarity": ev.clarity,
                "actionability": ev.actionability,
            },
            "compliance_justification": ev.compliance_justification,
        },
        "flags": ev.flags,
        "retrieved_policy_chunks": gen.retrieved_policy_chunks,
        "retrieved_similar_tickets": gen.retrieved_similar_tickets,
    }


def _demo() -> None:
    """Offline self-check of the decision boundary and helpers — no LLM calls."""
    # escalate wins regardless of confidence
    assert _decide(95, [], "R1.1", True, 80, 50) == "escalate"
    assert _decide(30, [], "R1.1", False, 80, 50) == "escalate"  # below t2
    # clean + confident + a cited rule -> auto
    assert _decide(85, [], "R1.1", False, 80, 50) == "auto"
    # a deterministic flag blocks auto -> review
    assert _decide(85, ["order_id_not_referenced (-4)"], "R1.1", False, 80, 50) == "review"
    # confident but no rule cited -> review (ambiguous)
    assert _decide(85, [], "", False, 80, 50) == "review"
    # mid confidence -> review
    assert _decide(65, [], "R1.1", False, 80, 50) == "review"

    assert _classify("I want a refund for my order") == "return"
    assert _classify("where is my package") == "shipping"
    assert _is_noise("Big summer sale! unsubscribe here", has_order=False)
    assert not _is_noise("please help with my return, order broke", has_order=False)
    assert not _is_noise("hi", has_order=True)  # a known order is always actionable

    # priority stays inside decision bands
    assert _priority("escalate", 249, True) > _priority("review", 999, True)
    assert _priority("review", 10, False) > _priority("auto", 999, True)
    print("router self-check OK")


if __name__ == "__main__":
    _demo()
