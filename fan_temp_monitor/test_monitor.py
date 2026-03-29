"""Tests for fan_temp_monitor/monitor.py"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.dirname(__file__))

import monitor


class TestReadFile(unittest.TestCase):
    def test_reads_content(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("  hello  \n")
            name = f.name
        try:
            self.assertEqual(monitor.read_file(name), "hello")
        finally:
            os.unlink(name)

    def test_missing_file_returns_none(self):
        self.assertIsNone(monitor.read_file("/nonexistent/path/file.txt"))


class TestConversions(unittest.TestCase):
    def test_millidegrees_to_celsius(self):
        self.assertAlmostEqual(monitor.millidegrees_to_celsius("45000"), 45.0)
        self.assertAlmostEqual(monitor.millidegrees_to_celsius("72500"), 72.5)

    def test_millidegrees_bad_input(self):
        self.assertIsNone(monitor.millidegrees_to_celsius("N/A"))
        self.assertIsNone(monitor.millidegrees_to_celsius(None))

    def test_rpm_value(self):
        self.assertEqual(monitor.rpm_value("1200"), 1200)
        self.assertEqual(monitor.rpm_value("0"), 0)

    def test_rpm_bad_input(self):
        self.assertIsNone(monitor.rpm_value("N/A"))
        self.assertIsNone(monitor.rpm_value(None))


class TestHwmonSensors(unittest.TestCase):
    def _make_hwmon_tree(self, tmp_dir):
        """Create a fake /sys/class/hwmon/hwmon0 tree."""
        hwmon = os.path.join(tmp_dir, "hwmon0")
        os.makedirs(hwmon)
        # chip name
        with open(os.path.join(hwmon, "name"), "w") as f:
            f.write("fake_chip\n")
        # temperature sensor
        with open(os.path.join(hwmon, "temp1_input"), "w") as f:
            f.write("55000\n")
        with open(os.path.join(hwmon, "temp1_label"), "w") as f:
            f.write("Core 0\n")
        with open(os.path.join(hwmon, "temp1_max"), "w") as f:
            f.write("100000\n")
        with open(os.path.join(hwmon, "temp1_crit"), "w") as f:
            f.write("105000\n")
        # fan sensor
        with open(os.path.join(hwmon, "fan1_input"), "w") as f:
            f.write("1500\n")
        with open(os.path.join(hwmon, "fan1_label"), "w") as f:
            f.write("CPU Fan\n")
        return tmp_dir

    def test_reads_temp_and_fan(self):
        import glob as _glob_module

        # Capture the real function before any mocking touches it
        _real_glob = _glob_module.glob.__wrapped__ if hasattr(_glob_module.glob, "__wrapped__") else _glob_module.glob

        with tempfile.TemporaryDirectory() as tmp:
            self._make_hwmon_tree(tmp)
            hwmon0 = os.path.join(tmp, "hwmon0")

            # Build the return values using os.glob directly via fnmatch
            import fnmatch

            def fake_glob(pattern):
                if pattern == "/sys/class/hwmon/hwmon*":
                    return [hwmon0]
                # Map /sys/class/hwmon/hwmon0/... -> tmp/hwmon0/...
                local_pattern = pattern.replace("/sys/class/hwmon/hwmon0", hwmon0)
                base_dir = os.path.dirname(local_pattern)
                file_pattern = os.path.basename(local_pattern)
                try:
                    entries = os.listdir(base_dir)
                except OSError:
                    return []
                return sorted(
                    os.path.join(base_dir, e)
                    for e in entries
                    if fnmatch.fnmatch(e, file_pattern)
                )

            with patch("monitor.glob.glob", side_effect=fake_glob):
                result = monitor.get_hwmon_sensors()

        self.assertEqual(len(result["temperatures"]), 1)
        t = result["temperatures"][0]
        self.assertEqual(t["chip"], "fake_chip")
        self.assertEqual(t["label"], "Core 0")
        self.assertAlmostEqual(t["celsius"], 55.0)
        self.assertAlmostEqual(t["max"], 100.0)
        self.assertAlmostEqual(t["crit"], 105.0)

        self.assertEqual(len(result["fans"]), 1)
        f = result["fans"][0]
        self.assertEqual(f["chip"], "fake_chip")
        self.assertEqual(f["label"], "CPU Fan")
        self.assertEqual(f["rpm"], 1500)


class TestThermalZones(unittest.TestCase):
    def test_reads_thermal_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            zone = os.path.join(tmp, "thermal_zone0")
            os.makedirs(zone)
            with open(os.path.join(zone, "type"), "w") as f:
                f.write("x86_pkg_temp\n")
            with open(os.path.join(zone, "temp"), "w") as f:
                f.write("60000\n")

            with patch("monitor.glob.glob", return_value=[zone]):
                result = monitor.get_thermal_zones()

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["celsius"], 60.0)
        self.assertEqual(result[0]["chip"], "x86_pkg_temp")

    def test_missing_temp_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            zone = os.path.join(tmp, "thermal_zone0")
            os.makedirs(zone)
            with open(os.path.join(zone, "type"), "w") as f:
                f.write("acpitz\n")
            # no temp file

            with patch("monitor.glob.glob", return_value=[zone]):
                result = monitor.get_thermal_zones()

        self.assertEqual(result, [])


class TestIpmiSensors(unittest.TestCase):
    def test_no_ipmitool(self):
        with patch("shutil.which", return_value=None):
            result = monitor.get_ipmi_sensors()
        self.assertEqual(result, {"temperatures": [], "fans": []})

    def test_parses_temperature_output(self):
        temp_output = (
            "Inlet Temp       | 04h | ok  |  7.1 | 22 degrees C\n"
            "CPU1 Temp        | 0Eh | ok  |  3.1 | 48 degrees C\n"
        )
        fan_output = "FAN1             | 41h | ok  | 29.1 | 3600 RPM\n"

        with patch("shutil.which", return_value="/usr/bin/ipmitool"):
            with patch("subprocess.check_output", side_effect=[
                temp_output.encode(), fan_output.encode()
            ]):
                result = monitor.get_ipmi_sensors()

        self.assertEqual(len(result["temperatures"]), 2)
        self.assertAlmostEqual(result["temperatures"][0]["celsius"], 22.0)
        self.assertEqual(len(result["fans"]), 1)
        self.assertEqual(result["fans"][0]["rpm"], 3600)


class TestDeduplication(unittest.TestCase):
    def test_deduplicates_temps(self):
        with patch("monitor.get_hwmon_sensors", return_value={
            "temperatures": [{"chip": "k10temp", "label": "Tdie", "celsius": 50.0, "crit": None, "max": None, "source": "hwmon"}],
            "fans": [],
        }):
            with patch("monitor.get_thermal_zones", return_value=[
                {"chip": "k10temp", "label": "Tdie", "celsius": 51.0, "crit": None, "max": None, "source": "thermal_zone"},
            ]):
                with patch("monitor.get_ipmi_sensors", return_value={"temperatures": [], "fans": []}):
                    with patch("monitor.get_lm_sensors", return_value={"temperatures": [], "fans": []}):
                        temps, fans = monitor.collect_all()

        # Duplicate chip+label pair should appear only once (first wins)
        matching = [t for t in temps if t["chip"].lower() == "k10temp" and t["label"].lower() == "tdie"]
        self.assertEqual(len(matching), 1)
        self.assertAlmostEqual(matching[0]["celsius"], 50.0)


class TestColorize(unittest.TestCase):
    def test_no_color(self):
        result = monitor.colorize("test", monitor.COLOR_RED, False)
        self.assertEqual(result, "test")

    def test_with_color(self):
        result = monitor.colorize("test", monitor.COLOR_RED, True)
        self.assertIn("\033[", result)
        self.assertIn("test", result)


class TestTempColor(unittest.TestCase):
    def setUp(self):
        monitor.TEMP_WARN = 70.0
        monitor.TEMP_CRIT = 90.0

    def test_normal(self):
        out = monitor.temp_color(50.0, False)
        self.assertIn("50.0", out)

    def test_warning(self):
        out = monitor.temp_color(75.0, True)
        self.assertIn(monitor.COLOR_YELLOW, out)

    def test_critical(self):
        out = monitor.temp_color(92.0, True)
        self.assertIn(monitor.COLOR_RED, out)


class TestPrintJson(unittest.TestCase):
    def test_valid_json_output(self):
        temps = [{"chip": "test", "label": "Sensor", "celsius": 42.0, "crit": None, "max": None, "source": "hwmon"}]
        fans = [{"chip": "test", "label": "Fan1", "rpm": 900, "source": "hwmon"}]
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            monitor.print_json(temps, fans)

        data = json.loads(buf.getvalue())
        self.assertIn("temperatures", data)
        self.assertIn("fans", data)
        self.assertEqual(data["temperatures"][0]["celsius"], 42.0)
        self.assertEqual(data["fans"][0]["rpm"], 900)


if __name__ == "__main__":
    unittest.main()
