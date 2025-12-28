from fastapi import FastAPI
import redis
from influxdb_client import InfluxDBClient
import os

INFLUX_DB_TOKEN = os.getenv("INFLUX_DB_TOKEN")

app = FastAPI()

# Połączenia
r = redis.Redis(host='terrarium_settings', port=6379, db=0)
client = InfluxDBClient(
    url="http://terrarium_influx:8086", 
    token=INFLUX_DB_TOKEN, 
    org="PRUT")

@app.get("/status")
def get_status():
    # Pobieranie ostatniego pomiaru z InfluxDB
    query = 'from(bucket: "sensor_data") |> range(start: -10m) |> last()'
    result = client.query_api().query(query)
    
    data = {}
    for table in result:
        for record in table.records:
            data[record.get_field()] = record.get_value()
    return data

@app.post("/settings/{parameter}")
def set_param(parameter: str, value: float):
    # Zmiana nastawy w Redis (np. target_temp)
    r.set(parameter, value)
    return {"status": "success", "param": parameter, "value": value}