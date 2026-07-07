"""Validate that the accuracy metric measures real quality — three checks.

1. Discriminative check   — deliberately bad control replies must score far
                            below genuinely generated replies.
2. Correlation check      — LLM alignment score vs cheap lexical overlap;
                            divergences (low overlap, high alignment) show the
                            judge rewards paraphrases, not word matching.
3. Judge trust check      — the compliance judge's own reading of the policy
                            ("policy requires X, per rule R") is compared with
                            the hand-labeled ground truth in
                            data/expected_outcomes.json. This is the ONLY place
                            that file is used; the judge never saw it. If the
                            judge reads the policy correctly on labeled cases,
                            its compliance scores on unlabeled ones can be
                            trusted — the strongest of the three checks.
"""

from __future__ import annotations

import json
import re

import numpy as np

from src.schema import EvaluationResult

RULE_RE = re.compile(r"R\d+(?:\.\d+)?", re.IGNORECASE)


def _norm_rule(text: str) -> str:
    m = RULE_RE.search(text or "")
    return m.group(0).upper() if m else ""


def validate(results: list[EvaluationResult], expected_outcomes: list[dict]) -> dict:
    generated = [r for r in results if r.reply_source == "generated"]
    controls = [r for r in results if r.reply_source == "control"]

    # 1. discriminative gap ------------------------------------------------
    gen_avg = float(np.mean([r.final_score for r in generated])) if generated else 0.0
    ctl_avg = float(np.mean([r.final_score for r in controls])) if controls else 0.0
    gap = round(gen_avg - ctl_avg, 1)

    # 2. alignment vs lexical-overlap correlation ---------------------------
    alignments = [r.alignment for r in results]
    overlaps = [r.lexical_overlap for r in results]
    if len(results) >= 3 and np.std(alignments) > 0 and np.std(overlaps) > 0:
        corr = float(np.corrcoef(alignments, overlaps)[0, 1])
    else:
        corr = float("nan")
    divergent = [
        {
            "ticket_id": r.ticket_id,
            "reply_source": r.reply_source,
            "alignment": r.alignment,
            "lexical_overlap": r.lexical_overlap,
            "note": "high semantic alignment despite low word overlap — good paraphrase correctly rewarded",
        }
        for r in results
        if r.alignment >= 4 and r.lexical_overlap < 0.35
    ]

    # 3. compliance-judge trust check ---------------------------------------
    expected_by_ticket = {e["ticket_id"]: e for e in expected_outcomes}
    comparisons, agree = [], 0
    for r in results:
        exp = expected_by_ticket.get(r.ticket_id)
        if not exp:
            continue
        judge_rule = _norm_rule(r.judge_cited_rule)
        expected_rule = _norm_rule(exp["citing_rule"])
        match = bool(judge_rule) and judge_rule == expected_rule
        agree += match
        comparisons.append(
            {
                "ticket_id": r.ticket_id,
                "reply_source": r.reply_source,
                "judge_said_policy_requires": r.judge_policy_requires,
                "judge_cited_rule": judge_rule or r.judge_cited_rule,
                "hand_labeled_remedy": exp["correct_remedy"],
                "hand_labeled_rule": expected_rule,
                "agreement": match,
            }
        )
    agreement_rate = round(agree / len(comparisons), 3) if comparisons else 0.0

    return {
        "check_1_discriminative": {
            "generated_avg_final_score": round(gen_avg, 1),
            "control_avg_final_score": round(ctl_avg, 1),
            "gap": gap,
            "passes": gap > 15,
            "interpretation": "The metric must clearly separate deliberately bad replies from real generated ones; a large positive gap means it does.",
        },
        "check_2_correlation": {
            "pearson_r_alignment_vs_lexical_overlap": round(corr, 3) if corr == corr else None,
            "divergent_examples": divergent,
            "interpretation": "Moderate positive correlation is expected (similar meaning often shares words). Divergent cases prove the alignment judge measures meaning, not string overlap.",
        },
        "check_3_judge_trust": {
            "agreement": f"{agree}/{len(comparisons)}",
            "agreement_rate": agreement_rate,
            "comparisons": comparisons,
            "interpretation": "Before trusting the compliance judge's scores, we verify its own reading of the policy (which rule applies) against hand-labeled ground truth it never saw. High agreement means its compliance scores are grounded in a correct reading of the document.",
        },
    }


def load_results(path: str) -> list[EvaluationResult]:
    with open(path) as f:
        return [EvaluationResult(**r) for r in json.load(f)]
