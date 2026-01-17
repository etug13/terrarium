import serial
import json
import time
from pydantic import BaseModel
import os
import logging
import requests
import asyncio
import aiohttp
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("terrarium_controller")

load_dotenv()

TS_LOGS_CHANNEL_ID = os.getenv("TS_LOGS_CHANNEL_ID")
TS_LOGS_WRITE_KEY = os.getenv("TS_LOGS_WRITE_API_KEY")
TS_LOGS_READ_KEY = os.getenv("TS_LOGS_READ_API_KEY")

TS_SETTINGS_CHANNEL_ID = os.getenv("TS_SETTINGS_CHANNEL_ID")
TS_SETTINGS_WRITE_KEY = os.getenv("TS_SETTINGS_WRITE_API_KEY")
TS_SETTINGS_READ_KEY = os.getenv("TS_SETTINGS_READ_API_KEY")

SEND_INTERVAL = 1.0  # Wyślij komendę do Arduino nie częściej niż co 2 sekundy

class Settings(BaseModel):
    temp_setting: float = 25.0
    hum_setting: float = 30.0
    aq_thresh_setting: float = 250
    # fan_delta: float = 5.0
    # heat_hysteresis: float = 0.5

class LogParams(BaseModel):
    pass

# --- KLASA PID DLA WENTYLATORA ---
class CoolingPID:
    def __init__(self, target, delta_range):
        self.target = target
        
        # Automatyczne dobranie Kp:
        # Chcemy, aby przy błędzie równym 'delta_range', wyjście wynosiło 255 (max).
        # P = Kp * error  =>  255 = Kp * delta  =>  Kp = 255 / delta
        self.Kp = 255.0 / delta_range 
        
        # Ki i Kd dobieramy eksperymentalnie, ale przy wentylatorach
        # zazwyczaj wystarczy samo P lub PI.
        self.Ki = 2.0  
        self.Kd = 0.5  

        self.prev_error = 0
        self.integral = 0
        self.prev_time = time.time()
        self.min_out = 170
        self.max_out = 255

    def compute(self, current_temp):
        now = time.time()
        dt = now - self.prev_time
        
        # Dla chłodzenia: Error jest dodatni, gdy jest ZA CIEPŁO
        error = current_temp - self.target
        
        # Jeśli jest zimniej niż cel, wentylator STOI.
        if error <= 0:
            self.prev_error = 0
            self.integral = 0
            self.prev_time = now
            return 0

        # --- PID CALCULATION ---
        # 1. Proportional
        P = self.Kp * error
        
        # 2. Integral (z ograniczeniem anti-windup)
        self.integral += error * dt
        # Ograniczamy całkę, żeby nie "rozbiegła się"
        limit_i = self.max_out / (self.Ki if self.Ki > 0 else 1)
        self.integral = max(min(self.integral, limit_i), -limit_i)
        I = self.Ki * self.integral

        # 3. Derivative
        if dt > 0:
            D = self.Kd * (error - self.prev_error) / dt
        else:
            D = 0

        output = P + I + D
        
        # --- ZABEZPIECZENIA WYJŚCIA ---
        if output < self.min_out:
            output = 0 # Poniżej progu startu wyłączamy
        elif output > self.max_out:
            output = self.max_out
            
        self.prev_error = error
        self.prev_time = now
        
        return int(output)
    
    def tune(self, kp, ki, kd):
        self.Kp = kp
        self.Ki = ki
        self.Kd = kd

    def set_target(self, new_target):
        self.target = new_target

class HeatingController:
    def __init__(self, heat_hysteresis=0.5, heat_setting=25.0):
        self.heating_hysteresis = heat_hysteresis
        self.setpoint = heat_setting
        self.is_heating = False

    def calculate_heating(self, current_temp):
        if not self.is_heating and current_temp < (self.setpoint - self.heating_hysteresis):
            self.is_heating = True
        elif self.is_heating and current_temp > (self.setpoint + self.heating_hysteresis):
            self.is_heating = False
        return self.is_heating

    def set_setpoint(self, new_temp: int):
        self.setpoint = new_temp

class HumidifierController:
    def __init__(self, hum_setting=60.0):
        self.hum_setting = hum_setting
        self.is_humidifier_on = False
    
    def calculate_humidifier(self, current_hum):
        if float(current_hum) < self.hum_setting and not self.is_humidifier_on:
            self.is_humidifier_on = True
        if float(current_hum) > self.hum_setting and self.is_humidifier_on:
            self.is_humidifier_on = False
        return self.is_humidifier_on

class Controller:
    def __init__(self, settings: Settings):
        self.temp_setting: float = settings.temp_setting
        self.hum_setting: float = settings.hum_setting
        self.aq_thresh_setting: float = settings.aq_thresh_setting
        # self.fan_delta: float = settings.fan_delta
        # self.heat_hysteresis: float = settings.heat_hysteresis
        self.cooling_pid = CoolingPID(settings.temp_setting + (settings.temp_setting * 0.05), delta_range=5.0)
        self.heating_controller = HeatingController(heat_hysteresis=0.5, heat_setting=settings.temp_setting)
        self.humidifier_controller = HumidifierController(hum_setting=60.0)
        self.full_speed = False

    def process_sensor_data(self, temp, hum, quality):
        
        # wywietrzenie w przypadku slabego powietrza
        if float(quality) > self.aq_thresh_setting and not self.full_speed:
            self.full_speed = True
            print("Zanieczyszczenie powietrza powyżej progu! Wentylator na pełnej mocy.")
        elif float(quality) <= self.aq_thresh_setting - 50 and self.full_speed:
            self.full_speed = False
            print("Jakość powietrza poprawiła się. Wentylator wraca do normalnej pracy.")
        
        if self.full_speed:
            fan_speed = 255
        else:
            fan_speed = self.cooling_pid.compute(float(temp))
        
        humidifier_on = self.humidifier_controller.calculate_humidifier(float(hum))
        heating_on = self.heating_controller.calculate_heating(float(temp))
        
        return fan_speed, heating_on, humidifier_on

    def update_settings(self, new_settings: Settings):

        if self.temp_setting != new_settings.temp_setting:
            self.heating_controller.set_setpoint(new_settings.temp_setting)
            self.cooling_pid.set_target(new_settings.temp_setting)

        if self.hum_setting != new_settings.hum_setting:
            self.hum_setting = new_settings.hum_setting
        
        if self.aq_thresh_setting != new_settings.aq_thresh_setting:
            self.aq_thresh_setting = new_settings.aq_thresh_setting

class ThingspeakClient:
    def __init__(self):
        self.ts_logs_channel_id = TS_LOGS_CHANNEL_ID
        self.ts_setings_channel_id = TS_SETTINGS_CHANNEL_ID
        self.ts_settings_read_key = TS_SETTINGS_READ_KEY
        self.ts_logs_write_key = TS_LOGS_WRITE_KEY

    # field1 - temperatur
    # field2 - humidity
    # field3 - air_quality
    # field4 - set_temp
    # field5 - set_hum

    def write_logs(self, params):
        pass

    def fetch_and_update_settings(self, update_settings: callable):
        pass

    


    
def main():

    for i in range(4):
        try:
            ser = serial.Serial(f'/dev/ttyUSB{i}', 9600, timeout=1)
            print(f"Znaleziono port szeregowy /dev/ttyUSB{i}")
            ser.flush()
            break
        except serial.serialutil.SerialException as e:
            print(f"Błąd otwarcia portu szeregowego {i}: {e}")
    else:
        print("Nie znaleziono dostępnego portu szeregowego. Upewnij się, że urządzenie jest podłączone.")
        return
    
    settings = Settings(
        temp_setting = 25.0,
        hum_setting = 60.0,
        aq_thresh_setting = 200
    )

    controller = Controller(settings)
    
    last_send_time = 0

    while True:
        if ser.in_waiting > 0:
            raw_line = ser.readline()
            try:
                line = raw_line.decode('utf-8').rstrip()
            except UnicodeDecodeError:
                logger.warning("Błąd dekodowania linii (śmieci na UART)")
                continue
            
            if not line: continue

            logger.info(f"RX: {line}")
            
            parts = line.split(";")
            
            # Walidacja: Obsłuż zarówno 3 parametry (dane) jak i 9 (dane + echo)
            if len(parts) >= 3:
                temp = float(parts[0])
                hum = float(parts[1])
                quality = float(parts[2])
                
                fan_speed, heating_on, humidifier_on = controller.process_sensor_data(temp, hum, quality)

                # WYSYŁKA TYLKO CO 2 SEKUNDY
                current_time = time.time()
                if current_time - last_send_time > SEND_INTERVAL:
                    heat_pwm = 255 if heating_on else 0
                    mist = 1 if humidifier_on else 0
                    
                    command = f"{fan_speed};{heat_pwm};{mist};{controller.temp_setting};{controller.hum_setting}\n"
                    ser.write(command.encode('utf-8'))
                    logger.info(f"TX: {command.strip()}")
                    last_send_time = current_time



if __name__ == "__main__":
    main()