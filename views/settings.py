"""Settings — swap the policy document and manage LLM provider credentials.

Key handling rules: existing key values are never read back, printed, or
logged; only presence ("configured") is shown. .env stays gitignored.

Two LLM steps can use different providers/models:
  - Email generation  → LLM_PROVIDER / LLM_MODEL
  - Email categorization → CLASSIFY_LLM_PROVIDER / CLASSIFY_LLM_MODEL
"""

from __future__ import annotations

import os

import streamlit as st

from src import email_source, llm_client, notify
from src.classifier import CATEGORY_LABELS
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

# ----------------------------------------------------------------- llm by step
st.header("LLM models by pipeline step")
st.caption(
    "Choose a provider and model for each step. API keys are stored per provider "
    "(shared if both steps use the same vendor). Leave a key blank to keep the existing one."
)

providers = list(PROVIDER_KEY_VARS)
gen_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
cls_provider = (
    os.getenv("CLASSIFY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "anthropic"
).lower()
if cls_provider not in providers:
    cls_provider = "mistral" if "mistral" in providers else providers[0]

for p, key_var in PROVIDER_KEY_VARS.items():
    configured = bool(os.getenv(key_var))
    used = []
    if p == gen_provider:
        used.append("generation")
    if p == cls_provider:
        used.append("categorization")
    where = f" · used for: {', '.join(used)}" if used else ""
    st.markdown(f"- {p}: {'configured ✓' if configured else 'no API key'}{where}")

# ---- Email generation
st.subheader("1. Email generation")
st.caption(
    "Drafts suggested replies (and runs evaluation judges). "
    "Env: `LLM_PROVIDER` / `LLM_MODEL`."
)
with st.form("generate_provider_form"):
    g_provider = st.selectbox(
        "Provider for email generation",
        providers,
        index=providers.index(gen_provider) if gen_provider in providers else 0,
        key="gen_provider",
    )
    g_api_key = st.text_input(
        "API key for this provider (leave blank to keep existing)",
        type="password",
        key="gen_api_key",
    )
    g_model = st.text_input(
        "Model for email generation (blank = provider default)",
        value=os.getenv("LLM_MODEL", ""),
        key="gen_model",
        help=f"Defaults: {llm_client.DEFAULT_MODELS}",
    )
    if st.form_submit_button("Save generation model", type="primary"):
        updates: dict[str, str | None] = {
            "LLM_PROVIDER": g_provider,
            "LLM_MODEL": g_model.strip() or None,
        }
        if g_api_key.strip():
            updates[PROVIDER_KEY_VARS[g_provider]] = g_api_key.strip()
        update_env(updates)
        st.success(f"Saved — email generation uses {g_provider}.")
        st.rerun()

if st.button("Test generation connection"):
    with st.spinner("Making one tiny generation LLM call..."):
        try:
            out = llm_client.complete(
                "You are a connectivity check. Reply with the single word OK.",
                "ping",
                max_tokens=8,
                purpose="generate",
            )
            p, m = llm_client.resolve_provider_model("generate")
            st.success(f"Connected — generation {p} ({m}) replied: {out.strip()[:40]}")
        except Exception as exc:
            st.error(f"Generation connection failed: {type(exc).__name__}: {exc}")

# ---- Email categorization
st.subheader("2. Email categorization")
st.caption(
    "Classifies each inbound email into: "
    + ", ".join(CATEGORY_LABELS.values())
    + ". Used only for triage / IGNORE noise — not for drafting replies. "
    "Env: `CLASSIFY_LLM_PROVIDER` / `CLASSIFY_LLM_MODEL` (falls back to generation settings if unset)."
)
# Default the classify form toward mistral when nothing is configured yet.
default_cls = os.getenv("CLASSIFY_LLM_PROVIDER", "mistral").lower()
if default_cls not in providers:
    default_cls = cls_provider

with st.form("classify_provider_form"):
    c_provider = st.selectbox(
        "Provider for email categorization",
        providers,
        index=providers.index(default_cls) if default_cls in providers else 0,
        key="cls_provider",
    )
    c_api_key = st.text_input(
        "API key for this provider (leave blank to keep existing)",
        type="password",
        key="cls_api_key",
    )
    c_model = st.text_input(
        "Model for email categorization (blank = provider default)",
        value=os.getenv("CLASSIFY_LLM_MODEL", ""),
        key="cls_model",
        help=f"Defaults: {llm_client.DEFAULT_MODELS}",
    )
    if st.form_submit_button("Save categorization model", type="primary"):
        updates = {
            "CLASSIFY_LLM_PROVIDER": c_provider,
            "CLASSIFY_LLM_MODEL": c_model.strip() or None,
        }
        if c_api_key.strip():
            updates[PROVIDER_KEY_VARS[c_provider]] = c_api_key.strip()
        update_env(updates)
        st.success(f"Saved — email categorization uses {c_provider}.")
        st.rerun()

if st.button("Test categorization connection"):
    with st.spinner("Making one tiny categorization LLM call..."):
        try:
            out = llm_client.complete(
                "You are a connectivity check. Reply with the single word OK.",
                "ping",
                max_tokens=8,
                purpose="classify",
            )
            p, m = llm_client.resolve_provider_model("classify")
            st.success(f"Connected — categorization {p} ({m}) replied: {out.strip()[:40]}")
        except Exception as exc:
            st.error(f"Categorization connection failed: {type(exc).__name__}: {exc}")

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
