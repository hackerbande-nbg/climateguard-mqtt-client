"""ClimateGuard MQTT ingestion for TTN and Mannheim weather stations.

Incoming messages are normalized before delivery.  Delivery is deliberately
disabled unless QUANTUM_WRITE_ENABLED=true is set by an operator.
"""

import base64
import json
import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv
from loguru import logger


MANNHEIM_TOPIC_DEVICES = {
    "climateguard/M1": "M1",
    "climateguard/Lufft": "Lufft",
}


@dataclass(frozen=True)
class SourceConfig:
    name: str
    broker: str
    port: int
    username: str
    password: str
    topic: str


@dataclass(frozen=True)
class AppConfig:
    sources: tuple[SourceConfig, ...]
    endpoint: str
    quantum_api_key: Optional[str]
    write_enabled: bool


@dataclass(frozen=True)
class Metric:
    device_name: str
    timestamp_device: int
    temperature: float
    humidity: float
    air_pressure: Optional[float] = None
    battery_voltage: Optional[float] = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_api_payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "timestamp_device": self.timestamp_device,
            "timestamp_server": int(datetime.now(timezone.utc).timestamp()),
            "device_name": self.device_name,
            "temperature": self.temperature,
            "humidity": self.humidity,
        }
        if self.air_pressure is not None:
            data["air_pressure"] = self.air_pressure
        if self.battery_voltage is not None:
            data["battery_voltage"] = self.battery_voltage
        data.update(self.extra)
        return data


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _port(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not 0 < value < 65536:
        raise ValueError(f"{name} must be a valid TCP port")
    return value


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def load_config() -> AppConfig:
    """Read both source configurations without doing network or file writes."""
    environment = os.getenv("ENV", "prod").strip().casefold()
    endpoint = os.getenv("QUANTUM_API_ENDPOINT")
    if not endpoint:
        endpoints_path = Path(__file__).parent.parent / "config" / "envs.json"
        with endpoints_path.open(encoding="utf-8") as source:
            endpoints = json.load(source).get(environment, [])
        if len(endpoints) != 1:
            raise ValueError(f"exactly one endpoint must be configured for ENV={environment!r}")
        endpoint = endpoints[0]

    return AppConfig(
        sources=(
            SourceConfig("ttn", os.getenv("MQTT_BROKER", "eu1.cloud.thethings.network"),
                         _port("MQTT_PORT", 8883), os.getenv("MQTT_USERNAME", "climateguard@ttn"),
                         _required("TTN_API_KEY"), os.getenv("MQTT_TOPIC", "v3/climateguard@ttn/devices/+/up")),
            SourceConfig("mannheim", os.getenv("CLIMATEGUARD_TEST_MH_HOST", "hsma-iot.de"),
                         _port("CLIMATEGUARD_TEST_MH_PORT", 8883),
                         _required("CLIMATEGUARD_TEST_MH_USERNAME"), _required("CLIMATEGUARD_TEST_MH_PASSWORD"),
                         os.getenv("CLIMATEGUARD_TEST_MH_TOPIC", "climateguard/#")),
        ),
        endpoint=endpoint,
        quantum_api_key=os.getenv("QUANTUM_API_KEY"),
        write_enabled=_truthy(os.getenv("QUANTUM_WRITE_ENABLED", "false")),
    )


def decode_payload(payload: str) -> tuple[float, float, float, float]:
    """Decode the established TTN v1 binary payload layout."""
    decoded = base64.b64decode(payload, validate=True)
    layout_path = Path(__file__).parent.parent / "config" / "payload_layout.json"
    with layout_path.open(encoding="utf-8") as source:
        layout = json.load(source)
    if not decoded:
        raise ValueError("empty TTN frame payload")
    if str(decoded[0]) != layout.get("version", "1"):
        logger.warning("TTN payload version {} differs from configured version {}", decoded[0], layout.get("version"))

    def extract(indices: list[int], signed: bool = False) -> float:
        try:
            return int.from_bytes(bytes(decoded[index] for index in indices), "big", signed=signed) / 100
        except IndexError as error:
            raise ValueError("TTN frame payload is shorter than configured layout") from error

    return (extract(layout["temperature"], signed=True), extract(layout["humidity"]),
            extract(layout["pressure"]), extract(layout["voltage"]))


def _parse_utc_timestamp(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("Mannheim payload requires a UTC string field 'time'")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Mannheim payload field 'time' is not ISO-8601") from error
    if timestamp.tzinfo is None:
        raise ValueError("Mannheim payload field 'time' must include UTC offset")
    return int(timestamp.timestamp())


def normalize_mannheim(payload: Mapping[str, Any], topic: str) -> Metric:
    """Normalize exactly the two known Mannheim station topics."""
    try:
        device_name = MANNHEIM_TOPIC_DEVICES[topic]
    except KeyError as error:
        raise ValueError(f"unrecognized Mannheim topic: {topic}") from error
    try:
        temperature, humidity = float(payload["Temperature0"]), float(payload["Humidity0"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Mannheim payload requires numeric Temperature0 and Humidity0") from error
    return Metric(device_name=device_name, timestamp_device=_parse_utc_timestamp(payload.get("time")),
                  temperature=temperature, humidity=humidity)


def normalize_ttn(payload: Mapping[str, Any]) -> Metric:
    uplink = payload.get("uplink_message")
    device_ids = payload.get("end_device_ids")
    if not isinstance(uplink, Mapping) or not isinstance(device_ids, Mapping):
        raise ValueError("TTN message is missing uplink_message or end_device_ids")
    frame = uplink.get("frm_payload")
    device_name = device_ids.get("device_id")
    if not isinstance(frame, str) or not frame or not isinstance(device_name, str) or not device_name:
        raise ValueError("TTN message requires frm_payload and device_id")
    temperature, humidity, pressure, battery_voltage = decode_payload(frame)
    extra: dict[str, Any] = {}
    for name in ("confirmed", "f_cnt"):
        if name in uplink:
            extra[name] = uplink[name]
    settings = uplink.get("settings")
    if isinstance(settings, Mapping) and "frequency" in settings:
        extra["frequency"] = settings["frequency"]
    return Metric(device_name=device_name, timestamp_device=int(datetime.now(timezone.utc).timestamp()),
                  temperature=temperature, humidity=humidity, air_pressure=pressure,
                  battery_voltage=battery_voltage, extra=extra)


class QuantumDelivery:
    """The only outbound path. Disabled unless explicitly opted in."""
    def __init__(self, config: AppConfig, post: Callable[..., Any] = requests.post):
        self.config = config
        self._post = post

    def deliver(self, metric: Metric) -> bool:
        if not self.config.write_enabled:
            logger.info("Quantum delivery disabled; metric: {}", metric.as_api_payload())
            return False
        if not self.config.quantum_api_key:
            raise ValueError("QUANTUM_API_KEY must be set when QUANTUM_WRITE_ENABLED=true")
        response = self._post(self.config.endpoint, json=metric.as_api_payload(), headers={
            "Content-Type": "application/json", "X-API-Key": self.config.quantum_api_key}, timeout=10)
        response.raise_for_status()
        return True


class MQTTSourceClient:
    def __init__(self, source: SourceConfig, handle: Callable[[str, bytes], None], client: Any = None):
        self.source, self.handle = source, handle
        self.client = client or mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(source.username, source.password)
        self.client.tls_set()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        failed = getattr(reason_code, "is_failure", None)
        if failed is None:
            failed = int(reason_code) >= 128
        if failed:
            logger.error("{} broker rejected connection: {}", self.source.name, reason_code)
            return
        result, _mid = client.subscribe(self.source.topic)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.error("{} subscription failed: {}", self.source.name, result)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        self.handle(message.topic, message.payload)

    def start(self) -> None:
        self.client.connect(self.source.broker, self.source.port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def start_clients(clients: list[MQTTSourceClient]) -> list[MQTTSourceClient]:
    """Start every source independently so one unavailable broker is isolated."""
    started: list[MQTTSourceClient] = []
    for client in clients:
        try:
            client.start()
        except Exception as error:
            logger.error("Failed to start {} MQTT source: {}", client.source.name, error)
        else:
            started.append(client)
    return started


def build_message_handler(source_name: str, delivery: QuantumDelivery) -> Callable[[str, bytes], None]:
    normalizer = normalize_ttn if source_name == "ttn" else normalize_mannheim

    def handle(topic: str, raw_payload: bytes) -> None:
        try:
            parsed = json.loads(raw_payload.decode("utf-8"))
            metric = normalizer(parsed) if source_name == "ttn" else normalizer(parsed, topic)
            delivery.deliver(metric)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            logger.error("Discarded invalid {} message on {}: {}", source_name, topic, error)
        except requests.RequestException as error:
            logger.error("Quantum delivery failed for {}: {}", source_name, error)
    return handle


def main() -> None:
    load_dotenv()
    try:
        config = load_config()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logger.error("Configuration error: {}", error)
        return
    if config.write_enabled:
        logger.warning("Quantum writes are enabled: each received metric causes one POST")
    else:
        logger.warning("Quantum writes are disabled (QUANTUM_WRITE_ENABLED is not true)")
    delivery = QuantumDelivery(config)
    clients = [MQTTSourceClient(source, build_message_handler(source.name, delivery)) for source in config.sources]
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    try:
        clients = start_clients(clients)
        if not clients:
            logger.error("No MQTT sources could be started")
            return
        stop.wait()
    finally:
        for client in clients:
            try:
                client.stop()
            except Exception as error:
                logger.warning("Failed to stop {} MQTT source: {}", client.source.name, error)


if __name__ == "__main__":
    main()
