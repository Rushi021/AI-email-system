"""Evaluation (internal) — batch results and metric-validation dashboards.

Required for the challenge submission; reads the artifacts written by
`python pipeline.py --all`.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from views.common import DATA, RESULTS, load_everything

transactions, tickets, policy_store, retriever = load_everything()
tickets_by_id = {t.ticket_id: t for t in tickets}

st.title("📊 Evaluation (internal)")
st.caption(
    "RAG over a policy PDF + past tickets, with a three-layer accuracy system. "
    "Everything company-specific lives in `data/` — swap those files and the code runs unchanged."
)

tab_results, tab_validation = st.tabs(["📊 Batch Results", "🧪 Metric Validation"])

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
