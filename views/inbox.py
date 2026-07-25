"""Inbox — sync the connected mailbox (or demo inbox), route every message
through the pipeline, and drop the results into the review queue.

Also routes a single pasted email, so the whole system is demoable with no
connector configured.
"""

from __future__ import annotations

import streamlit as st

from src import email_source, event_bus, notify, queue_store, router
from src.config import load_config
from src.email_parser import parse as parse_email
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


def _route_and_queue(emails: list[IncomingEmail]) -> tuple[list[dict], list[dict]]:
    """Parse each email, publish it to the event bus, then drain it through
    routing. drain() isolates each item in its own try/except, so one bad
    email (a router/generator exception) retries and eventually dead-letters
    instead of crashing the whole sync — every other email still gets routed.
    Returns (routed_items, failed_outcomes)."""
    for e in emails:
        event_bus.publish(parse_email(e))

    def _process(email: IncomingEmail) -> dict:
        item = router.route_email(email, transactions, policy_store, retriever, cfg)
        queue_store.upsert(item)
        return item

    outcomes = event_bus.drain(_process, limit=len(emails))
    items = [o["result"] for o in outcomes if o["ok"]]
    failed = [o for o in outcomes if not o["ok"]]
    return items, failed


def _summary(items: list[dict], failed: list[dict]) -> None:
    from collections import Counter

    tally = Counter(i["decision"] for i in items)
    cols = st.columns(5)
    cols[0].metric("Fetched", len(items) + len(failed))
    cols[1].metric("Auto-reply", tally.get("auto", 0))
    cols[2].metric("Needs review", tally.get("review", 0))
    cols[3].metric("Escalated", tally.get("escalate", 0))
    cols[4].metric("Ignored", tally.get("ignore", 0))
    st.success("Routed and queued. Open the **Review** page to act on them.")
    if failed:
        st.warning(
            f"{len(failed)} email(s) failed to route and will retry on the next sync "
            f"(dead-lettered after {event_bus.MAX_ATTEMPTS} failed attempts): "
            + ", ".join(f"{f['email_id']} ({f['status']})" for f in failed)
        )

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
            items, failed = _route_and_queue(emails)
        _summary(items, failed)
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
    email = parse_email(IncomingEmail(
        id=f"manual-{abs(hash(body)) % 10**8}",
        subject=subject,
        body=body,
        from_addr="pasted@manual",
    ))
    try:
        with st.spinner("Routing…"):
            item = router.route_email(email, transactions, policy_store, retriever, cfg)
            queue_store.upsert(item)
    except Exception as exc:  # keep the page alive — surface the failure instead
        st.error(f"Routing failed: {type(exc).__name__}: {exc}")
        st.stop()
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
