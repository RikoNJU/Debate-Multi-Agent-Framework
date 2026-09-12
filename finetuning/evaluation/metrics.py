from typing import Any

import json


def load_results(results_path: str) -> list[dict]:
    with open(results_path) as f:
        return json.load(f)


def category_accuracy(predictions: list[dict]) -> float:
    matches = 0
    total = 0
    for p in predictions:
        gold = p.get("gold", {})
        pred = p.get("prediction", {})
        gold_findings = gold.get("findings", [])
        pred_findings = pred.get("findings", [])
        gold_cats = {f.get("category") for f in gold_findings}
        pred_cats = {f.get("category") for f in pred_findings}
        if gold_cats == pred_cats:
            matches += 1
        total += 1
    return matches / max(1, total)


def severity_accuracy(predictions: list[dict]) -> float:
    matches = 0
    total = 0
    for p in predictions:
        gold_findings = p.get("gold", {}).get("findings", [])
        pred_findings = p.get("prediction", {}).get("findings", [])
        gold_severities = []
        pred_severities = []
        for gf in gold_findings:
            if "category" in gf and "severity" in gf:
                gold_severities.append((gf["category"], gf["severity"]))
        for pf in pred_findings:
            if "category" in pf and "severity" in pf:
                pred_severities.append((pf["category"], pf["severity"]))
        gold_severities.sort()
        pred_severities.sort()
        if gold_severities == pred_severities:
            matches += 1
        total += 1
    return matches / max(1, total)


def finding_count_mae(predictions: list[dict]) -> float:
    total_diff = 0
    for p in predictions:
        gold_count = len(p.get("gold", {}).get("findings", []))
        pred_count = len(p.get("prediction", {}).get("findings", []))
        total_diff += abs(gold_count - pred_count)
    return total_diff / max(1, len(predictions))


def false_positive_rate(predictions: list[dict]) -> float:
    fp = 0
    total = 0
    for p in predictions:
        gold_has_issues = len(p.get("gold", {}).get("findings", [])) > 0
        pred_has_issues = len(p.get("prediction", {}).get("findings", [])) > 0
        if not gold_has_issues and pred_has_issues:
            fp += 1
        if not gold_has_issues:
            total += 1
    return fp / max(1, total)


def json_parse_rate(predictions: list[dict]) -> float:
    parsed = sum(1 for p in predictions if p["prediction"].get("status") != "parse_error")
    return parsed / max(1, len(predictions))


def compute_all(results_path: str) -> dict:
    predictions = load_results(results_path)
    return {
        "total_samples": len(predictions),
        "json_parse_rate": json_parse_rate(predictions),
        "category_accuracy": category_accuracy(predictions),
        "severity_accuracy": severity_accuracy(predictions),
        "finding_count_mae": finding_count_mae(predictions),
        "false_positive_rate": false_positive_rate(predictions),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python metrics.py <results.json>")
        sys.exit(1)

    metrics = compute_all(sys.argv[1])
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"{k}: {v:.4f}")
        else:
            print(f"{k}: {v}")