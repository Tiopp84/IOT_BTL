import json
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import stdev

import joblib
import pandas as pd

SENSOR_KEYS = ("temperature_c", "humidity_pct", "pm1_ug_m3", "pm25_ug_m3")
PREFIXES = ("temp", "hum", "pm1", "pm25")
LAG = 5


def extract_features(samples):
    """Compute the notebook features for the newest sample in a contiguous run."""
    current = samples[-1]
    rolling = list(samples)[-LAG:]
    features = {}
    for index, name in enumerate(PREFIXES):
        for lag in (1, LAG):
            features[f"d_{name}_{lag}"] = (
                current[index] - samples[-lag - 1][index] if len(samples) > lag else 0.0
            )
    for name, values in zip(PREFIXES, zip(*rolling)):
        features[f"{name}_roll_5_std"] = stdev(values) if len(values) > 1 else 0.0
    _, _, pm1, pm25 = current
    features["pm_ratio"] = (pm25 + 1e-3) / (pm1 + 1e-3)
    return features


class Predictor:
    def __init__(self):
        backend = Path(__file__).resolve().parent
        bundle = backend.parent / "lightgbm_clean13_field_v2_bundle"
        metadata = json.loads((bundle / "metadata.json").read_text())
        model_path = bundle / "lightgbm_clean13_field_v2.joblib"
        self.model = joblib.load(model_path)
        self.features = metadata["features"]
        expected = extract_features([(0, 0, 0, 0)])
        if (
            metadata["variant"] != "clean13"
            or metadata["positive_label"] != 1
            or list(self.model.classes_) != [0, 1]
            or metadata.get("baseline", {}).get("used_by_exported_model", False)
            or self.features != list(self.model.feature_name_)
            or len(self.features) != metadata["n_features"]
            or set(self.features) != set(expected)
        ):
            raise ValueError("Model and online feature schema do not match")
        self.positive_index = list(self.model.classes_).index(metadata["positive_label"])
        policy = json.loads((backend / "decision_policy.json").read_text())
        threshold = float(metadata["classification_threshold"])
        low = policy["T_LOW"]
        window, votes = policy["window_size"], policy["required_votes"]
        if (
            not isfinite(threshold)
            or type(low) not in (int, float)
            or not 0 <= low < threshold <= 1
            or type(window) is not int
            or type(votes) is not int
            or not 1 <= votes <= window
        ):
            raise ValueError("Invalid server decision policy")
        gate = policy.get("pm_trend_gate", {})
        drop = gate.get("minimum_drop_ug_m3", 1.0)
        if (
            type(gate.get("enabled", False)) is not bool
            or type(drop) not in (int, float)
            or not isfinite(drop)
            or drop <= 0
        ):
            raise ValueError("Invalid PM trend policy")
        self.policy = {**policy, "T_HIGH": threshold}
        self.info = {
            "name": metadata["model_name"],
            "variant": metadata["variant"],
            "target": metadata["target"],
            "score_kind": "onset",
            "positive_meaning": metadata["positive_meaning"],
            "negative_meaning": metadata["negative_meaning"],
            "n_features": metadata["n_features"],
            "minimum_history_samples": LAG + 1,
            "classification_threshold": threshold,
            "sha256": sha256(model_path.read_bytes()).hexdigest(),
        }

    def score(self, features):
        frame = pd.DataFrame([features], columns=self.features)
        return float(self.model.predict_proba(frame, num_threads=1)[0, self.positive_index])
