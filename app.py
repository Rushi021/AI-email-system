"""Streamlit entrypoint — three pages via st.navigation.

  ✉️ Assistant             paste an email, get a suggested reply (+ lazy accuracy check)
  ⚙️ Settings              swap the policy PDF, manage provider API keys
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

nav = st.navigation(
    [
        st.Page("views/assistant.py", title="Assistant", icon="✉️", default=True),
        st.Page("views/settings.py", title="Settings", icon="⚙️"),
        st.Page("views/evaluation.py", title="Evaluation (internal)", icon="📊"),
    ]
)
nav.run()
