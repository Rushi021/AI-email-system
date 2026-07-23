"""Settings — swap the policy document and manage LLM provider credentials.

Key handling rules: existing key values are never read back, printed, or
logged; only presence ("configured") is shown. .env stays gitignored.
"""

from __future__ import annotations

import os

import streamlit as st

from src import email_source, llm_client, notify
from src.config import load_config, save_config
from views.common import DATA, PROVIDER_KEY_VARS, load_everything, update_env

st.title("⚙️ Settings")

# ------------------------------------------------------------- policy document
st.header("Policy document")

policy_path = DATA / "policy.pdf"
_, _, policy_store, _ = load_everything()

if policy_path.exists():
    size_kb = policy_path.stat().st_size / 1024
    st.markdown(
        f"Currently loaded: **{policy_path.name}** · {size_kb:.0f} KB · "
        f"{len(policy_store.chunks)} indexed clauses"
    )
    with st.expander("Preview (first indexed clause)"):
        st.code(policy_store.chunks[0], language=None, wrap_lines=True)
else:
    st.warning("No policy document found at data/policy.pdf.")

uploaded = st.file_uploader("Upload a new policy PDF", type=["pdf"])
if uploaded is not None and st.button("Replace policy and re-index", type="primary"):
    policy_path.write_bytes(uploaded.getvalue())
    load_everything.clear()  # drop the cached index so the new PDF takes effect now
    st.success(f"Policy replaced with {uploaded.name} and re-indexed.")
    st.rerun()

st.divider()

# ----------------------------------------------------------------- llm provider
st.header("LLM provider")

providers = list(PROVIDER_KEY_VARS)
current_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

for p, key_var in PROVIDER_KEY_VARS.items():
    configured = bool(os.getenv(key_var))
    active = " · **active**" if p == current_provider else ""
    st.markdown(f"- {p}: {'configured ✓' if configured else 'no API key'}{active}")

with st.form("provider_form"):
    provider = st.selectbox(
        "Provider",
        providers,
        index=providers.index(current_provider) if current_provider in providers else 0,
    )
    api_key = st.text_input(
        "API key (leave blank to keep the existing one)", type="password"
    )
    model = st.text_input(
        "Model override (optional — blank uses the provider default)",
        value=os.getenv("LLM_MODEL", ""),
    )
    if st.form_submit_button("Save", type="primary"):
        updates: dict[str, str | None] = {
            "LLM_PROVIDER": provider,
            "LLM_MODEL": model.strip() or None,
        }
        if api_key.strip():
            updates[PROVIDER_KEY_VARS[provider]] = api_key.strip()
        update_env(updates)
        st.success(f"Saved — provider set to {provider}.")
        st.rerun()

if st.button("Test connection"):
    with st.spinner("Making one tiny LLM call..."):
        try:
            out = llm_client.complete(
                "You are a connectivity check. Reply with the single word OK.",
                "ping",
                max_tokens=8,
            )
            provider_now = os.getenv("LLM_PROVIDER", "anthropic").lower()
            model_now = os.getenv("LLM_MODEL") or llm_client.DEFAULT_MODELS.get(provider_now, "?")
            st.success(f"Connected — {provider_now} ({model_now}) replied: {out.strip()[:40]}")
        except Exception as exc:  # surface the failure without touching key values
            st.error(f"Connection failed: {type(exc).__name__}: {exc}")

st.divider()

# ------------------------------------------------------------- email connection
cfg = load_config()
st.header("Email connection")
st.caption(
    "How the system fetches incoming mail and sends replies. **demo** uses the "
    "built-in offline inbox; **mcp** connects to a Gmail (or any) MCP server."
)

with st.form("email_form"):
    source = st.selectbox(
        "Email source", ["demo", "mcp"], index=["demo", "mcp"].index(cfg["email_source"])
    )
    mcp_url = st.text_input("MCP server URL", value=os.getenv("MCP_SERVER_URL", ""))
    mcp_token = st.text_input(
        "MCP auth token (leave blank to keep the existing one)", type="password"
    )
    if st.form_submit_button("Save email settings", type="primary"):
        save_config({"email_source": source})
        env_updates: dict[str, str | None] = {"MCP_SERVER_URL": mcp_url.strip() or None}
        if mcp_token.strip():
            env_updates["MCP_AUTH_TOKEN"] = mcp_token.strip()
        update_env(env_updates)
        st.success(f"Saved — email source set to {source}.")
        st.rerun()

if st.button("Test inbox connection"):
    with st.spinner("Fetching 1 message…"):
        try:
            got = email_source.fetch_unread(1, load_config())
            st.success(f"Connected to '{load_config()['email_source']}' — fetched {len(got)} message(s).")
        except Exception as exc:
            st.error(f"Inbox connection failed: {type(exc).__name__}: {exc}")

st.divider()

# -------------------------------------------------------------------- automation
st.header("Automation thresholds")
st.caption(
    "Confidence is the live routing score (0–100). At or above **T1** a clean, "
    "unambiguous reply is auto-sendable; below **T2** it is escalated to a human; "
    "in between it is queued for review."
)
with st.form("automation_form"):
    t1 = st.slider("T1 — auto-reply at or above", 0, 100, int(cfg["t1"]))
    t2 = st.slider("T2 — escalate below", 0, 100, int(cfg["t2"]))
    live = st.toggle(
        "Live send (actually dispatch auto-replies)",
        value=cfg["live_send"],
        help="Off = dry-run: auto-replies are queued and 'Send' only simulates. "
        "On = replies are dispatched through the connector.",
    )
    if st.form_submit_button("Save automation settings", type="primary"):
        if t2 >= t1:
            st.error("T2 must be below T1.")
        else:
            save_config({"t1": float(t1), "t2": float(t2), "live_send": live})
            st.success("Automation settings saved.")
            st.rerun()

st.divider()

# ----------------------------------------------------------------- notifications
st.header("Notifications")
st.caption("A review dashboard badge is always on. Optionally email a digest of pending items.")
with st.form("notify_form"):
    digest_enabled = st.checkbox("Email a digest after each inbox sync", value=cfg["digest_enabled"])
    recipient = st.text_input("Digest recipient email", value=cfg["digest_recipient"])
    if st.form_submit_button("Save notification settings", type="primary"):
        save_config({"digest_enabled": digest_enabled, "digest_recipient": recipient.strip()})
        st.success("Notification settings saved.")
        st.rerun()

if st.button("Send digest now"):
    d = notify.send_digest(load_config())
    (st.success if d["ok"] else st.info)(d["detail"])
    if d.get("preview"):
        st.code(d["preview"], language=None)
