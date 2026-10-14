#!/usr/bin/env python3
"""Export the approved 2025-Q4 alpha=100 calendar Ridge; never train on actuals.

Usage: python export_ridge.py --input TRAINING_CSV --selection RESULTS_JSON
The generated client artifact has coefficients and provenance, no source rows.
Run with the experiment's NumPy environment. No database/network access occurs.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np

REGIONS = ("北区", "西区", "南区", "四川地区", "江苏地区")
ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "assets/operations-ridge-model.json"
TEST_PATH = ROOT / "tools/operations-forecast/test_ridge.cjs"
FIXTURE_START = "// BEGIN GENERATED PYTHON PARITY FIXTURES"
FIXTURE_END = "// END GENERATED PYTHON PARITY FIXTURES"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def features(rows: list[tuple[date, str, float]]) -> np.ndarray:
    result = np.zeros((len(rows), 25))
    for i, (day, region, _) in enumerate(rows):
        result[i, REGIONS.index(region)] = 1
        result[i, 5 + day.month - 1] = 1
        result[i, 17 + day.weekday()] = 1
        result[i, 24] = day.day / calendar.monthrange(day.year, day.month)[1]
    return result


def read_training(path: Path) -> list[tuple[date, str, float]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not {"date", "region", "gmv_yuan"}.issubset(reader.fieldnames):
            raise ValueError("Training requires date, region and gmv_yuan")
        rows = [(date.fromisoformat(r["date"]), r["region"], float(r["gmv_yuan"])) for r in reader]
    expected = {(date(2025, 10, 1) + timedelta(days=d), r) for d in range(92) for r in REGIONS}
    if len(rows) != 460 or {(d, r) for d, r, _ in rows} != expected:
        raise ValueError("Only complete 2025-10--12 five-region rows are approved; no 2026 rows or imputation")
    if any(not np.isfinite(v) or v < 0 for _, _, v in rows):
        raise ValueError("Training GMV must be finite and nonnegative")
    return sorted(rows, key=lambda r: (r[0], r[1]))


def train(rows: list[tuple[date, str, float]]) -> dict:
    x = features(rows)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(26) * 100.0
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ np.log1p([r[2] for r in rows]))
    return {"mean": mean.tolist(), "scale": scale.tolist(), "coefficients": coefficients.tolist()}


def shape(parameters: dict, month: str, region: str) -> np.ndarray:
    start = date.fromisoformat(month + "-01")
    days = calendar.monthrange(start.year, start.month)[1]
    rows = [(start + timedelta(days=d), region, 0.0) for d in range(days)]
    x = features(rows)
    design = np.column_stack([np.ones(days), (x - parameters["mean"]) / parameters["scale"]])
    log = design @ parameters["coefficients"]
    return np.maximum(np.expm1(np.clip(log, -30.0, 50.0)), 1e-9)


def make_fixtures(parameters: dict) -> list[dict]:
    fixtures = []
    for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
        for index, region in enumerate(REGIONS):
            daily = shape(parameters, month, region)
            for cutoff in (1, 7, 13, 14, 21, len(daily)):
                cumulative = (index + 1) * 12345.67 + cutoff * 17.0  # Synthetic, not business actuals.
                prefix = float(daily[:cutoff].sum())
                share = prefix / float(daily.sum()) if cutoff < len(daily) else 1.0
                total = cumulative + cumulative / prefix * float(daily[cutoff:].sum())
                fixtures.append({"month": month, "region": region, "cutoff": cutoff,
                                 "syntheticGmv": cumulative, "share": share,
                                 "forecast": total, "firstDayShape": float(daily[0]),
                                 "lastDayShape": float(daily[-1])})
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    if selection.get("selected_parameters", {}).get("selected_alpha") != 100 or selection.get("training_months") != ["2025-10", "2025-11", "2025-12"]:
        raise ValueError("The selected model must be the approved 2025-Q4 alpha=100 prototype")
    rows = read_training(args.input)
    parameters = train(rows)
    parameter_sha = hashlib.sha256(json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    model = {"schemaVersion": 1, "modelVersion": "calendar-ridge-2025q4-alpha100-v1",
             "status": "prototype_reference_unreconciled", "alpha": 100,
             "forecastYear": 2026, "supportedMonths": ["09", "10", "11", "12"],
             "regions": list(REGIONS), "featureSchema": "region5-month12-mondayWeekday7-relativeDay-v1",
             "targetTransform": "log1p", "parameters": parameters, "parametersSha256": parameter_sha,
             "training": {"start": "2025-10-01", "end": "2025-12-31", "rows": len(rows),
                          "inputSha256": digest(args.input), "selectionSha256": digest(args.selection),
                          "table": "report.dws_data_merge", "organizationTable": "report.dws_store_number",
                          "parameterSelection": "forward 2025 folds only; fixed alpha=100; no 2026 fitting"},
             "validation": {"months": ["2026-09"], "cutoffDays": [7, 14, 21],
                            "regionWape": selection["metrics"]["calendar_ridge"]["regional_all_cutoffs"]["wape"]},
             "limitations": ["2025 historical reference is not fully reconciled; known monthly total gaps about 3.1%--3.7%",
                             "Only one independent validation month; cutoff-region rows are not independent months",
                             "2025 September daily source unavailable; September month coefficient is untrained",
                             "No separate holiday effect estimated; 2026 September replay uses restated post-month-end data",
                             "Fixed coefficients are calibrated against observed current-month GMV; refresh is not retraining"]}
    if args.source_manifest:
        model["training"]["manifestSha256"] = digest(args.source_manifest)
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    fixtures = make_fixtures(parameters)
    tests = TEST_PATH.read_text(encoding="utf-8")
    before, marker, rest = tests.partition(FIXTURE_START)
    _, end_marker, after = rest.partition(FIXTURE_END)
    if not marker or not end_marker:
        raise ValueError("JS test parity fixture markers are missing")
    generated = "\nconst PARITY_MODEL_PARAMETERS_SHA = " + json.dumps(parameter_sha) + ";\nconst PYTHON_FIXTURES = " + json.dumps(fixtures, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + ";\n"
    TEST_PATH.write_text(before + FIXTURE_START + generated + FIXTURE_END + after, encoding="utf-8")
    print(json.dumps({"model": str(MODEL_PATH), "modelSha256": digest(MODEL_PATH),
                      "trainingRows": len(rows), "alpha": 100, "parityFixtures": len(fixtures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
