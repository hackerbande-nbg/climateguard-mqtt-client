import os
import json
import random
import shutil
from datetime import datetime
import string
from app.main import process_message
# Test data

test_data_dir = "data/test"

TEST_PAYLOAD_V1 = {
    "uplink_message": {
        "frm_payload": "AQb+GaYBf1wBkw==",  # Encoded payload
        "rx_metadata": [{"gateway_ids": {"gateway_id": "test-gateway"}}],
    },
    "end_device_ids": {"device_id": "test-sensor"},
}


def setup_test_environment():
    """Set up the test environment by clearing the data folder."""

    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)
    os.makedirs(test_data_dir, exist_ok=True)


def generate_random_string(target_dir="data", length=10):
    """Generate a random directory path with a fixed-length random string."""
    random_dir = ''.join(random.choices(
        string.ascii_letters + string.digits, k=length))
    return random_dir


def test_process_message():
    """Test the process_message function."""
    setup_test_environment()

    # Call the function with the test payload
    data_dir = os.path.join(test_data_dir, generate_random_string())
    result = process_message(TEST_PAYLOAD_V1, data_dir)
    assert result == 0, "Test failed: process_message returned an error."

    # Verify that the data was saved in the correct folder
    assert os.path.exists(data_dir), "Data directory does not exist."

    # Verify that a file was created
    files = os.listdir(data_dir)
    assert len(files) == 1, "No file was created in the data directory."


if __name__ == "__main__":
    test_process_message()
