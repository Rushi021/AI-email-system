"""Review — human-action dashboard with structured feedback capture.

Send/dismiss/hallucination-flag/escalation-audit write immutable feedback_events.
"""

from __future__ import annotations

import streamlit as st

from src import email_source, feedback, notify, queue_store
from src.config import load_config
from src.schema import IncomingEmail, Remedy

cfg = load_config()
counts = queue_store.counts()

st.title("🗂️ Review queue")
st.caption(
    f"Auto-send is **{'LIVE' if cfg['live_send'] else 'dry-run (nothing is actually sent)'}**. "
    "Toggle it in Settings."
)

c = st.columns(3)
c[0].metric("🔴 Escalations", counts["escalate"])
c[1].metric("🟡 Needs review", counts["review"])
c[2].metric("🟢 Auto (pending)", counts["auto"])

if cfg.get("digest_recipient"):
    if st.button("✉️ Send digest now"):
        d = notify.send_digest(cfg)
        (st.success if d["ok"] else st.info)(d["detail"])


def _record(it: dict, label: str, remedy_diff_payload=None) -> None:
    feedback.record_feedback(
        response_id=it.get("response_id") or it["email_id"],
        ticket_id=it["email_id"],
        category=it.get("category") or "",
        cited_rule=(it.get("remedy") or {}).get("rule_cited")
        or (it.get("judge") or {}).get("cited_rule")
        or "",
        routing_decision=it.get("decision") or "",
        ragas_scores=it.get("ragas") or {},
        label=label,
        remedy_diff_payload=remedy_diff_payload,
    )


def _item_card(it: dict, *, audit: bool = False) -> None:
    j = it.get("judge") or {}
    ragas = it.get("ragas") or {}
    header = (
        f"{it['subject'] or '(no subject)'} · {it['category']} · "
        f"order {it['order_id'] or 'n/a'} · confidence {it['confidence']}/100 · status: {it['status']}"
    )
    with st.expander(header, expanded=(it["decision"] == "escalate" and it["status"] == "pending") or audit):
        st.markdown(f"**From:** {it['from_addr'] or '—'}")
        st.info(it["body"])

        if ragas:
            st.markdown(
                f"- **Faithfulness:** {ragas.get('faithfulness')}\n"
                f"- **Answer relevancy:** {ragas.get('answer_relevancy')}\n"
                f"- **Context precision:** {ragas.get('context_precision')}\n"
                f"- **Routing quality_score:** {ragas.get('quality_score')}\n"
                f"- **Retrieval disagreement:** {ragas.get('retrieval_disagreement')} "
                f"(checked={ragas.get('disagreement_checked')})\n"
                f"- **Flags:** {', '.join(it.get('flags') or []) or 'none'}"
            )
        elif j:
            st.markdown(
                f"- **Cited rule:** {j.get('cited_rule', '—')}\n"
                + (f"- **⚠️ Escalate:** {j.get('escalate_reason', '')}\n" if j.get("escalate") else "")
                + f"- **Flags:** {', '.join(it.get('flags') or []) or 'none'}"
            )

        details = ragas.get("faithfulness_details") or {}
        if details:
            with st.expander("Faithfulness claim details"):
                st.json(details)

        edited = st.text_area(
            "Suggested reply (edit before sending)",
            value=it["suggested_reply"],
            height=160,
            key=f"reply_{it['email_id']}_{'audit' if audit else 'main'}",
        )

        if audit and it["status"] == "pending":
            a1, a2 = st.columns(2)
            if a1.button("Confirm escalation was needed (missed)", key=f"miss_{it['email_id']}"):
                _record(it, "ESCALATED_MISSED")
                queue_store.set_status(it["email_id"], "dismissed")
                st.warning("Recorded ESCALATED_MISSED.")
                st.rerun()
            if a2.button("Confirm AUTO was fine", key=f"okaudit_{it['email_id']}"):
                _record(it, "ACCEPTED_AS_IS")
                st.success("Audit confirmed.")
                st.rerun()
            return

        b1, b2, b3, b4 = st.columns(4)
        send_label = "Send reply" if cfg["live_send"] else "Simulate send (dry-run)"
        if it["decision"] != "ignore" and it["status"] == "pending":
            if b1.button(send_label, key=f"send_{it['email_id']}", type="primary"):
                dry = not cfg["live_send"]
                to = IncomingEmail(
                    id=it["email_id"], thread_id=it["thread_id"],
                    from_addr=it["from_addr"], subject=it["subject"],
                )
                try:
                    original = it.get("original_reply") or it["suggested_reply"]
                    original_remedy = Remedy(**(it.get("remedy") or {}))
                    label, diff_payload = feedback.classify_send_label(
                        original, edited, original_remedy
                    )
                    res = email_source.send_reply(to, edited, dry_run=dry, config=cfg)
                    queue_store.set_status(
                        it["email_id"], "simulated" if dry else "sent", suggested_reply=edited
                    )
                    _record(it, label, remedy_diff_payload=diff_payload or None)
                    st.success(f"{res['detail']} · feedback={label}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Send failed: {type(exc).__name__}: {exc}")
            if b2.button("Save edit", key=f"save_{it['email_id']}"):
                queue_store.set_status(it["email_id"], "pending", suggested_reply=edited)
                st.toast("Draft saved.")
            if b4.button("Flag hallucination", key=f"hall_{it['email_id']}"):
                _record(it, "FLAGGED_HALLUCINATION")
                queue_store.set_status(it["email_id"], "dismissed")
                st.error("Flagged as hallucination.")
                st.rerun()
        if it["status"] == "pending":
            if b3.button("Dismiss / Reject", key=f"dismiss_{it['email_id']}"):
                if it.get("decision") == "escalate":
                    _record(it, "ESCALATED_CORRECTLY")
                else:
                    _record(it, "REJECTED")
                queue_store.set_status(it["email_id"], "dismissed")
                st.rerun()


tabs = st.tabs([
    "🔴 Escalations",
    "🟡 Needs review",
    "🟢 Auto (dry-run)",
    "🔎 Audit sample",
    "✅ Done",
])
with tabs[0]:
    items = queue_store.list_items(decision="escalate", status="pending")
    st.caption("Policy/remedy flagged escalate, or confidence was too low.")
    _ = [_item_card(i) for i in items] or st.info("Nothing to escalate.")
with tabs[1]:
    items = queue_store.list_items(decision="review", status="pending")
    st.caption("Decent draft, but not confident enough to auto-send. Approve or edit.")
    _ = [_item_card(i) for i in items] or st.info("Nothing awaiting review.")
with tabs[2]:
    items = [
        i for i in queue_store.list_items(decision="auto", status="pending")
        if not i.get("audit_sample")
    ]
    st.caption("High-confidence, gate-clean replies — pre-approved drafts in dry-run mode.")
    _ = [_item_card(i) for i in items] or st.info("Nothing pending auto-reply.")
with tabs[3]:
    audit_items = [
        i for i in queue_store.list_items(decision="auto", status="pending")
        if i.get("audit_sample")
    ]
    st.caption(
        "Deterministic sample of AUTO-routed tickets for escalation-miss audit. "
        "Confirm whether a human should have been involved."
    )
    _ = [_item_card(i, audit=True) for i in audit_items] or st.info("No audit samples pending.")
with tabs[4]:
    done = [
        i for s in ("sent", "simulated", "dismissed")
        for i in queue_store.list_items(status=s)
    ]
    st.caption("Sent, simulated, or dismissed.")
    _ = [_item_card(i) for i in done] or st.info("Nothing done yet.")
