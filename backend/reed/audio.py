from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

MAX_REFERENCE_BYTES = 25 * 1024 * 1024
MIN_REFERENCE_SECONDS = 1.2
MAX_REFERENCE_SECONDS = 25.0
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm", ".caf", ".mp4"}


class AudioError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def mp3_supported() -> bool:
    if not ffmpeg_available():
        return False
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "libmp3lame" in result.stdout


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1:
        samples = samples / peak
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise AudioError("Reed expected 16-bit WAV audio.")
    pcm = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    audio = pcm.astype(np.float32) / 32767.0
    return audio, sample_rate


def peaks(audio: np.ndarray, buckets: int = 140) -> list[float]:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return [0.0] * buckets
    edges = np.linspace(0, samples.size, buckets + 1, dtype=int)
    values = []
    for start, end in zip(edges[:-1], edges[1:]):
        chunk = samples[start:end]
        values.append(float(np.max(np.abs(chunk))) if chunk.size else 0.0)
    peak = max(values) or 1.0
    return [round(value / peak, 4) for value in values]


def join_segments(segments: list[np.ndarray], sample_rate: int, gap_ms: int = 70, fade_ms: int = 8) -> np.ndarray:
    usable = [np.asarray(segment, dtype=np.float32).reshape(-1) for segment in segments if len(segment)]
    if not usable:
        return np.zeros(0, dtype=np.float32)
    if len(usable) == 1:
        return usable[0]
    gap = np.zeros(int(sample_rate * gap_ms / 1000), dtype=np.float32)
    fade = max(1, int(sample_rate * fade_ms / 1000))
    pieces: list[np.ndarray] = []
    for index, segment in enumerate(usable):
        segment = segment.copy()
        if segment.size > fade * 2:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            if index > 0:
                segment[:fade] *= ramp
            if index < len(usable) - 1:
                segment[-fade:] *= ramp[::-1]
        pieces.append(segment)
        if index < len(usable) - 1:
            pieces.append(gap)
    return np.concatenate(pieces)


def apply_pace(wav_path: Path, pace: float) -> None:
    """Change duration without changing pitch. Pace is not a model control."""
    if abs(pace - 1) < 0.01:
        return
    if not ffmpeg_available():
        raise AudioError("Pace needs ffmpeg, which Reed did not find.")
    paced = wav_path.with_suffix(".pace.wav")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-filter:a",
            f"atempo={pace:.2f}",
            "-c:a",
            "pcm_s16le",
            str(paced),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not paced.is_file():
        paced.unlink(missing_ok=True)
        raise AudioError("Pace could not be applied to this take.")
    paced.replace(wav_path)


def to_mp3(wav_path: Path, mp3_path: Path) -> None:
    if not mp3_supported():
        raise AudioError("MP3 export needs ffmpeg with libmp3lame, which is not available.")
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not mp3_path.is_file():
        raise AudioError("MP3 export failed.")


def prepare_reference(source: Path, dest_wav: Path, suffix: str, size: int) -> dict:
    if size <= 0:
        raise AudioError("The reference file is empty.")
    if size > MAX_REFERENCE_BYTES:
        raise AudioError("Keep the reference clip under 25 MB.")
    if suffix.lower() not in ALLOWED_SUFFIXES:
        raise AudioError("Use WAV, MP3, M4A, FLAC, OGG, or a browser recording.")
    if not ffmpeg_available():
        raise AudioError("Reading reference clips needs ffmpeg and ffprobe.")
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(dest_wav),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not dest_wav.is_file():
        logging.getLogger("reed.audio").warning(
            "reference conversion failed: %s",
            (result.stderr or "")[-400:],
        )
        if dest_wav.exists():
            dest_wav.unlink()
        raise AudioError("This file is not a readable audio clip.")
    duration = probe_duration(dest_wav)
    if duration < MIN_REFERENCE_SECONDS:
        dest_wav.unlink(missing_ok=True)
        raise AudioError(
            "This clip is too short for a stable clone. Record at least a couple of seconds of clear speech."
        )
    if duration > MAX_REFERENCE_SECONDS:
        dest_wav.unlink(missing_ok=True)
        raise AudioError("Keep the reference under 25 seconds. Three to twelve seconds is the useful range.")
    audio, _ = read_wav(dest_wav)
    warning = ""
    if duration < 3 or duration > 12:
        warning = "Three to twelve seconds of clean speech usually clones more reliably than a very short or long clip."
    return {"duration": duration, "peaks": peaks(audio), "warning": warning}


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioError("Reed could not read the length of this audio.") from exc


def scale_markers(markers: list[dict], pace: float) -> list[dict]:
    if len(markers) < 2:
        return []
    if abs(pace - 1) < 0.01:
        return markers
    return [{**marker, "start": round(float(marker["start"]) / pace, 3)} for marker in markers]
