import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from itertools import count
from math import exp, sin
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.sse import EventSourceResponse
from paho.mqtt import client as mqtt

from backend.inference import Predictor
from backend.monitor import Monitor, validate_telemetry

log = logging.getLogger(__name__)
TOPIC = "fire/+/telemetry"
ALERT_TOPIC = "fire/{}/alert"
ALERT_HEARTBEAT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app):
    monitor = Monitor(Predictor())
    lock = Lock()
    app.state.monitor = monitor
    app.state.lock = lock
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            log.warning("MQTT connection rejected: %s", reason_code)
            return
        client.subscribe(TOPIC, qos=1)

    def on_subscribe(_client, _userdata, _mid, reason_codes, _properties):
        with lock:
            monitor.connected = bool(reason_codes) and all(not code.is_failure for code in reason_codes)

    def on_disconnect(_client, _userdata, _flags, _reason_code, _properties):
        with lock:
            monitor.connected = False
            for device in monitor.devices.values():
                device.reset_windows()
                device.last_seen = 0

    def on_message(_client, _userdata, message):
        if message.retain or len(message.payload) > 4096:
            return
        try:
            device_id = message.topic.split("/")[1]
            data = json.loads(message.payload)
            values = validate_telemetry(device_id, data)
        except (ValueError, IndexError, RecursionError):
            log.warning("Ignored invalid telemetry on %s", message.topic)
            return
        with lock:
            monitor.accept(device_id, data, values)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(os.environ.get("MQTT_HOST", "mosquitto"), 1883)
    client.loop_start()
    stop_alerts = threading.Event()

    def alert_loop():
        """Đẩy trạng thái model xuống từng thiết bị; gửi lại mỗi 5 giây làm heartbeat."""
        last_key = {}
        last_sent = {}
        while not stop_alerts.wait(1.0):
            with lock:
                snapshot = monitor.snapshot()
            now = time.monotonic()
            pending = []
            for device_id, data in snapshot["devices"].items():
                current = data["current"]
                key = (current["health"], current["risk_level"], current["reason"],
                       current["latched"], current["ir_confirmed"])
                if key == last_key.get(device_id) and now - last_sent.get(device_id, 0) < ALERT_HEARTBEAT_SECONDS:
                    continue
                last_key[device_id] = key
                last_sent[device_id] = now
                pending.append((device_id, json.dumps({
                    "schema_version": 1,
                    "device_id": device_id,
                    "health": current["health"],
                    "risk_level": current["risk_level"],
                    "p_fire": current["p_fire"],
                    "threshold": snapshot["threshold"],
                    "window": snapshot["window"],
                    "votes": current["votes"],
                    "ir_confirmed": current["ir_confirmed"],
                    "latched": current["latched"],
                    "reason": current["reason"],
                    "timestamp": current["timestamp"],
                }, ensure_ascii=False)))
            for device_id, body in pending:
                info = client.publish(ALERT_TOPIC.format(device_id), body, qos=1)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    log.debug("Alert publish skipped for %s: rc=%s", device_id, info.rc)

    alerts = threading.Thread(target=alert_loop, name="alerts", daemon=True)
    alerts.start()
    try:
        yield
    finally:
        stop_alerts.set()
        alerts.join(timeout=3)
        client.disconnect()
        client.loop_stop()


app = FastAPI(title="Giám sát cảnh báo cháy", lifespan=lifespan)


@app.get("/healthz")
def health(request: Request):
    with request.app.state.lock:
        if not request.app.state.monitor.connected:
            raise HTTPException(503, "Waiting for MQTT subscription")
    return {"status": "ok"}


@app.get("/api/status")
def status(request: Request):
    with request.app.state.lock:
        return request.app.state.monitor.snapshot()


@app.get("/api/events", response_class=EventSourceResponse)
async def events(request: Request):
    last = None
    while True:
        snapshot = await asyncio.to_thread(status, request)
        if snapshot != last:
            yield snapshot
            last = snapshot
        await asyncio.sleep(1)


BASELINE_SECONDS = 12
EXPOSURE_SECONDS = 12


def exposure(seconds, rise, decay):
    """Rise toward a peak during exposure, then gradually return to baseline."""
    peak = min(max(seconds, 0) / rise, 1)
    recovery = max(0, seconds - EXPOSURE_SECONDS)
    return peak * exp(-recovery / decay)


def simulated_readings(scenario, seq):
    elapsed = seq - BASELINE_SECONDS
    temperature = 23 + 0.15 * sin(seq / 6)
    humidity = 52 + 0.35 * sin(seq / 9)
    pm1 = 1 + 0.12 * sin(seq / 5)
    pm25 = 2 + 0.2 * sin(seq / 5)
    if scenario == "smoke":
        plume = exposure(elapsed + 1, rise=3, decay=8)
        warmth = exposure(elapsed - 3, rise=7, decay=12)
        return temperature + 4 * warmth, humidity - 6 * warmth, pm1 + 65 * plume, pm25 + 125 * plume

    warmth = exposure(elapsed + 1, rise=8, decay=12)
    return temperature + 15 * warmth, humidity - 24 * warmth, pm1, pm25


@app.get("/api/simulation/events", response_class=EventSourceResponse)
async def simulation_events(request: Request, scenario: Literal["smoke", "heat"] = "smoke"):
    state = request.app.state
    simulation = Monitor(state.monitor.predictor)
    simulation.connected = True

    def sample(seq):
        telemetry = dict(
            boot_id="simulation", seq=seq, uptime_ms=seq * 1000,
            ir_active=False, ir_active_duration_ms=0,
        )
        with state.lock:
            simulation.accept("sim-01", telemetry, simulated_readings(scenario, seq))
        snapshot = simulation.snapshot()
        snapshot["mqtt_connected"] = False
        elapsed = seq - BASELINE_SECONDS
        if elapsed < 0:
            snapshot["simulation_phase"] = f"Môi trường nền · sự kiện sau {-elapsed} giây"
        elif elapsed < EXPOSURE_SECONDS:
            snapshot["simulation_phase"] = (
                "Khói xuất hiện · bụi mịn tăng" if scenario == "smoke"
                else "Nguồn nhiệt xuất hiện · nhiệt độ tăng"
            )
        else:
            snapshot["simulation_phase"] = (
                "Khói tan dần · bụi mịn giảm" if scenario == "smoke"
                else "Ngừng gia nhiệt · nhiệt độ giảm dần"
            )
        return snapshot

    clock = asyncio.get_running_loop()
    for seq in count():
        next_sample = clock.time() + 1
        yield await asyncio.to_thread(sample, seq)
        await asyncio.sleep(max(0, next_sample - clock.time()))
