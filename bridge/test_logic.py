import unittest
from bridge import TerrariumController

class TestTerrariumController(unittest.TestCase):
    def setUp(self):
        # Targets: Temp=30, Hum=75. Hysteresis: Temp=0.5, Hum=2.0
        self.controller = TerrariumController(30.0, 75.0, 0.5, 2.0)

    def test_fan_control(self):
        # Temp <= Target -> Fan 0
        fan, _, _ = self.controller.process(25.0, 75.0)
        self.assertEqual(fan, 0)
        fan, _, _ = self.controller.process(30.0, 75.0)
        self.assertEqual(fan, 0)

        # Temp > Target -> Fan 50-100
        # Diff = 0.1 -> Speed = 50 + (0.1/5)*50 = 50 + 1 = 51
        fan, _, _ = self.controller.process(30.1, 75.0)
        self.assertEqual(fan, 51)

        # Diff = 2.5 -> Speed = 50 + (2.5/5)*50 = 50 + 25 = 75
        fan, _, _ = self.controller.process(32.5, 75.0)
        self.assertEqual(fan, 75)

        # Diff >= 5.0 -> Speed = 100
        fan, _, _ = self.controller.process(35.0, 75.0)
        self.assertEqual(fan, 100)
        fan, _, _ = self.controller.process(40.0, 75.0)
        self.assertEqual(fan, 100)

    def test_heater_control(self):
        # Initial State 0
        self.assertEqual(self.controller.heater_state, 0)

        # Temp < Target - Hyst (30 - 0.5 = 29.5) -> ON
        _, heater, _ = self.controller.process(29.0, 75.0)
        self.assertEqual(heater, 1)

        # Temp increases but still < Target (e.g. 29.8) -> ON (Keep state)
        _, heater, _ = self.controller.process(29.8, 75.0)
        self.assertEqual(heater, 1)

        # Temp >= Target (30.0) -> OFF
        _, heater, _ = self.controller.process(30.0, 75.0)
        self.assertEqual(heater, 0)

        # Temp decreases but still > Target - Hyst (e.g. 29.6) -> OFF (Keep state)
        _, heater, _ = self.controller.process(29.6, 75.0)
        self.assertEqual(heater, 0)

        # Temp drops below 29.5 -> ON
        _, heater, _ = self.controller.process(29.4, 75.0)
        self.assertEqual(heater, 1)

    def test_mist_control(self):
        # Initial State 0
        self.assertEqual(self.controller.mist_state, 0)

        # Hum < Target - Hyst (75 - 2 = 73) -> ON
        _, _, mist = self.controller.process(30.0, 70.0)
        self.assertEqual(mist, 1)

        # Hum increases but < Target (e.g. 74) -> ON
        _, _, mist = self.controller.process(30.0, 74.0)
        self.assertEqual(mist, 1)

        # Hum >= Target (75) -> OFF
        _, _, mist = self.controller.process(30.0, 75.0)
        self.assertEqual(mist, 0)

        # Hum decreases but > Target - Hyst (e.g. 74) -> OFF
        _, _, mist = self.controller.process(30.0, 74.0)
        self.assertEqual(mist, 0)

        # Hum drops below 73 -> ON
        _, _, mist = self.controller.process(30.0, 72.9)
        self.assertEqual(mist, 1)

if __name__ == '__main__':
    unittest.main()
