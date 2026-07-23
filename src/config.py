"""Non-secret runtime configuration (thresholds, connector choice, notification
settings) persisted to config.json. Secrets (API keys, MCP token) never live
here — they stay in .env via views/common.py::update_env.

Company-agnostic: these are product settings, not company facts.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path("config.json")

DEFAULTS: dict = {
    "email_source": "demo",  # demo | mcp
    # routing thresholds on the 0-100 live-confidence scale
    "t1": 80.0,  # >= t1 (and clean) -> auto-reply
    "t2": 50.0,  # <  t2            -> escalate
    "live_send": False,  # dry-run by default; nothing is actually sent until True
    "digest_enabled": False,
    "digest_recipient": "",
    # MCP tool-name overrides (blank -> auto-map by capability). Defaults match
    # common Gmail MCP servers; override per-server if names differ.
    "mcp_tool_search": "",
    "mcp_tool_get": "",
    "mcp_tool_send": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return cfg
