"""Tests for fan_temp_monitor/monitor.py (macOS)"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import monitor


# ---------------------------------------------------------------------------
# powermetrics parsing
# ---------------------------------------------------------------------------

POWERMETRICS_INTEL = """
Machine model: MacBookPro16,1

*** Sampled system activity ***

**** SMC sensors ****

Fan: 1800 rpm
CPU die temperature: 68.31 C
GPU die temperature: 55.00 C
CPU Proximity: 41.00 C
Mem Proximity: 35.00 C

**** CPU Power ****
"""

POWERMETRICS_MULTI_FAN = """
**** SMC sensors ****

Fan 0: 1200 rpm
Fan 1: 1400 rpm
CPU die temperature: 72.50 C

**** CPU Power ****
"""

POWERMETRICS_NO_FAN = """
**** SMC sensors ****

CPU die temperature: 45.00 C
GPU die temperature: 38.00 C

**** CPU Power ****
"""


class TestPowermetrics(unittest.TestCase):
    def _parse(self, fake_output):
        with patch("shutil.which", return_value="/usr/bin/powermetrics"):
            with patch("os.geteuid", return_value=0):
                with patch("monitor._run", return_value=fake_output):
                    return monitor.get_powermetrics()

    def test_single_fan_and_temps(self):
        result = self._parse(POWERMETRICS_INTEL)
        self.assertEqual(len(result["fans"]), 1)
        self.assertEqual(result["fans"][0]["rpm"], 1800)
        labels = [t["label"] for t in result["temperatures"]]
        self.assertIn("CPU die temperature", labels)
        self.assertIn("GPU die temperature", labels)
        self.assertIn("CPU Proximity", labels)
        self.assertIn("Mem Proximity", labels)

    def test_multi_fan(self):
        result = self._parse(POWERMETRICS_MULTI_FAN)
        self.assertEqual(len(result["fans"]), 2)
        rpms = {f["label"]: f["rpm"] for f in result["fans"]}
        self.assertEqual(rpms["Fan 0"], 1200)
        self.assertEqual(rpms["Fan 1"], 1400)

    def test_fanless_mac(self):
        result = self._parse(POWERMETRICS_NO_FAN)
        self.assertEqual(result["fans"], [])
        self.assertEqual(len(result["temperatures"]), 2)

    def test_celsius_values(self):
        result = self._parse(POWERMETRICS_INTEL)
        cpu = next(t for t in result["temperatures"] if t["label"] == "CPU die temperature")
        self.assertAlmostEqual(cpu["celsius"], 68.31)

    def test_no_powermetrics_binary(self):
        with patch("shutil.which", return_value=None):
            result = monitor.get_powermetrics()
        self.assertEqual(result["temperatures"], [])
        self.assertEqual(result["fans"], [])

    def test_run_failure_returns_empty(self):
        with patch("shutil.which", return_value="/usr/bin/powermetrics"):
            with patch("os.geteuid", return_value=0):
                with patch("monitor._run", return_value=None):
                    result = monitor.get_powermetrics()
        self.assertEqual(result["temperatures"], [])

    def test_chip_name_is_smc(self):
        result = self._parse(POWERMETRICS_INTEL)
        for t in result["temperatures"]:
            self.assertEqual(t["chip"], "SMC")
        for f in result["fans"]:
            self.assertEqual(f["chip"], "SMC")


# ---------------------------------------------------------------------------
# istats parsing
# ---------------------------------------------------------------------------

ISTATS_OUTPUT = """
--- CPU Stats ---
CPU temp:         52.9°C    ▁▂▃▅▆▇

--- Fan Stats ---
Total fans in system:  2
Fan 0 speed:      1196 RPM ▁▂▃▄▅▆▇
Fan 1 speed:      1200 RPM ▁▂▃▄▅▆▇

--- Battery Stats ---
Battery temp:     29.5°C
"""

ISTATS_NO_GRAPHS = """
--- CPU Stats ---
CPU temp:         60.1°C

--- Fan Stats ---
Total fans in system:  1
Fan 0 speed:      2000 RPM

--- Extra Stats ---
GPU temp:         55.0°C
"""


class TestIstats(unittest.TestCase):
    def _parse(self, fake_output):
        with patch("shutil.which", return_value="/usr/local/bin/istats"):
            with patch("monitor._run", return_value=fake_output):
                return monitor.get_istats()

    def test_cpu_temp_and_fans(self):
        result = self._parse(ISTATS_OUTPUT)
        temps = {t["label"]: t["celsius"] for t in result["temperatures"]}
        self.assertIn("CPU temp", temps)
        self.assertAlmostEqual(temps["CPU temp"], 52.9)
        self.assertEqual(len(result["fans"]), 2)
        rpms = {f["label"]: f["rpm"] for f in result["fans"]}
        self.assertEqual(rpms["Fan 0 speed"], 1196)
        self.assertEqual(rpms["Fan 1 speed"], 1200)

    def test_battery_stats_not_included(self):
        """Battery section should not be parsed as temperatures."""
        result = self._parse(ISTATS_OUTPUT)
        labels = [t["label"] for t in result["temperatures"]]
        self.assertNotIn("Battery temp", labels)

    def test_extra_stats_gpu(self):
        result = self._parse(ISTATS_NO_GRAPHS)
        labels = [t["label"] for t in result["temperatures"]]
        self.assertIn("GPU temp", labels)

    def test_no_istats_binary(self):
        with patch("shutil.which", return_value=None):
            result = monitor.get_istats()
        self.assertEqual(result["temperatures"], [])
        self.assertEqual(result["fans"], [])


# ---------------------------------------------------------------------------
# osx-cpu-temp parsing
# ---------------------------------------------------------------------------

class TestOsxCpuTemp(unittest.TestCase):
    def test_parses_temperature(self):
        with patch("shutil.which", return_value="/usr/local/bin/osx-cpu-temp"):
            with patch("monitor._run", return_value="52.0°C\n"):
                result = monitor.get_osx_cpu_temp()
        self.assertEqual(len(result["temperatures"]), 1)
        self.assertAlmostEqual(result["temperatures"][0]["celsius"], 52.0)

    def test_no_binary(self):
        with patch("shutil.which", return_value=None):
            result = monitor.get_osx_cpu_temp()
        self.assertEqual(result["temperatures"], [])

    def test_handles_plain_number(self):
        with patch("shutil.which", return_value="/usr/local/bin/osx-cpu-temp"):
            with patch("monitor._run", return_value="63.5 C\n"):
                result = monitor.get_osx_cpu_temp()
        self.assertAlmostEqual(result["temperatures"][0]["celsius"], 63.5)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication(unittest.TestCase):
    def test_same_chip_label_deduplicated(self):
        pm = {
            "temperatures": [{"chip": "SMC", "label": "CPU die temperature", "celsius": 68.0, "crit": None, "max": None}],
            "fans": [{"chip": "SMC", "label": "Fan", "rpm": 1800}],
            "source": "powermetrics",
        }
        ist = {
            "temperatures": [{"chip": "SMC", "label": "CPU die temperature", "celsius": 69.0, "crit": None, "max": None}],
            "fans": [{"chip": "SMC", "label": "Fan", "rpm": 1850}],
            "source": "istats",
        }
        with patch("monitor.get_powermetrics", return_value=pm):
            with patch("monitor.get_istats", return_value=ist):
                with patch("monitor.get_osx_cpu_temp", return_value={"temperatures": [], "fans": [], "source": "osx-cpu-temp"}):
                    temps, fans = monitor.collect_all()

        cpu_temps = [t for t in temps if t["label"] == "CPU die temperature"]
        self.assertEqual(len(cpu_temps), 1)
        self.assertAlmostEqual(cpu_temps[0]["celsius"], 68.0)  # first source wins

        fan_entries = [f for f in fans if f["label"] == "Fan"]
        self.assertEqual(len(fan_entries), 1)
        self.assertEqual(fan_entries[0]["rpm"], 1800)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestColorize(unittest.TestCase):
    def test_no_color(self):
        self.assertEqual(monitor.colorize("hi", monitor.COLOR_RED, False), "hi")

    def test_with_color(self):
        out = monitor.colorize("hi", monitor.COLOR_RED, True)
        self.assertIn("\033[", out)
        self.assertIn("hi", out)


class TestTempColor(unittest.TestCase):
    def setUp(self):
        monitor.TEMP_WARN = 70.0
        monitor.TEMP_CRIT = 90.0

    def test_normal(self):
        out = monitor.temp_color(50.0, True)
        self.assertIn(monitor.COLOR_GREEN, out)

    def test_warning(self):
        out = monitor.temp_color(75.0, True)
        self.assertIn(monitor.COLOR_YELLOW, out)

    def test_critical(self):
        out = monitor.temp_color(92.0, True)
        self.assertIn(monitor.COLOR_RED, out)


class TestPrintJson(unittest.TestCase):
    def test_valid_json(self):
        temps = [{"chip": "SMC", "label": "CPU die temperature", "celsius": 55.0, "crit": None, "max": None, "source": "powermetrics"}]
        fans  = [{"chip": "SMC", "label": "Fan", "rpm": 1800, "source": "powermetrics"}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            monitor.print_json(temps, fans)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["temperatures"][0]["celsius"], 55.0)
        self.assertEqual(data["fans"][0]["rpm"], 1800)


class TestPrintReport(unittest.TestCase):
    def test_stopped_fan_label(self):
        fans  = [{"chip": "SMC", "label": "Fan", "rpm": 0, "source": "powermetrics"}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            monitor.print_report([], fans, use_color=False)
        self.assertIn("stopped", buf.getvalue())

    def test_report_contains_temp_value(self):
        temps = [{"chip": "SMC", "label": "CPU die temperature", "celsius": 55.3, "crit": None, "max": None, "source": "powermetrics"}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            monitor.print_report(temps, [], use_color=False)
        self.assertIn("55.3", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
