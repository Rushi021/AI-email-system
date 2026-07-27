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

_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg: #08080a;
  --panel: #131316;
  --panel-2: #17171b;
  --border: #26262c;
  --border-soft: #1e1e23;
  --text: #ededf0;
  --muted: #9a9aa6;
  --accent: #6d5ef6;
  --accent-2: #8b7cf8;
}

/* --- base typography & canvas --- */
html, body, .stApp, [class*="css"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
  -webkit-font-smoothing: antialiased;
}
.stApp { background: radial-gradient(1200px 600px at 20% -10%, #14121f 0%, var(--bg) 55%) fixed; }

/* tighten and center the main column */
.block-container { padding-top: 3rem !important; padding-bottom: 4rem !important; max-width: 1080px; }

/* headings */
h1, h2, h3 { letter-spacing: -0.02em !important; font-weight: 700 !important; }
h1 { font-size: 2rem !important; background: linear-gradient(180deg,#fff, #c9c9d4); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
[data-testid="stCaptionContainer"], .stCaption, small { color: var(--muted) !important; }

/* --- sidebar / nav --- */
[data-testid="stSidebar"] { border-right: 1px solid var(--border-soft); }
[data-testid="stSidebarNav"] a { border-radius: 10px; margin: 2px 8px; transition: background .15s ease, color .15s ease; }
[data-testid="stSidebarNav"] a:hover { background: rgba(109,94,246,.10); }
[data-testid="stSidebarNav"] a[aria-current="page"] {
  background: linear-gradient(90deg, rgba(109,94,246,.22), rgba(109,94,246,.04));
  box-shadow: inset 2px 0 0 var(--accent);
}

/* --- buttons --- */
.stButton > button, .stDownloadButton > button {
  border-radius: 10px !important;
  border: 1px solid var(--border) !important;
  font-weight: 600 !important;
  transition: transform .12s ease, box-shadow .2s ease, border-color .2s ease, background .2s ease;
}
.stButton > button:hover { transform: translateY(-1px); border-color: var(--accent) !important; }
.stButton > button[kind="primary"] {
  background: linear-gradient(180deg, var(--accent-2), var(--accent)) !important;
  border: none !important;
  box-shadow: 0 6px 20px -6px rgba(109,94,246,.65), inset 0 1px 0 rgba(255,255,255,.18);
}
.stButton > button[kind="primary"]:hover { box-shadow: 0 10px 28px -6px rgba(109,94,246,.8); }

/* --- inputs --- */
[data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input,
[data-baseweb="select"] > div, [data-testid="stNumberInput"] input {
  background: var(--panel) !important;
  border-radius: 10px !important;
}
[data-testid="stTextArea"] textarea:focus, [data-testid="stTextInput"] input:focus {
  border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(109,94,246,.18) !important;
}

/* --- metrics as cards --- */
[data-testid="stMetric"] {
  background: linear-gradient(180deg, var(--panel-2), var(--panel));
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 1px 0 rgba(255,255,255,.03) inset, 0 8px 24px -18px #000;
}
[data-testid="stMetricValue"] { font-weight: 700 !important; letter-spacing: -0.02em; }
[data-testid="stMetricLabel"] { color: var(--muted) !important; text-transform: uppercase; font-size: .72rem !important; letter-spacing: .06em; }

/* --- expanders & containers --- */
[data-testid="stExpander"] {
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  background: var(--panel);
  overflow: hidden;
}
[data-testid="stExpander"] summary:hover { color: var(--accent-2); }

/* --- tabs --- */
[data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--border-soft); }
[data-baseweb="tab"] { border-radius: 10px 10px 0 0; }
[data-baseweb="tab"][aria-selected="true"] { color: var(--text) !important; }
[data-baseweb="tab-highlight"] { background: var(--accent) !important; height: 2px; }

/* --- code / info / alert blocks --- */
[data-testid="stCode"], pre, code { font-family: 'JetBrains Mono', monospace !important; }
[data-testid="stCode"] { border: 1px solid var(--border) !important; border-radius: 12px !important; }
[data-testid="stAlert"] { border-radius: 12px !important; border: 1px solid var(--border) !important; }

hr { border-color: var(--border-soft) !important; }

/* hide the default Streamlit chrome for a cleaner product feel */
#MainMenu, [data-testid="stToolbar"] { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; }
</style>
"""


def inject_theme() -> None:
    """Inject the premium CSS layer. Called once from app.py; applies to every page."""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)

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
