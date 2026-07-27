# CLAUDE.md — AI Email Suggested-Response System

Context for any Claude session working in this repo. Read this before changing code.

## What this is

A submission for the Hiver Open Challenge: generate suggested replies to customer-support
emails using an LLM grounded in a company's own data (policy PDF + transactions + past
tickets), and measure accuracy with **two unblended scores**:

1. **Response Quality (RAGAS)** — automated, reference-free, on every generated reply.
2. **System Reliability (human feedback)** — empirical, from real Review-dashboard actions only.

We simulate one fictional company (NorthPeak Outdoor Gear) but the code is company-agnostic.

## The one inviolable design rule

**Everything company-specific lives in `data/`. Nothing in `src/`, `pipeline.py`,
`api.py`, or `web/` may reference NorthPeak, a product, or a specific policy rule.** Swapping the
three data files (`policy.pdf`, `transactions.json`, `dataset.json`) for another company's
must work with zero code changes. Never hardcode a rule ID, product name, or remedy in code
or prompts — all company facts are injected at runtime from `data/`.

## Architecture

```
incoming email
      │
      ├─► PolicyStore — format-aware ingest + hybrid retrieve (BM25 + embeddings + RRF)
      │                 src/policy_ingest.py + src/policy_store.py
      │                 cache via BlobStore: policy_index/*
      ├─► intent scope — categories from the loaded policy  src/intent.py
      ├─► TicketRetriever — TF-IDF (dataset corpus + user_examples)
      └─► transaction lookup — data/transactions.json
      ▼
   generator ──► reply + cited_rules + structured remedy
      ▼
   RAGAS evaluator ──► faithfulness · relevancy · context precision
                       + dual-pass disagreement + deterministic diagnostics
                       ──► ragas_scores (StructuredStore)
      ▼
   router ──► AUTO / REVIEW / ESCALATE / IGNORE
              hard gate: faithfulness < gate OR disagreement OR scoring error → never AUTO
      ▼
   queue ──► Review dashboard ──► feedback_events ──► reliability
pipeline.py = batch CLI
UI: React + Vite (web/) over a thin FastAPI wrapper (api.py)
src/storage/ = pluggable StructuredStore + BlobStore (default local)
```

- **Two headline numbers, never blended.** Tier-1 RAGAS scores and Tier-2 reliability rates
  are stored and displayed separately. Routing-only combo:
  `quality_score = 0.5·faithfulness + 0.3·answer_relevancy + 0.2·context_precision`.
- **Hard AUTO gate:** `faithfulness < FAITHFULNESS_GATE` (default 0.7) OR
  `retrieval_disagreement == true` OR scoring failure → never AUTO.
- **Critical error rate** uses only human-labeled AUTO responses as denominator; report
  audit coverage separately. Every rate has `n` + Wilson CI; `n < 20` → insufficient data.
- **No hand labels / no synthetic controls.** `expected_outcomes.json` and
  `control_examples.json` are deleted. Do not reintroduce them.
- **Generation grounding hierarchy:** policy rule chunks determine the remedy; transaction
  confirms conditions; past tickets teach voice/tone only. Structured `remedy` object enables
  deterministic edit classification (minor vs major) at Send time.
- **RAGAS API:** pin `ragas==0.4.3`. Use `ragas.metrics.collections.*.ascore(**kwargs)`.
  Never `SingleTurnSample` / `.single_turn_ascore()`. LLM via native
  `llm_factory(model, provider=..., client=...)` for openai/mistral/anthropic.
  Embeddings via `embedding_factory("huggingface", model=...)`; hash shim only for mock.

## Automation layer (routing → queue → review)

- `src/email_parser.py` — normalize body before classify/generate.
- `src/event_bus.py` — StructuredStore table `event_bus`; retry ×3 then dead-letter.
- `src/classifier.py` — LLM triage; `other` ⇒ IGNORE.
- `src/router.py` — generate → RAGAS evaluate → decide; confidence = `quality_score * 100`
  minus deterministic penalty. Escalation from structured `remedy.escalate`.
- `src/queue_store.py` — StructuredStore table `queue`.
- `src/feedback.py` — append-only `feedback_events` from Review actions.
- `src/notify.py` — digest of pending review+escalation.
- `api.py` — FastAPI JSON wrapper over `src/` (company-agnostic; every fact still
  read from `data/`). Serves the built `web/dist` + SPA fallback in one process.
- `web/` — React + Vite frontend (JS). Pages: Assistant · Inbox · Review · Settings
  · Evaluation, calling `/api/*`.

## Storage

Third pluggable adapter (with LLM + email):

| Primitive | Interface | Default | Opt-in |
|---|---|---|---|
| Structured | `get_structured_store()` | LocalSQLiteStore | Postgres |
| Blob | `get_blob_store()` | LocalFileBlobStore | S3 / Azure / GCS / Postgres bytea |

Nothing outside `src/storage/` imports `sqlite3`, `boto3`, `azure.storage.blob`, or
`google.cloud.storage`. Bootstrap provider selection is always local `config.json` / `.env`.
Company source-of-truth files (`policy.pdf`, `transactions.json`, `dataset.json`) stay as
swap-the-files; operational data (queue, feedback, scores, index cache, generated JSON)
goes through the storage layer.

## Data-leakage rules

1. No pre-written "correct answer" file for any ticket.
2. `TicketRetriever` fits **only on `split == "corpus"`** tickets. Holdout tickets must
   never be retrievable.
3. User examples (`user_examples` table / Settings) are production-only — never the batch
   eval harness.

## LLM access

- `src/llm_client.py :: complete(system, user, max_tokens, purpose=...)` plus
  `get_sdk_client()` for RAGAS.
- `purpose="generate"` → `LLM_*`; `purpose="classify"` → `CLASSIFY_LLM_*`.
- Providers: anthropic · openai · mistral · mock.
- Never print, log, or commit key values. `.env` is gitignored.

## Environment & how to run

- Python venv at `.venv` (Python 3.12). Use `.venv/bin/python`.
- `python pipeline.py --all` → generate → RAGAS evaluate → reliability report.
- `--limit 1` = trial on one holdout ticket.
- React UI (dev): `.venv/bin/uvicorn api:app --reload --port 8000` + `cd web && npm run dev` (Vite proxies `/api` → :8000).
- React UI (single process): `cd web && npm run build`, then `uvicorn api:app --port 8000` serves `web/dist` + the API at one origin.
- Optional cloud deps: `pip install -r requirements-storage.txt`. Frontend deps: `cd web && npm install`.

## Dataset invariants (if you edit data/)

- 24 tickets: 18 `corpus` / 6 `holdout` (H01–H06).
- Every ticket's `order_id` must exist in `transactions.json`.
- Every policy rule states BOTH grant and denial conditions.

## Known gotchas

- Fail closed: RAGAS/scoring errors gate AUTO → REVIEW.
- `retrieval_disagreement` is nullable when sampling skips the check — store
  `disagreement_checked` separately; never treat "not checked" as agreement.
- A percentage with no `n` next to it must not render in the Evaluation UI.
- Pushing to GitHub is fine — create real commits, never force-push, never push secrets.
