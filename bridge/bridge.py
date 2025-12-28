import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS
import os
from dotenv import load_dotenv

token = os.getenv("INFLUX_DB_TOKEN")
org = "PRUT"
url = "http://localhost:8086"
bucket = "terrarium_logs"

client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=SYNCHRONOUS)

# Przykład zapisu pomiaru
point = influxdb_client.Point("air_quality") \
    .tag("location", "terrarium_1") \
    .field("temperature", 24.0) \
    .field("humidity", 58.0)

write_api.write(bucket=bucket, org=org, record=point)