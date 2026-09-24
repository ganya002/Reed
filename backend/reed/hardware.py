from __future__ import annotations

import json
import platform
import subprocess
import time
from shutil import disk_usage

from .paths import DATA, ensure

_CACHE: dict | None = None
_CACHE_AT = 0.0


def detect(force: bool = False) -> dict:
    global _CACHE, _CACHE_AT
    now = time.time()
    if _CACHE is not None and not force and now - _CACHE_AT < 20:
        usage = disk_usage(DATA if DATA.exists() else DATA.parent)
        cached = dict(_CACHE)
        cached["disk_free_bytes"] = usage.free
        cached["disk_total_bytes"] = usage.total
        return cached
    ensure()
    profile = _hardware_profile()
    memory = _memory_bytes()
    usage = disk_usage(DATA)
    sw = _sw_vers()
    chip = profile.get("chip") or _sysctl("machdep.cpu.brand_string") or "Unknown"
    info = {
        "chip": chip,
        "model_name": profile.get("model_name") or "",
        "model_identifier": profile.get("model_identifier") or "",
        "unified_memory_bytes": memory,
        "memory_label": profile.get("memory_label") or _format_bytes(memory),
        "macos_name": sw.get("ProductName", platform.system()),
        "macos_version": sw.get("ProductVersion", platform.mac_ver()[0]),
        "macos_build": sw.get("BuildVersion", ""),
        "arch": platform.machine(),
        "apple_silicon": platform.machine() == "arm64" and platform.system() == "Darwin",
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "disk_path": str(DATA),
        "detected_with": [
            "system_profiler SPHardwareDataType",
            "sysctl hw.memsize",
            "sw_vers",
            "disk free space on the Reed data directory",
        ],
    }
    _CACHE = info
    _CACHE_AT = now
    return dict(info)


def fit_for(checkpoint_bytes: int, memory_bytes: int) -> dict:
    """Studio guidance from the memory Reed detected. Not an official Qwen requirement."""
    if memory_bytes <= 0:
        return {
            "level": "unknown",
            "label": "Memory was not detected",
            "detail": "Reed could not read unified memory, so it will not guess whether this checkpoint fits.",
        }
    memory_gb = memory_bytes / (1024**3)
    checkpoint_gb = checkpoint_bytes / (1024**3)
    if checkpoint_gb <= 2.3 and memory_gb >= 16:
        level = "comfortable"
        label = "Comfortable"
        detail = (
            f"On the {memory_gb:.0f} GB Reed detected, this lighter checkpoint should sit "
            "comfortably beside a normal desktop."
        )
    elif checkpoint_gb <= 3.5 and memory_gb >= 24:
        level = "comfortable"
        label = "Comfortable"
        detail = (
            f"On the {memory_gb:.0f} GB Reed detected, this 1.7B checkpoint should load "
            "with room for other work."
        )
    elif checkpoint_gb <= 3.5 and memory_gb >= 16:
        level = "can_load"
        label = "Can load"
        detail = (
            f"This can load on the {memory_gb:.0f} GB Reed detected. It may compete with "
            "other apps for unified memory while it generates."
        )
    elif checkpoint_gb <= 5.2 and memory_gb >= 32:
        level = "comfortable"
        label = "Comfortable"
        detail = (
            f"On the {memory_gb:.0f} GB Reed detected, the bfloat16 checkpoint should load cleanly."
        )
    elif checkpoint_gb <= 5.2 and memory_gb >= 16:
        level = "competes"
        label = "May compete"
        detail = (
            f"This can load on the {memory_gb:.0f} GB Reed detected, and it is likely to "
            "compete with other apps for unified memory."
        )
    else:
        level = "tight"
        label = "Tight fit"
        detail = (
            "This checkpoint is large relative to the unified memory Reed detected. "
            "It may fail to load while other apps are open."
        )
    return {"level": level, "label": label, "detail": detail}


def recommend_quantization(task: str, memory_bytes: int) -> str:
    memory_gb = memory_bytes / (1024**3) if memory_bytes else 0
    if task == "design":
        return "bf16" if memory_gb >= 32 else "8bit"
    if memory_gb >= 24:
        return "1.7B-8bit"
    return "0.6B-8bit"


def _memory_bytes() -> int:
    raw = _sysctl("hw.memsize")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _sysctl(key: str) -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def _sw_vers() -> dict[str, str]:
    try:
        result = subprocess.run(
            ["sw_vers"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.strip().replace(" ", "")] = value.strip()
    return info


def _hardware_profile() -> dict[str, str]:
    try:
        result = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return _parse_hardware_text("")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    items = payload.get("SPHardwareDataType") or []
    if not items:
        return {}
    item = items[0]
    return {
        "chip": item.get("chip_type") or "",
        "model_name": item.get("machine_name") or "",
        "model_identifier": item.get("machine_model") or "",
        "memory_label": item.get("physical_memory") or "",
    }


def _parse_hardware_text(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        found[key.strip()] = value.strip()
    return {
        "chip": found.get("Chip", ""),
        "model_name": found.get("Model Name", ""),
        "model_identifier": found.get("Model Identifier", ""),
        "memory_label": found.get("Memory", ""),
    }


def _format_bytes(size: int) -> str:
    if size <= 0:
        return "Unknown"
    gib = size / (1024**3)
    return f"{gib:.0f} GB"
