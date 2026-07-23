"""Three-layer accuracy system — the core of this submission.

A. Policy Compliance Judge (LLM, 45%)  — is the offered remedy what the policy
   requires? The judge derives the correct remedy itself from retrieved policy
   text; it is never shown the hand-labeled ground truth (that file is reserved
   for validate_metric.py, which checks the judge's trustworthiness).
B. Alignment Judge (LLM, 25%)          — same meaning/resolution as the reply
   a human actually sent, regardless of wording?
C. Quality rubric (LLM, 30%)           — groundedness, tone/empathy vs the
   customer's sentiment, clarity, actionability.
D. Deterministic checks (no LLM)       — cheap penalty-only guards.
E. Lexical overlap (difflib)           — independent cross-check, reported but
   never blended into the score.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from datetime import date

from src import llm_client, prompts
from src.policy_store import PolicyStore
from src.schema import EvaluationResult, Ticket, Transaction

WEIGHTS = {"policy": 0.45, "alignment": 0.25, "quality": 0.30}
QUALITY_WEIGHTS = {"groundedness": 0.25, "tone_empathy": 0.25, "clarity": 0.20, "actionability": 0.30}

PLACEHOLDER_RE = re.compile(r"\[[A-Z_ ]{2,}\]|\{\{?[a-z_]+\}?\}|<[A-Z_]+>|XXXX+")
ABSOLUTE_PHRASES = [
    "we guarantee", "guaranteed", "100%", "no matter what", "always refund",
    "never fails", "under any circumstances", "in all cases",
]


def deterministic_checks(reply: str, order_id: str, policy_text: str) -> tuple[int, list[str]]:
    """Penalty-only checks. Returns (penalty capped at 20, flags)."""
    penalty, flags = 0, []

    if PLACEHOLDER_RE.search(reply):
        penalty += 8
        flags.append("placeholder_token (-8)")

    n_words = len(reply.split())
    if not 40 <= n_words <= 250:
        penalty += 4
        flags.append(f"length_{n_words}_words_outside_40_250 (-4)")

    # Unqualified absolute claims are only flagged when the policy document
    # itself doesn't use the phrase (e.g. "under any circumstances" is
    # legitimate when quoting a no-exceptions rule).
    lower_reply, lower_policy = reply.lower(), policy_text.lower()
    for phrase in ABSOLUTE_PHRASES:
        if phrase in lower_reply and phrase not in lower_policy:
            penalty += 8
            flags.append(f"absolute_claim:'{phrase}' (-8)")
            break

    if order_id and order_id not in reply:
        penalty += 4
        flags.append("order_id_not_referenced (-4)")

    return min(penalty, 20), flags


def lexical_overlap(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def evaluate_reply(
    ticket: Ticket,
    transaction: Transaction,
    reply: str,
    policy_store: PolicyStore,
    reply_source: str,
) -> EvaluationResult:
    today = os.getenv("EVAL_TODAY", date.today().isoformat())
    # The compliance judge must not miss the decisive rule because retrieval
    # ranked it low, so give it the full document when it's small (most
    # support policies fit comfortably); fall back to top-k for large ones.
    full_policy = policy_store.all_text()
    if len(full_policy) <= 12000:
        chunks_text = full_policy
    else:
        query = f"{ticket.incoming_email}\n{transaction.status} {transaction.product}"
        chunks_text = "\n\n".join(policy_store.retrieve(query, k=6))
    txn_json = json.dumps(transaction.model_dump(), indent=2)

    # --- A. policy compliance judge -------------------------------------
    compliance_raw = llm_client.complete(
        prompts.COMPLIANCE_JUDGE_SYSTEM,
        prompts.COMPLIANCE_JUDGE_USER.format(
            policy_chunks=chunks_text,
            transaction=txn_json,
            today=today,
            email=ticket.incoming_email,
            reply=reply,
        ),
    )
    comp = llm_client.extract_json(compliance_raw)

    # --- B + C. alignment with actual reply + quality rubric (one call) --
    aq_raw = llm_client.complete(
        prompts.ALIGNMENT_QUALITY_SYSTEM.format(sentiment=ticket.sentiment),
        prompts.ALIGNMENT_QUALITY_USER.format(
            email=ticket.incoming_email,
            actual_reply=ticket.actual_reply,
            reply=reply,
        ),
    )
    aq = llm_client.extract_json(aq_raw)

    # --- D. deterministic checks -----------------------------------------
    penalty, flags = deterministic_checks(reply, transaction.order_id, policy_store.all_text())

    # --- E. lexical overlap (reported, not blended) -----------------------
    overlap = lexical_overlap(reply, ticket.actual_reply)

    # --- blend -------------------------------------------------------------
    def clamp(v) -> int:
        return max(1, min(5, int(v)))

    compliance = clamp(comp.get("match_score", 1))
    alignment = clamp(aq.get("alignment", 1))
    quality_subscores = {k: clamp(aq.get(k, 1)) for k in QUALITY_WEIGHTS}

    policy_score = compliance / 5 * 100
    alignment_score = alignment / 5 * 100
    quality_score = (
        sum(quality_subscores[k] * w for k, w in QUALITY_WEIGHTS.items()) / 5 * 100
    )
    final = max(
        0.0,
        WEIGHTS["policy"] * policy_score
        + WEIGHTS["alignment"] * alignment_score
        + WEIGHTS["quality"] * quality_score
        - penalty,
    )

    return EvaluationResult(
        ticket_id=ticket.ticket_id,
        reply_source=reply_source,
        category=ticket.category,
        judge_policy_requires=str(comp.get("policy_requires", "")),
        judge_cited_rule=str(comp.get("rule", "")),
        judge_reply_offers=str(comp.get("reply_offers", "")),
        compliance_match_score=compliance,
        compliance_justification=str(comp.get("justification", "")),
        escalate=bool(comp.get("escalate", False)),
        escalate_reason=str(comp.get("escalate_reason", "")),
        alignment=alignment,
        alignment_justification=str(aq.get("alignment_justification", "")),
        groundedness=quality_subscores["groundedness"],
        tone_empathy=quality_subscores["tone_empathy"],
        clarity=quality_subscores["clarity"],
        actionability=quality_subscores["actionability"],
        quality_justification=str(aq.get("quality_justification", "")),
        deterministic_penalty=penalty,
        flags=flags,
        lexical_overlap=round(overlap, 4),
        policy_score=round(policy_score, 1),
        alignment_score=round(alignment_score, 1),
        quality_score=round(quality_score, 1),
        final_score=round(final, 1),
    )
