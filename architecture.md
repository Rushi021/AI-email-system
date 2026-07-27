# Architecture — AI Email Suggested-Response System

Repo-accurate map of the implemented pipeline. For the interactive Mermaid diagrams and
step cards, open **[`architecture.html`](./architecture.html)** in a browser.

Two unblended scores drive trust:

1. **Tier 1 — RAGAS response quality** (automated, every reply)
2. **Tier 2 — System reliability** (human Review feedback only)

Company-specific facts live only in `data/`. Code in `src/` is company-agnostic.

---

## Three entry paths

| Path | Entry | What runs |
|---|---|---|
| **Live** | Streamlit Inbox → Review | fetch → parse → event bus → classify → generate → RAGAS → route → queue → feedback |
| **Batch** | `python pipeline.py --all` | holdout generate → RAGAS evaluate → reliability report (no classifier/router/queue) |
| **Assist** | Streamlit Assistant | `generate_reply` only; optional on-demand RAGAS |

---

## Live pipeline (12 stages)

```
email_source.fetch_unread (demo | mcp)
        │
        ▼
email_parser.parse          # HTML / quotes / auth_status
        │
        ▼
event_bus.publish → drain   # retry ×3 → dead_letter
        │
        ▼
classifier.classify         # LLM · other → IGNORE (skip generate/eval)
        │
        ▼
detect_order_id + intent    # transactions.json · policy category scope
        │
        ├─► PolicyStore     # BM25 + embeddings + RRF (BlobStore cache)
        ├─► TicketRetriever # TF-IDF (corpus + user_examples in live UI)
        └─► transaction
        ▼
generator.generate_reply    # reply + cited_rules + structured remedy
        │
        ▼
ragas_evaluator             # faithfulness · relevancy · context precision
  + dual-pass disagreement (sampled)
  + deterministic diagnostics (non-blended)
  → ragas_scores table
        │
        ▼
router._decide              # AUTO / REVIEW / ESCALATE / IGNORE
  hard gate: faithfulness < gate OR disagreement OR scoring error OR flags
  → never AUTO
        │
        ▼
queue_store.upsert          # StructuredStore · optional notify digest
        │
        ▼
Review dashboard            # send / edit / dismiss / flag / audit
  → feedback_events
        │
        ▼
reliability                 # critical_error_rate · n + Wilson CI
```

### Hard AUTO gates

Never AUTO when any of:

- `faithfulness < FAITHFULNESS_GATE` (default `0.7`)
- `retrieval_disagreement == true`
- `scoring_error` non-empty
- any deterministic flag (placeholders, length, absolute claims, missing order id)

`retrieval_disagreement` is nullable when sampling skips the check — store
`disagreement_checked` separately; never treat “not checked” as agreement.

### Routing-only quality score (0–1)

```
quality_score = 0.5·faithfulness + 0.3·answer_relevancy + 0.2·context_precision
confidence    = clamp(quality_score × 100 − deterministic_penalty, 0, 100)
```

Tier-1 and Tier-2 numbers are **never blended** in the Evaluation UI.

### Structured remedy

```
{ remedy_type, remedy_amount, rule_cited, escalate }
```

Policy determines the remedy; past tickets teach voice/tone only. At Send time,
remedy field diffs classify `EDITED_MAJOR` vs `EDITED_MINOR` (not another LLM judge).

**AUTO never auto-sends** — it only queues a draft. `live_send` gates Review’s Send.

---

## Batch pipeline

```
data/{policy, transactions, dataset}
        │
        ▼
PolicyStore + TicketRetriever(fit corpus only)
        │
        ▼
generate_reply (holdout)  → results/generated_replies.json (+ BlobStore)
        │
        ▼
evaluate_generated (RAGAS) → results/evaluation_results.json + ragas_scores
        │
        ▼
validate_metric (feedback_events → reliability) → results/validation_report.json
```

---

## Storage

| Primitive | Interface | Default | Opt-in |
|---|---|---|---|
| Structured | `get_structured_store()` | Local SQLite | Postgres |
| Blob | `get_blob_store()` | Local filesystem | S3 / Azure / GCS / Postgres |

Tables (local files under `results/`): `queue`, `event_bus`, `feedback_events`,
`ragas_scores`, `user_examples`. Policy index cache: BlobStore `policy_index/*`.

---

## Module map

| Module | Role |
|---|---|
| `src/email_source.py` | Inbox connector (`demo` \| `mcp`) |
| `src/email_parser.py` | Normalize body + auth signal |
| `src/event_bus.py` | Ingestion queue, retry ×3, dead-letter |
| `src/classifier.py` | LLM triage; `other` → IGNORE |
| `src/policy_ingest.py` | PDF/DOCX/MD/txt → sections |
| `src/policy_store.py` | Hybrid BM25 + embeddings + RRF |
| `src/intent.py` | Retrieval scoping from policy categories |
| `src/retriever.py` | TF-IDF past tickets |
| `src/generator.py` | Reply + cited_rules + remedy |
| `src/ragas_evaluator.py` | Tier-1 scores + disagreement + AUTO gate |
| `src/evaluator.py` | RAGAS orchestration + deterministic checks |
| `src/router.py` | AUTO / REVIEW / ESCALATE / IGNORE |
| `src/queue_store.py` | Review queue |
| `src/feedback.py` | Review labels → `feedback_events` |
| `src/reliability.py` | Rates + Wilson CIs + calibration |
| `src/storage/` | Pluggable StructuredStore + BlobStore |
| `pipeline.py` | Batch CLI |
| `app.py` + `views/` | Streamlit UI |

Open [`architecture.html`](./architecture.html) for the full diagram and stage cards.
