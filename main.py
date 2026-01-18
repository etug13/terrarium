import serial
import json
import time
import glob
from pydantic import BaseModel
import os
import logging
import requests
import asyncio
import aiohttp
import threading
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

SEND_INTERVAL = 1.0

class Settings(BaseModel):
    temp_setting: float = 25.0
    hum_setting: float = 30.0
    aq_thresh_setting: float = 250
    # fan_delta: float = 5.0
    # heat_hysteresis: float = 0.5

class LogParams(BaseModel):
    pass


class CoolingPID:
    def __init__(self, target, delta_range):
        self.target = target
        
        # Automatyczne dobranie Kp:
        # Chcemy, aby przy błędzie równym 'delta_range', wyjście wynosiło 255 (max).
        # P = Kp * error  =>  255 = Kp * delta  =>  Kp = 255 / delta
        self.Kp = 255.0 / delta_range 
        
        # Ki i Kd dobieramy eksperymentalnie, ale przy wentylatorach
        # zazwyczaj wystarczy samo P lub PI.
        self.Ki = 0 
        self.Kd = 1.0

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
    def __init__(self, hum_setting):
        self.hum_setting = hum_setting
        self.is_humidifier_on = False
    
    def calculate_humidifier(self, current_hum):
        if float(current_hum) < self.hum_setting:
            self.is_humidifier_on = True
        if float(current_hum) > self.hum_setting:
            self.is_humidifier_on = False
        return self.is_humidifier_on

    def set_hum(self, new_hum):
        self.hum_setting = new_hum

class Controller:
    def __init__(self, settings: Settings):
        self.temp_setting: float = settings.temp_setting
        self.hum_setting: float = settings.hum_setting
        self.aq_thresh_setting: float = settings.aq_thresh_setting
        self.aq_trigger_delay = 0
        # self.fan_delta: float = settings.fan_delta
        # self.heat_hysteresis: float = settings.heat_hysteresis
        self.cooling_pid = CoolingPID(settings.temp_setting + (settings.temp_setting * 0.05), delta_range=5.0)
        self.heating_controller = HeatingController(heat_hysteresis=0.5, heat_setting=settings.temp_setting)
        self.humidifier_controller = HumidifierController(hum_setting=self.hum_setting)
        self.clear_smog = False
        self.clear_humidity = False

    def process_sensor_data(self, temp, hum, quality):
        
        if self.aq_trigger_delay > 0:
            self.aq_trigger_delay -= 1
        # wywietrzenie w przypadku slabego powietrza

        if float(quality) > self.aq_thresh_setting:
            if self.aq_trigger_delay == 0:
                self.clear_smog = True
                # print("Zanieczyszczenie powietrza powyżej progu! Wentylator na pełnej mocy.")
        elif float(quality) <= self.aq_thresh_setting * 0.9:
            self.clear_smog = False
            print("Jakość powietrza poprawiła się. Wentylator wraca do normalnej pracy.")


        if float(hum) > self.hum_setting + 10:
                self.clear_humidity = True
                print("wilgotnosc powietrza powyżej progu! Wentylator na pełnej mocy.")
        elif float(hum) <= self.hum_setting:
            self.clear_humidity = False
            print("wilgotnosc powietrza poprawiła się. Wentylator wraca do normalnej pracy.")
        
        if self.clear_smog:
                fan_speed=255
        elif self.clear_humidity:
            fan_speed = 170
        else:
            fan_speed = self.cooling_pid.compute(float(temp))
        
        if self.clear_smog:
            humidifier_on = False
        else:
            humidifier_on = self.humidifier_controller.calculate_humidifier(float(hum))

        if humidifier_on:
            self.aq_trigger_delay = 90

        heating_on = self.heating_controller.calculate_heating(float(temp))
        
        return fan_speed, heating_on, humidifier_on

    def update_settings(self, new_settings: Settings):

        if self.temp_setting != new_settings.temp_setting:
            self.heating_controller.set_setpoint(new_settings.temp_setting)
            self.cooling_pid.set_target(new_settings.temp_setting)
            self.temp_setting = new_settings.temp_setting

        if self.hum_setting != new_settings.hum_setting:
            self.humidifier_controller.set_hum(new_settings.hum_setting)
            self.hum_setting = new_settings.hum_setting
        
        if self.aq_thresh_setting != new_settings.aq_thresh_setting:
            self.aq_thresh_setting = new_settings.aq_thresh_setting

class ThingspeakClient:
    def __init__(self, update_settings_callback):
        self.ts_logs_channel_id = TS_LOGS_CHANNEL_ID
        self.ts_settings_channel_id = TS_SETTINGS_CHANNEL_ID
        self.ts_settings_read_key = TS_SETTINGS_READ_KEY
        self.ts_logs_write_key = TS_LOGS_WRITE_KEY
        self.update_settings_callback = update_settings_callback
        
        self.latest_data = {}
        # Flagi do akumulacji stanu (jeśli choć raz włączono w cyklu)
        self.fan_triggered = False
        self.mist_triggered = False
        self.heat_triggered = False
        
        self.lock = threading.Lock()
        self.running = True

    def start(self):
        """Uruchamia wątki w tle"""
        t_logs = threading.Thread(target=self._logs_loop, daemon=True)
        t_settings = threading.Thread(target=self._settings_loop, daemon=True)
        t_logs.start()
        t_settings.start()

    def update_current_state(self, temp, hum, set_temp, set_hum, aq, fan, mist, heat, aq_thresh):
        """Aktualizuje dane, które zostaną wysłane w najbliższym cyklu"""
        with self.lock:
            # Akumulacja stanów (sticky bits)
            if fan > 0:
                self.fan_triggered = True
            if mist > 0:
                self.mist_triggered = True
            if heat > 0:
                self.heat_triggered = True

            self.latest_data = {
                "field1": temp,
                "field2": hum,
                "field3": aq,
                "field4": set_temp,
                "field5": set_hum,
                # field6 jest obliczany w pętli wysyłania (fan + mist)
                "field7": heat,
                "field8": aq_thresh,
            }

    def _logs_loop(self):
        """Wysyłanie logów co 15 sekund"""
        while self.running:
            time.sleep(15)
            data_to_send = None
            with self.lock:
                if self.latest_data:
                    data_to_send = self.latest_data.copy()
                    
                    # Obliczanie statusu binarnego dla field6
                    # 1 = Fan ON, 2 = Mist ON, 3 = Both ON
                    status = 0
                    if self.fan_triggered:
                        status += 1
                    elif self.mist_triggered:
                        status += 2
                    
                    data_to_send["field6"] = status
                    
                    # Reset akumulatorów po przygotowaniu danych do wysyłki
                    self.fan_triggered = False
                    self.mist_triggered = False
                    self.heat_triggered = False
            
            if data_to_send:
                try:
                    url = "https://api.thingspeak.com/update"
                    data_to_send["api_key"] = self.ts_logs_write_key
                    requests.post(url, data=data_to_send, timeout=5)
                    logger.info(f"[TS] Wysłano logi do chmury (Status={data_to_send.get('field6')}).")
                except Exception as e:
                    logger.error(f"[TS] Błąd wysyłania logów: {e}")

    def _settings_loop(self):
        """Pobieranie ustawień co 15 sekund"""
        while self.running:
            try:
                url = f"https://api.thingspeak.com/channels/{self.ts_settings_channel_id}/feeds/last.json"
                params = {"api_key": self.ts_settings_read_key}
                response = requests.get(url, params=params, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    # Parsowanie pól (zakładamy: f1=Temp, f2=Hum, f3=AQ)
                    new_settings = Settings(
                        temp_setting=float(data.get("field1")),
                        hum_setting=float(data.get("field2")),
                        aq_thresh_setting=float(data.get("field3"))
                    )
                    self.update_settings_callback(new_settings)
                    # logger.info("[TS] Zaktualizowano ustawienia z chmury.")
            except Exception as e:
                logger.error(f"[TS] Błąd pobierania ustawień: {e}")
            
            time.sleep(15)
    
def find_and_connect_serial():
        """Szuka dostępnych portów ttyUSB/ttyACM i próbuje się połączyć"""
        # Wzorce nazw portów na Linux/Raspberry Pi
        patterns = ['/dev/ttyUSB*', '/dev/ttyACM*']
        candidates = []
        for p in patterns:
            candidates.extend(glob.glob(p))
            
        if not candidates:
            print("[INIT] ❌ BŁĄD: Nie znaleziono żadnych urządzeń Arduino!")
            print("       Sprawdź kabel USB (czy nie jest 'tylko do ładowania').")
            print("       Wpisz w terminalu: ls /dev/tty* aby zobaczyć dostępne urządzenia.")
            return None

        for port in candidates:
            try:
                print(f"[INIT] Próba połączenia z {port}...")
                ser = serial.Serial(port, 9600, timeout=0.1)
                print(f"[INIT] ✅ Sukces! Połączono z {port}")
                return ser
            except Exception as e:
                print(f"[INIT] Nie udało się otworzyć {port}: {e}")
        
        print("[INIT] Znaleziono porty, ale żadnego nie udało się otworzyć.")
        return None

    
def main():

    ser = find_and_connect_serial()
    
    settings = Settings(
        temp_setting = 25.0,
        hum_setting = 40.0,
        aq_thresh_setting = 200
    )

    controller = Controller(settings)
    
    # INICJALIZACJA THINGSPEAK
    # Przekazujemy metodę controller.update_settings jako callback
    ts_client = ThingspeakClient(controller.update_settings)
    ts_client.start()
    
    last_send_time = 0

    while True:
        if ser and ser.in_waiting > 0:
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
                try:
                    temp = float(parts[0])
                    hum = float(parts[1])
                    quality = float(parts[2])
                    
                    fan_speed, heating_on, humidifier_on = controller.process_sensor_data(temp, hum, quality)

                    # AKTUALIZACJA DANYCH DLA WĄTKU TŁA
                    heat_pwm = 255 if heating_on else 0
                    mist = 1 if humidifier_on else 0
                    
                    # POPRAWKA: Przekazujemy argumenty zgodnie z nową logiką
                    # Kolejność w update_current_state: temp, hum, set_temp, set_hum, aq, fan, mist, heat, aq_thresh
                    ts_client.update_current_state(
                        temp, 
                        hum, 
                        controller.temp_setting, 
                        controller.hum_setting, 
                        quality, 
                        fan_speed, 
                        mist,
                        heat_pwm,
                        controller.aq_thresh_setting # Dodano próg AQ
                    )

                    current_time = time.time()
                    if current_time - last_send_time > SEND_INTERVAL:
                        
                        command = f"{fan_speed};{heat_pwm};{mist};{controller.temp_setting};{controller.hum_setting}\n"
                        ser.write(command.encode('utf-8'))
                        logger.info(f"TX: {command.strip()}")
                        last_send_time = current_time
                except ValueError:
                    logger.error("Błąd parsowania danych")



if __name__ == "__main__":
    main()