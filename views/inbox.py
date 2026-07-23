"""Inbox — sync the connected mailbox (or demo inbox), route every message
through the pipeline, and drop the results into the review queue.

Also routes a single pasted email, so the whole system is demoable with no
connector configured.
"""

from __future__ import annotations

import streamlit as st

from src import email_source, notify, queue_store, router
from src.config import load_config
from src.schema import IncomingEmail
from views.common import load_everything

transactions, tickets, policy_store, retriever = load_everything()
cfg = load_config()

st.title("📥 Inbox")
st.caption(
    f"Source: **{cfg['email_source']}** · auto-send: "
    f"**{'LIVE' if cfg['live_send'] else 'dry-run'}** · "
    f"thresholds T1={cfg['t1']:.0f} / T2={cfg['t2']:.0f} — change these in Settings."
)


def _route_and_queue(emails: list[IncomingEmail]) -> list[dict]:
    items = []
    progress = st.progress(0.0)
    for i, e in enumerate(emails, 1):
        item = router.route_email(e, transactions, policy_store, retriever, cfg)
        queue_store.upsert(item)
        items.append(item)
        progress.progress(i / len(emails))
    progress.empty()
    return items


def _summary(items: list[dict]) -> None:
    from collections import Counter

    tally = Counter(i["decision"] for i in items)
    cols = st.columns(5)
    cols[0].metric("Fetched", len(items))
    cols[1].metric("Auto-reply", tally.get("auto", 0))
    cols[2].metric("Needs review", tally.get("review", 0))
    cols[3].metric("Escalated", tally.get("escalate", 0))
    cols[4].metric("Ignored", tally.get("ignore", 0))
    st.success("Routed and queued. Open the **Review** page to act on them.")

# ---------------------------------------------------------------- sync inbox
st.subheader("Sync inbox")
st.write(
    "Fetch unread messages from the connected source, route each one, and queue "
    "the results. Each email costs **2 LLM calls** (compliance + quality)."
)
n = st.number_input("Max messages to fetch", 1, 100, 20)
if st.button("Sync inbox now", type="primary"):
    try:
        with st.spinner("Fetching…"):
            emails = email_source.fetch_unread(int(n), cfg)
    except Exception as exc:  # connector/credentials problem — surface, don't crash
        st.error(f"Could not fetch from '{cfg['email_source']}': {type(exc).__name__}: {exc}")
        emails = []
    if not emails:
        st.info("No messages fetched.")
    else:
        with st.spinner(f"Routing {len(emails)} message(s)…"):
            items = _route_and_queue(emails)
        _summary(items)
        if cfg.get("digest_enabled") and cfg.get("digest_recipient"):
            d = notify.send_digest(cfg)
            st.caption(f"Digest: {d['detail']}")

st.divider()

# --------------------------------------------------------- route a single email
st.subheader("Route a single email")
st.caption("Paste one incoming email to route it through the exact same pipeline.")
body = st.text_area("Email body", height=150, key="single_email")
subject = st.text_input("Subject (optional)", key="single_subject")
if st.button("Route this email", disabled=not body.strip()):
    email = IncomingEmail(
        id=f"manual-{abs(hash(body)) % 10**8}",
        subject=subject,
        body=body,
        from_addr="pasted@manual",
    )
    with st.spinner("Routing…"):
        item = router.route_email(email, transactions, policy_store, retriever, cfg)
        queue_store.upsert(item)
    badge = {"auto": "🟢 Auto-reply", "review": "🟡 Needs review",
             "escalate": "🔴 Escalate", "ignore": "⚪ Ignore"}[item["decision"]]
    st.markdown(f"### {badge} · confidence {item['confidence']}/100")
    if item["judge"]:
        j = item["judge"]
        st.markdown(
            f"- **Policy requires:** {j.get('policy_requires', '—')} (rule {j.get('cited_rule', '—')})\n"
            f"- **Escalate:** {j.get('escalate')} — {j.get('escalate_reason') or 'n/a'}\n"
            f"- **Flags:** {', '.join(item['flags']) or 'none'}"
        )
    if item["suggested_reply"]:
        st.markdown("**Suggested reply**")
        st.code(item["suggested_reply"], language=None, wrap_lines=True)
    st.caption("Queued — manage it on the Review page.")
