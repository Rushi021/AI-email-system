"""FastAPI backend for the React frontend.

Thin JSON wrapper over the existing `src/` pipeline — nothing company-specific
lives here (every fact is still read from data/ at runtime). Mirrors exactly what
the Streamlit views did; the React app is a drop-in replacement for the UI only.

Run: .venv/bin/uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import email_source, event_bus, feedback, llm_client, notify, queue_store, router
from src.classifier import CATEGORY_LABELS
from src.config import DEFAULTS, load_config, save_config
from src.evaluator import evaluate_generated
from src.generator import generate_reply
from src.policy_store import PolicyStore, resolve_policy_path
from src.retriever import TicketRetriever
from src.schema import GeneratedReply, IncomingEmail, Remedy, Ticket, Transaction, detect_order_id, placeholder_transaction
from src.storage.factory import get_structured_store
from src.validate_metric import build_reliability_report
from src.app_data import (
    DATA,
    PROVIDER_KEY_VARS,
    RESULTS,
    load_user_examples,
    save_user_examples,
    update_env,
    user_examples_as_tickets,
)

app = FastAPI(title="AI Suggested-Response API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ponytail: dev-open CORS; lock to the deployed origin in prod
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------- pipeline resources
# Replaces views.common.load_everything (which used @st.cache_resource). Cached in
# a module global; cleared when policy or user examples change.
_CACHE: dict | None = None


def _resources():
    global _CACHE
    if _CACHE is None:
        cfg = load_config()
        transactions = {
            t["order_id"]: Transaction(**t)
            for t in json.loads((DATA / "transactions.json").read_text())
        }
        tickets = [Ticket(**t) for t in json.loads((DATA / "dataset.json").read_text())]
        policy_store = PolicyStore(str(resolve_policy_path(DATA, cfg)), config=cfg)
        retriever = TicketRetriever(tickets + user_examples_as_tickets())
        _CACHE = {
            "transactions": transactions,
            "tickets": tickets,
            "policy_store": policy_store,
            "retriever": retriever,
        }
    return _CACHE


def _clear_resources() -> None:
    global _CACHE
    _CACHE = None


def _txn_for(order_id: str | None):
    txns = _resources()["transactions"]
    if order_id and order_id in txns:
        return txns[order_id]
    return placeholder_transaction()


# ============================================================== bootstrap / shared
@app.get("/api/bootstrap")
def bootstrap():
    r = _resources()
    cfg = load_config()
    return {
        "orders": [
            {"order_id": oid, "product": t.product, "price": t.price, "status": t.status}
            for oid, t in sorted(r["transactions"].items())
        ],
        "config": {
            "email_source": cfg["email_source"],
            "live_send": cfg["live_send"],
            "t1": cfg["t1"],
            "t2": cfg["t2"],
        },
        "categories": r["policy_store"].categories(),
        "queue_counts": queue_store.counts(),
    }


# ===================================================================== Assistant
class SuggestReq(BaseModel):
    email: str
    order_id: str | None = None


@app.post("/api/assistant/suggest")
def assistant_suggest(req: SuggestReq):
    if not req.email.strip():
        raise HTTPException(400, "email is required")
    r = _resources()
    txns = r["transactions"]
    detected = detect_order_id(req.email, txns)
    order_id = req.order_id or detected
    txn = _txn_for(order_id)
    gen = generate_reply(req.email, txn, r["policy_store"], r["retriever"])
    return {
        "detected_order_id": detected,
        "order_id": order_id or "",
        "gen": gen.model_dump(),
    }


class EvalReq(BaseModel):
    email: str
    order_id: str | None = None
    gen: dict  # a GeneratedReply.model_dump() from /suggest


@app.post("/api/assistant/evaluate")
def assistant_evaluate(req: EvalReq):
    r = _resources()
    txn = _txn_for(req.order_id)
    gen = GeneratedReply(**req.gen)
    live = Ticket(
        ticket_id="LIVE",
        order_id=txn.order_id,
        category="live",
        split="holdout",
        sentiment="neutral",
        incoming_email=req.email,
        actual_reply=gen.reply,
    )
    ev = evaluate_generated(live, txn, gen, r["policy_store"])
    return ev.model_dump()


# ========================================================================= Inbox
class SyncReq(BaseModel):
    limit: int = 20


@app.post("/api/inbox/sync")
def inbox_sync(req: SyncReq):
    from collections import Counter

    from src.email_parser import parse as parse_email

    r = _resources()
    cfg = load_config()
    try:
        emails = email_source.fetch_unread(int(req.limit), cfg)
    except Exception as exc:  # connector/credentials problem — surface, don't crash
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    if not emails:
        return {"fetched": 0, "tally": {}, "failed": [], "digest": None}

    for e in emails:
        event_bus.publish(parse_email(e))

    def _process(email: IncomingEmail) -> dict:
        item = router.route_email(email, r["transactions"], r["policy_store"], r["retriever"], cfg)
        queue_store.upsert(item)
        return item

    outcomes = event_bus.drain(_process, limit=len(emails))
    items = [o["result"] for o in outcomes if o["ok"]]
    failed = [{"email_id": o["email_id"], "status": o["status"]} for o in outcomes if not o["ok"]]
    tally = Counter(i["decision"] for i in items)
    digest = None
    if cfg.get("digest_enabled") and cfg.get("digest_recipient"):
        digest = notify.send_digest(cfg)
    return {
        "fetched": len(items) + len(failed),
        "tally": {
            "auto": tally.get("auto", 0),
            "review": tally.get("review", 0),
            "escalate": tally.get("escalate", 0),
            "ignore": tally.get("ignore", 0),
        },
        "failed": failed,
        "max_attempts": event_bus.MAX_ATTEMPTS,
        "digest": digest,
    }


class RouteOneReq(BaseModel):
    body: str
    subject: str = ""


@app.post("/api/inbox/route-one")
def inbox_route_one(req: RouteOneReq):
    from src.email_parser import parse as parse_email

    if not req.body.strip():
        raise HTTPException(400, "body is required")
    r = _resources()
    cfg = load_config()
    email = parse_email(IncomingEmail(
        id=f"manual-{abs(hash(req.body)) % 10**8}",
        subject=req.subject,
        body=req.body,
        from_addr="pasted@manual",
    ))
    try:
        item = router.route_email(email, r["transactions"], r["policy_store"], r["retriever"], cfg)
        queue_store.upsert(item)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
    return item


# ========================================================================= Review
@app.get("/api/queue")
def queue_list(decision: str | None = None, status: str | None = None):
    return queue_store.list_items(decision=decision, status=status)


@app.get("/api/queue/counts")
def queue_counts():
    return queue_store.counts()


def _record(it: dict, label: str, remedy_diff_payload=None) -> None:
    feedback.record_feedback(
        response_id=it.get("response_id") or it["email_id"],
        ticket_id=it["email_id"],
        category=it.get("category") or "",
        cited_rule=(it.get("remedy") or {}).get("rule_cited")
        or (it.get("judge") or {}).get("cited_rule")
        or "",
        routing_decision=it.get("decision") or "",
        ragas_scores=it.get("ragas") or {},
        label=label,
        remedy_diff_payload=remedy_diff_payload,
    )


class SendReq(BaseModel):
    edited: str


@app.post("/api/queue/{email_id}/send")
def queue_send(email_id: str, req: SendReq):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    cfg = load_config()
    dry = not cfg["live_send"]
    to = IncomingEmail(
        id=it["email_id"], thread_id=it["thread_id"],
        from_addr=it["from_addr"], subject=it["subject"],
    )
    original = it.get("original_reply") or it["suggested_reply"]
    original_remedy = Remedy(**(it.get("remedy") or {}))
    label, diff_payload = feedback.classify_send_label(original, req.edited, original_remedy)
    try:
        res = email_source.send_reply(to, req.edited, dry_run=dry, config=cfg)
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    queue_store.set_status(email_id, "simulated" if dry else "sent", suggested_reply=req.edited)
    _record(it, label, remedy_diff_payload=diff_payload or None)
    return {"detail": res["detail"], "label": label}


@app.post("/api/queue/{email_id}/save")
def queue_save(email_id: str, req: SendReq):
    if not queue_store.get(email_id):
        raise HTTPException(404, "not found")
    queue_store.set_status(email_id, "pending", suggested_reply=req.edited)
    return {"ok": True}


@app.post("/api/queue/{email_id}/dismiss")
def queue_dismiss(email_id: str):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    _record(it, "ESCALATED_CORRECTLY" if it.get("decision") == "escalate" else "REJECTED")
    queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


@app.post("/api/queue/{email_id}/flag")
def queue_flag(email_id: str):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    _record(it, "FLAGGED_HALLUCINATION")
    queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


class AuditReq(BaseModel):
    ok: bool  # True = AUTO was fine, False = escalation was missed


@app.post("/api/queue/{email_id}/audit")
def queue_audit(email_id: str, req: AuditReq):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    if req.ok:
        _record(it, "ACCEPTED_AS_IS")
    else:
        _record(it, "ESCALATED_MISSED")
        queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


@app.post("/api/notify/digest")
def send_digest():
    return notify.send_digest(load_config())


# ===================================================================== Evaluation
@app.get("/api/evaluation/quality")
def evaluation_quality():
    eval_path = RESULTS / "evaluation_results.json"
    ragas_rows = get_structured_store().query("ragas_scores", order_by="-timestamp")
    results = json.loads(eval_path.read_text()) if eval_path.exists() else []
    source = ragas_rows if ragas_rows else results
    # Drop offline mock rows so the Evaluation page never shows stub scores as real.
    def _is_mock(row: dict) -> bool:
        details = row.get("faithfulness_details") or {}
        blob = details if isinstance(details, str) else json.dumps(details)
        return "mock ragas" in blob.lower() or "mock ragas" in str(row.get("note", "")).lower()

    source = [r for r in source if not _is_mock(r)]
    if not source:
        return {"rows": [], "averages": None}

    def avg(key):
        vals = [r[key] for r in source if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None, len(vals)

    faith, nf = avg("faithfulness")
    relev, nr = avg("answer_relevancy")
    prec, np_ = avg("context_precision")
    gated = sum(1 for r in source if r.get("gated_from_auto"))
    return {
        "rows": source,
        "averages": {
            "faithfulness": faith, "n_faithfulness": nf,
            "answer_relevancy": relev, "n_answer_relevancy": nr,
            "context_precision": prec, "n_context_precision": np_,
            "gated": gated, "total": len(source),
        },
    }


@app.get("/api/evaluation/reliability")
def evaluation_reliability():
    return build_reliability_report()


# ======================================================================= Settings
@app.get("/api/settings")
def settings_get():
    cfg = load_config()
    r = _resources()
    ps = r["policy_store"]
    gen_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    cls_provider = (os.getenv("CLASSIFY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "anthropic").lower()
    return {
        "config": cfg,
        "policy": {
            "filename": cfg.get("policy_filename") or "policy.pdf",
            "rules": len(ps.rules),
            "categories": ps.categories(),
            "preview": ps.chunks[0] if ps.chunks else "",
        },
        "providers": [
            {
                "name": p,
                "configured": bool(os.getenv(key_var)),
                "used_for": [u for u, prov in (("generation", gen_provider), ("categorization", cls_provider)) if prov == p],
            }
            for p, key_var in PROVIDER_KEY_VARS.items()
        ],
        "llm": {
            "gen_provider": gen_provider,
            "gen_model": os.getenv("LLM_MODEL", ""),
            "cls_provider": cls_provider,
            "cls_model": os.getenv("CLASSIFY_LLM_MODEL", ""),
            "default_models": llm_client.DEFAULT_MODELS,
        },
        "category_labels": CATEGORY_LABELS,
        "examples": load_user_examples(),
        "defaults": DEFAULTS,
    }


class ConfigReq(BaseModel):
    updates: dict


@app.post("/api/settings/config")
def settings_config(req: ConfigReq):
    if "t1" in req.updates and "t2" in req.updates and req.updates["t2"] >= req.updates["t1"]:
        raise HTTPException(400, "T2 must be below T1.")
    save_config(req.updates)
    _clear_resources()
    return {"ok": True, "config": load_config()}


class EnvReq(BaseModel):
    updates: dict  # value None removes the key; keys are env var names


@app.post("/api/settings/env")
def settings_env(req: EnvReq):
    update_env(req.updates)
    return {"ok": True}


class LLMSaveReq(BaseModel):
    step: str  # "generate" | "classify"
    provider: str
    model: str = ""
    api_key: str = ""


@app.post("/api/settings/llm")
def settings_llm(req: LLMSaveReq):
    if req.provider not in PROVIDER_KEY_VARS:
        raise HTTPException(400, f"unknown provider {req.provider}")
    prefix = "" if req.step == "generate" else "CLASSIFY_"
    updates: dict[str, str | None] = {
        f"{prefix}LLM_PROVIDER": req.provider,
        f"{prefix}LLM_MODEL": req.model.strip() or None,
    }
    if req.api_key.strip():
        updates[PROVIDER_KEY_VARS[req.provider]] = req.api_key.strip()
    update_env(updates)
    return {"ok": True}


@app.post("/api/settings/test/{target}")
def settings_test(target: str):
    try:
        if target in ("generate", "classify"):
            out = llm_client.complete(
                "You are a connectivity check. Reply with the single word OK.",
                "ping", max_tokens=8, purpose=target,
            )
            p, m = llm_client.resolve_provider_model(target)
            return {"ok": True, "detail": f"{p} ({m}) replied: {out.strip()[:40]}"}
        if target == "inbox":
            got = email_source.fetch_unread(1, load_config())
            return {"ok": True, "detail": f"fetched {len(got)} message(s)"}
        if target == "storage":
            from src.storage.factory import get_blob_store
            get_structured_store().test_connection()
            get_blob_store().test_connection()
            return {"ok": True, "detail": "structured + blob connected"}
        raise HTTPException(404, f"unknown test target {target}")
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


class ExamplesReq(BaseModel):
    examples: list[dict]


@app.post("/api/settings/examples")
def settings_examples(req: ExamplesReq):
    for i, row in enumerate(req.examples):
        if not row.get("ticket_id"):
            row["ticket_id"] = f"U{i + 1:03d}"
    save_user_examples(req.examples)
    _clear_resources()
    return {"ok": True, "examples": load_user_examples()}


@app.post("/api/settings/policy")
async def settings_policy(file: UploadFile):
    suffix = Path(file.filename or "policy.pdf").suffix.lower() or ".pdf"
    new_name = f"policy{suffix}"
    raw = await file.read()
    (DATA / new_name).write_bytes(raw)
    try:
        from src.storage.factory import get_blob_store
        get_blob_store().put(f"policy/{new_name}", raw)
    except Exception:
        pass
    for p in DATA.glob("policy.*"):
        if p.name != new_name:
            try:
                p.unlink()
            except OSError:
                pass
    save_config({"policy_filename": new_name})
    _clear_resources()
    ps = _resources()["policy_store"]
    return {"ok": True, "filename": new_name, "rules": len(ps.rules)}


# --------------------------------------------------------- serve built frontend
# Assets from the mount; every other non-/api path falls back to index.html so
# client-side routes (/review, /settings, …) survive a hard refresh.
_DIST = Path("web/dist")
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(_DIST / "index.html")
