from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("REED_DATA", ROOT / "data")).resolve()


def ensure() -> None:
    for name in ("models", "audio", "references", "tmp"):
        (DATA / name).mkdir(parents=True, exist_ok=True)


def models() -> Path:
    path = DATA / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def audio() -> Path:
    path = DATA / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def references() -> Path:
    path = DATA / "references"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tmp() -> Path:
    path = DATA / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    ensure()
    return DATA / "reed.sqlite"


def benchmarks_path() -> Path:
    ensure()
    return DATA / "benchmarks.json"


def model_dir(model_id: str) -> Path:
    safe = model_id.replace("/", "--")
    if not safe or "/" in safe or safe.startswith("."):
        raise ValueError("Invalid model id")
    root = models().resolve()
    dest = (root / safe).resolve()
    if dest.parent != root:
        raise ValueError("Invalid model path")
    return dest
