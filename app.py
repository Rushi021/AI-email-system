"""Streamlit app: batch results dashboard, metric-validation view, live demo.

Run: streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.evaluator import evaluate_reply
from src.generator import generate_reply
from src.policy_store import PolicyStore
from src.retriever import TicketRetriever
from src.schema import Ticket, Transaction

DATA = Path("data")
RESULTS = Path("results")

st.set_page_config(page_title="AI Suggested-Response System", layout="wide")


@st.cache_resource
def load_everything():
    transactions = {
        t["order_id"]: Transaction(**t)
        for t in json.loads((DATA / "transactions.json").read_text())
    }
    tickets = [Ticket(**t) for t in json.loads((DATA / "dataset.json").read_text())]
    policy_store = PolicyStore(str(DATA / "policy.pdf"))
    retriever = TicketRetriever(tickets)
    return transactions, tickets, policy_store, retriever


transactions, tickets, policy_store, retriever = load_everything()
tickets_by_id = {t.ticket_id: t for t in tickets}

st.title("AI Email Suggested-Response System")
st.caption(
    "RAG over a policy PDF + past tickets, with a three-layer accuracy system. "
    "Everything company-specific lives in `data/` — swap those files and the code runs unchanged."
)

tab_results, tab_validation, tab_demo = st.tabs(
    ["📊 Batch Results", "🧪 Metric Validation", "✉️ Live Demo"]
)

# ---------------------------------------------------------------- batch results
with tab_results:
    eval_path = RESULTS / "evaluation_results.json"
    if not eval_path.exists():
        st.warning("No results yet — run `python pipeline.py --all` first.")
    else:
        results = json.loads(eval_path.read_text())
        generated = [r for r in results if r["reply_source"] == "generated"]
        gen_replies = {
            g["ticket_id"]: g
            for g in json.loads((RESULTS / "generated_replies.json").read_text())
        }

        overall = sum(r["final_score"] for r in generated) / len(generated)
        c1, c2, c3 = st.columns(3)
        c1.metric("Overall score (generated)", f"{overall:.1f} / 100")
        c2.metric("Holdout tickets", len(generated))
        c3.metric("Control (bad) replies", len(results) - len(generated))

        df = pd.DataFrame(generated)
        st.subheader("Score by category")
        st.bar_chart(df.groupby("category")["final_score"].mean())

        st.subheader("Per-ticket detail")
        for r in results:
            t = tickets_by_id[r["ticket_id"]]
            label = (
                f"{r['ticket_id']} · {r['category']} · {r['reply_source']} "
                f"· final score {r['final_score']}"
            )
            with st.expander(label):
                st.markdown("**Incoming email**")
                st.info(t.incoming_email)
                st.markdown("**Actual reply (human agent)**")
                st.success(t.actual_reply)
                st.markdown(
                    "**Generated reply**" if r["reply_source"] == "generated" else "**Control (deliberately bad) reply**"
                )
                if r["reply_source"] == "generated":
                    st.warning(gen_replies[r["ticket_id"]]["reply"])
                else:
                    controls = json.loads((DATA / "control_examples.json").read_text())
                    bad = next(c for c in controls if c["holdout_id"] == r["ticket_id"])
                    st.error(bad["bad_reply"])
                    st.caption(f"Why it's bad: {bad['why_bad']}")

                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Policy compliance", f"{r['compliance_match_score']}/5")
                s2.metric("Alignment w/ actual", f"{r['alignment']}/5")
                s3.metric(
                    "Quality (g/t/c/a)",
                    f"{r['groundedness']}/{r['tone_empathy']}/{r['clarity']}/{r['actionability']}",
                )
                s4.metric("Penalty", f"-{r['deterministic_penalty']}")

                st.markdown(
                    f"- **Judge — policy requires:** {r['judge_policy_requires']} "
                    f"(rule {r['judge_cited_rule']})\n"
                    f"- **Judge — reply offers:** {r['judge_reply_offers']}\n"
                    f"- **Compliance justification:** {r['compliance_justification']}\n"
                    f"- **Alignment justification:** {r['alignment_justification']}\n"
                    f"- **Quality justification:** {r['quality_justification']}\n"
                    f"- **Flags:** {', '.join(r['flags']) or 'none'}\n"
                    f"- **Lexical overlap with actual reply (reported only):** {r['lexical_overlap']:.2f}"
                )

# ------------------------------------------------------------- metric validation
with tab_validation:
    report_path = RESULTS / "validation_report.json"
    if not report_path.exists():
        st.warning("No validation report yet — run `python pipeline.py --all` first.")
    else:
        report = json.loads(report_path.read_text())

        c1 = report["check_1_discriminative"]
        st.subheader("1 · Discriminative check — does the metric catch bad replies?")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Generated avg", c1["generated_avg_final_score"])
        col_b.metric("Control (bad) avg", c1["control_avg_final_score"])
        col_c.metric("Gap", c1["gap"])
        st.write(c1["interpretation"])

        c2 = report["check_2_correlation"]
        st.subheader("2 · Correlation check — meaning vs word overlap")
        st.metric("Pearson r (alignment vs lexical overlap)", c2["pearson_r_alignment_vs_lexical_overlap"])
        st.write(c2["interpretation"])
        if c2["divergent_examples"]:
            st.dataframe(pd.DataFrame(c2["divergent_examples"]))

        c3 = report["check_3_judge_trust"]
        st.subheader("3 · Compliance-judge trust check — can we trust the judge at all?")
        st.metric("Agreement with hand-labeled ground truth", f"{c3['agreement']} ({c3['agreement_rate']:.0%})")
        st.write(c3["interpretation"])
        st.dataframe(pd.DataFrame(c3["comparisons"]))

# ---------------------------------------------------------------------- live demo
with tab_demo:
    st.subheader("Generate & score a reply live")
    email = st.text_area(
        "Incoming customer email",
        height=140,
        placeholder="Hi, my order ORD-1001 arrived and the shoes don't fit...",
    )

    mode = st.radio("Transaction", ["Look up by order ID", "Enter manually"], horizontal=True)
    if mode == "Look up by order ID":
        order_id = st.selectbox("Order ID", sorted(transactions))
        txn = transactions[order_id]
        st.json(txn.model_dump(), expanded=False)
    else:
        col1, col2 = st.columns(2)
        with col1:
            m_order = st.text_input("Order ID", "ORD-9999")
            m_product = st.text_input("Product", "Sample Product")
            m_price = st.number_input("Price", value=99.0)
            m_status = st.text_input("Status", "delivered")
        with col2:
            m_odate = st.text_input("Order date (YYYY-MM-DD)", "2026-06-20")
            m_ddate = st.text_input("Delivery date (blank if none)", "2026-06-24")
            m_final = st.checkbox("Final sale")
            m_returns = st.number_input("Returns in last 90 days", value=0, step=1)
        txn = Transaction(
            order_id=m_order, customer_id="C-manual", product=m_product, price=m_price,
            order_date=m_odate, delivery_date=m_ddate or None, status=m_status,
            final_sale=m_final, returns_last_90_days=int(m_returns),
        )

    actual = st.text_area("Actual reply, if known (optional — enables alignment scoring)", height=100)

    if st.button("Generate & score", type="primary", disabled=not email.strip()):
        with st.spinner("Generating reply..."):
            g = generate_reply(email, txn, policy_store, retriever)
        st.markdown("**Suggested reply**")
        st.success(g.reply)
        with st.expander("Retrieved grounding"):
            st.markdown("**Policy chunks**")
            for ch in g.retrieved_policy_chunks:
                st.code(ch, language=None)
            st.markdown(f"**Similar past tickets:** {', '.join(g.retrieved_similar_tickets)}")

        with st.spinner("Scoring reply..."):
            demo_ticket = Ticket(
                ticket_id="LIVE", order_id=txn.order_id, category="live_demo",
                split="holdout", sentiment="neutral",
                incoming_email=email,
                actual_reply=actual.strip() or g.reply,  # self-alignment if no ground truth
            )
            r = evaluate_reply(demo_ticket, txn, g.reply, policy_store, "generated")

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Final score", f"{r.final_score}/100")
        s2.metric("Policy compliance", f"{r.compliance_match_score}/5")
        s3.metric(
            "Alignment" + ("" if actual.strip() else " (vs itself — provide actual reply for a real number)"),
            f"{r.alignment}/5",
        )
        s4.metric("Penalty", f"-{r.deterministic_penalty}")
        st.markdown(
            f"- **Judge — policy requires:** {r.judge_policy_requires} (rule {r.judge_cited_rule})\n"
            f"- **Judge — reply offers:** {r.judge_reply_offers}\n"
            f"- **Justification:** {r.compliance_justification}\n"
            f"- **Flags:** {', '.join(r.flags) or 'none'}"
        )
