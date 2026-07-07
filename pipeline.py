"""Batch pipeline: generate replies for the holdout set, evaluate them plus
the control (deliberately bad) replies, and validate the metric.

Usage:
    python pipeline.py --all           # full run
    python pipeline.py --generate      # only generation
    python pipeline.py --evaluate      # only evaluation (needs generated_replies.json)
    python pipeline.py --validate      # only metric validation (needs evaluation_results.json)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.evaluator import evaluate_reply
from src.generator import generate_reply
from src.policy_store import PolicyStore
from src.retriever import TicketRetriever
from src.schema import Ticket, Transaction
from src.validate_metric import load_results, validate

DATA = Path("data")
RESULTS = Path("results")
console = Console()


def load_data():
    transactions = {
        t["order_id"]: Transaction(**t)
        for t in json.loads((DATA / "transactions.json").read_text())
    }
    tickets = [Ticket(**t) for t in json.loads((DATA / "dataset.json").read_text())]
    controls = json.loads((DATA / "control_examples.json").read_text())
    return transactions, tickets, controls


def run_generate(transactions, tickets, limit: int = 0) -> list[dict]:
    policy_store = PolicyStore(str(DATA / "policy.pdf"))
    retriever = TicketRetriever(tickets)
    holdout = [t for t in tickets if t.split == "holdout"]
    if limit:
        holdout = holdout[:limit]

    generated = []
    for t in holdout:
        console.print(f"  generating reply for [bold]{t.ticket_id}[/] ({t.category})...")
        g = generate_reply(
            t.incoming_email, transactions[t.order_id], policy_store, retriever, ticket_id=t.ticket_id
        )
        generated.append(g.model_dump())

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "generated_replies.json").write_text(json.dumps(generated, indent=2))
    console.print(f"[green]wrote results/generated_replies.json ({len(generated)} replies)[/]")
    return generated


def run_evaluate(transactions, tickets, controls, limit: int = 0) -> list[dict]:
    policy_store = PolicyStore(str(DATA / "policy.pdf"))
    tickets_by_id = {t.ticket_id: t for t in tickets}
    generated = json.loads((RESULTS / "generated_replies.json").read_text())
    if limit:
        controls = controls[:limit]

    results = []
    for g in generated:
        t = tickets_by_id[g["ticket_id"]]
        console.print(f"  evaluating generated reply for [bold]{t.ticket_id}[/]...")
        r = evaluate_reply(t, transactions[t.order_id], g["reply"], policy_store, "generated")
        results.append(r.model_dump())

    for c in controls:
        t = tickets_by_id[c["holdout_id"]]
        console.print(f"  evaluating control (bad) reply for [bold]{t.ticket_id}[/]...")
        r = evaluate_reply(t, transactions[t.order_id], c["bad_reply"], policy_store, "control")
        results.append(r.model_dump())

    (RESULTS / "evaluation_results.json").write_text(json.dumps(results, indent=2))
    console.print(f"[green]wrote results/evaluation_results.json ({len(results)} evaluations)[/]")
    return results


def run_validate() -> dict:
    results = load_results(str(RESULTS / "evaluation_results.json"))
    expected = json.loads((DATA / "expected_outcomes.json").read_text())
    report = validate(results, expected)
    (RESULTS / "validation_report.json").write_text(json.dumps(report, indent=2))
    console.print("[green]wrote results/validation_report.json[/]")
    return report


def print_summary(results: list[dict], report: dict):
    generated = [r for r in results if r["reply_source"] == "generated"]

    console.rule("[bold]Summary")
    overall = sum(r["final_score"] for r in generated) / len(generated)
    console.print(f"Overall score (generated replies): [bold]{overall:.1f}/100[/]")

    by_cat = defaultdict(list)
    for r in generated:
        by_cat[r["category"]].append(r["final_score"])
    table = Table(title="Per-category (generated)")
    table.add_column("category")
    table.add_column("avg final score", justify="right")
    for cat, scores in sorted(by_cat.items()):
        table.add_row(cat, f"{sum(scores) / len(scores):.1f}")
    console.print(table)

    c1, c2, c3 = report["check_1_discriminative"], report["check_2_correlation"], report["check_3_judge_trust"]
    console.print(
        f"Control-vs-generated gap: [bold]{c1['gap']}[/] "
        f"(generated {c1['generated_avg_final_score']} vs control {c1['control_avg_final_score']})"
    )
    console.print(f"Alignment↔lexical-overlap correlation: [bold]{c2['pearson_r_alignment_vs_lexical_overlap']}[/]")
    console.print(f"Compliance-judge agreement with hand labels: [bold]{c3['agreement']} ({c3['agreement_rate']:.0%})[/]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Trial mode: only the first N holdout tickets and N controls "
        "(--limit 1 => exactly 5 LLM calls: 1 generation + 2x2 judge calls).",
    )
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(1)

    transactions, tickets, controls = load_data()

    results = None
    if args.all or args.generate:
        console.rule("[bold]1. Generate")
        run_generate(transactions, tickets, limit=args.limit)
    if args.all or args.evaluate:
        console.rule("[bold]2. Evaluate")
        results = run_evaluate(transactions, tickets, controls, limit=args.limit)
    if args.all or args.validate:
        console.rule("[bold]3. Validate metric")
        report = run_validate()
        if results is None:
            results = json.loads((RESULTS / "evaluation_results.json").read_text())
        print_summary(results, report)


if __name__ == "__main__":
    main()
