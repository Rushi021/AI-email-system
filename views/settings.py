"""Settings — swap the policy document and manage LLM provider credentials.

Key handling rules: existing key values are never read back, printed, or
logged; only presence ("configured") is shown. .env stays gitignored.
"""

from __future__ import annotations

import os

import streamlit as st

from src import llm_client
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
