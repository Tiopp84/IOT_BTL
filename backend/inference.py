import json
from pathlib import Path
from statistics import mean, stdev

import joblib
import pandas as pd

SENSOR_KEYS = ("temperature_c", "humidity_pct", "pm1_ug_m3", "pm25_ug_m3")
PREFIXES = ("temp", "hum", "pm1", "pm25")
LAG = 5


def extract_features(samples):
    """Compute the notebook features for the newest sample in a contiguous run."""
    current = samples[-1]
    rolling = list(samples)[-LAG:]
    features = dict(zip(SENSOR_KEYS, current))
    temperature, humidity, pm1, pm25 = current
    features["pm_ratio"] = (pm25 + 1e-3) / (pm1 + 1e-3)
    features["temp_hum_ratio"] = temperature / (humidity + 1e-3)
    for index, (name, values) in enumerate(zip(PREFIXES, zip(*rolling))):
        features[f"d_{name}_5"] = current[index] - samples[-LAG - 1][index] if len(samples) > LAG else 0.0
        features[f"{name}_roll_5_mean"] = mean(values)
        if name in ("temp", "hum"):
            features[f"{name}_roll_5_std"] = stdev(values) if len(values) > 1 else 0.0
    return features


class Predictor:
    def __init__(self):
        bundle = Path(__file__).resolve().parents[1] / "smoke_model_bundle"
        metadata = json.loads((bundle / "metadata.json").read_text())
        self.model = joblib.load(bundle / "lightgbm_smoke_model.joblib")
        self.features = metadata["features"]
        expected = extract_features([(0, 0, 0, 0)])
        if (
            self.features != list(self.model.feature_name_)
            or len(self.features) != metadata["num_features"]
            or set(self.features) != set(expected)
        ):
            raise ValueError("Model and online feature schema do not match")
        self.positive_index = list(self.model.classes_).index(metadata["positive_label"])
        self.policy = metadata["decision_policy"]

    def score(self, features):
        frame = pd.DataFrame([features], columns=self.features)
        return float(self.model.predict_proba(frame, num_threads=1)[0, self.positive_index])
