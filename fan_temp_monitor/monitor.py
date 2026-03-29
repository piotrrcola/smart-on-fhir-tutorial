#!/usr/bin/env python3
"""
System Temperature and Fan Speed Monitor
Reads data from hwmon, thermal zones, ACPI, and IPMI sources.
"""

import os
import glob
import subprocess
import shutil
import argparse
import time
from typing import Optional


def read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def millidegrees_to_celsius(value: str) -> Optional[float]:
    try:
        return int(value) / 1000.0
    except (ValueError, TypeError):
        return None


def rpm_value(value: str) -> Optional[int]:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# hwmon subsystem
# ---------------------------------------------------------------------------

def get_hwmon_sensors():
    """Read all temperature and fan sensors from /sys/class/hwmon/."""
    results = {"temperatures": [], "fans": []}

    for hwmon_path in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = read_file(os.path.join(hwmon_path, "name")) or os.path.basename(hwmon_path)

        # Temperatures: tempN_input / tempN_label
        for temp_input in sorted(glob.glob(os.path.join(hwmon_path, "temp*_input"))):
            index = os.path.basename(temp_input).split("_")[0]  # e.g. "temp1"
            label_path = os.path.join(hwmon_path, f"{index}_label")
            label = read_file(label_path) or index
            raw = read_file(temp_input)
            celsius = millidegrees_to_celsius(raw) if raw else None

            crit_path = os.path.join(hwmon_path, f"{index}_crit")
            crit_raw = read_file(crit_path)
            crit = millidegrees_to_celsius(crit_raw) if crit_raw else None

            max_path = os.path.join(hwmon_path, f"{index}_max")
            max_raw = read_file(max_path)
            temp_max = millidegrees_to_celsius(max_raw) if max_raw else None

            if celsius is not None:
                results["temperatures"].append(
                    {
                        "source": "hwmon",
                        "chip": name,
                        "label": label,
                        "celsius": celsius,
                        "crit": crit,
                        "max": temp_max,
                    }
                )

        # Fans: fanN_input / fanN_label
        for fan_input in sorted(glob.glob(os.path.join(hwmon_path, "fan*_input"))):
            index = os.path.basename(fan_input).split("_")[0]  # e.g. "fan1"
            label_path = os.path.join(hwmon_path, f"{index}_label")
            label = read_file(label_path) or index
            raw = read_file(fan_input)
            rpm = rpm_value(raw) if raw else None

            if rpm is not None:
                results["fans"].append(
                    {
                        "source": "hwmon",
                        "chip": name,
                        "label": label,
                        "rpm": rpm,
                    }
                )

    return results


# ---------------------------------------------------------------------------
# Thermal zones (/sys/class/thermal/)
# ---------------------------------------------------------------------------

def get_thermal_zones():
    """Read ACPI / kernel thermal zones."""
    temps = []
    for zone_path in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        zone_name = os.path.basename(zone_path)
        zone_type = read_file(os.path.join(zone_path, "type")) or zone_name
        raw = read_file(os.path.join(zone_path, "temp"))
        celsius = millidegrees_to_celsius(raw) if raw else None
        if celsius is not None:
            temps.append(
                {
                    "source": "thermal_zone",
                    "chip": zone_type,
                    "label": zone_name,
                    "celsius": celsius,
                    "crit": None,
                    "max": None,
                }
            )
    return temps


# ---------------------------------------------------------------------------
# IPMI (ipmitool)
# ---------------------------------------------------------------------------

def get_ipmi_sensors():
    """Query IPMI sensors via ipmitool if available."""
    results = {"temperatures": [], "fans": []}
    if not shutil.which("ipmitool"):
        return results

    try:
        output = subprocess.check_output(
            ["ipmitool", "sdr", "type", "Temperature"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode()
    except (subprocess.SubprocessError, FileNotFoundError):
        return results

    for line in output.splitlines():
        # Format: Sensor Name      | Sensor ID | Status | Entity ID | Value
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        label = parts[0]
        value_str = parts[4]
        if "degrees" in value_str.lower() or "C" in value_str:
            try:
                celsius = float(value_str.split()[0])
                results["temperatures"].append(
                    {
                        "source": "ipmi",
                        "chip": "IPMI",
                        "label": label,
                        "celsius": celsius,
                        "crit": None,
                        "max": None,
                    }
                )
            except ValueError:
                pass

    try:
        fan_output = subprocess.check_output(
            ["ipmitool", "sdr", "type", "Fan"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode()
    except (subprocess.SubprocessError, FileNotFoundError):
        return results

    for line in fan_output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        label = parts[0]
        value_str = parts[4]
        if "RPM" in value_str.upper():
            try:
                rpm = int(float(value_str.split()[0]))
                results["fans"].append(
                    {"source": "ipmi", "chip": "IPMI", "label": label, "rpm": rpm}
                )
            except ValueError:
                pass

    return results


# ---------------------------------------------------------------------------
# lm-sensors (sensors command)
# ---------------------------------------------------------------------------

def get_lm_sensors():
    """Parse output of `sensors -j` if lm-sensors is installed."""
    results = {"temperatures": [], "fans": []}
    if not shutil.which("sensors"):
        return results

    try:
        import json

        output = subprocess.check_output(
            ["sensors", "-j"], stderr=subprocess.DEVNULL, timeout=10
        ).decode()
        data = json.loads(output)
    except Exception:
        return results

    for chip_name, chip_data in data.items():
        if not isinstance(chip_data, dict):
            continue
        for feature_name, feature_data in chip_data.items():
            if not isinstance(feature_data, dict):
                continue
            for subkey, value in feature_data.items():
                if not isinstance(value, (int, float)):
                    continue
                if "_input" in subkey:
                    if "temp" in subkey.lower() or "temp" in feature_name.lower():
                        results["temperatures"].append(
                            {
                                "source": "lm-sensors",
                                "chip": chip_name,
                                "label": feature_name,
                                "celsius": float(value),
                                "crit": feature_data.get(subkey.replace("_input", "_crit")),
                                "max": feature_data.get(subkey.replace("_input", "_max")),
                            }
                        )
                    elif "fan" in subkey.lower() or "fan" in feature_name.lower():
                        results["fans"].append(
                            {
                                "source": "lm-sensors",
                                "chip": chip_name,
                                "label": feature_name,
                                "rpm": int(value),
                            }
                        )

    return results


# ---------------------------------------------------------------------------
# Aggregation & deduplication
# ---------------------------------------------------------------------------

def collect_all():
    hwmon = get_hwmon_sensors()
    thermal = get_thermal_zones()
    ipmi = get_ipmi_sensors()
    lm = get_lm_sensors()

    temps = hwmon["temperatures"] + thermal + ipmi["temperatures"] + lm["temperatures"]
    fans = hwmon["fans"] + ipmi["fans"] + lm["fans"]

    # Deduplicate: prefer hwmon over lm-sensors for the same (chip, label) pair
    seen_temps = {}
    deduped_temps = []
    for t in temps:
        key = (t["chip"].lower(), t["label"].lower())
        if key not in seen_temps:
            seen_temps[key] = True
            deduped_temps.append(t)

    seen_fans = {}
    deduped_fans = []
    for f in fans:
        key = (f["chip"].lower(), f["label"].lower())
        if key not in seen_fans:
            seen_fans[key] = True
            deduped_fans.append(f)

    return deduped_temps, deduped_fans


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

TEMP_WARN = 70.0
TEMP_CRIT = 90.0

COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_GREEN = "\033[92m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"


def colorize(text: str, color: str, use_color: bool) -> str:
    return f"{color}{text}{COLOR_RESET}" if use_color else text


def temp_color(celsius: float, use_color: bool) -> str:
    if celsius >= TEMP_CRIT:
        return colorize(f"{celsius:.1f}°C", COLOR_RED, use_color)
    if celsius >= TEMP_WARN:
        return colorize(f"{celsius:.1f}°C", COLOR_YELLOW, use_color)
    return colorize(f"{celsius:.1f}°C", COLOR_GREEN, use_color)


def print_report(temps, fans, use_color: bool = True, compact: bool = False):
    header = colorize("=== System Temperature & Fan Monitor ===", COLOR_BOLD, use_color)
    print(header)
    print()

    # Group by chip
    temp_by_chip: dict = {}
    for t in temps:
        temp_by_chip.setdefault(t["chip"], []).append(t)

    fan_by_chip: dict = {}
    for f in fans:
        fan_by_chip.setdefault(f["chip"], []).append(f)

    all_chips = sorted(set(list(temp_by_chip) + list(fan_by_chip)))

    for chip in all_chips:
        chip_label = colorize(chip, COLOR_CYAN, use_color)
        print(f"  {chip_label}")

        for t in temp_by_chip.get(chip, []):
            val = temp_color(t["celsius"], use_color)
            extras = []
            if t["max"]:
                extras.append(f"max={t['max']:.0f}°C")
            if t["crit"]:
                extras.append(f"crit={t['crit']:.0f}°C")
            extra_str = f"  ({', '.join(extras)})" if extras and not compact else ""
            print(f"    {t['label']:<30} {val}{extra_str}")

        for f in fan_by_chip.get(chip, []):
            rpm_str = f"{f['rpm']} RPM"
            if f["rpm"] == 0:
                rpm_str = colorize("0 RPM (stopped)", COLOR_YELLOW, use_color)
            print(f"    {f['label']:<30} {rpm_str}")

        print()


def print_json(temps, fans):
    import json

    print(json.dumps({"temperatures": temps, "fans": fans}, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor system fan speeds and temperatures."
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    parser.add_argument(
        "--compact", action="store_true", help="Suppress threshold annotations"
    )
    parser.add_argument(
        "--watch", metavar="SECONDS", type=float, default=0,
        help="Repeat every N seconds (e.g. --watch 2)"
    )
    parser.add_argument(
        "--warn", metavar="TEMP", type=float, default=TEMP_WARN,
        help=f"Warning threshold in °C (default: {TEMP_WARN})"
    )
    parser.add_argument(
        "--crit", metavar="TEMP", type=float, default=TEMP_CRIT,
        help=f"Critical threshold in °C (default: {TEMP_CRIT})"
    )
    return parser.parse_args()


def main():
    global TEMP_WARN, TEMP_CRIT
    args = parse_args()
    TEMP_WARN = args.warn
    TEMP_CRIT = args.crit
    use_color = not args.no_color and os.isatty(1)

    def run_once():
        temps, fans = collect_all()
        if not temps and not fans:
            print("No sensor data found. Try running as root or install lm-sensors / ipmitool.")
            return
        if args.json:
            print_json(temps, fans)
        else:
            print_report(temps, fans, use_color=use_color, compact=args.compact)

    if args.watch:
        try:
            while True:
                if not args.json:
                    os.system("clear")
                run_once()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        run_once()


if __name__ == "__main__":
    main()
