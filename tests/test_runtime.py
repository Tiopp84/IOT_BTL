import unittest
from collections import deque

import pandas as pd

from backend.inference import LAG, PREFIXES, Predictor, extract_features
from backend.monitor import Monitor, validate_telemetry


class FeatureTests(unittest.TestCase):
    def test_online_features_match_causal_training_features(self):
        samples = [(23 + i * .03, 60 - i * .05, i * i * .2, i * i * .35) for i in range(20)]
        frame = pd.DataFrame(samples, columns=PREFIXES)
        expected = pd.DataFrame(index=frame.index)
        for name in PREFIXES:
            for lag in (1, 5):
                expected[f'd_{name}_{lag}'] = frame[name].diff(lag).fillna(0)
            expected[f'{name}_roll_5_std'] = frame[name].rolling(5, min_periods=1).std().fillna(0)
        expected['pm_ratio'] = (frame.pm25 + .001) / (frame.pm1 + .001)
        history = deque(maxlen=LAG + 1)
        for index, sample in enumerate(samples):
            history.append(sample)
            actual = extract_features(history)
            for name, value in actual.items():
                self.assertAlmostEqual(value, expected.loc[index, name], places=8)

    def test_new_bundle_schema_and_policy(self):
        predictor = Predictor()
        self.assertEqual(predictor.info['name'], 'LightGBM_Clean13_Field_v2')
        self.assertEqual(predictor.info['variant'], 'clean13')
        self.assertEqual(predictor.info['score_kind'], 'onset')
        self.assertEqual(predictor.features, list(predictor.model.feature_name_))
        self.assertEqual(len(predictor.features), 13)
        self.assertEqual(predictor.policy['T_HIGH'], .25)
        self.assertLess(predictor.policy['T_LOW'], predictor.policy['T_HIGH'])
        score = predictor.score(extract_features([(23, 60, 1, 2)] * 6))
        self.assertTrue(0 <= score <= 1)


class StubPredictor:
    policy = {'T_LOW': .10, 'T_HIGH': .25, 'window_size': 5, 'required_votes': 3}
    features = list(extract_features([(0, 0, 0, 0)]))
    info = {'name': 'test', 'variant': 'clean13', 'score_kind': 'onset'}

    def __init__(self, scores):
        self.scores = iter(scores)
        self.calls = 0

    def score(self, features):
        self.calls += 1
        return next(self.scores)


def telemetry(seq, boot='test-boot'):
    return dict(schema_version=1, device_id='test-node', boot_id=boot,
                seq=seq, uptime_ms=seq * 1000, sensor_status='OK',
                temperature_c=23, humidity_pct=60, pm1_ug_m3=1, pm25_ug_m3=2,
                ir_active=False, ir_active_duration_ms=0)


class MonitorTests(unittest.TestCase):
    def test_six_sample_history_then_five_valid_votes(self):
        predictor = StubPredictor([.26, .02, .26, .02, .26])
        monitor = Monitor(predictor)
        monitor.connected = True
        for seq in range(1, 11):
            data = telemetry(seq)
            monitor.accept('test-node', data, validate_telemetry('test-node', data))
            current = monitor.devices['test-node'].current
            if seq <= 5:
                self.assertIsNone(current['p_fire'])
                self.assertEqual(current['vote_samples'], 0)
            if seq < 10:
                self.assertEqual(current['health'], 'WARMUP')
                self.assertNotEqual(current['risk_level'], 'FIRE_ALERT')
        self.assertEqual(predictor.calls, 5)
        self.assertEqual(current['health'], 'OK')
        self.assertEqual(current['votes'], 3)
        self.assertEqual(current['risk_level'], 'FIRE_ALERT')
        self.assertEqual(monitor.snapshot()['model']['variant'], 'clean13')

    def pm_run(self, values, ir=False):
        predictor = StubPredictor([.99] * (len(values) - 5))
        predictor.policy = {**predictor.policy, "pm_trend_gate": {
            "enabled": True, "minimum_drop_ug_m3": 1.0}}
        monitor = Monitor(predictor)
        for seq, (pm1, pm25) in enumerate(values, 1):
            data = telemetry(seq)
            data.update(pm1_ug_m3=pm1, pm25_ug_m3=pm25,
                        ir_active=ir, ir_active_duration_ms=2000 if ir else 0)
            monitor.accept('test-node', data, validate_telemetry('test-node', data))
        return monitor.devices['test-node'].current

    def test_declining_pm_blocks_votes_without_changing_score(self):
        current = self.pm_run([(50-i, 70-i) for i in range(10)])
        self.assertEqual(current['p_fire'], .99)
        self.assertTrue(current['pm_declining'])
        self.assertFalse(current['onset_vote_eligible'])
        self.assertEqual(current['votes'], 0)
        self.assertNotIn(current['risk_level'], ('FIRE_ALERT', 'SAFE', 'ATTENTION'))

    def test_rising_or_mixed_pm_remains_eligible(self):
        for values in ([(i, i*2) for i in range(10)],
                       [(50-i, 70+i) for i in range(10)],
                       [(20, 30)] * 10):
            with self.subTest(values=values):
                current = self.pm_run(values)
                self.assertFalse(current['pm_declining'])
                self.assertEqual(current['votes'], 5)
                self.assertEqual(current['risk_level'], 'FIRE_ALERT')

    def test_declining_pm_does_not_suppress_ir_confirmation(self):
        current = self.pm_run([(50-i, 70-i) for i in range(10)], ir=True)
        self.assertEqual(current['votes'], 0)
        self.assertEqual(current['risk_level'], 'FIRE_ALERT')

    def test_decline_does_not_erase_existing_votes(self):
        values = [(10+i, 20+i) for i in range(10)] + [(0, 0)]
        current = self.pm_run(values)
        self.assertTrue(current['pm_declining'])
        self.assertEqual(current['votes'], 4)
        self.assertEqual(current['risk_level'], 'FIRE_ALERT')

    def test_sample_gap_resets_history_and_votes(self):
        predictor = StubPredictor([.26] * 5)
        monitor = Monitor(predictor)
        for seq in range(1, 11):
            data = telemetry(seq)
            monitor.accept('test-node', data, validate_telemetry('test-node', data))
        data = telemetry(12)
        monitor.accept('test-node', data, validate_telemetry('test-node', data))
        current = monitor.devices['test-node'].current
        self.assertIsNone(current['p_fire'])
        self.assertEqual(current['vote_samples'], 0)
        self.assertEqual(current['health'], 'WARMUP')
        self.assertEqual(predictor.calls, 5)

    def test_sensor_error_clears_model_window(self):
        monitor = Monitor(StubPredictor([.26] * 5))
        for seq in range(1, 11):
            data = telemetry(seq)
            monitor.accept('test-node', data, validate_telemetry('test-node', data))
        data = telemetry(11)
        data['sensor_status'] = 'PMS_ERROR'
        monitor.accept('test-node', data, validate_telemetry('test-node', data))
        current = monitor.devices['test-node'].current
        self.assertIsNone(current['p_fire'])
        self.assertEqual(current['vote_samples'], 0)
        self.assertEqual(current['health'], 'SENSOR_ERROR')

    def test_ir_attention_during_feature_warmup(self):
        predictor = StubPredictor([])
        monitor = Monitor(predictor)
        data = telemetry(1)
        data.update(ir_active=True, ir_active_duration_ms=2000)
        monitor.accept('test-node', data, validate_telemetry('test-node', data))
        current = monitor.devices['test-node'].current
        self.assertEqual(current['risk_level'], 'ATTENTION')
        self.assertIsNone(current['p_fire'])
        self.assertEqual(predictor.calls, 0)


if __name__ == '__main__':
    unittest.main()
