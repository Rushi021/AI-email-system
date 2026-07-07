"""Shared data models. Everything here is company-agnostic: the fields describe
generic e-commerce support concepts (orders, tickets, replies, scores), not any
specific company's policy."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    """A generic customer transaction record. Any company's order export can be
    mapped onto this shape. Extra fields are allowed so a different company's
    export can carry additional columns without code changes."""

    model_config = {"extra": "allow"}

    order_id: str
    customer_id: str
    product: str
    price: float
    order_date: str
    delivery_date: Optional[str] = None
    # promised_delivery_date makes shipping-delay rules verifiable from data alone
    promised_delivery_date: Optional[str] = None
    status: str  # e.g. delivered, in_transit, lost_carrier_confirmed, processing...
    final_sale: bool = False
    returns_last_90_days: int = 0


class Ticket(BaseModel):
    """One historical support interaction: the incoming email and the reply a
    human agent actually sent."""

    ticket_id: str
    order_id: str
    category: str
    split: str  # "corpus" (retrieval pool) or "holdout" (test set)
    sentiment: str = "neutral"  # frustrated / neutral / polite ...
    incoming_email: str
    actual_reply: str


class GeneratedReply(BaseModel):
    ticket_id: str
    reply: str
    retrieved_policy_chunks: list[str] = Field(default_factory=list)
    retrieved_similar_tickets: list[str] = Field(default_factory=list)  # ticket_ids


class EvaluationResult(BaseModel):
    """Every sub-score and justification for one scored reply."""

    ticket_id: str
    reply_source: str  # "generated" or "control"
    category: str

    # A. policy compliance judge
    judge_policy_requires: str = ""
    judge_cited_rule: str = ""
    judge_reply_offers: str = ""
    compliance_match_score: int = 0  # 1-5
    compliance_justification: str = ""

    # B. alignment with actual reply
    alignment: int = 0  # 1-5
    alignment_justification: str = ""

    # C. quality rubric (1-5 each)
    groundedness: int = 0
    tone_empathy: int = 0
    clarity: int = 0
    actionability: int = 0
    quality_justification: str = ""

    # D. deterministic checks
    deterministic_penalty: int = 0
    flags: list[str] = Field(default_factory=list)

    # E. lexical overlap with actual reply (reported, not blended)
    lexical_overlap: float = 0.0

    # blended scores (0-100)
    policy_score: float = 0.0
    alignment_score: float = 0.0
    quality_score: float = 0.0
    final_score: float = 0.0
