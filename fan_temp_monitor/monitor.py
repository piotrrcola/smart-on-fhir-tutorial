#!/usr/bin/env python3
"""
macOS System Temperature and Fan Speed Monitor

Data sources (tried in order):
  1. powermetrics  – built-in Apple tool, reads SMC directly (requires sudo)
  2. istats        – optional Ruby gem  (gem install iStats)
  3. osx-cpu-temp  – optional Homebrew formula (brew install osx-cpu-temp)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Source 1: powermetrics  (sudo powermetrics --samplers smc -n 1)
# ---------------------------------------------------------------------------

def _run(cmd: list, timeout: int = 15) -> Optional[str]:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=timeout
        ).decode(errors="replace")
    except (subprocess.SubprocessError, FileNotFoundError, PermissionError):
        return None


def get_powermetrics() -> dict:
    """Parse SMC sensors from powermetrics output."""
    result = {"temperatures": [], "fans": [], "source": "powermetrics"}

    if not shutil.which("powermetrics"):
        return result

    # -i 100 = sample interval 100 ms (minimum), -n 1 = one sample
    if os.geteuid() == 0:
        cmd = ["powermetrics", "--samplers", "smc", "-n", "1", "-i", "100"]
    else:
        cmd = ["sudo", "-n", "powermetrics", "--samplers", "smc", "-n", "1", "-i", "100"]

    output = _run(cmd)
    if not output:
        # sudo -n failed (no cached credentials); try with a password prompt
        if os.geteuid() != 0:
            cmd_interactive = ["sudo", "powermetrics", "--samplers", "smc", "-n", "1", "-i", "100"]
            try:
                output = subprocess.check_output(
                    cmd_interactive, timeout=30
                ).decode(errors="replace")
            except (subprocess.SubprocessError, FileNotFoundError, PermissionError):
                return result

    if not output:
        return result

    in_smc = False
    fan_index = 0
    for line in output.splitlines():
        line = line.strip()

        if "SMC sensors" in line:
            in_smc = True
            fan_index = 0
            continue

        # Stop parsing SMC block when the next section starts
        if in_smc and line.startswith("****") and "SMC" not in line:
            break

        if not in_smc or not line:
            continue

        # Fan lines: "Fan: 1800 rpm"  or  "Fan 0: 1800 rpm"
        fan_match = re.match(r"^(Fan\s*\d*)\s*:\s*([\d.]+)\s*rpm", line, re.IGNORECASE)
        if fan_match:
            label = fan_match.group(1).strip() or f"Fan {fan_index}"
            rpm = int(float(fan_match.group(2)))
            result["fans"].append({"chip": "SMC", "label": label, "rpm": rpm})
            fan_index += 1
            continue

        # Temperature lines: "CPU die temperature: 68.31 C"
        temp_match = re.match(r"^(.+?):\s*([\d.]+)\s*C$", line)
        if temp_match:
            label = temp_match.group(1).strip()
            celsius = float(temp_match.group(2))
            result["temperatures"].append(
                {"chip": "SMC", "label": label, "celsius": celsius, "crit": None, "max": None}
            )

    return result


# ---------------------------------------------------------------------------
# Source 2: istats  (gem install iStats)
# ---------------------------------------------------------------------------

def get_istats() -> dict:
    result = {"temperatures": [], "fans": [], "source": "istats"}

    if not shutil.which("istats"):
        return result

    output = _run(["istats", "all", "--no-graphs"])
    if not output:
        output = _run(["istats", "all"])
    if not output:
        return result

    section = None
    for line in output.splitlines():
        line_stripped = line.strip()

        if re.match(r"^---\s*CPU Stats", line_stripped, re.IGNORECASE):
            section = "cpu"
        elif re.match(r"^---\s*Fan Stats", line_stripped, re.IGNORECASE):
            section = "fan"
        elif re.match(r"^---\s*Battery Stats", line_stripped, re.IGNORECASE):
            section = None
        elif re.match(r"^---\s*Extra Stats", line_stripped, re.IGNORECASE):
            section = "extra"

        if section in ("cpu", "extra"):
            # "CPU temp:         52.9°C"
            m = re.match(r"^(.+?):\s*([\d.]+)\s*[°º]?C", line_stripped)
            if m:
                label = m.group(1).strip()
                celsius = float(m.group(2))
                result["temperatures"].append(
                    {"chip": "iStats", "label": label, "celsius": celsius, "crit": None, "max": None}
                )

        if section == "fan":
            # "Fan 0 speed:      1196 RPM"
            m = re.match(r"^(Fan\s*\d+\s*\w*)\s*:\s*([\d.]+)\s*RPM", line_stripped, re.IGNORECASE)
            if m:
                label = m.group(1).strip()
                rpm = int(float(m.group(2)))
                result["fans"].append({"chip": "iStats", "label": label, "rpm": rpm})

    return result


# ---------------------------------------------------------------------------
# Source 3: osx-cpu-temp  (brew install osx-cpu-temp)
# ---------------------------------------------------------------------------

def get_osx_cpu_temp() -> dict:
    result = {"temperatures": [], "fans": [], "source": "osx-cpu-temp"}

    if not shutil.which("osx-cpu-temp"):
        return result

    output = _run(["osx-cpu-temp"])
    if not output:
        return result

    # Output is just "52.0°C\n"
    m = re.search(r"([\d.]+)\s*[°º]?C", output)
    if m:
        result["temperatures"].append(
            {
                "chip": "osx-cpu-temp",
                "label": "CPU temp",
                "celsius": float(m.group(1)),
                "crit": None,
                "max": None,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def collect_all() -> tuple:
    sources = [get_powermetrics(), get_istats(), get_osx_cpu_temp()]

    seen_temps: dict = {}
    seen_fans: dict = {}
    temps = []
    fans = []

    for src in sources:
        for t in src["temperatures"]:
            key = (t["chip"].lower(), t["label"].lower())
            if key not in seen_temps:
                seen_temps[key] = True
                t["source"] = src["source"]
                temps.append(t)
        for f in src["fans"]:
            key = (f["chip"].lower(), f["label"].lower())
            if key not in seen_fans:
                seen_fans[key] = True
                f["source"] = src["source"]
                fans.append(f)

    return temps, fans


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

TEMP_WARN = 70.0
TEMP_CRIT = 90.0

COLOR_RED    = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_GREEN  = "\033[92m"
COLOR_CYAN   = "\033[96m"
COLOR_BOLD   = "\033[1m"
COLOR_RESET  = "\033[0m"


def colorize(text: str, color: str, use_color: bool) -> str:
    return f"{color}{text}{COLOR_RESET}" if use_color else text


def temp_color(celsius: float, use_color: bool) -> str:
    if celsius >= TEMP_CRIT:
        return colorize(f"{celsius:.1f}°C", COLOR_RED, use_color)
    if celsius >= TEMP_WARN:
        return colorize(f"{celsius:.1f}°C", COLOR_YELLOW, use_color)
    return colorize(f"{celsius:.1f}°C", COLOR_GREEN, use_color)


def print_report(temps: list, fans: list, use_color: bool = True, compact: bool = False):
    print(colorize("=== System Temperature & Fan Monitor ===", COLOR_BOLD, use_color))
    print()

    temp_by_chip: dict = {}
    for t in temps:
        temp_by_chip.setdefault(t["chip"], []).append(t)

    fan_by_chip: dict = {}
    for f in fans:
        fan_by_chip.setdefault(f["chip"], []).append(f)

    for chip in sorted(set(list(temp_by_chip) + list(fan_by_chip))):
        print(f"  {colorize(chip, COLOR_CYAN, use_color)}")

        for t in temp_by_chip.get(chip, []):
            val = temp_color(t["celsius"], use_color)
            extras = []
            if t.get("max"):
                extras.append(f"max={t['max']:.0f}°C")
            if t.get("crit"):
                extras.append(f"crit={t['crit']:.0f}°C")
            extra_str = f"  ({', '.join(extras)})" if extras and not compact else ""
            print(f"    {t['label']:<35} {val}{extra_str}")

        for f in fan_by_chip.get(chip, []):
            if f["rpm"] == 0:
                rpm_str = colorize("0 RPM  (stopped / fanless)", COLOR_YELLOW, use_color)
            else:
                rpm_str = f"{f['rpm']} RPM"
            print(f"    {f['label']:<35} {rpm_str}")

        print()


def print_json(temps: list, fans: list):
    import json
    print(json.dumps({"temperatures": temps, "fans": fans}, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor macOS fan speeds and temperatures (SMC via powermetrics/istats)."
    )
    parser.add_argument("--json",     action="store_true", help="Output as JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour output")
    parser.add_argument("--compact",  action="store_true", help="Suppress threshold annotations")
    parser.add_argument(
        "--watch", metavar="SECONDS", type=float, default=0,
        help="Refresh every N seconds, e.g. --watch 2"
    )
    parser.add_argument(
        "--warn", metavar="TEMP", type=float, default=TEMP_WARN,
        help=f"Warning threshold °C (default {TEMP_WARN})"
    )
    parser.add_argument(
        "--crit", metavar="TEMP", type=float, default=TEMP_CRIT,
        help=f"Critical threshold °C (default {TEMP_CRIT})"
    )
    return parser.parse_args()


def main():
    global TEMP_WARN, TEMP_CRIT
    args = parse_args()
    TEMP_WARN = args.warn
    TEMP_CRIT = args.crit
    use_color = not args.no_color and os.isatty(sys.stdout.fileno())

    def run_once():
        temps, fans = collect_all()
        if not temps and not fans:
            print(
                "No sensor data found.\n"
                "Try one of:\n"
                "  sudo python3 monitor.py          # use powermetrics (most complete)\n"
                "  gem install iStats               # no-sudo option\n"
                "  brew install osx-cpu-temp        # CPU temp only\n"
            )
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
            print()
    else:
        run_once()


if __name__ == "__main__":
    main()
