"""Evaluation dashboard — two unblended panels:

1. Response Quality (RAGAS)
2. System Reliability (Human Feedback)

Every rate shows n and a Wilson CI; n < 20 → insufficient data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.storage.factory import get_structured_store
from src.validate_metric import build_reliability_report
from views.common import RESULTS, load_everything

transactions, tickets, policy_store, retriever = load_everything()
tickets_by_id = {t.ticket_id: t for t in tickets}

st.title("📊 Evaluation")
st.caption(
    "Two separate numbers — never blended. Response Quality is automated (RAGAS); "
    "System Reliability comes only from real human feedback."
)


def _render_rate(title: str, payload: dict) -> None:
    n = payload.get("n") or 0
    if payload.get("insufficient_data") or payload.get("rate") is None:
        st.metric(title, "insufficient data", help=f"n={n} (need ≥20)")
        st.caption(f"n = {n}")
        return
    rate = payload["rate"]
    st.metric(
        title,
        f"{rate:.1%}",
        help=f"n={n}, Wilson 95% CI [{payload.get('ci_low')}, {payload.get('ci_high')}]",
    )
    st.caption(
        f"n = {n} · 95% CI [{payload.get('ci_low'):.3f}, {payload.get('ci_high'):.3f}]"
    )


tab_q, tab_r = st.tabs(["📈 Response Quality (RAGAS)", "🛡️ System Reliability (Human Feedback)"])

# ---------------------------------------------------------------- RAGAS panel
with tab_q:
    eval_path = RESULTS / "evaluation_results.json"
    ragas_rows = get_structured_store().query("ragas_scores", order_by="-timestamp")

    if not eval_path.exists() and not ragas_rows:
        st.warning("No RAGAS scores yet — run `python pipeline.py --all` or route live mail.")
    else:
        results = []
        if eval_path.exists():
            results = json.loads(eval_path.read_text())
        # Prefer live structured store when present.
        source = ragas_rows if ragas_rows else results

        faiths = [r.get("faithfulness") for r in source if r.get("faithfulness") is not None]
        relevs = [r.get("answer_relevancy") for r in source if r.get("answer_relevancy") is not None]
        precs = [r.get("context_precision") for r in source if r.get("context_precision") is not None]
        gated = sum(1 for r in source if r.get("gated_from_auto"))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Faithfulness avg", f"{sum(faiths)/len(faiths):.3f}" if faiths else "—", help=f"n={len(faiths)}")
        c2.metric("Answer relevancy avg", f"{sum(relevs)/len(relevs):.3f}" if relevs else "—", help=f"n={len(relevs)}")
        c3.metric("Context precision avg", f"{sum(precs)/len(precs):.3f}" if precs else "—", help=f"n={len(precs)}")
        c4.metric("Gated from AUTO", f"{gated} / {len(source)}")

        if faiths:
            st.subheader("Faithfulness distribution")
            st.bar_chart(pd.DataFrame({"faithfulness": faiths}))

        st.subheader("Per-response detail")
        for r in source:
            tid = r.get("ticket_id") or r.get("response_id") or "?"
            label = (
                f"{tid} · faith={r.get('faithfulness')} · "
                f"relev={r.get('answer_relevancy')} · prec={r.get('context_precision')} · "
                f"gated={r.get('gated_from_auto')}"
            )
            with st.expander(label):
                st.json({
                    k: r.get(k)
                    for k in (
                        "faithfulness", "answer_relevancy", "context_precision",
                        "quality_score", "retrieval_disagreement", "disagreement_checked",
                        "gated_from_auto", "scoring_error", "faithfulness_details", "flags",
                    )
                    if k in r or r.get(k) is not None
                })
                if tid in tickets_by_id:
                    st.markdown("**Incoming email**")
                    st.info(tickets_by_id[tid].incoming_email)

# ---------------------------------------------------------- Reliability panel
with tab_r:
    report = build_reliability_report()
    st.subheader("Critical error rate (labeled AUTO only)")
    _render_rate("Critical error rate", report.get("critical_error_rate") or {})
    cov = report.get("audit_coverage") or {}
    st.caption(
        f"Audit coverage: {cov.get('labeled_auto')} labeled AUTO "
        f"/ {cov.get('all_auto')} total AUTO in queue"
    )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Overall reliability")
        _render_rate("Reliability rate", report.get("reliability_rate") or {})
    with c2:
        st.subheader("Escalation-miss rate")
        _render_rate("Escalation miss", report.get("escalation_miss_rate") or {})

    st.subheader("By category")
    by_cat = report.get("critical_error_by_category") or {}
    if by_cat:
        rows = []
        for cat, payload in by_cat.items():
            rows.append({
                "category": cat,
                "rate": payload.get("rate"),
                "n": payload.get("n"),
                "ci_low": payload.get("ci_low"),
                "ci_high": payload.get("ci_high"),
                "insufficient_data": payload.get("insufficient_data"),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch")
    else:
        st.info("No feedback events yet.")

    st.subheader("Calibration — quality_score decile vs real acceptance")
    cal = report.get("calibration") or {}
    buckets = cal.get("buckets") or []
    if buckets:
        st.dataframe(pd.DataFrame(buckets), width="stretch")
        st.caption(
            "If a high-faithfulness / high-quality bucket still has a high critical-error "
            "rate, tighten FAITHFULNESS_GATE in Settings."
        )
    else:
        st.info("Need feedback events with joined RAGAS scores for calibration.")

    st.subheader("Weekly trend")
    weekly = report.get("weekly_reliability") or []
    if weekly:
        st.dataframe(pd.DataFrame(weekly), width="stretch")
    st.caption(f"Total feedback events: n = {report.get('n_feedback_events', 0)}")
