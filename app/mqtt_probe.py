"""Bounded manual MQTT probe for the ClimateGuard weather-station broker."""

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple

import paho.mqtt.client as mqtt
from dotenv import load_dotenv


DEFAULT_HOST = "hsma-iot.de"
DEFAULT_PORT = 8883
DEFAULT_TOPIC = "climateguard/#"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_SENSORS = ("Lufft", "M1")
ENV_PREFIX = "CLIMATEGUARD_TEST_MH_"


@dataclass(frozen=True)
class ProbeConfig:
    host: str
    port: int
    username: str
    password: str
    topic: str
    timeout_seconds: int
    expected_sensors: Tuple[str, ...]


@dataclass
class ProbeResult:
    messages: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    connection_error: Optional[str] = None


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError("%s must be set in .env" % name)
    return value


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("%s must be an integer" % name) from error
    if parsed <= 0:
        raise ValueError("%s must be greater than zero" % name)
    return parsed


def load_config() -> ProbeConfig:
    """Load probe settings after ``load_dotenv`` has populated the environment."""
    sensors = tuple(
        sensor.strip()
        for sensor in os.getenv(ENV_PREFIX + "EXPECTED_SENSORS", ",".join(DEFAULT_SENSORS)).split(",")
        if sensor.strip()
    )
    if not sensors:
        raise ValueError(ENV_PREFIX + "EXPECTED_SENSORS must not be empty")

    return ProbeConfig(
        host=os.getenv(ENV_PREFIX + "HOST", DEFAULT_HOST),
        port=_positive_int(os.getenv(ENV_PREFIX + "PORT", str(DEFAULT_PORT)), ENV_PREFIX + "PORT"),
        username=_required_env(ENV_PREFIX + "USERNAME"),
        password=_required_env(ENV_PREFIX + "PASSWORD"),
        topic=os.getenv(ENV_PREFIX + "TOPIC", DEFAULT_TOPIC),
        timeout_seconds=_positive_int(
            os.getenv(ENV_PREFIX + "TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
            ENV_PREFIX + "TIMEOUT_SECONDS",
        ),
        expected_sensors=sensors,
    )


def sensor_for_topic(topic: str, expected_sensors: Iterable[str]) -> Optional[str]:
    """Return the expected sensor name when it appears as a topic segment."""
    segments = {segment.casefold() for segment in topic.split("/")}
    for sensor in expected_sensors:
        if sensor.casefold() in segments:
            return sensor
    return None


def format_payload(payload: bytes) -> str:
    """Pretty-print JSON payloads while retaining malformed or plain payloads."""
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=True)
    except json.JSONDecodeError:
        return text


def reason_code_is_failure(reason_code) -> bool:
    """Handle paho's version-2 ReasonCode objects and simple test doubles."""
    is_failure = getattr(reason_code, "is_failure", None)
    return is_failure if is_failure is not None else int(reason_code) >= 128


class MQTTProbe:
    def __init__(self, config: ProbeConfig, client=None):
        self.config = config
        self.result = ProbeResult()
        self._received_all = threading.Event()
        self.client = client or mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(config.username, config.password)
        self.client.tls_set()
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

    def _on_connect(self, client, _userdata, _connect_flags, reason_code, _properties):
        if reason_code_is_failure(reason_code):
            self.result.connection_error = "broker rejected connection (code %s)" % reason_code
            self._received_all.set()
            return
        subscription = client.subscribe(self.config.topic)
        if isinstance(subscription, tuple) and subscription[0] != mqtt.MQTT_ERR_SUCCESS:
            self.result.connection_error = "could not subscribe to %s (code %s)" % (self.config.topic, subscription[0])
            self._received_all.set()

    def _on_subscribe(self, _client, _userdata, _mid, reason_code_list, _properties):
        rejected = [reason_code for reason_code in reason_code_list if reason_code_is_failure(reason_code)]
        if rejected:
            self.result.connection_error = "broker rejected subscription to %s (code %s)" % (
                self.config.topic,
                rejected[0],
            )
            self._received_all.set()

    def _on_message(self, _client, _userdata, message):
        sensor = sensor_for_topic(message.topic, self.config.expected_sensors)
        if sensor:
            self.result.messages[sensor] = (message.topic, format_payload(message.payload))
            if all(expected in self.result.messages for expected in self.config.expected_sensors):
                self._received_all.set()

    def run(self) -> ProbeResult:
        try:
            self.client.connect(self.config.host, self.config.port, keepalive=30)
            self.client.loop_start()
            self._received_all.wait(self.config.timeout_seconds)
        except Exception as error:
            self.result.connection_error = str(error)
        finally:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        return self.result


def print_result(result: ProbeResult, expected_sensors: Iterable[str]) -> int:
    if result.connection_error:
        print("Connection failed: %s" % result.connection_error, file=sys.stderr)
        return 2

    for sensor in expected_sensors:
        if sensor in result.messages:
            topic, payload = result.messages[sensor]
            print("[%s] %s" % (sensor, topic))
            print(payload)

    missing = [sensor for sensor in expected_sensors if sensor not in result.messages]
    if missing:
        print("Timed out before receiving: %s" % ", ".join(missing), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    load_dotenv()
    try:
        config = load_config()
    except ValueError as error:
        print("Configuration error: %s" % error, file=sys.stderr)
        return 2
    return print_result(MQTTProbe(config).run(), config.expected_sensors)


if __name__ == "__main__":
    sys.exit(main())
