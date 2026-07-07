# CLAUDE.md — AI Email Suggested-Response System

Context for any Claude session working in this repo. Read this before changing code.

## What this is

A submission for the Hiver Open Challenge: generate suggested replies to customer-support
emails using an LLM grounded in a company's own data (policy PDF + transactions + past
tickets), and — the heaviest-graded part — **measure accuracy with a validated metric**.
We simulate one fictional company (NorthPeak Outdoor Gear) but the code is company-agnostic.

## The one inviolable design rule

**Everything company-specific lives in `data/`. Nothing in `src/`, `pipeline.py`, or
`app.py` may reference NorthPeak, a product, or a specific policy rule.** Swapping the
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
pipeline.py = batch CLI · app.py = Streamlit UI · results/*.json = outputs
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

## Current state (as of 2026-07-07)

- Full pipeline verified end-to-end with `mock`; `--limit 1` trial verified live on
  Mistral (generated 97.0 vs control 20.2, judge agreement 2/2).
- Full 24-call run NOT yet executed — `results/` currently holds the 1-ticket trial.
- README.md documents approach/dataset/metric/validation — keep it in sync with any
  design change.
