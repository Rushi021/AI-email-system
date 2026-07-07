# AI Email Suggested-Response System

A complete, runnable system that suggests customer-support email replies grounded in a
company's **own data** — its policy document, its transaction records, and the replies its
agents actually sent — and, most importantly, **measures how accurate those suggestions are**
with a validated, three-layer accuracy system.

```
incoming email ──► TF-IDF retrieve policy clauses (data/policy.pdf)
      │       ──► TF-IDF retrieve similar past tickets (data/dataset.json corpus split)
      │       ──► transaction record lookup (data/transactions.json)
      ▼
   LLM generator ──► suggested reply
      ▼
   3-layer evaluator ──► policy-compliance judge (45%) + alignment judge (25%)
                         + quality rubric (30%) − deterministic penalties
      ▼
   metric validation ──► control gap · semantic-vs-lexical correlation · judge trust check
```

## 1. Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then put your ANTHROPIC_API_KEY (or OPENAI_API_KEY) in it
python pipeline.py --all      # generate → evaluate → validate; writes results/*.json
streamlit run app.py          # Assistant UI · Settings · Evaluation (internal) dashboards
```

`LLM_PROVIDER=anthropic|openai|mistral` selects the provider (one interface in
`src/llm_client.py` is used for generation and every judge call; Mistral runs through its
OpenAI-compatible endpoint with free-tier rate-limit throttling built in). `LLM_MODEL`
optionally overrides the model.
Dataset dates are anchored around 2026-07-07; set `EVAL_TODAY=2026-07-07` if you run the
evaluation much later, so date-window rules (30/90-day returns, 1-year warranty) still
resolve the way the labels assume.

*(A third provider value, `mock`, exists purely to smoke-test the plumbing offline with a
deterministic stub — its scores are meaningless and it is not part of the submission flow.)*

## 2. The core design principle: company-agnostic code

**Everything company-specific lives in `data/`. Nothing in `src/` names a company, a product,
or a rule.** We simulate one fictional company — *NorthPeak Outdoor Gear*, an online outdoor
retailer — but if you replaced `data/policy.pdf`, `data/transactions.json`, and
`data/dataset.json` with another company's files, every line of `src/`, `pipeline.py`, and
`app.py` would work unchanged:

- `src/policy_store.py` ingests **any** PDF, chunks it on numbered-rule headings (with a
  paragraph fallback for unstructured documents), and serves TF-IDF retrieval over it.
- `src/retriever.py` fits TF-IDF over whatever ticket corpus it is given.
- `src/schema.py` uses a generic transaction shape (`extra="allow"`, so extra columns from a
  different company's export ride along without code changes).
- Every prompt in `src/prompts.py` injects policy text, transaction data and past replies at
  runtime; none contains a company fact.

That is the point of the design: this is a *product*, not a demo hard-coded to one dataset.

## 3. Dataset — how it was built and why it's representative

The dataset is synthetic but **structurally faithful to real support data**, built in three
linked layers (the same three exports any e-commerce company could produce):

1. **`data/policy.pdf`** — 15 numbered rules rendered to a real PDF by
   `scripts/build_policy_pdf.py`. Every rule states **both the grant condition and the
   explicit denial condition** (e.g. R1.1: ≤30 days unworn → full refund; worn → no refund).
   That two-sided structure is deliberate: a compliance judge can only distinguish right from
   wrong replies if the document itself defines both sides. It also includes two escalation
   rules (R6: disputed value > $200 → human agent; R7: 3+ returns in 90 days → manual review)
   because knowing *when not to answer autonomously* is a core part of real support quality.
2. **`data/transactions.json`** — 24 orders that deliberately vary every dimension the policy
   branches on: on-time/late/lost/damaged delivery, final-sale vs not, cancelled before vs
   after shipment, inside vs outside the warranty window, prices above and below the $200
   escalation threshold, and one customer with 4 recent returns to exercise R7.
3. **`data/dataset.json`** — 24 (incoming email, actual reply) pairs, each tied to a real
   `order_id`. The replies were written in a consistent "house voice" (named agents, warm but
   concrete, always states the remedy and the next step) so there is a real style for the
   generator to learn. Emails vary in sentiment (polite/neutral/frustrated) and include
   realistic complications: a sympathetic gift story attached to a non-negotiable final-sale
   denial, a customer demanding a refund where policy prescribes a replacement, a "lost"
   package the carrier hasn't confirmed.

**Split:** 18 tickets are tagged `corpus` (the retrieval pool) and 6 `holdout` (the test
set). The retriever is fitted **only on the corpus split**, so the holdout can never leak
into generation. Holdout tickets are genuinely different scenarios — different orders,
different complications — not reworded corpus tickets; each targets a distinct policy
branch, including both escalation rules and the unconfirmed-duplicate-charge branch that has
no corpus example at all.

Why representative: returns, shipping problems, cancellations, warranty claims and billing
disputes are the canonical intent taxonomy of e-commerce support; the categories, the
policy-conditioned outcomes and the emotional range mirror what a real inbox contains, and
because every ticket is grounded in a transaction record, the "correct" answer is *decidable*
— which is exactly what a real accuracy measurement needs.

Two supporting files complete the evaluation harness:

- **`data/control_examples.json`** — 3 deliberately bad replies (a generic non-answer, a
  final-sale refund that contradicts policy, a confident auto-approval that ignores the R7
  escalation trigger) used to prove the metric can tell bad from good.
- **`data/expected_outcomes.json`** — hand-labeled ground truth (correct remedy + rule) for
  the 6 holdout tickets, written by reading `policy.pdf` and `transactions.json` as the
  dataset author. **This file is used only by `src/validate_metric.py`** — never by the
  generator and never by the per-response evaluator — so there is no leakage: it exists to
  audit the judge, not to help the system.

## 4. Generation approach — RAG over policy + past tickets, and why

For each incoming email the generator (`src/generator.py`) retrieves the top policy clauses
and the top similar past tickets, and prompts the LLM with both plus the transaction record.
The prompt is explicit about the hierarchy: **policy determines the remedy; past tickets
teach only voice and structure; escalation rules are checked first.**

Why this combination beats the alternatives:

- **Policy alone** tells you the rule but not the house voice, reply structure, or precedent
  for handling emotion — replies come out legally correct but robotic.
- **Past tickets alone** can't guarantee the historical agent followed policy correctly, and
  can't answer scenarios with no precedent (our holdout deliberately contains one).
  Retrieval also drifts: a similar-sounding email can have the opposite correct outcome
  (in-window vs final-sale return look nearly identical textually).
- **Combining both** is robust to each one's failure mode — and the evaluator's compliance
  judge closes the loop by checking the output against the policy document itself.
- **vs fine-tuning:** fine-tuning bakes today's policy into weights. Real policies version
  (ours is stamped v3.1); real ticket corpora grow daily. With RAG, updating the system is
  *replacing a file*. Fine-tuning also needs orders of magnitude more data than any single
  team's corpus, costs money per iteration, and can't cite the rule it applied.
- **vs zero-shot:** ignores the owned data entirely — no grounding in the actual policy, no
  house voice, and (as the task requires) no use of the dataset at all.
- **Retrieval choice:** TF-IDF cosine (scikit-learn) rather than embeddings — free, instant,
  fully offline, zero extra API dependency, and at this corpus scale (tens to thousands of
  tickets) it retrieves the same neighbors an embedding index would. Swapping in embeddings
  later is a one-class change behind the same `retrieve()`/`top_k()` interface.

## 5. The accuracy system — what "accurate" means and why this metric

**Exact match fails immediately:** two replies can share almost no words and both be perfect,
or share 90% of their words while one grants a refund the policy forbids. So we define
accuracy as three separately measured questions:

| Layer | Question | Weight | How |
|---|---|---|---|
| **A. Policy compliance** | Does the reply offer the remedy the policy actually requires? | 45% | LLM judge is given the retrieved policy clauses + transaction + reply. It must state (1) what the policy requires and which rule, (2) what the reply offers, (3) a 1–5 match score, (4) justification. It derives the correct remedy **itself from the document** — it is never shown the hand labels. |
| **B. Alignment with actual reply** | Does it convey the same resolution and key information as the reply a human actually sent, in meaning, regardless of wording? | 25% | LLM judge, 1–5. |
| **C. Quality rubric** | Is it a *good email*? | 30% | LLM rubric, 1–5 each: groundedness (25%), tone/empathy vs the customer's sentiment (25%), clarity (20%), actionability (30%). |
| **D. Deterministic checks** | Cheap failure modes | penalty only | No LLM: placeholder tokens (−8), length outside 40–250 words (−4), unqualified absolute claims not present in the policy text (−8), order ID never referenced (−4). Capped at −20. |
| **E. Lexical overlap** | Independent cross-check | reported, never blended | `difflib.SequenceMatcher` ratio vs the actual reply. |

```
final_score = max(0, 0.45·policy + 0.25·alignment + 0.30·quality − penalty)   # each on 0–100
```

Policy compliance carries the largest weight because it is the only layer judged against a
**document, not vibes** — and because the most expensive support failure is a confidently
wrong remedy (refunding a final-sale item, skipping a mandated escalation). Alignment is
weighted below compliance on purpose: the historical agent is a strong reference but not an
oracle. Every sub-score, judge justification, flag and the lexical ratio is stored per
response (`results/evaluation_results.json`) — nothing is a single opaque number.

## 6. Validating the metric — the part that makes the number trustworthy

A metric you haven't validated is just a number. `src/validate_metric.py` runs three checks
(saved to `results/validation_report.json`, visualized in the app):

1. **Discriminative check.** Three deliberately bad control replies (wrong remedy, generic
   non-answer, ignored escalation) are scored by the same evaluator as the generated replies.
   If the metric is real, the control average must sit far below the generated average. A
   metric that can't fail a reply that refunds a final-sale item measures nothing.
2. **Correlation check.** Pearson correlation between the LLM alignment score and the
   lexical-overlap ratio across all scored replies. We *expect* moderate positive correlation
   (same meaning often shares words) — but the interesting evidence is the **divergent
   cases** the report lists: low word overlap with high alignment means the judge correctly
   rewarded a paraphrase that exact/lexical matching would have failed. That is precisely the
   failure of exact-match this metric exists to fix.
3. **Compliance-judge trust check — the strongest of the three.** The compliance judge's own
   stated reading of the policy ("this case requires X, per rule R") is compared, for all
   6 holdout + 3 control evaluations, against the hand-labeled ground truth in
   `data/expected_outcomes.json` — a file the judge has never seen. This is the right order
   of operations: **before trusting the judge's compliance scores on generated replies, we
   verify the judge reads the policy correctly on cases where a human established the
   answer.** Checks 1 and 2 show the blended score *behaves* sensibly; check 3 grounds the
   heaviest-weighted component in human-verified truth. It reports an agreement rate (e.g.
   8/9 = 89%) plus the full per-ticket comparison table, so a disagreement is inspectable,
   not hidden in an average. (The comparison is on the cited rule ID — deterministic, no
   second LLM grading the first one.)

Run `python pipeline.py --all` with a real API key and the three headline numbers (control
gap, correlation coefficient, judge agreement rate) print in the summary and populate the
app's Metric Validation tab.

## 7. Repo map

```
scripts/build_policy_pdf.py   renders the policy text into data/policy.pdf (one-time)
data/                         ALL company-specific inputs (swap these for a new company)
src/policy_store.py           generic PDF → chunks → TF-IDF retrieval
src/retriever.py              TF-IDF retrieval over past-ticket corpus (corpus split only)
src/generator.py              policy + precedent → suggested reply
src/evaluator.py              3-layer accuracy system (the core of the submission)
src/validate_metric.py        the three validation checks
pipeline.py                   batch CLI: --all | --generate | --evaluate | --validate
app.py                        Streamlit entrypoint (st.navigation over views/)
views/                        Assistant (paste email → reply + lazy accuracy check) ·
                              Settings (policy PDF upload, provider/API keys) ·
                              Evaluation (internal): batch results · metric validation
results/                      generated_replies / evaluation_results / validation_report
```

## 8. AI tools disclosure

This submission was built with Claude Code (Claude Fable 5) doing the implementation under
the direction of a human-authored design brief: architecture, dataset design, metric design
and validation strategy were specified up front; the agent wrote the code, synthesized the
dataset content, and verified the pipeline end-to-end. LLM calls at runtime use the
Anthropic or OpenAI API via the pluggable client in `src/llm_client.py`.
