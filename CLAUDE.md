# CLAUDE.md — AI Email Suggested-Response System

Context for any Claude session working in this repo. Read this before changing code.

## What this is

A submission for the Hiver Open Challenge: generate suggested replies to customer-support
emails using an LLM grounded in a company's own data (policy PDF + transactions + past
tickets), and — the heaviest-graded part — **measure accuracy with a validated metric**.
We simulate one fictional company (NorthPeak Outdoor Gear) but the code is company-agnostic.

## The one inviolable design rule

**Everything company-specific lives in `data/`. Nothing in `src/`, `pipeline.py`,
`app.py`, or `views/` may reference NorthPeak, a product, or a specific policy rule.** Swapping the
three data files (`policy.pdf`, `transactions.json`, `dataset.json`) for another company's
must work with zero code changes. Never hardcode a rule ID, product name, or remedy in code
or prompts — all company facts are injected at runtime from `data/`.

## Architecture

```
incoming email ─► PolicyStore (TF-IDF over any PDF)      src/policy_store.py
             ─► TicketRetriever (TF-IDF, corpus split)   src/retriever.py
             ─► Transaction lookup                       data/transactions.json
             ─► generator (LLM, RAG prompt)              src/generator.py + src/prompts.py
             ─► evaluator (3 layers + penalties)         src/evaluator.py
             ─► metric validation (3 checks)             src/validate_metric.py
pipeline.py = batch CLI · app.py + views/ = Streamlit UI · results/*.json = outputs

Automation layer (built on the same generator + evaluator):
  inbox (MCP server | demo)  ─► email_source.py  ─► IncomingEmail[]
                             ─► router.py  (reuses generate_reply + evaluate_reply)
                                  AUTO / REVIEW / ESCALATE / IGNORE + confidence + priority
                             ─► queue_store.py (SQLite results/queue.db)
                             ─► notify.py (email digest via the connector)
Streamlit st.navigation: Assistant · Inbox · Review · Settings · Evaluation.
Non-secret runtime config in config.json (src/config.py); secrets stay in .env.
```

- **Scoring formula** (do not change weights casually — they're justified in README §5):
  `final = max(0, 0.45·policy_compliance + 0.25·alignment + 0.30·quality − penalty)`,
  each component 0–100, penalty capped at 20. Quality sub-weights: groundedness .25,
  tone .25, clarity .20, actionability .30. Lexical overlap (difflib) is **reported,
  never blended**.
- **Compliance judge** derives the correct remedy itself from the policy text. It gets the
  **full policy** when the document is ≤12K chars (retrieval ranking once made it fixate on
  the wrong rule), top-k chunks otherwise. Its `rule` field must be a bare ID ("R1.1") —
  the prompt enforces this; `validate_metric.py` compares that ID against hand labels.

## Automation layer (routing → queue → review)

- **`src/router.py`** turns one `IncomingEmail` into a decision, reusing `generate_reply`
  + `evaluate_reply` unchanged. Live emails have no human reply, so alignment is dropped and
  **live confidence** = `clamp(0.6·policy_score + 0.4·quality_score − penalty, 0, 100)`.
  Decision: ESCALATE if the judge's `escalate` flag is true or `conf < t2`; AUTO if
  `conf ≥ t1` AND zero deterministic flags AND a rule was cited AND not escalate; else REVIEW;
  IGNORE for non-support/no-order noise (cheap keyword gate — no LLM spent).
- **Escalation is company-agnostic:** the compliance judge emits a boolean `escalate` +
  `escalate_reason` derived from the policy text (prompts.py / EvaluationResult). Never hardcode
  R6/R7 or any rule id in router code. Thresholds `t1`/`t2` live in config.json, not code.
- **`src/email_source.py`** — one connector interface (like llm_client). `demo` reads
  `data/demo_inbox.json` (offline, no creds); `mcp` is an MCP client to a Gmail MCP server
  (`MCP_SERVER_URL` + `MCP_AUTH_TOKEN` in .env, `mcp` SDK, async wrapped in `asyncio.run`).
  Tool names auto-map by capability (`_pick_tool`) with config overrides; the `mcp` import is
  lazy so the app runs without the SDK. Live sends are gated by `config["live_send"]` (default
  off = dry-run; `send_reply(..., dry_run=...)`).
- **`src/queue_store.py`** — SQLite queue at `results/queue.db` (gitignored). `upsert` dedupes on
  `email_id` and never clobbers a human status (sent/simulated/dismissed) on re-route. Priority =
  decision band (escalate>review>auto) + value + frustration; dashboard sorts desc.
- **`src/notify.py`** — `send_digest` emails pending review+escalation items via the connector;
  fired on demand or after a sync when `digest_enabled`. No scheduler yet.
- Each of router / queue_store / email_source has a `python -m src.<mod>` offline self-check.

## Data-leakage rules (grading depends on these)

1. `data/expected_outcomes.json` (hand-labeled ground truth) is read **only** by
   `src/validate_metric.py`. Never feed it to the generator or the per-response evaluator.
2. `TicketRetriever` fits **only on `split == "corpus"`** tickets. Holdout tickets must
   never be retrievable.
3. Control examples (`data/control_examples.json`) are scored by the same evaluator as
   generated replies — that's the point (discriminative check).

## LLM access

- One interface: `src/llm_client.py :: complete(system, user, max_tokens)`. All generation
  and judge calls go through it. Provider via `LLM_PROVIDER` env:
  `anthropic` (default, `claude-opus-4-8`) · `openai` (`gpt-4o`) ·
  `mistral` (`mistral-small-latest`, via Mistral's OpenAI-compatible endpoint at
  `https://api.mistral.ai/v1`, throttled to ~1 req/s for the free tier, `max_retries=8`) ·
  `mock` (deterministic offline stub for plumbing tests only — scores are meaningless).
- **Current active setup: `LLM_PROVIDER=mistral` with `MISTRAL_API_KEY` in `.env`**
  (free tier — keep calls frugal; a full pipeline run ≈ 24 calls).
- `LLM_MODEL` overrides the model. Judges return JSON parsed by
  `llm_client.extract_json()` (handles fences/prose — don't replace with bare
  `json.loads`).
- Never print, log, or commit key values. `.env` is gitignored.

## Environment & how to run

- Python venv at `.venv` (Python 3.12 via Homebrew — system python is 3.9, too old).
  Use `.venv/bin/python` / `.venv/bin/streamlit`, not bare `python`.
- `python pipeline.py --all` → writes `results/generated_replies.json`,
  `evaluation_results.json`, `validation_report.json`, prints summary.
- `--limit 1` = trial mode: exactly 5 LLM calls (1 generation + 2 evaluations × 2 calls).
- **`EVAL_TODAY=2026-07-07`** must be set when evaluating: dataset dates are anchored
  around 2026-07-07 and the date-window rules (30/90-day returns, 1-year warranty) resolve
  wrong otherwise.
- `streamlit run app.py` for the UI. Verify UI changes with
  `streamlit.testing.v1.AppTest` (see git history) rather than manual clicking.
- `scripts/build_policy_pdf.py` regenerates `data/policy.pdf` (fpdf2 — `multi_cell`
  needs `new_x="LMARGIN", new_y="NEXT"` or it crashes).

## Dataset invariants (if you edit data/)

- 24 tickets: 18 `corpus` / 6 `holdout` (H01–H06). Holdouts are *scenarios distinct from
  corpus*, each targeting a different policy branch incl. escalations (R6 high-value,
  R7 frequent-returner) and one branch with no corpus precedent (H06, unconfirmed
  duplicate charge).
- Every ticket's `order_id` must exist in `transactions.json`; the transaction's fields
  must actually satisfy the policy branch the ticket is supposed to exercise (the judge
  reads the transaction record).
- Every policy rule states BOTH grant and denial conditions — required so the compliance
  judge can tell right from wrong.
- If you add/change holdouts or controls, update `expected_outcomes.json` labels
  (remedy + rule ID) by hand-reading the policy — they are the trust anchor for check 3.

## Known gotchas / past mistakes (don't repeat)

- Judge `rule` field: small models write sentences into it unless the prompt demands the
  bare identifier — keep the "Field rules" block in `COMPLIANCE_JUDGE_SYSTEM`.
- Feeding the judge top-k chunks instead of the full (small) policy caused a wrong-rule
  fixation. Keep the ≤12K-chars full-document path in `evaluator.py`.
- `ALIGNMENT_QUALITY_SYSTEM` is `.format()`ed → literal JSON braces in it must be `{{ }}`.
  `COMPLIANCE_JUDGE_SYSTEM` is NOT formatted → single braces are fine there.
- Committed `results/*.json` should come from a real provider run, not `mock`.
- Don't push to GitHub; commit locally at milestones.

## Current state (as of 2026-07-21)

- **Automation layer added** (router / email_source / queue_store / notify / config) plus
  Inbox + Review Streamlit pages and expanded Settings (email connector, thresholds, live-send,
  notifications). Connector = MCP client + offline demo inbox (`data/demo_inbox.json`, 5 emails
  covering auto/review/escalate×2/ignore).
- **Verified offline:** all three module self-checks pass; mock end-to-end sync→route→queue with
  correct priority ordering + IGNORE gating; interactive AppTest (Inbox sync populates queue,
  Review simulate-send flips status); batch `pipeline.py --all --limit 1` regression green with the
  new schema; every Streamlit page renders exception-free.
- **NOT yet verified live:** the Mistral free-tier key in `.env` now returns **401 Unauthorized**
  (worked at build time, since expired). Semantic routing correctness (judge citing R6/R7 and
  setting `escalate`) needs a valid `MISTRAL_API_KEY`/`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`.
- Full 24-call batch run still NOT executed — `results/` holds the original 1-ticket trial.
- Keep README.md in sync with design changes.
