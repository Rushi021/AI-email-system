"""Shared helpers for the Streamlit views.

Company-agnostic: every company fact (order ids, policy text, tickets) is read
from data/ at runtime — nothing here names a company, product, or policy rule.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from src.policy_store import PolicyStore
from src.retriever import TicketRetriever
from src.schema import Ticket, Transaction, detect_order_id, placeholder_transaction  # noqa: F401 (re-exported)

DATA = Path("data")
RESULTS = Path("results")
ENV_PATH = Path(".env")

PROVIDER_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


@st.cache_resource
def load_everything():
    transactions = {
        t["order_id"]: Transaction(**t)
        for t in json.loads((DATA / "transactions.json").read_text())
    }
    tickets = [Ticket(**t) for t in json.loads((DATA / "dataset.json").read_text())]
    policy_store = PolicyStore(str(DATA / "policy.pdf"))
    retriever = TicketRetriever(tickets)
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
