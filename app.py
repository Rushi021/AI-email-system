"""Streamlit entrypoint — pages via st.navigation.

  Assistant · Inbox · Review · Settings · Evaluation

Run: streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(page_title="AI Suggested-Response System", page_icon="✉️", layout="wide")

from views.common import inject_theme  # noqa: E402

inject_theme()

from src.queue_store import counts  # noqa: E402

pending = counts()["pending_total"]
review_title = f"Review ({pending})" if pending else "Review"

nav = st.navigation(
    [
        st.Page("views/assistant.py", title="Assistant", icon="✉️", default=True),
        st.Page("views/inbox.py", title="Inbox", icon="📥"),
        st.Page("views/review.py", title=review_title, icon="🗂️"),
        st.Page("views/settings.py", title="Settings", icon="⚙️"),
        st.Page("views/evaluation.py", title="Evaluation", icon="📊"),
    ]
)
nav.run()
