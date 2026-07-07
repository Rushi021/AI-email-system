"""Assistant — the landing page. Paste a customer email, get a suggested reply.

The accuracy check is deliberately lazy: the two extra LLM judge calls fire
only when the user asks (metered API tiers make every call count).
"""

from __future__ import annotations

import streamlit as st

from src.evaluator import evaluate_reply
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

    with st.expander("How accurate is this reply?"):
        result = st.session_state.get("assistant_eval")
        if result is None:
            st.write(
                "This scores the suggested reply against your policy document "
                "and a quality rubric. It makes **2 extra LLM calls**, so it "
                "only runs when you ask."
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
                        # no human reply exists for a live email; alignment is
                        # scored against the reply itself and labeled as such below
                        actual_reply=state["gen"].reply,
                    )
                    st.session_state.assistant_eval = evaluate_reply(
                        live_ticket, state["txn"], state["gen"].reply, policy_store, "generated"
                    )
                st.rerun()
        else:
            r = result
            c1, c2, c3 = st.columns(3)
            c1.metric("Overall score", f"{r.final_score}/100")
            c2.metric("Policy compliance", f"{r.compliance_match_score}/5")
            c3.metric("Deductions", f"-{r.deterministic_penalty}")
            st.caption(
                "No human-written reply exists for a live email, so the alignment "
                "component (25% of the score) is scored against the suggested reply "
                "itself and always comes out perfect — treat the overall score as an "
                "upper bound. Policy compliance and quality are measured normally."
            )
            st.markdown(
                f"- **What the policy requires:** {r.judge_policy_requires} "
                f"(rule {r.judge_cited_rule})\n"
                f"- **What the reply offers:** {r.judge_reply_offers}\n"
                f"- **Judge's reasoning:** {r.compliance_justification}\n"
                f"- **Quality (groundedness / tone / clarity / actionability):** "
                f"{r.groundedness} / {r.tone_empathy} / {r.clarity} / {r.actionability} (each out of 5)\n"
                f"- **Automatic checks flagged:** {', '.join(r.flags) or 'nothing'}"
            )

        st.divider()
        st.markdown("**Grounding used to draft this reply**")
        st.markdown("*Policy clauses retrieved:*")
        for chunk in state["gen"].retrieved_policy_chunks:
            st.code(chunk, language=None, wrap_lines=True)
        similar = ", ".join(state["gen"].retrieved_similar_tickets) or "none"
        st.markdown(f"*Similar past tickets retrieved:* {similar}")
