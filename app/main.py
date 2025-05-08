import json
import base64
import os
import random
import string
from loguru import logger
from dotenv import load_dotenv
from datetime import datetime
import requests
import paho.mqtt.client as mqtt

# Load environment variables from .env file
load_dotenv()

# MQTT connection parameters
MQTT_BROKER = os.getenv("MQTT_BROKER", "eu1.cloud.thethings.network")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "v3/climateguard@ttn/devices/+/up")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "climateguard@ttn")
API_ENDPOINT = os.getenv(
    "API_ENDPOINT", "http://localhost:8001/sensormetrics")


class MQTTClient:
    def __init__(self, broker, port, username, password, topic, on_message_callback):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.topic = topic
        self.on_message_callback = on_message_callback
        self.client = mqtt.Client()

    def connect(self):
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message_callback
        self.client.tls_set()  # Use default certificate settings
        self.client.connect(self.broker, self.port, 60)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to the MQTT Broker")
            client.subscribe(self.topic)
        else:
            logger.error(f"Connection failed with error code {rc}")

    def loop_forever(self):
        self.client.loop_forever()


def decode_payload(payload):
    decoded = base64.b64decode(payload)
    try:
        temperature = int.from_bytes(
            decoded[0:2], byteorder="big", signed=True) / 100
        humidity = int.from_bytes(decoded[2:4], byteorder="big") / 100
        pressure = int.from_bytes(decoded[4:7], byteorder="big") / 100
    except Exception as e:
        logger.error(f"Error decoding payload: {e}")
        raise e
    return temperature, humidity, pressure


def persist_raw_data(device_id, data, target_dir="data"):
    try:
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(target_dir, f"{device_id}_{timestamp}.json")
        with open(filename, "w") as file:
            json.dump(data, file, indent=2)

        logger.info(f"Data saved to {filename}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")


def send_data_to_api(device_id, temperature, humidity):
    data = {
        "timestamp_device": int(datetime.now().timestamp()),
        "timestamp_server": int(datetime.now().timestamp()),
        "device_id": device_id,
        "temperature": temperature,
        "humidity": humidity,
    }
    response = requests.post(API_ENDPOINT, json=data, headers={
        "Content-Type": "application/json"})
    if response.status_code == 200:
        logger.info(f"Data successfully sent to API: {response.json()}")
    else:
        logger.error(
            f"Failed to send data to API: {response.status_code} - {response.text}")


def process_message(payload, data_dir="data"):
    try:
        uplink_message = payload.get("uplink_message", {})
        frm_payload = uplink_message.get("frm_payload", "")
        rx_metadata = uplink_message.get("rx_metadata", [])
        end_sensor_ids = payload.get("end_device_ids", {})
        device_id = end_sensor_ids.get("device_id", "unknown_device")

        if not frm_payload:
            logger.error("Error: 'frm_payload' is missing!")
            return

        persist_raw_data(device_id, payload, data_dir)
        temperature, humidity, pressure = decode_payload(frm_payload)
        send_data_to_api(device_id, temperature, humidity)
        logger.info("Data successfully processed and saved.")
        return 0
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return 1


def on_message(client, userdata, msg):
    try:
        logger.info(f"Received message: {msg.payload.decode()}")
        payload = json.loads(msg.payload.decode())
        process_message(payload)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding the message: {e}")
    except Exception as e:
        logger.error(f"General error: {e}")


def main():
    ttn_api_key = os.getenv("TTN_API_KEY")
    if not ttn_api_key:
        logger.error("Error: TTN_API_KEY is not set!")
        return
    mqtt_client = MQTTClient(
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        username=MQTT_USERNAME,
        password=ttn_api_key,
        topic=MQTT_TOPIC,
        on_message_callback=on_message,
    )
    mqtt_client.connect()
    mqtt_client.loop_forever()


if __name__ == "__main__":
    main()
