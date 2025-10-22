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
logger.info("Environment variables loaded from .env file")

# MQTT connection parameters
MQTT_BROKER = os.getenv("MQTT_BROKER", "eu1.cloud.thethings.network")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "v3/climateguard@ttn/devices/+/up")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "climateguard@ttn")

# Load API endpoints from envs.json based on environment


def load_api_endpoints():
    env = os.getenv("ENV")
    if not env:
        logger.error("ENV environment variable is not set!")
        raise ValueError("ENV environment variable is required")

    config_path = os.path.join(os.path.dirname(
        __file__), "..", "config", "envs.json")

    try:
        with open(config_path, 'r') as f:
            envs_config = json.load(f)

        endpoints = envs_config.get(env, [])
        if not endpoints:
            logger.error(
                f"No endpoints found for environment '{env}' in config file")
            raise ValueError(
                f"No endpoints configured for environment '{env}'")

        logger.info(
            f"Loaded {len(endpoints)} API endpoints for environment '{env}'")
        return endpoints
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing config file: {e}")
        raise ValueError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        logger.error(f"Unexpected error loading config: {e}")
        raise


try:
    API_ENDPOINTS = load_api_endpoints()
except Exception as e:
    logger.error(f"Failed to load API endpoints: {e}")
    logger.error(
        "Application cannot start without proper endpoint configuration")
    exit(1)

# Log configuration
logger.info(
    f"MQTT Configuration - Broker: {MQTT_BROKER}, Port: {MQTT_PORT}, Topic: {MQTT_TOPIC}, Username: {MQTT_USERNAME}")
logger.info(f"API Endpoints: {API_ENDPOINTS}")
logger.info(f"Environment: {os.getenv('ENV', 'not set')}")


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
        logger.info(
            f"Attempting to connect to MQTT broker {self.broker}:{self.port}")
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message_callback
        self.client.tls_set()  # Use default certificate settings
        logger.info("TLS enabled for MQTT connection")
        try:
            self.client.connect(self.broker, self.port, 60)
            logger.info("MQTT connection initiated")
        except Exception as e:
            logger.error(f"Failed to initiate MQTT connection: {e}")
            raise e

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to the MQTT Broker")
            logger.info(f"Subscribing to topic: {self.topic}")
            client.subscribe(self.topic)
            logger.info("Successfully subscribed to MQTT topic")
        else:
            logger.error(f"Connection failed with error code {rc}")

    def loop_forever(self):
        logger.info("Starting MQTT client loop...")
        self.client.loop_forever()


def decode_payload(payload):
    logger.debug(f"Decoding payload: {payload}")
    decoded = base64.b64decode(payload)
    logger.debug(f"Base64 decoded bytes: {decoded.hex()}")
    try:
        version = decoded[0]
        logger.info(f"Payload version: {version}")
        # Load payload layout config
        config_path = os.path.join(os.path.dirname(
            __file__), "..", "config", "payload_layout.json")
        with open(config_path, 'r') as f:
            layout = json.load(f)
        if str(version) != layout.get("version", "1"):
            logger.warning(
                f"Payload version {version} does not match config version {layout.get('version')}")
        # Helper to extract bytes and convert

        def extract_value(indices, signed=False):
            b = bytes([decoded[i] for i in indices])
            return int.from_bytes(b, byteorder="big", signed=signed) / 100
        temperature = extract_value(layout["temperature"], signed=True)
        humidity = extract_value(layout["humidity"])
        pressure = extract_value(layout["pressure"])
        battery_voltage = extract_value(layout["voltage"])
        logger.info(
            f"Decoded sensor data - Temperature: {temperature}°C, Humidity: {humidity}%, Pressure: {pressure} hPa, Battery Voltage: {battery_voltage}V")
    except Exception as e:
        logger.error(f"Error decoding payload: {e}")
        raise e
    return temperature, humidity, pressure, battery_voltage


def persist_raw_data(device_id, data, target_dir="data"):
    try:
        logger.debug(
            f"Persisting raw data for device {device_id} to directory {target_dir}")
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(target_dir, f"{device_id}_{timestamp}.json")

        with open(filename, "w") as file:
            json.dump(data, file, indent=2)

        logger.info(f"Raw data saved to {filename}")
    except Exception as e:
        logger.error(f"Error saving data to file: {e}")
        raise e


def send_data_to_api(device_id, temperature, humidity, air_pressure=None,
                     battery_voltage=None, confirmed=None, consumed_airtime=None,
                     f_cnt=None, frequency=None, rx_metadata=None, settings=None):
    logger.info(
        f"Sending data to API for device {device_id}: Temperature={temperature}°C, Humidity={humidity}%, Pressure={air_pressure} hPa, Battery Voltage={battery_voltage}V")

    # Get API key from environment variables
    api_key = os.getenv("QUANTUM_API_KEY")
    if not api_key:
        logger.error("Error: QUANTUM_API_KEY is not set!")
        return

    logger.debug(
        f"Using API key: {api_key[:8]}..." if api_key else "No API key found")

    # Build base metric data
    data = {
        "timestamp_device": int(datetime.now().timestamp()),
        "timestamp_server": int(datetime.now().timestamp()),
        "device_name": device_id,
        "temperature": temperature,
        "humidity": humidity,
    }

    # Add optional fields
    if air_pressure is not None:
        data["air_pressure"] = air_pressure
    if battery_voltage is not None:
        data["battery_voltage"] = battery_voltage
    if confirmed is not None:
        data["confirmed"] = confirmed
    if consumed_airtime is not None:
        data["consumed_airtime"] = consumed_airtime
    if f_cnt is not None:
        data["f_cnt"] = f_cnt
    if frequency is not None:
        data["frequency"] = frequency

    # Build sensor_messages from rx_metadata
    if rx_metadata and len(rx_metadata) > 0:
        sensor_messages = []

        # Extract LoRa settings from uplink settings (shared across all gateways)
        lora_bandwidth = None
        lora_spreading_factor = None
        lora_coding_rate = None

        if settings:
            data_rate = settings.get("data_rate", {})
            lora = data_rate.get("lora", {})
            lora_bandwidth = lora.get("bandwidth")
            lora_spreading_factor = lora.get("spreading_factor")
            lora_coding_rate = lora.get("coding_rate")
            logger.debug(
                f"LoRa settings - bandwidth={lora_bandwidth}, SF={lora_spreading_factor}, coding_rate={lora_coding_rate}")

        for metadata in rx_metadata:
            sensor_message = {}

            # Extract gateway ID
            gateway_ids = metadata.get("gateway_ids", {})
            gateway_id = gateway_ids.get("gateway_id")
            if gateway_id:
                sensor_message["gateway_id"] = gateway_id

            # Extract signal quality metrics
            if "rssi" in metadata:
                sensor_message["rssi"] = metadata["rssi"]
            if "snr" in metadata:
                sensor_message["snr"] = metadata["snr"]
            if "channel_rssi" in metadata:
                sensor_message["channel_rssi"] = metadata["channel_rssi"]

            # Add LoRa settings (same for all gateways in this message)
            if lora_bandwidth is not None:
                sensor_message["lora_bandwidth"] = lora_bandwidth
            if lora_spreading_factor is not None:
                sensor_message["lora_spreading_factor"] = lora_spreading_factor
            if lora_coding_rate is not None:
                sensor_message["lora_coding_rate"] = lora_coding_rate

            sensor_messages.append(sensor_message)

        if sensor_messages:
            data["sensor_messages"] = sensor_messages
            logger.debug(
                f"Added {len(sensor_messages)} sensor messages from rx_metadata")

    logger.debug(f"API request payload: {data}")

    # Add API key to headers as required by the API
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }

    # Send to all configured endpoints
    success_count = 0
    total_endpoints = len(API_ENDPOINTS)

    for i, endpoint in enumerate(API_ENDPOINTS):
        logger.debug(
            f"Making POST request to endpoint {i+1}/{total_endpoints}: {endpoint}")

        try:
            response = requests.post(
                endpoint, json=data, headers=headers, timeout=10)
            logger.info(
                f"API response from {endpoint}: status {response.status_code}")

            if response.status_code == 200:
                logger.info(
                    f"Data successfully sent to {endpoint}: {response.json()}")
                success_count += 1
            else:
                logger.error(
                    f"Failed to send data to {endpoint}: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Network error while sending data to {endpoint}: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error while sending data to {endpoint}: {e}")

    logger.info(
        f"Successfully sent data to {success_count}/{total_endpoints} endpoints")
    return success_count


def process_message(payload, data_dir="data"):
    logger.info("Processing incoming MQTT message")
    try:
        uplink_message = payload.get("uplink_message", {})
        frm_payload = uplink_message.get("frm_payload", "")
        rx_metadata = uplink_message.get("rx_metadata", [])
        end_sensor_ids = payload.get("end_device_ids", {})
        device_id = end_sensor_ids.get("device_id", "unknown_device")

        logger.info(f"Processing message from device: {device_id}")
        logger.debug(f"Received {len(rx_metadata)} metadata entries")

        if not frm_payload:
            logger.error("Error: 'frm_payload' is missing from the message!")
            return

        logger.debug(f"Frame payload received: {frm_payload}")

        # Persist raw data first
        persist_raw_data(device_id, payload, data_dir)

        # Decode the sensor data - now returns 4 values including battery_voltage
        temperature, humidity, pressure, battery_voltage = decode_payload(
            frm_payload)

        # Extract LoRaWAN metadata
        confirmed = uplink_message.get("confirmed")
        consumed_airtime = uplink_message.get("consumed_airtime")
        f_cnt = uplink_message.get("f_cnt")

        # Extract frequency from settings
        settings = uplink_message.get("settings", {})
        frequency = settings.get("frequency")

        # Convert consumed_airtime from string (e.g., "1.482752s") to float
        if consumed_airtime and isinstance(consumed_airtime, str):
            try:
                # Remove the 's' suffix and convert to float
                consumed_airtime = float(consumed_airtime.rstrip('s'))
                logger.debug(f"Parsed consumed_airtime: {consumed_airtime}s")
            except ValueError as e:
                logger.warning(
                    f"Failed to parse consumed_airtime '{consumed_airtime}': {e}")
                consumed_airtime = None

        logger.debug(
            f"Additional metadata - confirmed={confirmed}, f_cnt={f_cnt}, frequency={frequency}, consumed_airtime={consumed_airtime}")

        # Send processed data to API with all metadata
        send_data_to_api(
            device_id=device_id,
            temperature=temperature,
            humidity=humidity,
            air_pressure=pressure,
            battery_voltage=battery_voltage,
            confirmed=confirmed,
            consumed_airtime=consumed_airtime,
            f_cnt=f_cnt,
            frequency=frequency,
            rx_metadata=rx_metadata,
            settings=settings
        )

        logger.info(
            f"Message from device {device_id} successfully processed and saved.")
        return 0
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        logger.debug(f"Full message payload: {payload}")
        return 1


def on_message(client, userdata, msg):
    logger.info(f"Received MQTT message on topic: {msg.topic}")
    logger.debug(f"Message QoS: {msg.qos}, Retain: {msg.retain}")

    try:
        raw_message = msg.payload.decode()
        logger.debug(f"Raw message length: {len(raw_message)} characters")
        logger.debug(f"Raw message: {raw_message}")

        payload = json.loads(raw_message)
        logger.info("Successfully parsed JSON message")

        result = process_message(payload)
        if result == 0:
            logger.info("Message processing completed successfully")
        else:
            logger.warning("Message processing completed with errors")

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON message: {e}")
        logger.debug(f"Invalid JSON content: {msg.payload.decode()}")
    except Exception as e:
        logger.error(f"Unexpected error in message handler: {e}")
        logger.debug(f"Full error details: {type(e).__name__}: {str(e)}")


def main():
    logger.info("Starting ClimateGuard MQTT Client")
    logger.info("="*50)

    # Check for required environment variables
    ttn_api_key = os.getenv("TTN_API_KEY")
    QUANTUM_API_KEY = os.getenv("QUANTUM_API_KEY")

    if not ttn_api_key:
        logger.error("Error: TTN_API_KEY is not set!")
        return

    if not QUANTUM_API_KEY:
        logger.error("Error: QUANTUM_API_KEY is not set!")
        return

    logger.info("All required API keys are configured")
    logger.debug(f"TTN API key: {ttn_api_key[:8]}...")
    logger.debug(f"QUAN API key: {QUANTUM_API_KEY[:8]}...")

    # Initialize MQTT client
    logger.info("Initializing MQTT client...")
    mqtt_client = MQTTClient(
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        username=MQTT_USERNAME,
        password=ttn_api_key,
        topic=MQTT_TOPIC,
        on_message_callback=on_message,
    )

    # Connect and start listening
    try:
        mqtt_client.connect()
        logger.info("MQTT client connected successfully")
        logger.info("Starting message processing loop...")
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
        logger.debug(f"Full error details: {type(e).__name__}: {str(e)}")
    finally:
        logger.info("ClimateGuard MQTT Client shutting down")
        logger.info("="*50)


if __name__ == "__main__":
    main()
