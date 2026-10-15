from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeModelError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimePrediction:
    model_id: str
    device_id: str
    detected_unit: str
    predicted_runtime_ms: float
    feature_count: int
    inferred_high_load_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "pre6g.runtime-prediction/v1",
            "model_id": self.model_id,
            "device_id": self.device_id,
            "detected_unit": self.detected_unit,
            "predicted_runtime_ms": self.predicted_runtime_ms,
            "feature_count": self.feature_count,
        }
        if self.inferred_high_load_score is not None:
            payload["inferred_high_load_score"] = self.inferred_high_load_score
        return payload


def _numbers(values: Any, length: int, label: str) -> list[float]:
    if not isinstance(values, list) or len(values) != length:
        raise RuntimeModelError(f"{label} must contain {length} values")
    try:
        parsed = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise RuntimeModelError(f"{label} contains a non-numeric value") from exc
    if not all(math.isfinite(value) for value in parsed):
        raise RuntimeModelError(f"{label} contains a non-finite value")
    return parsed


def _positive_scales(values: Any, length: int, label: str) -> list[float]:
    scales = _numbers(values, length, label)
    if any(value <= 0.0 for value in scales):
        raise RuntimeModelError(f"{label} contains a non-positive scale")
    return scales


def load_model(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    required = {
        "schema_version",
        "model_id",
        "model_type",
        "device_id",
        "detected_unit",
        "feature_names",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise RuntimeModelError(
            f"runtime model is missing required fields: {missing}"
        )

    names = payload["feature_names"]
    if not isinstance(names, list) or not names or not all(
        isinstance(name, str) and name for name in names
    ) or len(set(names)) != len(names):
        raise RuntimeModelError("runtime model has invalid feature_names")
    width = len(names)
    version = payload["schema_version"]
    model_type = payload["model_type"]

    if version == "pre6g.runtime-model/v1" and model_type == "ridge_log_runtime":
        preprocessing = payload.get("preprocessing")
        ridge = payload.get("model")
        if not isinstance(preprocessing, dict) or not isinstance(ridge, dict):
            raise RuntimeModelError("ridge runtime model is missing preprocessing/model")
        _numbers(preprocessing.get("means"), width, "preprocessing.means")
        _positive_scales(preprocessing.get("scales"), width, "preprocessing.scales")
        _numbers(ridge.get("coefficients"), width, "model.coefficients")
        _numbers([ridge.get("intercept")], 1, "model.intercept")
    elif version == "pre6g.runtime-model/v2" and model_type == "trace_soft_regime_interactions_crossfit_gate":
        gate = payload.get("gate")
        ridge = payload.get("ridge")
        if not isinstance(gate, dict) or not isinstance(ridge, dict):
            raise RuntimeModelError("soft-gate runtime model is missing gate/ridge")
        _numbers(gate.get("means"), width, "gate.means")
        _positive_scales(gate.get("scales"), width, "gate.scales")
        centroids = gate.get("centroids")
        if not isinstance(centroids, dict):
            raise RuntimeModelError("soft-gate runtime model is missing centroids")
        for condition in ("clean", "high_load_01"):
            _numbers(centroids.get(condition), width, f"gate.centroids.{condition}")
        temperature = _numbers([gate.get("temperature")], 1, "gate.temperature")[0]
        if temperature <= 0.0:
            raise RuntimeModelError("gate.temperature must be positive")
        expanded_width = 2 * width + 1
        _numbers(ridge.get("means"), expanded_width, "ridge.means")
        _positive_scales(ridge.get("scales"), expanded_width, "ridge.scales")
        _numbers(ridge.get("coefficients"), expanded_width + 1, "ridge.coefficients")
    else:
        raise RuntimeModelError(f"unsupported runtime model schema/type: {version}/{model_type}")

    return payload


def load_feature_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if payload.get("schema_version") != "pre6g.runtime-features/v1":
        raise RuntimeModelError(
            "unsupported runtime feature artifact schema"
        )

    if not isinstance(payload.get("trace_features"), dict):
        raise RuntimeModelError(
            "runtime feature artifact must contain trace_features"
        )

    return payload


def predict_runtime(
    model: dict[str, Any],
    feature_artifact: dict[str, Any],
) -> RuntimePrediction:
    model_device = str(model["device_id"])
    sample_device = str(feature_artifact.get("device_id", ""))

    if sample_device != model_device:
        raise RuntimeModelError(
            f"device mismatch: model={model_device}, sample={sample_device}"
        )

    model_unit = str(model["detected_unit"])
    sample_unit = str(feature_artifact.get("detected_unit", ""))

    if sample_unit != model_unit:
        raise RuntimeModelError(
            f"detected_unit mismatch: model={model_unit}, sample={sample_unit}"
        )

    detector_profile = model.get("detector_profile")
    sample_profile = feature_artifact.get("detector_profile")
    if detector_profile and sample_profile != detector_profile:
        raise RuntimeModelError(
            "detector profile mismatch: "
            f"model={detector_profile}, sample={sample_profile}"
        )

    feature_map = feature_artifact["trace_features"]
    names = list(model["feature_names"])

    missing = [name for name in names if name not in feature_map]
    if missing:
        raise RuntimeModelError(
            f"missing runtime features: {missing}"
        )

    values = _numbers([feature_map[name] for name in names], len(names), "trace_features")
    score = None
    if model["model_type"] == "ridge_log_runtime":
        means = model["preprocessing"]["means"]
        scales = model["preprocessing"]["scales"]
        standardized = [(value - mean) / scale for value, mean, scale in zip(values, means, scales)]
        ridge = model["model"]
        log_runtime = ridge["intercept"] + sum(
            coefficient * value for coefficient, value in zip(ridge["coefficients"], standardized)
        )
    else:
        gate = model["gate"]
        point = [(value - mean) / scale for value, mean, scale in zip(values, gate["means"], gate["scales"])]
        centroids = gate["centroids"]
        clean_distance = sum((value - center) ** 2 for value, center in zip(point, centroids["clean"]))
        loaded_distance = sum((value - center) ** 2 for value, center in zip(point, centroids["high_load_01"]))
        logit = max(-50.0, min(50.0, (clean_distance - loaded_distance) / gate["temperature"]))
        score = 1.0 / (1.0 + math.exp(-logit))
        expanded = [*values, score, *[score * value for value in values]]
        ridge = model["ridge"]
        standardized = [1.0] + [
            (value - mean) / scale for value, mean, scale in zip(expanded, ridge["means"], ridge["scales"])
        ]
        log_runtime = sum(value * coefficient for value, coefficient in zip(standardized, ridge["coefficients"]))

    try:
        prediction = math.exp(log_runtime)
    except OverflowError as exc:
        raise RuntimeModelError("runtime prediction overflow") from exc

    if not math.isfinite(prediction) or prediction <= 0.0:
        raise RuntimeModelError(
            f"invalid runtime prediction: {prediction}"
        )

    return RuntimePrediction(
        model_id=str(model["model_id"]),
        device_id=model_device,
        detected_unit=model_unit,
        predicted_runtime_ms=prediction,
        feature_count=len(names),
        inferred_high_load_score=score,
    )
