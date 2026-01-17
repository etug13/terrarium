import serial
import json
import time
from pydantic import BaseModel
import os
import influxdb_client
import logging
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("terrarium_controller")


INFLUX_TOKEN = os.getenv("INFLUX_DB_TOKEN")
INFLUX_ORG = "Terrarium"
INFLUX_URL = "http://terrarium_influx:8086"
INFLUX_BUCKET = "terrarium_logs"
SEND_INTERVAL = 2.0  # Wyślij komendę do Arduino nie częściej niż co 2 sekundy

class Settings(BaseModel):
    temp_setting: float = 25.0
    hum_setting: float = 30.0
    pm_setting: float = 250
    fan_delta: float = 5.0
    heat_hysteresis: float = 0.5

    def load_settings(file_path='settings.json'):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                settings = Settings(**data)
                return settings
        except Exception as e:
            print(f"Błąd wczytywania ustawień: {e}")
            return Settings()  # Domyślne ustawienia


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

    # NOWA METODA: Zmiana celu temperatury
    def set_target(self, new_target):
        self.target = new_target

class HeatingController:
    def __init__(self, heat_hysteresis=0.5, setpoint=25.0):
        self.heating_hysteresis = heat_hysteresis
        self.setpoint = setpoint
        self.is_heating = False

    def update(self, current_temp):
        if not self.is_heating and current_temp < (self.setpoint - self.heating_hysteresis):
            self.is_heating = True
        elif self.is_heating and current_temp > (self.setpoint + self.heating_hysteresis):
            self.is_heating = False
        return self.is_heating


class Controller:

    def __init__(self, settings):
        self.settings: Settings = settings
        self.cooling_pid = CoolingPID(settings.temp_setting + (settings.temp_setting * 0.05), delta_range=5.0)
        self.heating_controller = HeatingController(heat_hysteresis=0.5, setpoint=settings.temp_setting)
        self.full_speed = False

    def process_sensor_data(self, temp, hum, quality):
        
        if int(quality) > self.settings.pm_setting and not self.full_speed:
            self.full_speed = True
            print("Zanieczyszczenie powietrza powyżej progu! Wentylator na pełnej mocy.")
        elif int(quality) <= self.settings.pm_setting - 50 and self.full_speed:
            self.full_speed = False
            print("Jakość powietrza poprawiła się. Wentylator wraca do normalnej pracy.")
        

        if self.full_speed:
            fan_speed = 255
        else:
            fan_speed = self.cooling_pid.compute(float(temp))
        
        heating_on = self.heating_controller.update(float(temp))
        
        return fan_speed, heating_on


    
def main():

    if INFLUX_TOKEN:
        try:
            client = influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = client.write_api(write_options=SYNCHRONOUS)
            logger.info("Connected to InfluxDB")
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
    else:
        logger.warning("INFLUX_DB_TOKEN not set. Logging disabled.")

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
    
    settings = Settings.load_settings()
    controller = Controller(settings)
    
    last_send_time = 0  # <--- MUSI BYĆ TUTAJ, przed while

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
            
            try:
                parts = line.split(";")
                
                # Walidacja: Obsłuż zarówno 3 parametry (dane) jak i 9 (dane + echo)
                if len(parts) >= 3:
                    temp = float(parts[0])
                    hum = float(parts[1])
                    quality = float(parts[2])
                    
                    fan_speed, heating_on = controller.process_sensor_data(temp, hum, quality)

                    # WYSYŁKA TYLKO CO 2 SEKUNDY
                    current_time = time.time()
                    if current_time - last_send_time > SEND_INTERVAL:
                        heat_pwm = 255 if heating_on else 0
                        mist_pwm = 0 
                        
                        # Zmieniam 255 na obliczone fan_speed!
                        command = f"{fan_speed};{heat_pwm};{mist_pwm};{settings.temp_setting};{settings.hum_setting}\n"
                        ser.write(command.encode('utf-8'))
                        logger.info(f"TX: {command.strip()}")
                        last_send_time = current_time  # <--- Aktualizacja czasu
                    
            except Exception as e:
                logger.error(f"Błąd: {e}")


if __name__ == "__main__":
    main()