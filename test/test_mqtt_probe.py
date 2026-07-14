import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mqtt_probe
from app.mqtt_probe import MQTTProbe, ProbeConfig, ProbeResult, format_payload, load_config, print_result, sensor_for_topic


class FakeClient:
    def __init__(self):
        self.subscriptions = []

    def username_pw_set(self, _username, _password):
        pass

    def tls_set(self):
        pass

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        return (mqtt_probe.mqtt.MQTT_ERR_SUCCESS, 1)


def probe_config():
    return ProbeConfig(
        host="broker.example",
        port=8883,
        username="user",
        password="password",
        topic="climateguard/#",
        timeout_seconds=1,
        expected_sensors=("Lufft", "M1"),
    )


def test_sensor_for_topic_matches_case_insensitive_segment():
    assert sensor_for_topic("climateguard/lufft/telemetry", ("Lufft", "M1")) == "Lufft"
    assert sensor_for_topic("climateguard/m10/telemetry", ("Lufft", "M1")) is None


def test_format_payload_pretty_prints_json_and_preserves_text():
    assert format_payload(b'{"humidity": 43, "temperature": 21}') == '{\n  "humidity": 43,\n  "temperature": 21\n}'
    assert format_payload(b"not-json") == "not-json"


def test_load_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("CLIMATEGUARD_TEST_MH_USERNAME", raising=False)
    monkeypatch.delenv("CLIMATEGUARD_TEST_MH_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="CLIMATEGUARD_TEST_MH_USERNAME"):
        load_config()


def test_print_result_returns_timeout_when_sensor_is_missing(capsys):
    result = ProbeResult(messages={"Lufft": ("climateguard/Lufft", '{"temperature": 20}')})
    assert print_result(result, ("Lufft", "M1")) == 1
    captured = capsys.readouterr()
    assert "[Lufft] climateguard/Lufft" in captured.out
    assert "Timed out before receiving: M1" in captured.err


def test_probe_uses_version_2_callback_api(monkeypatch):
    captured = {}

    def client_factory(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(mqtt_probe.mqtt, "Client", client_factory)
    MQTTProbe(probe_config())
    assert captured["callback_api_version"] == mqtt_probe.mqtt.CallbackAPIVersion.VERSION2


def test_denied_suback_is_reported_as_connection_error(capsys):
    client = FakeClient()
    probe = MQTTProbe(probe_config(), client=client)
    probe._on_connect(client, None, {}, 0, None)
    probe._on_subscribe(client, None, 1, [128], None)

    assert client.subscriptions == ["climateguard/#"]
    assert probe._received_all.is_set()
    assert print_result(probe.result, probe.config.expected_sensors) == 2
    assert "broker rejected subscription" in capsys.readouterr().err
