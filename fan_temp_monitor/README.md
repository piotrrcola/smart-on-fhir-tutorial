# System Temperature & Fan Speed Monitor (macOS)

Reads all available SMC temperature and fan data on macOS, with color-coded terminal output, JSON mode, and a live watch mode.

## Data Sources

| Source | How to get it | Notes |
|---|---|---|
| `powermetrics` | Built-in (ships with macOS) | Most complete — requires `sudo` |
| `istats` | `gem install iStats` | No sudo needed |
| `osx-cpu-temp` | `brew install osx-cpu-temp` | CPU temp only, no sudo |

All three are tried automatically; results are deduplicated (powermetrics wins on overlap).

## Quick Start

```bash
# Clone the repo and check out the branch
git clone https://github.com/piotrrcola/smart-on-fhir-tutorial.git
cd smart-on-fhir-tutorial
git checkout claude/system-temp-fan-monitor-CVolA

# Live watch — refreshes every 2 seconds (most complete output)
sudo python3 fan_temp_monitor/monitor.py --watch 2

# Or without sudo (requires istats or osx-cpu-temp installed)
python3 fan_temp_monitor/monitor.py --watch 2
```

## All Options

```
usage: monitor.py [-h] [--json] [--no-color] [--compact] [--watch SECONDS] [--warn TEMP] [--crit TEMP]

  --watch SECONDS   Refresh every N seconds (e.g. --watch 2)
  --json            Machine-readable JSON output
  --no-color        Disable ANSI colour (for log files)
  --compact         Hide max/crit threshold annotations
  --warn TEMP       Warning threshold °C  (default: 70)
  --crit TEMP       Critical threshold °C (default: 90)
```

## Example Output

```
=== System Temperature & Fan Monitor ===

  SMC
    CPU die temperature             68.3°C  (no threshold data from SMC)
    GPU die temperature             55.0°C
    CPU Proximity                   41.0°C
    Mem Proximity                   35.0°C
    Fan                             1800 RPM
```

Colours:
- **Green** — normal (below `--warn`, default 70 °C)
- **Yellow** — warm (≥ `--warn`)
- **Red** — hot (≥ `--crit`, default 90 °C)
- **Yellow** — fan stopped / 0 RPM

## Notes

- **Fanless Macs** (MacBook Air M1/M2 etc.) will show no fan entries — that's expected.
- **Apple Silicon** temperatures are available via `powermetrics`; sensor names may differ from Intel.
- `sudo` password is cached by macOS for ~5 minutes, so `--watch` mode won't re-prompt on each refresh.

## Running Tests

```bash
python3 -m unittest fan_temp_monitor/test_monitor.py -v
```
