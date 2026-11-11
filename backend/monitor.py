import math
import re
from collections import deque
from dataclasses import dataclass, field
from time import monotonic, time

from backend.inference import LAG, SENSOR_KEYS, extract_features

TIMEOUT_SECONDS = 5
MAX_SAMPLE_GAP_MS = 2500
SAFE_SAMPLES = 10
ATTENTION_SAMPLES = 3
IR_CONFIRM_MS = 2000
MAX_DEVICES = 32
HISTORY_SIZE = 120
DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,48}")


def validate_telemetry(device_id, data):
    if not isinstance(data, dict) or not DEVICE_ID.fullmatch(device_id):
        raise ValueError("Invalid telemetry envelope")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    if data.get("device_id") != device_id:
        raise ValueError("Topic and device ID do not match")
    boot_id = data.get("boot_id")
    if not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 64:
        raise ValueError("Invalid boot_id")
    for key in ("seq", "uptime_ms", "ir_active_duration_ms"):
        if type(data.get(key)) is not int or not 0 <= data[key] <= 2**53 - 1:
            raise ValueError(f"Invalid {key}")
    if type(data.get("ir_active")) is not bool:
        raise ValueError("Invalid ir_active")
    ir_raw = data.get("ir_raw")
    if ir_raw is not None and (type(ir_raw) is not int or not 0 <= ir_raw <= 100000):
        raise ValueError("Invalid ir_raw")
    status = data.get("sensor_status")
    if not isinstance(status, str) or not 1 <= len(status) <= 32:
        raise ValueError("Invalid sensor_status")
    if status != "OK":
        return None
    bounds = ((-100, 150), (0, 100), (0, 100000), (0, 100000))
    values = tuple(data.get(key) for key in SENSOR_KEYS)
    for value, (lower, upper) in zip(values, bounds):
        if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
            raise ValueError("Invalid sensor measurement")
    return values


@dataclass
class Device:
    scores: deque
    samples: deque = field(default_factory=lambda: deque(maxlen=LAG + 1))
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))
    events: deque = field(default_factory=lambda: deque(maxlen=30))
    retired_boots: deque = field(default_factory=lambda: deque(maxlen=16))
    boot_id: str | None = None
    seq: int = -1
    uptime_ms: int = -1
    last_seen: float = 0
    current: dict | None = None
    safe_streak: int = 0
    attention_streak: int = 0
    buzzer_enabled: bool = True
    last_state: tuple | None = None

    def reset_windows(self):
        self.samples.clear()
        self.scores.clear()
        self.safe_streak = 0
        self.attention_streak = 0

    def record_state(self, health, risk_level, reason, timestamp):
        state = (health, risk_level)
        if state != self.last_state:
            self.events.appendleft(dict(health=health, risk_level=risk_level, reason=reason, timestamp=timestamp))
            self.last_state = state


class Monitor:
    """State accessed under the application's lock by MQTT and HTTP threads."""

    def __init__(self, predictor):
        self.predictor = predictor
        policy = predictor.policy
        self.low_threshold = policy["T_LOW"]
        self.threshold = policy["T_HIGH"]
        self.window = policy["window_size"]
        self.required_votes = policy["required_votes"]
        self.devices = {}
        self.connected = False

    def accept(self, device_id, data, values):
        now = monotonic()
        device = self.devices.get(device_id)
        if device is None:
            if len(self.devices) >= MAX_DEVICES:
                return
            device = self.devices[device_id] = Device(deque(maxlen=self.window))
        boot_id, seq, uptime = data["boot_id"], data["seq"], data["uptime_ms"]
        if boot_id in device.retired_boots:
            return
        if boot_id == device.boot_id:
            if seq <= device.seq or uptime <= device.uptime_ms:
                return
            if seq != device.seq + 1 or uptime - device.uptime_ms > MAX_SAMPLE_GAP_MS or now - device.last_seen >= TIMEOUT_SECONDS:
                device.reset_windows()
        else:
            if device.boot_id is not None:
                device.retired_boots.append(device.boot_id)
            device.reset_windows()
        if isinstance(data.get("buzzer_enabled"), bool):
            device.buzzer_enabled = data["buzzer_enabled"]

        score = features = None
        ir_confirmed = data["ir_active"] and data["ir_active_duration_ms"] >= IR_CONFIRM_MS
        health = "SENSOR_ERROR"
        risk_level = None
        reason = "Thiết bị báo lỗi cảm biến"
        votes = 0
        if values is None:
            device.reset_windows()
        else:
            device.samples.append(values)
            features = extract_features(device.samples)
            score = self.predictor.score(features)
            device.scores.append(score)
            device.attention_streak = device.attention_streak + 1 if score >= self.low_threshold else 0
            device.safe_streak = device.safe_streak + 1 if score < self.low_threshold and not data["ir_active"] else 0
            votes = sum(point >= self.threshold for point in device.scores)
            full_window = len(device.scores) == self.window
            health = "OK" if full_window else "WARMUP"
            if full_window and votes >= self.required_votes:
                risk_level, reason = "FIRE_ALERT", "LightGBM đủ phiếu cảnh báo"
            elif ir_confirmed and score >= self.low_threshold:
                risk_level, reason = "FIRE_ALERT", "Flame IR xác nhận và điểm LightGBM tăng"
            elif ir_confirmed:
                risk_level, reason = "ATTENTION", "Flame IR xác nhận"
            elif device.attention_streak >= ATTENTION_SAMPLES:
                risk_level, reason = "ATTENTION", "Điểm LightGBM đạt ngưỡng chú ý trong 3 mẫu liên tiếp"
            elif device.safe_streak >= SAFE_SAMPLES:
                risk_level, reason = "SAFE", "Đủ 10 mẫu điểm thấp và IR không hoạt động"
            elif device.attention_streak:
                reason = f"Đang xác nhận mức chú ý ({device.attention_streak}/{ATTENTION_SAMPLES} mẫu)"
            else:
                reason = "Đang tích lũy mẫu" if not full_window else "Đang xác nhận mức an toàn"

        measured_risk_level, measured_reason = risk_level, reason
        timestamp = time()
        current = dict(zip(SENSOR_KEYS, values)) if values is not None else dict.fromkeys(SENSOR_KEYS)
        current.update(
            boot_id=boot_id, seq=seq, uptime_ms=uptime, timestamp=timestamp,
            ir_active=data["ir_active"], ir_confirmed=ir_confirmed, p_fire=score,
            ir_active_duration_ms=data["ir_active_duration_ms"], ir_raw=data.get("ir_raw"),
            health=health, risk_level=risk_level, reason=reason,
            measured_risk_level=measured_risk_level, measured_reason=measured_reason,
            votes=votes, vote_samples=len(device.scores), buzzer_enabled=device.buzzer_enabled,
        )
        device.boot_id, device.seq, device.uptime_ms = boot_id, seq, uptime
        device.last_seen = now
        device.current = dict(current, features=features, score_window=list(device.scores))
        device.history.append(current)
        device.record_state(health, measured_risk_level, measured_reason, timestamp)

    def snapshot(self):
        now = monotonic()
        devices = {}
        for device_id, device in self.devices.items():
            current = device.current.copy()
            current["buzzer_enabled"] = device.buzzer_enabled
            if not self.connected or now - device.last_seen >= TIMEOUT_SECONDS:
                current.update(
                    health="OFFLINE", p_fire=None, votes=0, vote_samples=0,
                    features=None, score_window=[],
                    measured_risk_level=None,
                    risk_level=None,
                    reason="Chưa nhận được dữ liệu mới" if self.connected else "Mất kết nối MQTT",
                )
                current["measured_reason"] = current["reason"]
                device.record_state("OFFLINE", None, current["measured_reason"], time())
            devices[device_id] = dict(current=current, history=list(device.history), events=list(device.events))
        return dict(mqtt_connected=self.connected, threshold=self.threshold, window=self.window,
                    required_votes=self.required_votes, feature_names=self.predictor.features,
                    low_threshold=self.low_threshold, attention_samples=ATTENTION_SAMPLES,
                    ir_confirm_ms=IR_CONFIRM_MS, devices=devices)
