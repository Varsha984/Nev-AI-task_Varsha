"""Pathology classification eval harness.

Produces a precision/recall/F1 report across the 9 pathology classes (plus
'control') by running the deterministic detectors against every trader in
the seed dataset and comparing predictions to ground-truth labels.

Outputs:
  * eval/reports/classification_report.json — machine-readable
  * eval/reports/classification_report.md   — human-readable
  * eval/reports/per_trader.json            — per-trader scores + prediction
  * stdout                                  — summary table

Run:
  python -m eval.run_eval
  # or, in Docker:
  docker compose exec app python -m eval.run_eval

Reproducibility: detectors are deterministic, so two runs with the same seed
file produce byte-identical reports. CI can hash the JSON output to check
for accidental regressions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly: `python eval/run_eval.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.metrics import classification_report  # noqa: E402

from app.detectors import score_all, predict_dominant  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed_data" / "nevup_seed_dataset.json"
OUT = ROOT / "eval" / "reports"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with SEED.open() as f:
        data = json.load(f)

    y_true: list[str] = []
    y_pred: list[str] = []
    per_trader: list[dict] = []

    for trader in data["traders"]:
        truth_list = trader.get("groundTruthPathologies") or []
        truth = truth_list[0] if truth_list else "control"

        scores = score_all(trader)
        pred = predict_dominant(scores)
        y_true.append(truth)
        y_pred.append(pred)

        per_trader.append(
            {
                "userId": trader["userId"],
                "name": trader["name"],
                "truth": truth,
                "predicted": pred,
                "correct": truth == pred,
                "scores": {
                    p: {
                        "score": round(r.score, 4),
                        "evidenceSessions": r.evidence_sessions[:5],
                        "evidenceTrades": r.evidence_trades[:5],
                    }
                    for p, r in scores.items()
                },
            }
        )

    labels = sorted(set(y_true) | set(y_pred))

    report_dict = classification_report(
        y_true, y_pred, labels=labels, output_dict=True, zero_division=0
    )
    report_text = classification_report(
        y_true, y_pred, labels=labels, output_dict=False, zero_division=0
    )

    accuracy = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true)

    summary = {
        "accuracy": round(accuracy, 4),
        "n_traders": len(y_true),
        "labels": labels,
        "report": report_dict,
        "y_true": y_true,
        "y_pred": y_pred,
    }

    (OUT / "classification_report.json").write_text(json.dumps(summary, indent=2))
    (OUT / "per_trader.json").write_text(json.dumps(per_trader, indent=2))

    md = _markdown_report(per_trader, report_text, accuracy)
    (OUT / "classification_report.md").write_text(md)

    # ---- stdout summary ---- #
    print(f"\n{'Trader':<14} {'Truth':<32} {'Predicted':<32} {'Top score':>10}")
    print("-" * 92)
    for r in per_trader:
        flag = "✓" if r["correct"] else "✗"
        top = max((s["score"] for s in r["scores"].values()), default=0)
        print(
            f"{r['name']:<14} {r['truth']:<32} {r['predicted']:<32} "
            f"{top:>10.3f}  {flag}"
        )

    print(f"\nAccuracy: {accuracy:.2%} ({sum(t==p for t,p in zip(y_true,y_pred))}/{len(y_true)})")
    print(f"\nClassification report:\n{report_text}")
    print(f"Reports written to: {OUT}")
    return 0


def _markdown_report(per_trader: list[dict], report_text: str, accuracy: float) -> str:
    lines = ["# NevUp Track 2 — Pathology Classification Eval", ""]
    lines.append(f"**Accuracy:** {accuracy:.2%} on the 10-trader seed dataset.")
    lines.append("")
    lines.append("## Per-trader predictions")
    lines.append("")
    lines.append("| Trader | Truth | Predicted | Correct | Top score |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in per_trader:
        top = max((s["score"] for s in r["scores"].values()), default=0)
        lines.append(
            f"| {r['name']} | `{r['truth']}` | `{r['predicted']}` | "
            f"{'✅' if r['correct'] else '❌'} | {top:.3f} |"
        )
    lines.append("")
    lines.append("## sklearn classification_report")
    lines.append("")
    lines.append("```")
    lines.append(report_text.strip())
    lines.append("```")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
