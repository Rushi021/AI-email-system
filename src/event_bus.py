"""Durable queue between ingestion and routing. One row per parsed email —
sits between src.email_parser (produces the payload) and src.router (consumes
it) so a burst of mail doesn't get lost mid-batch and one email's processing
failure can't take the others down with it.

# ponytail: single-file SQLite in-process queue, same as queue_store.py — no
# broker (Kafka/SQS) until this needs to run across more than one process.

publish() is idempotent per email_id (re-syncing the same inbox twice is safe).
drain() is the whole retry/isolation contract: each item is processed inside
its own try/except; a raised exception increments its attempt count and either
requeues it (attempts < MAX_ATTEMPTS) or moves it to dead_letter — it never
stops the batch.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.schema import IncomingEmail

DB_PATH = Path("results") / "event_bus.db"
MAX_ATTEMPTS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    email_id    TEXT PRIMARY KEY,
    payload     TEXT,     -- JSON-serialized IncomingEmail, post-parse
    status      TEXT,     -- pending | done | dead_letter
    attempts    INTEGER DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT,
    updated_at  TEXT
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


def publish(email: IncomingEmail, db_path: Path = DB_PATH) -> None:
    """Enqueue a parsed email. Re-publishing the same email_id resets it to
    pending with a fresh payload (e.g. a re-sync picked up an edited draft)
    without touching its attempt history."""
    now = _now()
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO events (email_id, payload, status, attempts, last_error, created_at, updated_at)
               VALUES (:id, :payload, 'pending', 0, NULL, :now, :now)
               ON CONFLICT(email_id) DO UPDATE SET
                 payload=excluded.payload, status='pending', attempts=0, last_error=NULL,
                 updated_at=excluded.updated_at""",
            {"id": email.id, "payload": email.model_dump_json(), "now": now},
        )


def _pending(limit: int, db_path: Path) -> list[sqlite3.Row]:
    with _conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM events WHERE status='pending' AND attempts < ? ORDER BY created_at ASC LIMIT ?",
            (MAX_ATTEMPTS, limit),
        ).fetchall()


def _ack(email_id: str, db_path: Path) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE events SET status='done', updated_at=? WHERE email_id=?", (_now(), email_id)
        )


def _fail(email_id: str, attempts: int, error: str, db_path: Path) -> str:
    attempts += 1
    status = "dead_letter" if attempts >= MAX_ATTEMPTS else "pending"
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE events SET status=?, attempts=?, last_error=?, updated_at=? WHERE email_id=?",
            (status, attempts, error[:2000], _now(), email_id),
        )
    return status


def drain(
    process: Callable[[IncomingEmail], dict],
    limit: int = 100,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Process every pending event. Returns one result per item:
    {"email_id", "ok": bool, "result": dict | None, "error": str | None, "status": str}.
    A failure in `process` for one item never prevents the rest from running."""
    results = []
    for row in _pending(limit, db_path):
        email = IncomingEmail(**json.loads(row["payload"]))
        try:
            outcome = process(email)
            _ack(email.id, db_path)
            results.append({"email_id": email.id, "ok": True, "result": outcome, "error": None, "status": "done"})
        except Exception as exc:  # isolate: one bad email must not block the rest
            status = _fail(email.id, row["attempts"], f"{type(exc).__name__}: {exc}", db_path)
            results.append({"email_id": email.id, "ok": False, "result": None, "error": str(exc), "status": status})
    return results


def dead_letters(db_path: Path = DB_PATH) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM events WHERE status='dead_letter' ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def _demo() -> None:
    """Offline self-check: publish, isolated failure + retry, dead-letter after
    MAX_ATTEMPTS, and a clean item succeeding regardless of a broken neighbor."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bus.db"
        publish(IncomingEmail(id="ok-1", body="fine"), db)
        publish(IncomingEmail(id="bad-1", body="boom"), db)
        publish(IncomingEmail(id="ok-2", body="fine too"), db)

        def flaky(email: IncomingEmail) -> dict:
            if email.id == "bad-1":
                raise RuntimeError("simulated processing failure")
            return {"processed": email.id}

        results = drain(flaky, db_path=db)
        by_id = {r["email_id"]: r for r in results}
        assert by_id["ok-1"]["ok"] and by_id["ok-2"]["ok"], "clean items must succeed despite a bad neighbor"
        assert not by_id["bad-1"]["ok"] and by_id["bad-1"]["status"] == "pending"

        # retry twice more -> attempts hits MAX_ATTEMPTS -> dead_letter
        drain(flaky, db_path=db)
        final = drain(flaky, db_path=db)
        assert final[0]["status"] == "dead_letter", final
        assert len(dead_letters(db)) == 1

        # re-publishing the dead letter resets it to pending and it can be drained again
        publish(IncomingEmail(id="bad-1", body="fixed now"), db)
        recovered = drain(lambda e: {"processed": e.id}, db_path=db)
        assert recovered[0]["ok"] is True
        assert len(dead_letters(db)) == 0

    print("event_bus self-check OK")


if __name__ == "__main__":
    _demo()
