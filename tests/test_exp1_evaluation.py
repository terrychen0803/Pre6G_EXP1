from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALUATOR = script("evaluate_validation_runs")
SERIES = script("summarize_validation_series")
EXCEL = script("normalize_pdu_excel")


class Exp1EvaluationTests(unittest.TestCase):
    def test_point_power_uses_trapezoids_and_interpolates_boundaries(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "pdu.csv"
            path.write_text("timestamp,power_w\n2026-10-01T00:00:00Z,100\n2026-10-01T00:01:00Z,200\n", encoding="utf-8")
            result = EVALUATOR.pdu_point_energy(path, start=EVALUATOR.timestamp("2026-10-01T00:00:15Z"),
                end=EVALUATOR.timestamp("2026-10-01T00:00:45Z"), timestamp_column="timestamp",
                power_column="power_w", max_gap_seconds=60)
            self.assertAlmostEqual(result["gross_energy_wh"], 150 * 30 / 3600)
            with self.assertRaisesRegex(ValueError, "gap"):
                EVALUATOR.pdu_point_energy(path, start=EVALUATOR.timestamp("2026-10-01T00:00:15Z"),
                    end=EVALUATOR.timestamp("2026-10-01T00:00:45Z"), timestamp_column="timestamp",
                    power_column="power_w", max_gap_seconds=30)

    def test_excel_naive_time_gets_explicit_offset(self):
        value = EXCEL.timestamp("2026-10-01 08:00:00", EXCEL.offset("+08:00"))
        self.assertEqual(value, "2026-10-01T00:00:00Z")

    def test_excel_outlet_filter_and_kw_conversion(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl is not installed in this offline Python runtime")
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "meter.xlsx"
            target = Path(raw) / "outlet.csv"
            book = Workbook()
            sheet = book.active
            sheet.title = "power"
            sheet.append(["time", "outlet", "kW"])
            sheet.append(["2026-10-01 08:00:00", "A", 0.1])
            sheet.append(["2026-10-01 08:00:30", "B", 0.8])
            sheet.append(["2026-10-01 08:01:00", "A", 0.2])
            book.save(source)
            manifest = EXCEL.normalize(source, target, sheet="power", header_row=1,
                timestamp_column="time", power_column="kW", outlet_column="outlet",
                outlet_value="A", timezone_offset="+08:00", unit="kW")
            self.assertEqual(manifest["sample_count"], 2)
            self.assertEqual(target.read_text(encoding="utf-8").splitlines(),
                ["timestamp,power_w", "2026-10-01T00:00:00Z,100.0", "2026-10-01T00:01:00Z,200.0"])

    def test_series_excludes_incomplete_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            complete = Path(raw) / "complete.json"
            incomplete = Path(raw) / "incomplete.json"
            complete.write_text(json.dumps({"schema_version": "pre6g.validation-result/v1", "validation_id": "a",
                "evaluation_complete": True, "selection_match": True,
                "nodes": [{"runtime_proxy_error_percent": 10, "energy_proxy_error_percent": 20}]}), encoding="utf-8")
            incomplete.write_text(json.dumps({"schema_version": "pre6g.validation-result/v1", "validation_id": "b",
                "evaluation_complete": False, "selection_match": None, "nodes": []}), encoding="utf-8")
            summary = SERIES.summarize([(16, complete), (16, incomplete)])
            self.assertEqual(summary["groups"][0]["completed_count"], 1)
            self.assertEqual(summary["groups"][0]["selection_accuracy"], 1)


if __name__ == "__main__":
    unittest.main()
