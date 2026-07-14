import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import AppConfig, Metric, QuantumDelivery, SourceConfig, build_message_handler, load_config, normalize_mannheim, start_clients


def app_config(write_enabled=False):
    return AppConfig((SourceConfig("ttn", "ttn", 8883, "u", "p", "topic"), SourceConfig("mannheim", "mh", 8883, "u", "p", "climateguard/#")),
                     "https://api.quantum.hackerban.de/v2/metrics", "key", write_enabled)


def test_mannheim_normalization_uses_topic_values_and_utc_time():
    metric = normalize_mannheim({"time": "2026-07-14T10:00:00Z", "Temperature0": 27.8, "Humidity0": 43}, "climateguard/M1")
    assert (metric.device_name, metric.timestamp_device, metric.temperature, metric.humidity, metric.air_pressure) == ("M1", 1784023200, 27.8, 43.0, None)


def test_mannheim_normalization_rejects_unrecognized_wildcard_topic():
    with pytest.raises(ValueError, match="unrecognized Mannheim topic"):
        normalize_mannheim({"time": "2026-07-14T10:00:00Z", "Temperature0": 27.8, "Humidity0": 43}, "climateguard/M2")


def test_delivery_gate_never_calls_network_when_disabled():
    calls = []
    delivery = QuantumDelivery(app_config(), post=lambda *args, **kwargs: calls.append((args, kwargs)))
    assert delivery.deliver(Metric("M1", 1, 2.0, 3.0)) is False
    assert calls == []


def test_delivery_posts_a_single_normalized_metric_when_enabled():
    calls = []
    class Response:
        def raise_for_status(self): pass
    delivery = QuantumDelivery(app_config(write_enabled=True), post=lambda *args, **kwargs: calls.append((args, kwargs)) or Response())
    assert delivery.deliver(Metric("M1", 1, 2.0, 3.0)) is True
    assert len(calls) == 1
    assert calls[0][0] == ("https://api.quantum.hackerban.de/v2/metrics",)


def test_sources_are_independent_when_one_payload_is_invalid():
    delivered = []
    handler = build_message_handler("mannheim", type("Delivery", (), {"deliver": delivered.append})())
    handler("climateguard/M1", b"not-json")
    handler("climateguard/Lufft", json.dumps({"time": "2026-07-14T10:00:00Z", "Temperature0": 27.6, "Humidity0": 41.7}).encode())
    assert len(delivered) == 1
    assert delivered[0].device_name == "Lufft"


def test_source_startup_is_independent_when_one_broker_fails():
    class Client:
        def __init__(self, name, fails=False):
            self.source = SourceConfig(name, "broker", 8883, "user", "password", "topic")
            self.fails = fails
            self.started = False
        def start(self):
            if self.fails:
                raise OSError("unavailable")
            self.started = True

    unavailable, healthy = Client("ttn", fails=True), Client("mannheim")
    assert start_clients([unavailable, healthy]) == [healthy]
    assert healthy.started is True


def test_load_config_reads_both_source_sets(monkeypatch):
    settings = {"TTN_API_KEY": "ttn", "CLIMATEGUARD_TEST_MH_USERNAME": "mh", "CLIMATEGUARD_TEST_MH_PASSWORD": "secret", "QUANTUM_API_ENDPOINT": "https://example.test/metrics"}
    for name, value in settings.items(): monkeypatch.setenv(name, value)
    config = load_config()
    assert [source.name for source in config.sources] == ["ttn", "mannheim"]
    assert config.write_enabled is False


def test_compose_passes_both_sources_and_keeps_writes_disabled_by_default():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    assert "CLIMATEGUARD_TEST_MH_USERNAME" in compose
    assert "CLIMATEGUARD_TEST_MH_PASSWORD" in compose
    assert "QUANTUM_WRITE_ENABLED: ${QUANTUM_WRITE_ENABLED:-false}" in compose


def test_dockerignore_excludes_local_env_and_telemetry_data():
    dockerignore = (Path(__file__).resolve().parents[1] / ".dockerignore").read_text().splitlines()
    assert ".env" in dockerignore
    assert "data/" in dockerignore
