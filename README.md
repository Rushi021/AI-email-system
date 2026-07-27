# AI Email Suggested-Response System

A complete, runnable system that suggests customer-support email replies grounded in a
company's **own data** — its policy document, its transaction records, and the replies its
agents actually sent — and measures accuracy with **two unblended scores**: automated RAGAS
response quality (Tier 1) and human-feedback system reliability (Tier 2).

## ▶ How to run & access the app

```bash
# one-time setup (skip if .venv already exists)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your API key(s) — or do it later in the app's Settings page

# start the app
streamlit run app.py
```

Streamlit prints a local URL — open **http://localhost:8501** in your browser.
You land on the **✉️ Assistant** page; the left sidebar switches between the three pages:

| Page | What you do there |
|---|---|
| **✉️ Assistant** (landing) | Paste a customer email → click **Suggest a reply**. Expand **"How accurate is this reply?"** for on-demand RAGAS scoring (faithfulness / relevancy / context precision). |
| **📥 Inbox** | **Sync inbox** to fetch unread mail from the connected source (an MCP email server, or the built-in demo inbox) and route every message through the pipeline; or paste a single email to route it. Each email is classified **auto-reply / needs-review / escalate / ignore** and dropped into the queue. |
| **🗂️ Review** | The human-action dashboard: Escalations · Needs review · Auto · Audit sample · Done. Send/edit/dismiss, flag hallucinations, and confirm escalation audits — every action writes a `feedback_events` row. |
| **⚙️ Settings** | Upload a policy document; manage example replies; LLM providers; email connector; automation thresholds; **evaluation gates** (faithfulness gate, disagreement/audit sample rates); **storage** (local / Postgres / S3 / Azure / GCS). |
| **📊 Evaluation** | Two unblended panels: Response Quality (RAGAS) and System Reliability (human feedback with n + Wilson CIs). |

No API key yet? The app still opens — configure a provider on the Settings page first,
then use the Assistant.

```
incoming email ──► format-aware policy ingest + hybrid retrieve (BM25 + embeddings + RRF)
      │       ──► TF-IDF retrieve similar past tickets (dataset corpus + user_examples)
      │       ──► transaction record lookup (data/transactions.json)
      ▼
   LLM generator ──► suggested reply + cited rules + structured remedy
      ▼
   RAGAS evaluator ──► faithfulness · answer relevancy · context precision
                       + dual-pass retrieval disagreement (sampled)
                       + deterministic diagnostics (non-blended)
      ▼
   router ──► AUTO / REVIEW / ESCALATE / IGNORE
              (hard gate: faithfulness < gate OR disagreement → never AUTO)
      ▼
   Review dashboard ──► human feedback_events
      ▼
   reliability ──► critical_error_rate · reliability_rate · calibration
                   (every rate carries n + Wilson CI)
```

## 1. Quick start (batch pipeline)

Setup is the same as "How to run & access the app" above; then:

```bash
python pipeline.py --all      # generate → RAGAS evaluate → reliability report
python pipeline.py --all --limit 1   # frugal trial on one holdout ticket
```

`LLM_PROVIDER=anthropic|openai|mistral` selects the provider for **email generation**
(and RAGAS judges). `CLASSIFY_LLM_PROVIDER` / `CLASSIFY_LLM_MODEL` select the model
used only for **email categorization** (falls back to `LLM_*` when unset). Both go through
one interface in `src/llm_client.py`. RAGAS uses native `llm_factory` for all three providers
and `embedding_factory("huggingface", ...)` for local sentence-transformers embeddings
(hash-embedding shim only for offline mock).

*(A third provider value, `mock`, exists purely to smoke-test the plumbing offline with a
deterministic stub — its scores are meaningless and it is not part of the submission flow.)*

Storage defaults to local SQLite + filesystem (`src/storage/`). Opt into Postgres / S3 /
Azure / GCS from Settings; secrets stay in `.env`.

## 2. The core design principle: company-agnostic code

**Everything company-specific lives in `data/`. Nothing in `src/` names a company, a product,
or a rule.** We simulate one fictional company — *NorthPeak Outdoor Gear*, an online outdoor
retailer — but if you replaced `data/policy.pdf`, `data/transactions.json`, and
`data/dataset.json` with another company's files, every line of `src/`, `pipeline.py`, and
`app.py` would work unchanged:

- `src/policy_store.py` ingests **any** PDF/DOCX/Markdown/txt policy, extracts structured
  rules (id / condition / outcome / category / effective date), caches by section content-hash,
  and serves hybrid BM25 + local embedding retrieval with RRF (optional cross-encoder rerank).
- `src/retriever.py` fits TF-IDF over whatever ticket corpus it is given (eval `dataset.json`
  corpus split plus optional production `user_examples.json` in the live UI).
- `src/schema.py` uses a generic transaction shape (`extra="allow"`, so extra columns from a
  different company's export ride along without code changes).
- Every prompt in `src/prompts.py` injects policy text, transaction data and past replies at
  runtime; none contains a company fact. Past tickets teach **voice/tone only** and must never
  override policy.

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
from the policy + transaction alone — which is what Tier-1 RAGAS faithfulness checks against
retrieved context, and what human reviewers later confirm or correct in Tier-2 feedback.

**No hand-labeled answer key and no synthetic control replies.** Accuracy trust comes from
organic Review-dashboard feedback (`feedback_events`) plus reference-free RAGAS metrics.

## 4. Generation approach — RAG over policy + past tickets, and why

For each incoming email the generator (`src/generator.py`) retrieves the top policy clauses
and the top similar past tickets, and prompts the LLM with both plus the transaction record.
The prompt is explicit about the hierarchy: **policy determines the remedy; past tickets
teach only voice and structure; escalation rules are checked first.** The model also emits a
structured remedy object (`remedy_type`, `remedy_amount`, `rule_cited`, `escalate`) used later
for deterministic edit classification — not for LLM-judged scoring.

Why this combination beats the alternatives:

- **Policy alone** tells you the rule but not the house voice, reply structure, or precedent
  for handling emotion — replies come out legally correct but robotic.
- **Past tickets alone** can't guarantee the historical agent followed policy correctly, and
  can't answer scenarios with no precedent (our holdout deliberately contains one).
  Retrieval also drifts: a similar-sounding email can have the opposite correct outcome
  (in-window vs final-sale return look nearly identical textually).
- **Combining both** is robust to each one's failure mode — and RAGAS faithfulness closes the
  loop by checking claims against the *retrieved* policy chunks.
- **vs fine-tuning:** fine-tuning bakes today's policy into weights. Real policies version
  (ours is stamped v3.1); real ticket corpora grow daily. With RAG, updating the system is
  *replacing a file*. Fine-tuning also needs orders of magnitude more data than any single
  team's corpus, costs money per iteration, and can't cite the rule it applied.
- **vs zero-shot:** ignores the owned data entirely — no grounding in the actual policy, no
  house voice, and (as the task requires) no use of the dataset at all.
- **Retrieval choice:** hybrid BM25 + local `sentence-transformers` embeddings, fused with
  Reciprocal Rank Fusion, after intent-based category scoping (plus always-on `global`
  escalation rules). Optional cross-encoder rerank is off by default. Section content-hashes
  make re-uploads reprocess only changed sections (`results/policy_index/` via BlobStore).
  Ticket retrieval remains TF-IDF over the corpus (and Settings-managed `user_examples`).

## 5. Accuracy — two numbers, never blended

### Tier 1 — RAGAS Response Quality (automated, zero setup)

| Metric | What it catches |
|---|---|
| **Faithfulness** | Unsupported claims vs retrieved policy chunks (hallucinations) |
| **Answer relevancy** | On-topic-sounding but non-responsive drafts |
| **Context precision** | Retrieved chunks that were not actually useful (retriever quality) |

Scores are stored individually in the `ragas_scores` table. For routing only:

```
quality_score = 0.5·faithfulness + 0.3·answer_relevancy + 0.2·context_precision   # 0–1
```

**Hard AUTO gate:** `faithfulness < FAITHFULNESS_GATE` (default 0.7) **or**
`retrieval_disagreement == true` **or** scoring failure → never AUTO.
Disagreement is a sampled dual-pass check (top-k cited rule vs full-document rule extraction)
— no hand labels required. Deterministic length/placeholder/absolute-claim checks remain as
non-blended diagnostic flags.

### Tier 2 — System Reliability (human feedback only)

Review actions produce mutually exclusive labels: `ACCEPTED_AS_IS`, `EDITED_MINOR`,
`EDITED_MAJOR`, `REJECTED`, `ESCALATED_CORRECTLY`, `ESCALATED_MISSED`,
`FLAGGED_HALLUCINATION`. Minor vs major edits are classified by structured remedy diff
(not another LLM judgment).

**Critical error rate** (business headline) uses only human-labeled AUTO responses as the
denominator — unaudited AUTO is excluded and audit coverage is reported separately:

```
critical_error_rate =
  count(label ∈ {EDITED_MAJOR, REJECTED, ESCALATED_MISSED, FLAGGED_HALLUCINATION}
        ∧ routing_decision == AUTO ∧ labeled)
  / count(routing_decision == AUTO ∧ labeled)
```

Every rate carries `n` and a Wilson 95% CI; slices with `n < 20` render as insufficient data.
Calibration buckets RAGAS `quality_score` deciles against real acceptance — if a high-score
bucket has high critical-error rate, tighten the faithfulness gate.

## 6. Validating trustworthiness

`src/validate_metric.py` / the Evaluation dashboard report reliability from organic
`feedback_events` — not from synthetic controls or hand-labeled answer keys. A brand-new
customer with only a policy PDF gets full Tier-1 scoring on response one. The number an
owner sees first (`critical_error_rate`) is computed exclusively from actions humans took.

## 6b. The automation layer — inbox → route → review

The suggested-reply engine and the validated metric are the hard part; the automation layer wraps
them into an end-to-end support system. **The metric is the control system**: it decides how much
autonomy each reply earns.

```
inbox (MCP email server | built-in demo)  ── src/email_source.py ──► IncomingEmail[]
        │
        ▼
   src/email_parser.py   strip HTML/quoted-history/signatures, normalize whitespace,
        │                surface SPF/DKIM/DMARC when the connector supplies headers
        ▼
   src/event_bus.py (SQLite)   publish → drain(): each email isolated in its own
        │                      try/except — retry ×3, else dead-letter. One bad
        │                      email can't block the rest of a sync.
        ▼
   src/classifier.py   LLM categorizes into refund / cancellation / complain /
        │               billing / technical_support / general_inquiry / other (noise);
        │               frustration stays a cheap regex for priority only
        ▼
   src/router.py   reuses generate_reply + evaluate_reply, unchanged
        │   AUTO      confident + policy-clean + a rule was cited, and policy does not mandate a human
        │   REVIEW    decent draft, not confident enough → queued for a human to approve/edit
        │   ESCALATE  the compliance judge says the policy requires a human, or confidence < T2
        │   IGNORE    category is other (noise) — no generation/eval LLM spent
        ▼
   src/queue_store.py (SQLite)  ── priority-sorted queue ──►  🗂️ Review dashboard
        └── src/notify.py  ── email digest of pending items via the same connector
```

- **Live confidence.** A live email has no human reply to align against, so alignment is dropped and
  confidence is renormalized over compliance + quality minus deterministic penalties. Auto-reply
  additionally requires zero flags, a cited rule, and no policy-mandated escalation.
- **Escalation stays company-agnostic.** The compliance judge emits a boolean `escalate` +
  `escalate_reason` **read from the policy document** — the router never hardcodes a rule id. Swap
  the policy PDF and escalation behavior changes with it.
- **Email connector.** One pluggable interface (mirroring `llm_client`). `demo` runs fully offline
  from `data/demo_inbox.json`; `mcp` makes the app an **MCP client** to a Gmail (or any) MCP server —
  server URL + token in Settings, tools auto-mapped by capability. Configured per deployment in the
  Settings dashboard, exactly like the LLM provider.
- **Dry-run by default.** Nothing is actually sent until the **live-send** switch is on; until then
  auto-replies are queued as pre-approved drafts and "Send" simulates. Every threshold, connector,
  and notification setting is changed from Settings — no code edits to adapt the system to a new company.

## 7. Repo map

```
scripts/build_policy_pdf.py   renders the policy text into data/policy.pdf (one-time)
data/                         ALL company-specific inputs (swap these for a new company)
data/demo_inbox.json          offline demo inbox (live inbound emails, no human replies)
src/policy_store.py           generic PDF → chunks → TF-IDF retrieval
src/retriever.py              TF-IDF retrieval over past-ticket corpus (corpus split only)
src/generator.py              policy + precedent → suggested reply
src/evaluator.py              3-layer accuracy system (the core of the submission)
src/validate_metric.py        the three validation checks
src/email_source.py           pluggable inbox connector (demo | mcp), one interface
src/email_parser.py           normalize a fetched email (strip HTML/quotes, auth signal)
src/event_bus.py              SQLite ingestion queue (results/event_bus.db) — isolates failures
src/classifier.py             LLM categorization (7 categories) + frustration regex
src/router.py                 routing engine: email → AUTO / REVIEW / ESCALATE / IGNORE
src/queue_store.py            SQLite review/action queue (results/queue.db)
src/notify.py                 email digest of pending review + escalation items
src/config.py                 non-secret runtime config (config.json): thresholds, source, digest
pipeline.py                   batch CLI: --all | --generate | --evaluate | --validate
app.py                        Streamlit entrypoint (st.navigation over views/)
views/                        Assistant · Inbox · Review · Settings · Evaluation (internal)
results/                      generated_replies / evaluation_results / validation_report / queue.db / event_bus.db
```

## 8. Future implementation — from assistant to autonomous support layer

Everything below builds on what already exists: the company-agnostic RAG core, the
**validated accuracy metric**, and the swap-the-data-files design. The metric is the key —
it stops being a report card and becomes the **control system** that decides how much
autonomy the product is allowed.

### Target architecture

```
Gmail / IMAP / helpdesk API (Hiver, Zendesk, ...)
      │  inbox sync (Gmail API watch + Pub/Sub push, no polling)
      ▼
Ingestion & PII redaction ──► thread reconstruction · attachment/OCR parsing
      ▼
Triage classifier (small/cheap model)
      │  category · sentiment · urgency · language · is-support vs noise
      ▼
Router ──────────────┬──────────────────────┬─────────────────────┐
      ▼              ▼                      ▼                     ▼
 AUTO-REPLY     DRAFT FOR REVIEW      ESCALATE TO HUMAN      IGNORE/ARCHIVE
 (score ≥ T₁)   (T₂ ≤ score < T₁)     (score < T₂, or        (newsletters,
      │              │                 policy says so:        auto-replies)
      │              │                 high-value, legal,
      │              │                 repeat contact)
      ▼              ▼                      ▼
 RAG generator ──► evaluator gate (same 3-layer metric, online) ──► send / queue
      ▲                                                              │
      └────────── learning loop: agent edits, outcomes, CSAT ◄───────┘
```

The generator and evaluator in the middle are **exactly the modules in this repo** —
`src/generator.py` and `src/evaluator.py` — promoted from batch tools to online services.

### Roadmap

**Status:** items 1–4 below are now **implemented** in this repo (see §6b) — an MCP email connector
(plus an offline demo inbox) with a normalization + durable-queue front end, a cheap keyword
triage/noise gate as its own stage, confidence-gated routing with a dry-run auto-send switch, and a
human-in-the-loop review dashboard with an email digest. Items 5–9 remain the forward path.
Details of what shipped:

- **Inbox integration** via the **MCP** connector (`src/email_source.py`) — the "same adapter
  interface covers any provider" idea, realized as one pluggable connector selected in Settings.
  (Gmail-API OAuth / IMAP / Pub/Sub push are the same-interface extensions still to add.)
- **Ingestion normalization + a durable queue** — `src/email_parser.py` strips HTML, quoted-history,
  and signature blocks and normalizes whitespace/punctuation before anything downstream sees the
  email, plus reads a provider's `Authentication-Results` header for SPF/DKIM/DMARC when one is
  supplied. `src/event_bus.py` (SQLite) sits between fetch and routing so a batch sync survives one
  email's failure — that email retries and dead-letters instead of crashing the sync. This is a
  slice of target-architecture item 1 below (full thread reconstruction and PII redaction are not
  built yet).
- **Triage** is an LLM/SLM classifier (`src/classifier.py`) that labels each email
  refund / cancellation / complain / billing / technical_support / general_inquiry / other
  and IGNORE-routes `other` before generation. Provider/model are chosen separately from
  generation in Settings (`CLASSIFY_LLM_*`).
- **Confidence-gated auto-reply** is the T1/T2 thresholding in `src/router.py`, gated further by
  zero flags + a cited rule + no policy-mandated escalation, with a global dry-run/live-send switch.
- **Human-in-the-loop** is the 🗂️ Review dashboard + SQLite queue + email digest.

1. **Gmail inbox integration.** OAuth per mailbox, Gmail API `watch` + Pub/Sub for
   real-time push (no polling), full thread reconstruction so the model sees the
   conversation, not one message, and PII redaction on ingest. The same adapter interface
   covers IMAP and helpdesk APIs (Hiver, Zendesk, Front) so the core never knows which inbox
   it serves. *(Email fetch + body normalization + a durable ingestion queue already exist;
   OAuth/watch/Pub-Sub, full thread reconstruction, and PII redaction do not yet.)*

2. **Triage & categorization.** A small/cheap model labels every incoming email into the
   support categories above (with `other` as the noise gate) so the expensive generation
   model stays off non-support mail. *(LLM categorization is in `src/classifier.py`;
   sentiment/urgency/language as separate signals are still open.)*

3. **Confidence-gated auto-reply for straightforward cases.** Every draft is scored by
   the same 3-layer evaluator **before** anything is sent. Score ≥ T₁ *and* zero
   deterministic flags *and* the cited rule is unambiguous → send automatically.
   The thresholds are not guesses: they are calibrated on the validation set exactly the
   way §6 calibrates the metric, and tightened per category until the measured
   false-approve rate is below an agreed SLA. Autonomy is earned by the metric, per
   category, not switched on globally.

4. **Human-in-the-loop for everything else.** Mid-confidence drafts land in the agent's
   inbox as editable suggestions with the grounding attached (policy clauses, transaction,
   precedent tickets — the same transparency panel the Assistant page already shows).
   Low-confidence or policy-mandated cases (high-value orders, legal threats, frequent
   returners — rules the policy already encodes) skip drafting and escalate with a
   one-paragraph brief of what the policy requires. Every accept / edit / reject is
   captured as labeled data.

5. **Beyond complaints: an operational-knowledge policy.** Info requests ("what's your
   sizing?", "do you ship to X?", "where is my invoice?") don't need a remedies policy —
   they need an *operational information document*: shipping matrices, store hours, product
   FAQs, account procedures. Because the whole pipeline is document-agnostic, this is
   literally a second PDF in `data/` and a routing rule: the compliance judge's question
   changes from "did the reply offer the required remedy?" to "is every stated fact
   present in the operational document?" — same structure, same validation method.

6. **Multi-document / versioned policy store.** Hybrid BM25 + embeddings + RRF (with
   section-hash incremental reindexing) is already in place. Next: a **versioned
   multi-document** store with effective dates — so "which policy was in force when this
   order shipped?" has a correct answer, and a policy update triggers automatic
   re-evaluation of the golden set (catching rules the new document silently changed).

7. **Continuous learning & drift detection.** Agent edits become preference pairs for
   the generator; accept/reject decisions continuously re-validate the judge
   (human-vs-judge agreement is monitored the same way §6's check 3 does it once).
   If agreement drifts below threshold, autonomy automatically steps down a tier —
   the system degrades to draft-mode instead of failing silently.

8. **From suggested text to suggested action.** The judge already extracts the required
   remedy in structured form ("full refund of $88.00 under R1.1"). Connect that to
   order-management / payment APIs (Shopify, Stripe) so approving a reply also executes
   the refund — with the same tiered autonomy: auto-execute small refunds, require a
   click for large ones, dual-approval above a limit. This closes the loop on the actual
   business problem: not writing emails, but resolving tickets.

9. **Enterprise hardening.** Multi-tenant data isolation (per-company namespace — the
   company-agnostic design was built for this), PII redaction before any LLM call,
   BYO-model/VPC deployment via the pluggable `llm_client`, immutable audit log of every
   suggestion + its grounding + who approved it, RBAC, per-language support, and
   cost tiering (small model for triage, large for generation, cached embeddings).

**Why this is credible rather than aspirational:** every stage of the diagram is gated by
the metric this submission validates. The hard part of autonomous support isn't generating
text — it's *knowing when the text is safe to send*. That is exactly what was built here.

## 9. AI tools disclosure

This submission was built with Claude Code (Claude Fable 5) doing the implementation under
the direction of a human-authored design brief: architecture, dataset design, metric design
and validation strategy were specified up front; the agent wrote the code, synthesized the
dataset content, and verified the pipeline end-to-end. LLM calls at runtime use the
Anthropic or OpenAI API via the pluggable client in `src/llm_client.py`.
