import json
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
        bundle = Path(__file__).resolve().parents[1] / "model_13f"
        metadata = json.loads((bundle / "metadata.json").read_text())
        self.model = joblib.load(bundle / "lightgbm_dynamic_model.joblib")
        self.features = metadata["features"]
        expected = extract_features([(0, 0, 0, 0)])
        if (
            self.features != list(self.model.feature_name_)
            or len(self.features) != metadata["n_features"]
            or set(self.features) != set(expected)
        ):
            raise ValueError("Model and online feature schema do not match")
        self.positive_index = list(self.model.classes_).index(1)
        self.policy = metadata["thresholds"]

    def score(self, features):
        frame = pd.DataFrame([features], columns=self.features)
        return float(self.model.predict_proba(frame, num_threads=1)[0, self.positive_index])
