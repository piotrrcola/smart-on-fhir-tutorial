# System Temperature & Fan Speed Monitor

Reads all available hardware sensor data on Linux and displays temperatures and fan speeds in a clear, color-coded report.

## Data Sources

| Source | What it covers |
|---|---|
| `/sys/class/hwmon/` | CPU, GPU, mainboard, NVMe, PSU sensors via the kernel hwmon subsystem |
| `/sys/class/thermal/` | ACPI / kernel thermal zones |
| `ipmitool` | Server IPMI sensors (BMC) — optional |
| `sensors` (lm-sensors) | Cross-checks hwmon data and adds chip-specific labels — optional |

## Requirements

- Python 3.7+
- Linux kernel with `hwmon` / `thermal` sysfs support (standard on all modern kernels)
- *(Optional)* `lm-sensors` — `sudo apt install lm-sensors`
- *(Optional)* `ipmitool` — `sudo apt install ipmitool` (server hardware only)

## Usage

```bash
# Basic report
python3 fan_temp_monitor/monitor.py

# JSON output (pipe-friendly)
python3 fan_temp_monitor/monitor.py --json

# Continuous watch mode (refresh every 2 seconds)
python3 fan_temp_monitor/monitor.py --watch 2

# Custom warning/critical thresholds
python3 fan_temp_monitor/monitor.py --warn 75 --crit 95

# No color (for log files)
python3 fan_temp_monitor/monitor.py --no-color

# Compact (hide threshold annotations)
python3 fan_temp_monitor/monitor.py --compact
```

## Example Output

```
=== System Temperature & Fan Monitor ===

  coretemp
    Package id 0                   52.0°C  (max=100°C, crit=100°C)
    Core 0                         48.0°C
    Core 1                         50.0°C

  nct6795
    SYSTIN                         33.0°C
    CPUTIN                         51.0°C
    Fan1                           1200 RPM
    Fan2                           980 RPM

  nvme
    Composite                      38.9°C  (crit=84°C)
```

Temperatures are color-coded:
- **Green** — normal (below `--warn` threshold, default 70 °C)
- **Yellow** — warm (at or above `--warn`)
- **Red** — hot (at or above `--crit`, default 90 °C)

## Running Tests

```bash
python3 -m pytest fan_temp_monitor/test_monitor.py -v
# or
python3 -m unittest fan_temp_monitor/test_monitor.py
```

## Notes

- No root access is required for hwmon/thermal sysfs reads on most distros.
- IPMI access may require root or membership in the `ipmi` group.
- On systems without any readable sensor data the script exits with a helpful message.
