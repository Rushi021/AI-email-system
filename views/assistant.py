"""Assistant — paste a customer email, get a suggested reply + on-demand RAGAS."""

from __future__ import annotations

import streamlit as st

from src.evaluator import evaluate_generated
from src.generator import generate_reply
from src.schema import Ticket
from views.common import detect_order_id, load_everything, placeholder_transaction

NO_ORDER = "Continue without a transaction record"

transactions, tickets, policy_store, retriever = load_everything()

st.title("✉️ Reply Assistant")
st.caption("Paste an incoming customer email and get a suggested reply, grounded in your policy and past tickets.")

email = st.text_area(
    "Incoming customer email",
    height=180,
    placeholder="Paste the customer's email here...",
    key="assistant_email",
)

txn = None
if email.strip():
    detected = detect_order_id(email, transactions)
    if detected:
        txn = transactions[detected]
        st.caption(f"Order **{detected}** detected — its transaction record will be used.")
    else:
        st.info("No known order ID found in the email — pick the order below, or continue without one.")
        choice = st.selectbox("Order", [NO_ORDER] + sorted(transactions), key="assistant_order_choice")
        if choice != NO_ORDER:
            txn = transactions[choice]

if st.button("Suggest a reply", type="primary", disabled=not email.strip()):
    with st.spinner("Drafting a reply..."):
        gen = generate_reply(email, txn or placeholder_transaction(), policy_store, retriever)
    st.session_state.assistant_result = {
        "email": email,
        "txn": txn or placeholder_transaction(),
        "gen": gen,
    }
    st.session_state.pop("assistant_eval", None)

state = st.session_state.get("assistant_result")
if state:
    st.subheader("Suggested reply")
    st.code(state["gen"].reply, language=None, wrap_lines=True)
    rem = state["gen"].remedy
    st.caption(
        f"Remedy: {rem.remedy_type or '—'} · amount={rem.remedy_amount} · "
        f"rule={rem.rule_cited or '—'} · escalate={rem.escalate}"
    )

    with st.expander("How accurate is this reply? (RAGAS)"):
        result = st.session_state.get("assistant_eval")
        if result is None:
            st.write(
                "Scores faithfulness, answer relevancy, and context precision against "
                "the retrieved policy chunks (reference-free). Runs when you ask."
            )
            if st.button("Check accuracy"):
                with st.spinner("Scoring the reply..."):
                    live_ticket = Ticket(
                        ticket_id="LIVE",
                        order_id=state["txn"].order_id,
                        category="live",
                        split="holdout",
                        sentiment="neutral",
                        incoming_email=state["email"],
                        actual_reply=state["gen"].reply,
                    )
                    st.session_state.assistant_eval = evaluate_generated(
                        live_ticket,
                        state["txn"],
                        state["gen"],
                        policy_store,
                    )
                st.rerun()
        else:
            r = result
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Faithfulness", f"{r.faithfulness}" if r.faithfulness is not None else "err")
            c2.metric("Answer relevancy", f"{r.answer_relevancy}" if r.answer_relevancy is not None else "err")
            c3.metric("Context precision", f"{r.context_precision}" if r.context_precision is not None else "err")
            c4.metric("Routing quality", f"{r.quality_score}" if r.quality_score is not None else "—")
            st.markdown(
                f"- **Gated from AUTO:** {r.gated_from_auto}\n"
                f"- **Retrieval disagreement:** {r.retrieval_disagreement} "
                f"(checked={r.disagreement_checked})\n"
                f"- **Deterministic flags:** {', '.join(r.flags) or 'none'}\n"
                f"- **Scoring error:** {r.scoring_error or 'none'}"
            )
            if r.faithfulness_details:
                st.json(r.faithfulness_details)

        st.divider()
        st.markdown("**Grounding used to draft this reply**")
        ids = getattr(state["gen"], "retrieved_rule_ids", None) or []
        cited = getattr(state["gen"], "cited_rule_ids", None) or []
        if ids:
            st.markdown(f"*Retrieved rule IDs:* {', '.join(ids)}")
        if cited:
            st.markdown(f"*Cited rule IDs:* {', '.join(cited)}")
        st.markdown("*Policy clauses retrieved:*")
        for chunk in state["gen"].retrieved_policy_chunks:
            st.code(chunk, language=None, wrap_lines=True)
        similar = ", ".join(state["gen"].retrieved_similar_tickets) or "none"
        st.markdown(f"*Similar past tickets retrieved (tone only):* {similar}")
