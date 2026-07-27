"""Settings — swap the policy document and manage LLM provider credentials.

Key handling rules: existing key values are never read back, printed, or
logged; only presence ("configured") is shown. .env stays gitignored.

Two LLM steps can use different providers/models:
  - Email generation  → LLM_PROVIDER / LLM_MODEL
  - Email categorization → CLASSIFY_LLM_PROVIDER / CLASSIFY_LLM_MODEL
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

import streamlit as st

from src import email_source, llm_client, notify
from src.classifier import CATEGORY_LABELS
from src.config import load_config, save_config
from views.common import (
    DATA,
    PROVIDER_KEY_VARS,
    load_everything,
    load_user_examples,
    save_user_examples,
    update_env,
)


def _parse_examples_upload(raw: bytes, filename: str) -> list[dict]:
    """Validate CSV/JSON bulk upload into user_examples rows."""
    name = (filename or "").lower()
    rows: list[dict] = []
    if name.endswith(".json"):
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            data = data.get("examples") or data.get("tickets") or [data]
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of objects")
        for item in data:
            if not isinstance(item, dict):
                continue
            email = str(item.get("incoming_email") or "").strip()
            reply = str(item.get("actual_reply") or "").strip()
            if not email or not reply:
                continue
            row = {"incoming_email": email, "actual_reply": reply}
            if item.get("order_id"):
                row["order_id"] = str(item["order_id"]).strip()
            if item.get("ticket_id"):
                row["ticket_id"] = str(item["ticket_id"]).strip()
            rows.append(row)
        return rows

    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    for item in reader:
        email = str(item.get("incoming_email") or "").strip()
        reply = str(item.get("actual_reply") or "").strip()
        if not email or not reply:
            continue
        row = {"incoming_email": email, "actual_reply": reply}
        if item.get("order_id"):
            row["order_id"] = str(item["order_id"]).strip()
        if item.get("ticket_id"):
            row["ticket_id"] = str(item["ticket_id"]).strip()
        rows.append(row)
    return rows


st.title("⚙️ Settings")
cfg = load_config()

# ------------------------------------------------------------- policy document
st.header("Policy document")

policy_name = cfg.get("policy_filename") or "policy.pdf"
policy_path = DATA / policy_name
_, _, policy_store, _ = load_everything()

if policy_path.exists():
    size_kb = policy_path.stat().st_size / 1024
    stats = getattr(policy_store, "last_ingest_stats", {}) or {}
    st.markdown(
        f"Currently loaded: **{policy_path.name}** · {size_kb:.0f} KB · "
        f"{len(policy_store.rules)} indexed rules"
        + (f" · categories: {', '.join(stats.get('categories') or policy_store.categories())}" if policy_store.rules else "")
    )
    if stats:
        st.caption(
            f"Last ingest — reused {stats.get('reused', 0)}, "
            f"reprocessed {stats.get('reprocessed', 0)}, "
            f"deleted {stats.get('deleted', 0)}"
        )
    with st.expander("Preview (first indexed rule)"):
        preview = policy_store.chunks[0] if policy_store.chunks else "(empty)"
        st.code(preview, language=None, wrap_lines=True)
else:
    st.warning(f"No policy document found at data/{policy_name}.")

uploaded = st.file_uploader(
    "Upload a new policy document",
    type=["pdf", "docx", "md", "txt"],
    key="policy_upload",
)
if uploaded is not None and st.button("Replace policy and re-index", type="primary"):
    # Keep the uploaded extension so format-aware ingest can run.
    suffix = Path(uploaded.name).suffix.lower() or ".pdf"
    new_name = f"policy{suffix}"
    dest = DATA / new_name
    raw = uploaded.getvalue()
    dest.write_bytes(raw)
    try:
        from src.storage.factory import get_blob_store

        get_blob_store().put(f"policy/{new_name}", raw)
    except Exception:
        pass
    # Remove previous policy.* siblings so resolve stays unambiguous.
    for p in DATA.glob("policy.*"):
        if p.name != new_name:
            try:
                p.unlink()
            except OSError:
                pass
    save_config({"policy_filename": new_name})
    load_everything.clear()
    st.success(f"Policy replaced with {uploaded.name} and re-indexed.")
    st.rerun()

st.divider()

# ------------------------------------------------------------- example replies
st.header("Example replies (production corpus)")
st.caption(
    "Past (email → reply) pairs used only for voice/tone retrieval in live drafting. "
    "They are **not** part of the internal evaluation harness (`dataset.json` corpus/holdout split stays untouched)."
)

examples = load_user_examples()

bulk = st.file_uploader(
    "Bulk upload CSV or JSON (`incoming_email`, `actual_reply`, optional `order_id`)",
    type=["csv", "json"],
    key="examples_bulk",
)
if bulk is not None and st.button("Import examples", type="primary"):
    try:
        raw = bulk.getvalue()
        new_rows = _parse_examples_upload(raw, bulk.name)
        if not new_rows:
            st.error("No valid rows found — need non-empty incoming_email and actual_reply.")
        else:
            # Assign stable ticket ids for new rows.
            next_n = len(examples) + 1
            for row in new_rows:
                if not row.get("ticket_id"):
                    row["ticket_id"] = f"U{next_n:03d}"
                    next_n += 1
            examples = examples + new_rows
            save_user_examples(examples)
            load_everything.clear()
            st.success(f"Imported {len(new_rows)} example(s). Retriever refreshed.")
            st.rerun()
    except Exception as exc:
        st.error(f"Import failed: {type(exc).__name__}: {exc}")

with st.form("manual_example_form"):
    st.subheader("Add one example")
    man_email = st.text_area("Customer email", height=100, key="man_email")
    man_reply = st.text_area("Agent reply sent", height=100, key="man_reply")
    man_oid = st.text_input("Order ID (optional)", key="man_oid")
    if st.form_submit_button("Add example", type="primary"):
        email_s, reply_s = man_email.strip(), man_reply.strip()
        if not email_s or not reply_s:
            st.error("Both customer email and agent reply are required.")
        else:
            row = {
                "ticket_id": f"U{len(examples) + 1:03d}",
                "incoming_email": email_s,
                "actual_reply": reply_s,
            }
            if man_oid.strip():
                row["order_id"] = man_oid.strip()
            examples = examples + [row]
            save_user_examples(examples)
            load_everything.clear()
            st.success("Example added. Retriever refreshed.")
            st.rerun()

if examples:
    st.subheader(f"Saved examples ({len(examples)})")
    for i, row in enumerate(examples):
        cols = st.columns([3, 3, 1])
        cols[0].markdown(f"**{row.get('ticket_id', f'#{i+1}')}**")
        cols[0].caption((row.get("incoming_email") or "")[:120])
        cols[1].caption((row.get("actual_reply") or "")[:120])
        if cols[2].button("Delete", key=f"del_ex_{i}"):
            examples = [e for j, e in enumerate(examples) if j != i]
            save_user_examples(examples)
            load_everything.clear()
            st.rerun()
else:
    st.info("No user-supplied examples yet.")

st.divider()

# ------------------------------------------------------------- retrieval
st.header("Policy retrieval")
st.caption("Hybrid BM25 + local embeddings with RRF. Cross-encoder rerank is optional (heavier).")
with st.form("retrieval_form"):
    use_emb = st.toggle("Use local embeddings", value=bool(cfg.get("use_embeddings", True)))
    rrf_k = st.number_input("RRF k", min_value=1, max_value=200, value=int(cfg.get("rrf_k", 60)))
    k_policy = st.number_input("Top-k policy rules", min_value=1, max_value=12, value=int(cfg.get("k_policy", 4)))
    rerank = st.toggle(
        "Cross-encoder rerank (optional)",
        value=bool(cfg.get("cross_encoder_rerank", False)),
    )
    llm_chunk = st.toggle(
        "LLM fallback for unstructured sections",
        value=bool(cfg.get("policy_llm_chunking", False)),
    )
    if st.form_submit_button("Save retrieval settings", type="primary"):
        save_config(
            {
                "use_embeddings": use_emb,
                "rrf_k": int(rrf_k),
                "k_policy": int(k_policy),
                "cross_encoder_rerank": rerank,
                "policy_llm_chunking": llm_chunk,
            }
        )
        load_everything.clear()
        st.success("Retrieval settings saved; policy index will rebuild on next load.")
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

# ------------------------------------------------------------- evaluation gates
st.header("Evaluation gates")
st.caption(
    "RAGAS faithfulness gate and sampling rates for dual-pass disagreement checks "
    "and AUTO escalation audits. These are product settings, not company facts."
)
with st.form("eval_gates_form"):
    faith_gate = st.slider(
        "Faithfulness gate (block AUTO below)",
        0.0, 1.0, float(cfg.get("faithfulness_gate", 0.7)), 0.05,
    )
    disagree_rate = st.slider(
        "Retrieval-disagreement sample rate",
        0.0, 1.0, float(cfg.get("retrieval_disagreement_sample_rate", 0.1)), 0.05,
    )
    audit_rate = st.slider(
        "AUTO escalation-audit sample rate",
        0.0, 1.0, float(cfg.get("audit_sample_rate", 0.05)), 0.01,
    )
    if st.form_submit_button("Save evaluation gates", type="primary"):
        save_config({
            "faithfulness_gate": float(faith_gate),
            "retrieval_disagreement_sample_rate": float(disagree_rate),
            "audit_sample_rate": float(audit_rate),
        })
        st.success("Evaluation gates saved.")
        st.rerun()

st.divider()

# -------------------------------------------------------------------- storage
st.header("Storage")
st.caption(
    "Where operational data lives (queue, feedback, RAGAS scores, generated artifacts). "
    "Default is local — zero setup. Cloud backends are opt-in; secrets go to .env only."
)
from src.storage.factory import clear_store_cache, get_blob_store, get_structured_store

with st.form("storage_form"):
    s_provider = st.selectbox(
        "Structured store",
        ["local", "postgres"],
        index=["local", "postgres"].index(cfg.get("storage_structured_provider", "local")),
    )
    b_provider = st.selectbox(
        "Blob store",
        ["local", "s3", "azure", "gcs", "postgres"],
        index=["local", "s3", "azure", "gcs", "postgres"].index(
            cfg.get("storage_blob_provider", "local")
            if cfg.get("storage_blob_provider", "local") in ("local", "s3", "azure", "gcs", "postgres")
            else 0
        ),
    )
    s3_bucket = st.text_input("S3 bucket", value=cfg.get("storage_s3_bucket", ""))
    s3_endpoint = st.text_input("S3 endpoint URL (optional, for MinIO/R2)", value=cfg.get("storage_s3_endpoint_url", ""))
    s3_region = st.text_input("S3 region", value=cfg.get("storage_s3_region", ""))
    azure_container = st.text_input("Azure container", value=cfg.get("storage_azure_container", "app-data"))
    gcs_bucket = st.text_input("GCS bucket", value=cfg.get("storage_gcs_bucket", ""))
    pg_dsn = st.text_input("Postgres DSN (leave blank to keep existing)", type="password")
    s3_key = st.text_input("S3 access key (leave blank to keep existing)", type="password")
    s3_secret = st.text_input("S3 secret key (leave blank to keep existing)", type="password")
    azure_cs = st.text_input("Azure connection string (leave blank to keep existing)", type="password")
    if st.form_submit_button("Save storage settings", type="primary"):
        save_config({
            "storage_structured_provider": s_provider,
            "storage_blob_provider": b_provider,
            "storage_s3_bucket": s3_bucket.strip(),
            "storage_s3_endpoint_url": s3_endpoint.strip(),
            "storage_s3_region": s3_region.strip(),
            "storage_azure_container": azure_container.strip(),
            "storage_gcs_bucket": gcs_bucket.strip(),
        })
        env_updates: dict[str, str | None] = {
            "STORAGE_STRUCTURED_PROVIDER": s_provider,
            "STORAGE_BLOB_PROVIDER": b_provider,
        }
        if pg_dsn.strip():
            env_updates["STORAGE_POSTGRES_DSN"] = pg_dsn.strip()
        if s3_key.strip():
            env_updates["STORAGE_S3_ACCESS_KEY"] = s3_key.strip()
        if s3_secret.strip():
            env_updates["STORAGE_S3_SECRET_KEY"] = s3_secret.strip()
        if azure_cs.strip():
            env_updates["STORAGE_AZURE_CONNECTION_STRING"] = azure_cs.strip()
        if s3_bucket.strip():
            env_updates["STORAGE_S3_BUCKET"] = s3_bucket.strip()
        if gcs_bucket.strip():
            env_updates["STORAGE_GCS_BUCKET"] = gcs_bucket.strip()
        update_env(env_updates)
        clear_store_cache()
        st.success("Storage settings saved.")
        st.rerun()

if st.button("Test storage connection"):
    try:
        get_structured_store().test_connection()
        get_blob_store().test_connection()
        st.success(
            f"Connected — structured={load_config().get('storage_structured_provider')} · "
            f"blob={load_config().get('storage_blob_provider')}"
        )
    except Exception as exc:
        st.error(f"Storage connection failed: {type(exc).__name__}: {exc}")

if st.button("Migrate existing local data to configured backend"):
    try:
        from src.storage.factory import migrate_local_to

        clear_store_cache()
        stats = migrate_local_to(get_structured_store(), get_blob_store())
        st.success(f"Migration complete: {stats}")
    except Exception as exc:
        st.error(f"Migration failed: {type(exc).__name__}: {exc}")

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
