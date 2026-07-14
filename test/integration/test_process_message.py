import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.main import normalize_ttn


TEST_PAYLOAD_V1 = {
    "uplink_message": {"frm_payload": "AQb+GaYBf1wBkw==", "rx_metadata": [{"gateway_ids": {"gateway_id": "test-gateway"}}]},
    "end_device_ids": {"device_id": "test-sensor"},
}


def test_normalize_ttn_payload_v1():
    metric = normalize_ttn(TEST_PAYLOAD_V1)
    assert metric.device_name == "test-sensor"
    assert (metric.temperature, metric.humidity, metric.air_pressure, metric.battery_voltage) == (17.9, 65.66, 981.4, 4.03)
