import os
import time
import json
import requests
import random
from dotenv import load_dotenv
# field1 - temperatur
# field2 - humidity
# field3 - air_quality
# field4 - set_temp
# field5 - set_hum
load_dotenv()
TS_LOGS_CHANNEL_ID = os.getenv("TS_LOGS_CHANNEL_ID")
TS_LOGS_WRITE_KEY = os.getenv("TS_LOGS_WRITE_API_KEY")
TS_LOGS_READ_KEY = os.getenv("TS_LOGS_READ_API_KEY")

TS_SETTINGS_CHANNEL_ID = os.getenv("TS_SETTINGS_CHANNEL_ID")
TS_SETTINGS_WRITE_KEY = os.getenv("TS_SETTINGS_WRITE_API_KEY")
TS_SETTINGS_READ_KEY = os.getenv("TS_SETTINGS_READ_API_KEY")

def update_thingspeak(
        temperature,
        humidity,
        air_quality,
        set_temp,
        set_hum
):
    url = "https://api.thingspeak.com/update"
    params = {
        "api_key": TS_LOGS_WRITE_KEY,
        "field1": temperature,
        "field2": humidity,
        "field3": air_quality,
        "field4": set_temp,
        "field5": set_hum
    }
    
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200 and response.text != '0':
            print(f"(Entry ID: {response.text})")
        else:
            print(f"[ERROR] ThingSpeak odrzucił dane (Limit 15s?): {response.text}")
    except requests.RequestException as e:
        print(f"[ERROR] Błąd sieci (Upload): {e}")

def update_settings(
        set_temp,
        set_hum,
        set_aq_thresh
):
    url = "https://api.thingspeak.com/update"
    params = {
        "api_key": TS_SETTINGS_WRITE_KEY,
        "field1": set_temp,
        "field2": set_hum,
        "field3": set_aq_thresh,
        # "field4": set_temp,
        # "field5": set_hum
    }
    
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200 and response.text != '0':
            print(f"(Entry ID: {response.text})")
        else:
            print(f"[ERROR] ThingSpeak odrzucił dane {response.text}")
    except requests.RequestException as e:
        print(f"[ERROR] Błąd sieci (Upload): {e}")


# 3. Funkcja pobierająca ustawienia (READ LAST)
def get_settings():
    # Pobieramy ostatni wpis z kanału, żeby sprawdzić pola ustawień (Field 3, 4, 5)
    url = f"https://api.thingspeak.com/channels/{TS_LOGS_CHANNEL_ID}/feeds"
    params = {
        "api_key": TS_LOGS_READ_KEY,
        "results": 10,
        }
    
    try:

        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data
        
    except requests.RequestException as e:
        print(f"[ERROR] Błąd sieci (Download): {e}")
        return None

if __name__ == "__main__":

    # for x in range(1):
    #     temp = 25 - 0.3 * x**2
    #     hum = 30 + 2*x
    #     air_quality = 200 + random.randint(-20, +20)
    #     update_thingspeak(temp, hum, air_quality, 25, 30)
    #     time.sleep(15)
    #     if x == 4:
    #         update_thingspeak(temp, hum, air_quality, 25, 30)
    #         time.sleep(15)
    # response_dict = get_settings()
    # for feed in response_dict["feeds"]:
    #     print(feed["created_at"] + " " + feed["field4"])

    update_settings(25, 30, 200)