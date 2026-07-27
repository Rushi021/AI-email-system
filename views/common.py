"""Shared helpers for the Streamlit views.

Company-agnostic: every company fact (order ids, policy text, tickets) is read
from data/ at runtime — nothing here names a company, product, or policy rule.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from src.config import load_config
from src.policy_store import PolicyStore, resolve_policy_path
from src.retriever import TicketRetriever
from src.schema import Ticket, Transaction, detect_order_id, placeholder_transaction  # noqa: F401

DATA = Path("data")
RESULTS = Path("results")
ENV_PATH = Path(".env")
USER_EXAMPLES_PATH = DATA / "user_examples.json"

PROVIDER_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def load_user_examples() -> list[dict]:
    """Production-only example replies (never part of the eval harness).

    Prefers the structured `user_examples` table; falls back to a one-time
    import from data/user_examples.json when the table is empty.
    """
    from src.storage.factory import get_structured_store

    store = get_structured_store()
    rows = store.query("user_examples", order_by="ticket_id")
    if rows:
        return rows
    # One-time bootstrap from legacy JSON file.
    if USER_EXAMPLES_PATH.exists():
        try:
            data = json.loads(USER_EXAMPLES_PATH.read_text())
            if isinstance(data, list) and data:
                for i, row in enumerate(data):
                    rid = str(row.get("ticket_id") or f"U{i + 1:03d}")
                    store.insert("user_examples", {**row, "id": rid, "ticket_id": rid})
                return store.query("user_examples", order_by="ticket_id")
        except json.JSONDecodeError:
            pass
    return []


def save_user_examples(examples: list[dict]) -> None:
    """Replace the user_examples structured table (and keep a JSON mirror for local)."""
    from src.storage.factory import get_blob_store, get_structured_store

    store = get_structured_store()
    for old in store.query("user_examples"):
        store.delete("user_examples", old["id"])
    for i, row in enumerate(examples):
        rid = str(row.get("ticket_id") or row.get("id") or f"U{i + 1:03d}")
        store.insert("user_examples", {**row, "id": rid, "ticket_id": rid})
    # Local JSON mirror so existing tools / git workflows still see the file.
    USER_EXAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_EXAMPLES_PATH.write_text(json.dumps(examples, indent=2) + "\n")
    try:
        get_blob_store().put(
            "data/user_examples.json",
            json.dumps(examples, indent=2) + "\n",
            "application/json",
        )
    except Exception:
        pass


def user_examples_as_tickets(examples: list[dict] | None = None) -> list[Ticket]:
    """Adapt user_examples.json rows into Ticket objects for TicketRetriever.

    Assigned split='corpus' in memory only — the file itself has no split field
    and is never merged into dataset.json.
    """
    rows = examples if examples is not None else load_user_examples()
    tickets = []
    for i, row in enumerate(rows):
        email = (row.get("incoming_email") or "").strip()
        reply = (row.get("actual_reply") or "").strip()
        if not email or not reply:
            continue
        tid = str(row.get("ticket_id") or f"U{i + 1:03d}")
        tickets.append(
            Ticket(
                ticket_id=tid,
                order_id=str(row.get("order_id") or ""),
                category=str(row.get("category") or "user_example"),
                split="corpus",
                sentiment=str(row.get("sentiment") or "neutral"),
                incoming_email=email,
                actual_reply=reply,
            )
        )
    return tickets


@st.cache_resource
def load_everything():
    cfg = load_config()
    transactions = {
        t["order_id"]: Transaction(**t)
        for t in json.loads((DATA / "transactions.json").read_text())
    }
    # Eval harness dataset — untouched. User examples are a separate production corpus.
    tickets = [Ticket(**t) for t in json.loads((DATA / "dataset.json").read_text())]
    user_tickets = user_examples_as_tickets()
    policy_path = resolve_policy_path(DATA, cfg)
    policy_store = PolicyStore(str(policy_path), config=cfg)
    # TicketRetriever unchanged: corpus split from dataset + all user examples.
    retriever = TicketRetriever(tickets + user_tickets)
    return transactions, tickets, policy_store, retriever


def update_env(updates: dict[str, str | None]) -> None:
    """Create/update keys in .env in place (value None removes the key) and
    mirror the change into os.environ so it takes effect without a restart.
    Never reads back or displays existing values."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for key, value in updates.items():
        prefix = f"{key}="
        lines = [ln for ln in lines if not ln.startswith(prefix)]
        if value is None:
            os.environ.pop(key, None)
        else:
            lines.append(f"{key}={value}")
            os.environ[key] = value
    ENV_PATH.write_text("\n".join(lines) + "\n")
