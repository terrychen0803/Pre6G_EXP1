"""File/CLI integration only: fixtures do not validate EXP1 trace extraction."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema

from pre6g_experiment.runtime_model import load_model
from pre6g_experiment.runtime_aggregation import aggregate_runtime

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = {
    "RTX4090": ("RTX4090_yolo_soft_gate_v1.json", "v2", 46.492214348373416,
                "a89e3f980a9836129b65d78d09bb8cb9b6e6336b272751b382e34a8ade2db75f"),
    "RTX5090": ("RTX5090_yolo_trace_only_v1.json", "v1", 109.25171448874927,
                "f6bc6800424c1d524863f441c8bfe7674116aba58c4b9e6dcaf8c376f4d3aa53"),
}


class FrozenRuntimeIntegrationTests(unittest.TestCase):
    def test_feature_file_to_prediction_and_semantic_output(self):
        schema = json.loads((ROOT / "schemas/runtime-prediction.schema.json").read_text())
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        with tempfile.TemporaryDirectory() as raw:
            for device, (filename, version, expected, digest) in REFERENCES.items():
                with self.subTest(device=device):
                    model_path = ROOT / "models/runtime" / filename
                    self.assertEqual(hashlib.sha256(model_path.read_bytes()).hexdigest(), digest)
                    model = load_model(model_path)
                    self.assertEqual(model["schema_version"], "pre6g.runtime-model/" + version)
                    features = ROOT / "tests/fixtures/runtime" / (device + ".json")
                    jsonschema.validate(json.loads(features.read_text()), json.loads((ROOT / "schemas/runtime-features.schema.json").read_text()))
                    output = Path(raw) / (device + ".json")
                    subprocess.run([sys.executable, "scripts/predict_runtime.py", "--model", str(model_path),
                        "--features", str(features), "--output", str(output)], cwd=ROOT, env=env,
                        check=True, capture_output=True, text=True)
                    prediction = json.loads(output.read_text())
                    jsonschema.validate(prediction, schema)
                    self.assertEqual(prediction["model_id"], model["model_id"])
                    self.assertEqual(prediction["device_id"], device)
                    self.assertEqual(prediction["node"], json.loads(features.read_text())["node"])
                    self.assertEqual(prediction["detector_profile"], "yolo-v1")
                    self.assertAlmostEqual(prediction["predicted_runtime_ms"], expected, places=9)
                    self.assertEqual("inferred_high_load_score" in prediction, device == "RTX4090")
                    if device == "RTX4090":
                        self.assertAlmostEqual(prediction["inferred_high_load_score"], 0.1815933454732066, places=12)
                    discovery = json.loads(subprocess.check_output([sys.executable, "scripts/discover_work.py",
                        "--job", "examples/yolo26/validation-source-job.yaml", "--output", str(Path(raw) / "discovery.json")], cwd=ROOT, env=env, text=True))
                    semantic = aggregate_runtime(prediction, discovery)
                    jsonschema.validate(semantic, json.loads((ROOT / "schemas/semantic-runtime.schema.json").read_text()))
                    if device == "RTX4090":
                        self.assertEqual(semantic["inferred_high_load_score"], prediction["inferred_high_load_score"])

    def test_cli_rejects_wrong_device_and_detector_for_both_models(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        with tempfile.TemporaryDirectory() as raw:
            for device, (filename, _, _, _) in REFERENCES.items():
                original = json.loads((ROOT / "tests/fixtures/runtime" / (device + ".json")).read_text())
                for field, wrong in (("device_id", "RTX3090"), ("detector_profile", "yolo-v2-long")):
                    with self.subTest(device=device, field=field):
                        sample = dict(original, **{field: wrong})
                        path = Path(raw) / "bad.json"
                        path.write_text(json.dumps(sample))
                        result = subprocess.run([sys.executable, "scripts/predict_runtime.py", "--model",
                            str(ROOT / "models/runtime" / filename), "--features", str(path)],
                            cwd=ROOT, env=env, capture_output=True, text=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("mismatch", result.stderr)
