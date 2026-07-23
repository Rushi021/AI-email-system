"""SQLite-backed review/action queue. One row per routed email; the review
dashboard reads it, the router writes it. stdlib sqlite3 only.

# ponytail: single-file SQLite, no external DB until this goes multi-tenant.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("results") / "queue.db"

# decision -> base weight for priority ordering (higher surfaces first)
DECISION_WEIGHT = {"escalate": 300.0, "review": 200.0, "auto": 100.0, "ignore": 0.0}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    email_id        TEXT PRIMARY KEY,
    thread_id       TEXT,
    from_addr       TEXT,
    subject         TEXT,
    body            TEXT,
    order_id        TEXT,
    category        TEXT,
    decision        TEXT,          -- auto | review | escalate | ignore
    status          TEXT,          -- pending | sent | simulated | dismissed
    confidence      REAL,
    priority        REAL,
    suggested_reply TEXT,
    judge           TEXT,          -- JSON blob of judge fields
    flags           TEXT,          -- JSON list
    created_at      TEXT,
    updated_at      TEXT,
    sent_at         TEXT
);
"""


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(item: dict, db_path: Path = DB_PATH) -> None:
    """Insert a routed item, or refresh it if the same email_id is re-routed.
    A human status (sent/simulated/dismissed) is preserved on re-route."""
    now = _now()
    with _conn(db_path) as conn:
        existing = conn.execute(
            "SELECT status FROM items WHERE email_id = ?", (item["email_id"],)
        ).fetchone()
        status = item.get("status", "pending")
        if existing and existing["status"] != "pending":
            status = existing["status"]  # don't clobber a human decision
        conn.execute(
            """INSERT INTO items
               (email_id, thread_id, from_addr, subject, body, order_id, category,
                decision, status, confidence, priority, suggested_reply, judge, flags,
                created_at, updated_at, sent_at)
               VALUES (:email_id,:thread_id,:from_addr,:subject,:body,:order_id,:category,
                :decision,:status,:confidence,:priority,:suggested_reply,:judge,:flags,
                :created_at,:updated_at,NULL)
               ON CONFLICT(email_id) DO UPDATE SET
                 decision=excluded.decision, status=excluded.status,
                 confidence=excluded.confidence, priority=excluded.priority,
                 suggested_reply=excluded.suggested_reply, judge=excluded.judge,
                 flags=excluded.flags, category=excluded.category,
                 order_id=excluded.order_id, updated_at=excluded.updated_at""",
            {
                "email_id": item["email_id"],
                "thread_id": item.get("thread_id", ""),
                "from_addr": item.get("from_addr", ""),
                "subject": item.get("subject", ""),
                "body": item.get("body", ""),
                "order_id": item.get("order_id", ""),
                "category": item.get("category", ""),
                "decision": item["decision"],
                "status": status,
                "confidence": item.get("confidence", 0.0),
                "priority": item.get("priority", 0.0),
                "suggested_reply": item.get("suggested_reply", ""),
                "judge": json.dumps(item.get("judge", {})),
                "flags": json.dumps(item.get("flags", [])),
                "created_at": now,
                "updated_at": now,
            },
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["judge"] = json.loads(d["judge"]) if d["judge"] else {}
    d["flags"] = json.loads(d["flags"]) if d["flags"] else []
    return d


def list_items(
    decision: str | None = None,
    status: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Items, highest priority first. Optional decision/status filters."""
    clauses, params = [], []
    if decision:
        clauses.append("decision = ?")
        params.append(decision)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM items {where} ORDER BY priority DESC, created_at ASC", params
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get(email_id: str, db_path: Path = DB_PATH) -> dict | None:
    with _conn(db_path) as conn:
        row = conn.execute("SELECT * FROM items WHERE email_id = ?", (email_id,)).fetchone()
    return _row_to_dict(row) if row else None


def set_status(
    email_id: str,
    status: str,
    *,
    suggested_reply: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    now = _now()
    sent_at = now if status in ("sent", "simulated") else None
    with _conn(db_path) as conn:
        if suggested_reply is not None:
            conn.execute(
                "UPDATE items SET status=?, suggested_reply=?, updated_at=?, sent_at=? WHERE email_id=?",
                (status, suggested_reply, now, sent_at, email_id),
            )
        else:
            conn.execute(
                "UPDATE items SET status=?, updated_at=?, sent_at=? WHERE email_id=?",
                (status, now, sent_at, email_id),
            )


def counts(db_path: Path = DB_PATH) -> dict:
    """Pending counts per decision, plus total pending — for dashboard badges."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT decision, COUNT(*) n FROM items WHERE status='pending' GROUP BY decision"
        ).fetchall()
    by = {r["decision"]: r["n"] for r in rows}
    return {
        "escalate": by.get("escalate", 0),
        "review": by.get("review", 0),
        "auto": by.get("auto", 0),
        "pending_total": sum(by.get(d, 0) for d in ("escalate", "review", "auto")),
    }


def _demo() -> None:
    """Offline self-check: insert, ordered listing, dedupe, status update."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "q.db"
        upsert({"email_id": "e1", "decision": "review", "priority": 200.0}, db)
        upsert({"email_id": "e2", "decision": "escalate", "priority": 300.0}, db)
        upsert({"email_id": "e3", "decision": "auto", "priority": 100.0}, db)

        items = list_items(db_path=db)
        assert [i["email_id"] for i in items] == ["e2", "e1", "e3"], "priority ordering"
        assert counts(db)["pending_total"] == 3
        assert len(list_items(decision="escalate", db_path=db)) == 1

        set_status("e2", "sent", db_path=db)
        assert get("e2", db)["status"] == "sent"
        assert counts(db)["escalate"] == 0, "sent item drops out of pending"

        # re-routing e2 must NOT clobber the human 'sent' status
        upsert({"email_id": "e2", "decision": "escalate", "priority": 300.0}, db)
        assert get("e2", db)["status"] == "sent", "human status preserved on re-route"
    print("queue_store self-check OK")


if __name__ == "__main__":
    _demo()
