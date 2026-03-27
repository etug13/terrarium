import os
import time
import serial
import logging
from dotenv import load_dotenv
import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load env
load_dotenv()

# Configuration
INFLUX_TOKEN = os.getenv("INFLUX_DB_TOKEN")
INFLUX_ORG = "Terrarium"
INFLUX_URL = "http://terrarium_influx:8086"
INFLUX_BUCKET = "terrarium_logs"

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
BAUD_RATE = int(os.getenv("BAUD_RATE", 9600))

TARGET_TEMP = 30.0
TARGET_HUM = 75.0
HYST_TEMP = 0.5
HYST_HUM = 2.0

class TerrariumController:
    def __init__(self, target_temp, target_hum, hyst_temp, hyst_hum):
        self.target_temp = target_temp
        self.target_hum = target_hum
        self.hyst_temp = hyst_temp
        self.hyst_hum = hyst_hum

        self.heater_state = 0
        self.mist_state = 0

    def process(self, current_temp, current_hum):
        # Fan Control (Cooling)
        fan_speed = 0
        if current_temp > self.target_temp:
            diff = current_temp - self.target_temp
            # Scale 0-5 deg diff to 50-100% speed
            # If diff is tiny (e.g. 0.01), we still want to start at 50%?
            # User said "gradually in range 50-100%"
            # Formula: 50 + (diff / 5.0) * 50
            speed = 50 + (diff / 5.0) * 50
            fan_speed = int(min(max(speed, 50), 100))

        # Heater Control (Heating)
        # Turn ON if we drop below target - hysteresis
        if current_temp < (self.target_temp - self.hyst_temp):
            self.heater_state = 1
        # Turn OFF if we reach target
        elif current_temp >= self.target_temp:
            self.heater_state = 0
        # Else: keep previous state

        # Mist Control (Humidifying)
        # Turn ON if we drop below target - hysteresis
        if current_hum < (self.target_hum - self.hyst_hum):
            self.mist_state = 1
        # Turn OFF if we reach target
        elif current_hum >= self.target_hum:
            self.mist_state = 0
        # Else: keep previous state

        return fan_speed, self.heater_state, self.mist_state

def main():
    # Influx Setup
    client = None
    write_api = None
    if INFLUX_TOKEN:
        try:
            client = influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = client.write_api(write_options=SYNCHRONOUS)
            logger.info("Connected to InfluxDB")
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
    else:
        logger.warning("INFLUX_DB_TOKEN not set. Logging disabled.")

    # Controller Setup
    controller = TerrariumController(TARGET_TEMP, TARGET_HUM, HYST_TEMP, HYST_HUM)

    # Serial Loop
    ser = None
    last_log_time = 0

    while True:
        try:
            if ser is None or not ser.is_open:
                logger.info(f"Connecting to serial port {SERIAL_PORT}...")
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                logger.info(f"Connected to {SERIAL_PORT}")
                time.sleep(2) # Wait for connection to stabilize

            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                if not line:
                    continue

                parts = line.split(',')
                # Expecting at least: temperature, humidity
                if len(parts) >= 2:
                    try:
                        current_temp = float(parts[0])
                        current_hum = float(parts[1])

                        fan, heater, mist = controller.process(current_temp, current_hum)

                        # Send command: fan,heater,mist
                        cmd = f"{fan},{heater},{mist}\n"
                        ser.write(cmd.encode('utf-8'))
                        logger.debug(f"Read: {current_temp}C, {current_hum}% -> Sent: {cmd.strip()}")

                        # Log to Influx
                        now = time.time()
                        if now - last_log_time >= 60:
                            if write_api:
                                point = influxdb_client.Point("environment_readings") \
                                    .field("temperature", current_temp) \
                                    .field("humidity", current_hum) \
                                    .field("fan_speed", fan) \
                                    .field("heater_state", heater) \
                                    .field("mist_state", mist)
                                write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
                                logger.info("Logged to InfluxDB")
                            last_log_time = now

                    except ValueError:
                        logger.error(f"Invalid numeric data received: {line}")
                else:
                    logger.warning(f"Invalid data format received: {line}")

            # Short sleep to prevent CPU hogging, but responsive enough
            time.sleep(0.1)

        except serial.SerialException as e:
            logger.error(f"Serial error: {e}")
            if ser:
                ser.close()
            ser = None
            time.sleep(5) # Wait before retrying
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
