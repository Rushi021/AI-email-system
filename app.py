"""Streamlit entrypoint — pages via st.navigation.

  ✉️ Assistant             paste an email, get a suggested reply (+ lazy accuracy check)
  📥 Inbox                 sync a mailbox, route every email, queue the results
  🗂️ Review                human-action dashboard for the routed queue
  ⚙️ Settings              policy PDF, LLM provider, email connector, automation, notifications
  📊 Evaluation (internal)  batch results + metric-validation dashboards

Run: streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Page files are exec'd, not imported; make `src` and `views` importable from
# them regardless of how the app is launched (streamlit run, AppTest, ...).
sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(page_title="AI Suggested-Response System", page_icon="✉️", layout="wide")

from src.queue_store import counts  # noqa: E402

pending = counts()["pending_total"]
review_title = f"Review ({pending})" if pending else "Review"

nav = st.navigation(
    [
        st.Page("views/assistant.py", title="Assistant", icon="✉️", default=True),
        st.Page("views/inbox.py", title="Inbox", icon="📥"),
        st.Page("views/review.py", title=review_title, icon="🗂️"),
        st.Page("views/settings.py", title="Settings", icon="⚙️"),
        st.Page("views/evaluation.py", title="Evaluation (internal)", icon="📊"),
    ]
)
nav.run()
