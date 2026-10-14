"""Summarize repeated, completed ground-truth comparisons by batch size."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(inputs: list[tuple[int, Path]]) -> dict:
    seen = set()
    groups: dict[int, list[dict]] = {}
    for batch, path in inputs:
        if batch <= 0:
            raise ValueError("batch size must be positive")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("schema_version") != "pre6g.validation-result/v1":
            raise ValueError(f"{path}: unexpected validation schema")
        run_id = result.get("validation_id")
        if not run_id or run_id in seen:
            raise ValueError(f"{path}: missing or duplicate validation ID")
        seen.add(run_id)
        groups.setdefault(batch, []).append(result)
    rows = []
    for batch, runs in sorted(groups.items()):
        complete = [run for run in runs if run.get("evaluation_complete") is True]
        node_rows = [node for run in complete for node in run["nodes"]]
        runtime_errors = [abs(float(node["runtime_proxy_error_percent"])) for node in node_rows]
        energy_errors = [abs(float(node["energy_proxy_error_percent"])) for node in node_rows
                         if node.get("energy_proxy_error_percent") is not None]
        rows.append({"batch_size": batch, "run_count": len(runs), "completed_count": len(complete),
                     "incomplete_validation_ids": [run["validation_id"] for run in runs if not run.get("evaluation_complete")],
                     "selection_matches": sum(run["selection_match"] is True for run in complete),
                     "selection_accuracy": sum(run["selection_match"] is True for run in complete) / len(complete) if complete else None,
                     "steady_runtime_proxy_mape_percent": sum(runtime_errors) / len(runtime_errors) if runtime_errors else None,
                     "steady_energy_proxy_mape_percent": sum(energy_errors) / len(energy_errors) if energy_errors else None,
                     "validation_ids": [run["validation_id"] for run in runs]})
    return {"schema_version": "pre6g.validation-series/v1", "groups": rows,
            "completed_total": sum(row["completed_count"] for row in rows),
            "limits": ["Steady proxy errors compare model estimates with full trainer measurements, including non-steady work.",
                       "Selection accuracy describes these measured candidate runs only; it is not an unseen-workload guarantee."]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize repeated validation-result.json files by batch size")
    parser.add_argument("--run", action="append", required=True, metavar="BATCH=RESULT_JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = []
    for item in args.run:
        batch, separator, path = item.partition("=")
        if not separator:
            parser.error("--run requires BATCH=RESULT_JSON")
        inputs.append((int(batch), Path(path)))
    summary = summarize(inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("refusing to overwrite an existing series result")
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
