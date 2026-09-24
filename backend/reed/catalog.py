from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from .hardware import fit_for, recommend_quantization
from .paths import DATA, ensure

# Byte totals exclude README and .gitattributes. Measured from the Hugging Face
# model API on 2026-09-23 and refreshed when the repository can be reached.
FALLBACK_BYTES = {
    "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit": 1_973_572_801,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit": 3_080_138_901,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16": 4_520_193_026,
    "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit": 1_991_296_593,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit": 3_104_156_243,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16": 4_544_210_194,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit": 3_080_138_280,
    "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16": 4_520_192_405,
}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    task: str
    family: str
    parameters: str
    quantization: str
    official_repo: str
    supports_instruct: bool
    supports_speaker: bool
    supports_reference: bool
    summary: str
    recommend_key: str


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
        task="design",
        family="VoiceDesign",
        parameters="1.7B",
        quantization="8-bit",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        supports_instruct=True,
        supports_speaker=False,
        supports_reference=False,
        summary="1.7B VoiceDesign, 8-bit MLX conversion. The practical design checkpoint on a 16 GB Mac.",
        recommend_key="8bit",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16",
        task="design",
        family="VoiceDesign",
        parameters="1.7B",
        quantization="bfloat16",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        supports_instruct=True,
        supports_speaker=False,
        supports_reference=False,
        summary="1.7B VoiceDesign, bfloat16 MLX conversion. Higher precision, heavier download and memory use.",
        recommend_key="bf16",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        task="clone",
        family="Base",
        parameters="0.6B",
        quantization="8-bit",
        official_repo="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        supports_instruct=False,
        supports_speaker=False,
        supports_reference=True,
        summary="0.6B Base clone checkpoint, 8-bit MLX conversion. The lighter starting point.",
        recommend_key="0.6B-8bit",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        task="clone",
        family="Base",
        parameters="1.7B",
        quantization="8-bit",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        supports_instruct=False,
        supports_speaker=False,
        supports_reference=True,
        summary="1.7B Base clone checkpoint, 8-bit MLX conversion. Higher quality, heavier than 0.6B.",
        recommend_key="1.7B-8bit",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
        task="clone",
        family="Base",
        parameters="1.7B",
        quantization="bfloat16",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        supports_instruct=False,
        supports_speaker=False,
        supports_reference=True,
        summary="1.7B Base clone checkpoint, bfloat16 MLX conversion.",
        recommend_key="1.7B-bf16",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
        task="preset",
        family="CustomVoice",
        parameters="0.6B",
        quantization="8-bit",
        official_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        supports_instruct=False,
        supports_speaker=True,
        supports_reference=False,
        summary="0.6B CustomVoice presets, 8-bit MLX conversion. Published speakers, no delivery instructions.",
        recommend_key="0.6B-8bit",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        task="preset",
        family="CustomVoice",
        parameters="1.7B",
        quantization="8-bit",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        supports_instruct=True,
        supports_speaker=True,
        supports_reference=False,
        summary="1.7B CustomVoice presets, 8-bit MLX conversion. Published speakers plus delivery instructions.",
        recommend_key="1.7B-8bit",
    ),
    ModelSpec(
        id="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
        task="preset",
        family="CustomVoice",
        parameters="1.7B",
        quantization="bfloat16",
        official_repo="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        supports_instruct=True,
        supports_speaker=True,
        supports_reference=False,
        summary="1.7B CustomVoice presets, bfloat16 MLX conversion.",
        recommend_key="1.7B-bf16",
    ),
)

BY_ID = {model.id: model for model in MODELS}


def get_model(model_id: str) -> ModelSpec:
    model = BY_ID.get(model_id)
    if model is None:
        raise KeyError(model_id)
    return model


def manifest_cache_path():
    ensure()
    return DATA / "manifest_cache.json"


def load_byte_table() -> dict:
    path = manifest_cache_path()
    table = {
        model_id: {"bytes": size, "source": "huggingface-api-2026-09-23", "fetched_at": "2026-09-23"}
        for model_id, size in FALLBACK_BYTES.items()
    }
    if path.exists():
        try:
            saved = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            saved = {}
        for model_id, entry in saved.get("models", {}).items():
            if model_id in BY_ID and isinstance(entry.get("bytes"), int) and entry["bytes"] > 0:
                table[model_id] = entry
    return table


def save_byte_table(table: dict) -> None:
    manifest_cache_path().write_text(
        json.dumps({"models": table}, indent=2)
    )


def refresh_byte_table(timeout: float = 20) -> dict:
    table = load_byte_table()
    changed = False
    for model in MODELS:
        try:
            entry = _fetch_repo_bytes(model.id, timeout=timeout)
        except Exception:
            continue
        table[model.id] = entry
        changed = True
    if changed:
        save_byte_table(table)
    return table


def _fetch_repo_bytes(model_id: str, timeout: float) -> dict:
    url = f"https://huggingface.co/api/models/{model_id}?blobs=true"
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", str(max(5, int(timeout))), url],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "Hugging Face file list was unavailable")
    payload = json.loads(result.stdout)
    total = 0
    files = []
    for item in payload.get("siblings") or []:
        name = item.get("rfilename") or ""
        if name.endswith(".md") or name == ".gitattributes":
            continue
        size = int(item.get("size") or 0)
        total += size
        files.append({"path": name, "bytes": size})
    if total <= 0:
        raise RuntimeError(f"No file sizes for {model_id}")
    return {
        "bytes": total,
        "files": files,
        "source": "huggingface",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def describe(model: ModelSpec, memory_bytes: int, byte_table: dict, installed: bool, download: dict | None) -> dict:
    entry = byte_table.get(model.id, {})
    size = int(entry.get("bytes") or FALLBACK_BYTES.get(model.id) or 0)
    fit = fit_for(size, memory_bytes)
    recommended = recommend_quantization(model.task, memory_bytes) == model.recommend_key
    unsupported = []
    if model.task == "design":
        unsupported.append("Preset speakers and reference clips are not used. The description is the voice.")
    elif model.task == "clone":
        unsupported.append(
            "A Base checkpoint clones the reference clip. It does not take a written voice description or a preset speaker."
        )
        unsupported.append("Delivery instructions are not part of the Base checkpoints.")
    elif not model.supports_instruct:
        unsupported.append(
            "This 0.6B CustomVoice checkpoint does not support delivery instructions. "
            "The 1.7B CustomVoice checkpoint does."
        )
    return {
        "id": model.id,
        "task": model.task,
        "family": model.family,
        "parameters": model.parameters,
        "quantization": model.quantization,
        "official_repo": model.official_repo,
        "mlx_repo": model.id,
        "supports_instruct": model.supports_instruct,
        "supports_speaker": model.supports_speaker,
        "supports_reference": model.supports_reference,
        "summary": model.summary,
        "bytes": size,
        "bytes_source": entry.get("source") or "fallback",
        "bytes_fetched_at": entry.get("fetched_at"),
        "files": entry.get("files") or [],
        "installed": installed,
        "recommended": recommended,
        "fit": fit,
        "unsupported_notes": unsupported,
        "download": download,
    }
