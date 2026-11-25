"""Temperaturen en CPU-mogelijkheden rechtstreeks uit sysfs (geen daemon nodig)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

MODEL_CLOCK_RE = re.compile(r"@\s*([0-9]+(?:\.[0-9]+)?)\s*(GHz|MHz)", re.IGNORECASE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def temperatures(base: str | Path = "/") -> list[dict[str, Any]]:
    """Geeft [{"label": "Core 0", "celsius": 59.0}, ...] terug."""
    root = Path(base)
    found: list[dict[str, Any]] = []
    for sensor in sorted((root / "sys/class/hwmon").glob("hwmon*/temp*_input")):
        raw = _read(sensor)
        if not raw:
            continue
        try:
            celsius = round(int(raw) / 1000, 1)
        except ValueError:
            continue
        label = _read(sensor.with_name(sensor.name.replace("_input", "_label")))
        found.append({"label": label or sensor.stem.replace("_input", ""), "celsius": celsius})
    if found:
        return found
    # Fallback: ACPI-thermal zones (minder precies, maar bijna altijd aanwezig).
    for zone in sorted((root / "sys/class/thermal").glob("thermal_zone*/temp")):
        raw = _read(zone)
        if not raw:
            continue
        try:
            label = _read(zone.with_name("type")) or zone.parent.name
            found.append({"label": label, "celsius": round(int(raw) / 1000, 1)})
        except ValueError:
            continue
    return found


def nominal_mhz(base: str | Path = "/") -> int | None:
    """Nominale klok (zonder turbo) uit de model name in /proc/cpuinfo."""
    for line in _read(Path(base) / "proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            match = MODEL_CLOCK_RE.search(line)
            if not match:
                return None
            value = float(match.group(1))
            return int(
                value * 1000 if match.group(2).lower() == "ghz" else value
            )
    return None


def cpu_info(base: str | Path = "/") -> dict[str, Any]:
    """Huidige governor/frequenties en de waarden die cpupower kan instellen."""
    root = Path(base)
    freq = root / "sys/devices/system/cpu/cpu0/cpufreq"
    values = {
        int(value) // 1000
        for value in _read(freq / "scaling_available_frequencies").split()
        if value.isdigit()
    }
    available = sorted(values)
    nominal = nominal_mhz(base)
    info: dict[str, Any] = {
        "available": freq.is_dir(),
        "governor": _read(freq / "scaling_governor"),
        "min_mhz": None,
        "max_mhz": None,
        "available_mhz": available,
        "available_governors": [
            value for value in _read(freq / "scaling_available_governors").split() if value
        ],
        "cpupower": shutil.which("cpupower") is not None,
        "nominal_mhz": nominal,
        "suggested_min_mhz": None,
        "suggested_max_mhz": nominal,
    }
    for key, target in (("min_mhz", "scaling_min_freq"), ("max_mhz", "scaling_max_freq")):
        raw = _read(freq / target)
        if raw.isdigit():
            info[key] = int(raw) // 1000
    if nominal:
        if available:
            info["suggested_min_mhz"] = min(
                available, key=lambda value: abs(value - nominal / 2)
            )
            info["suggested_max_mhz"] = min(
                available, key=lambda value: abs(value - nominal)
            )
        else:
            info["suggested_min_mhz"] = nominal // 2
    return info
