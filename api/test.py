import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

token = "xg-dDa0kH-D71Sm5CEUiJdMQZMbASfZBtd3yg3qUF0WPwlHjYbXp1RFEuBEZquhV2tpIgOU61IdGpPGHElVtVQ=="
org = "PRUT"
url = "http://localhost:8086"
bucket = "terrarium_logs"

client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=SYNCHRONOUS)

# Przykład zapisu pomiaru
point = influxdb_client.Point("air_quality") \
    .tag("location", "terrarium_1") \
    .field("temperature", 25.7) \
    .field("humidity", 58.0)

write_api.write(bucket=bucket, org=org, record=point)