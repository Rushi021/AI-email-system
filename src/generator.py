"""Reply generator: RAG over the policy PDF + similar past tickets.

For a new email it retrieves (a) the most relevant policy clauses and (b) the
most similar historical tickets, then asks the LLM to draft a reply grounded
in both. See README for why this beats fine-tuning or zero-shot here.
"""

from __future__ import annotations

import json

from src import llm_client, prompts
from src.policy_store import PolicyStore
from src.retriever import TicketRetriever
from src.schema import GeneratedReply, Transaction


def generate_reply(
    email: str,
    transaction: Transaction,
    policy_store: PolicyStore,
    retriever: TicketRetriever,
    ticket_id: str = "",
    k_policy: int = 4,
    k_tickets: int = 3,
) -> GeneratedReply:
    # Retrieve on email + transaction status so policy clauses about e.g.
    # shipping states are found even when the email doesn't use policy wording.
    query = f"{email}\n{transaction.status} {transaction.product}"
    policy_chunks = policy_store.retrieve(query, k=k_policy)
    similar = retriever.top_k(email, k=k_tickets)

    examples = "\n\n".join(
        f"### Past ticket {t.ticket_id}\nCustomer: {t.incoming_email}\nAgent reply: {t.actual_reply}"
        for t in similar
    )
    user_prompt = prompts.GENERATOR_USER.format(
        policy_chunks="\n\n".join(policy_chunks),
        transaction=json.dumps(transaction.model_dump(), indent=2),
        examples=examples,
        email=email,
    )
    reply = llm_client.complete(prompts.GENERATOR_SYSTEM, user_prompt).strip()

    return GeneratedReply(
        ticket_id=ticket_id,
        reply=reply,
        retrieved_policy_chunks=policy_chunks,
        retrieved_similar_tickets=[t.ticket_id for t in similar],
    )
