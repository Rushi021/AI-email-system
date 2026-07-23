"""Review — the human-action dashboard. Priority-sorted queue of routed emails,
split by decision. Approve/send, edit, or dismiss each item.

Auto-reply items respect the global live-send switch: with it off (default),
'Send' records a simulated send instead of dispatching anything.
"""

from __future__ import annotations

import streamlit as st

from src import email_source, notify, queue_store
from src.config import load_config
from src.schema import IncomingEmail

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


def _item_card(it: dict) -> None:
    j = it["judge"]
    header = (
        f"{it['subject'] or '(no subject)'} · {it['category']} · "
        f"order {it['order_id'] or 'n/a'} · confidence {it['confidence']}/100 · status: {it['status']}"
    )
    with st.expander(header, expanded=(it["decision"] == "escalate" and it["status"] == "pending")):
        st.markdown(f"**From:** {it['from_addr'] or '—'}")
        st.info(it["body"])

        if j:
            st.markdown(
                f"- **Policy requires:** {j.get('policy_requires', '—')} (rule {j.get('cited_rule', '—')})\n"
                f"- **Reply offers:** {j.get('reply_offers', '—')}\n"
                + (f"- **⚠️ Escalate:** {j.get('escalate_reason', '')}\n" if j.get("escalate") else "")
                + f"- **Flags:** {', '.join(it['flags']) or 'none'}"
            )

        edited = st.text_area(
            "Suggested reply (edit before sending)",
            value=it["suggested_reply"],
            height=160,
            key=f"reply_{it['email_id']}",
        )

        b1, b2, b3 = st.columns(3)
        send_label = "Send reply" if cfg["live_send"] else "Simulate send (dry-run)"
        if it["decision"] != "ignore" and it["status"] == "pending":
            if b1.button(send_label, key=f"send_{it['email_id']}", type="primary"):
                dry = not cfg["live_send"]
                to = IncomingEmail(
                    id=it["email_id"], thread_id=it["thread_id"],
                    from_addr=it["from_addr"], subject=it["subject"],
                )
                try:
                    res = email_source.send_reply(to, edited, dry_run=dry, config=cfg)
                    queue_store.set_status(
                        it["email_id"], "simulated" if dry else "sent", suggested_reply=edited
                    )
                    st.success(res["detail"])
                    st.rerun()
                except Exception as exc:
                    st.error(f"Send failed: {type(exc).__name__}: {exc}")
            if b2.button("Save edit", key=f"save_{it['email_id']}"):
                queue_store.set_status(it["email_id"], "pending", suggested_reply=edited)
                st.toast("Draft saved.")
        if it["status"] == "pending":
            if b3.button("Dismiss", key=f"dismiss_{it['email_id']}"):
                queue_store.set_status(it["email_id"], "dismissed")
                st.rerun()


tabs = st.tabs(["🔴 Escalations", "🟡 Needs review", "🟢 Auto (dry-run)", "✅ Done"])
with tabs[0]:
    items = queue_store.list_items(decision="escalate", status="pending")
    st.caption("Policy mandates a human, or confidence was too low. Act on these first.")
    [_item_card(i) for i in items] or st.info("Nothing to escalate.")
with tabs[1]:
    items = queue_store.list_items(decision="review", status="pending")
    st.caption("Decent draft, but not confident enough to auto-send. Approve or edit.")
    [_item_card(i) for i in items] or st.info("Nothing awaiting review.")
with tabs[2]:
    items = queue_store.list_items(decision="auto", status="pending")
    st.caption("High-confidence, policy-clean replies — pre-approved drafts in dry-run mode.")
    [_item_card(i) for i in items] or st.info("Nothing pending auto-reply.")
with tabs[3]:
    done = [
        i for s in ("sent", "simulated", "dismissed")
        for i in queue_store.list_items(status=s)
    ]
    st.caption("Sent, simulated, or dismissed.")
    [_item_card(i) for i in done] or st.info("Nothing done yet.")
