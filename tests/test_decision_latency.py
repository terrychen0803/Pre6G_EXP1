from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
try:
    import yaml  # noqa: F401
except ImportError:  # The benchmark itself only replays already-rendered JSON.
    sys.modules["yaml"] = types.ModuleType("yaml")

import measure_decision_latency as latency  # noqa: E402


class DecisionLatencyTests(unittest.TestCase):
    def test_replay_measures_prediction_commands_without_changing_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts" / "node-a"
            artifacts.mkdir(parents=True)
            for relative in latency.PREDICTION_ARTIFACTS:
                target = artifacts / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")

            def fake_predict(plan, output):
                latency.dryrun._invoke(
                    ["python", "predict_runtime.py"],
                    cwd=output,
                    log=output / "logs" / "predict-node-a.log",
                )
                (output / "provisional-ranking.json").write_text(
                    json.dumps({"selected_node": "node-a"}), encoding="utf-8"
                )

            with patch.object(latency.dryrun, "_predict", fake_predict), patch.object(
                latency.dryrun, "_invoke", return_value="ok"
            ):
                sample = latency._replay_once({"candidates": [{"node": "node-a"}]}, run_dir, "node-a", root)

            self.assertGreaterEqual(sample["duration_ms"], sample["commands"][0]["duration_ms"])
            self.assertEqual(sample["commands"][0]["script"], "predict_runtime.py")
            self.assertEqual(sample["commands"][0]["node"], "node-a")
            self.assertFalse((run_dir / "provisional-ranking.json").exists())

    def test_warmup_is_excluded_from_mean_kpi(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = root / "timing.json"
            durations = iter((999.0, 80.0, 120.0))

            def fake_replay(*_args):
                return {
                    "duration_ms": next(durations),
                    "selected_node": "node-a",
                    "commands": [],
                    "other_processing_ms": 0.0,
                }

            with patch.object(latency, "_check_run", return_value=({"run_id": "test", "candidates": [{}, {}]}, "node-a")), patch.object(
                latency, "_replay_once", side_effect=fake_replay
            ):
                report = latency.benchmark(root, result, repeats=2, warmups=1, threshold_ms=100.0)

            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["completed_repeats"], 2)
            self.assertAlmostEqual(report["statistics"]["mean_ms"], 100.0)
            self.assertAlmostEqual(report["statistics"]["p95_ms"], 118.0)
            self.assertTrue(report["mean_kpi_pass"])
            self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["completed_repeats"], 2)


if __name__ == "__main__":
    unittest.main()

