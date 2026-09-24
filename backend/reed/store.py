from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .paths import audio, db_path, references


class Store:
    def __init__(self) -> None:
        self.path = db_path()
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    script TEXT NOT NULL,
                    language TEXT NOT NULL,
                    language_note TEXT NOT NULL,
                    voice_description TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    instruct TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    duration_sec REAL NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    load_sec REAL,
                    first_audio_sec REAL,
                    generate_sec REAL,
                    peaks_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voices (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    language TEXT NOT NULL,
                    voice_description TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    pace REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS say_as (
                    id TEXT PRIMARY KEY,
                    written TEXT NOT NULL,
                    spoken TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS references_audio (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    duration_sec REAL NOT NULL,
                    warning TEXT NOT NULL,
                    peaks_json TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(generations)")}
            if "markers_json" not in columns:
                self.conn.execute(
                    "ALTER TABLE generations ADD COLUMN markers_json TEXT NOT NULL DEFAULT '[]'"
                )
            self.conn.commit()

    def add_reference(self, original_name: str, transcript: str, duration: float, warning: str, peak_values: list[float]) -> dict:
        ref_id = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO references_audio
                (id, created_at, original_name, transcript, duration_sec, warning, peaks_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ref_id, now, original_name, transcript, duration, warning, json.dumps(peak_values)),
            )
            self.conn.commit()
        return self.get_reference(ref_id)

    def get_reference(self, ref_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM references_audio WHERE id = ?",
                (ref_id,),
            ).fetchone()
        if row is None:
            return None
        return _reference_row(row)

    def list_references(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM references_audio ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [_reference_row(row) for row in rows]

    def delete_reference(self, ref_id: str) -> bool:
        path = reference_wav(ref_id)
        with self._lock:
            cur = self.conn.execute("DELETE FROM references_audio WHERE id = ?", (ref_id,))
            self.conn.commit()
            deleted = cur.rowcount > 0
        if path.exists():
            path.unlink()
        return deleted

    def add_generation(self, payload: dict) -> dict:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO generations (
                    id, created_at, mode, script, language, language_note, voice_description,
                    speaker, instruct, model_id, reference_id, duration_sec, sample_rate,
                    load_sec, first_audio_sec, generate_sec, peaks_json, markers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["created_at"],
                    payload["mode"],
                    payload["script"],
                    payload["language"],
                    payload.get("language_note") or "",
                    payload.get("voice_description") or "",
                    payload.get("speaker") or "",
                    payload.get("instruct") or "",
                    payload["model_id"],
                    payload.get("reference_id") or "",
                    payload["duration_sec"],
                    payload["sample_rate"],
                    payload.get("load_sec"),
                    payload.get("first_audio_sec"),
                    payload.get("generate_sec"),
                    json.dumps(payload.get("peaks") or []),
                    json.dumps(payload.get("markers") or []),
                ),
            )
            self.conn.commit()
        return self.get_generation(payload["id"])

    def get_generation(self, gen_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM generations WHERE id = ?", (gen_id,)).fetchone()
        if row is None:
            return None
        return _generation_row(row)

    def list_generations(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM generations ORDER BY created_at DESC LIMIT 40"
            ).fetchall()
        return [_generation_row(row) for row in rows]

    def add_voice(self, name: str, mode: str, language: str, description: str, speaker: str, reference_id: str, pace: float) -> dict:
        voice_id = uuid.uuid4().hex
        with self._lock:
            count = self.conn.execute("SELECT COUNT(*) AS n FROM voices").fetchone()["n"]
            if count >= 30:
                raise ValueError("Reed keeps up to 30 saved voices. Delete one before saving another.")
            self.conn.execute(
                """
                INSERT INTO voices
                (id, created_at, name, mode, language, voice_description, speaker, reference_id, pace)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (voice_id, _now(), name, mode, language, description, speaker, reference_id, pace),
            )
            self.conn.commit()
        return self.get_voice(voice_id)

    def get_voice(self, voice_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM voices WHERE id = ?", (voice_id,)).fetchone()
        if row is None:
            return None
        return _voice_row(row)

    def list_voices(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM voices ORDER BY created_at DESC").fetchall()
        return [_voice_row(row) for row in rows]

    def delete_voice(self, voice_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM voices WHERE id = ?", (voice_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def add_say_as(self, written: str, spoken: str) -> dict:
        say_id = uuid.uuid4().hex
        with self._lock:
            count = self.conn.execute("SELECT COUNT(*) AS n FROM say_as").fetchone()["n"]
            if count >= 40:
                raise ValueError("Reed keeps up to 40 pronunciations.")
            existing = self.conn.execute(
                "SELECT id FROM say_as WHERE lower(written) = lower(?)",
                (written,),
            ).fetchone()
            if existing is not None:
                self.conn.execute(
                    "UPDATE say_as SET spoken = ? WHERE id = ?",
                    (spoken, existing["id"]),
                )
                self.conn.commit()
                say_id = existing["id"]
            else:
                self.conn.execute(
                    "INSERT INTO say_as (id, written, spoken) VALUES (?, ?, ?)",
                    (say_id, written, spoken),
                )
                self.conn.commit()
        return self.get_say_as(say_id)

    def get_say_as(self, say_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM say_as WHERE id = ?", (say_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "written": row["written"], "spoken": row["spoken"]}

    def list_say_as(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM say_as ORDER BY written COLLATE NOCASE").fetchall()
        return [{"id": row["id"], "written": row["written"], "spoken": row["spoken"]} for row in rows]

    def delete_say_as(self, say_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM say_as WHERE id = ?", (say_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_generation(self, gen_id: str) -> bool:
        wav = generation_wav(gen_id)
        mp3 = generation_mp3(gen_id)
        with self._lock:
            cur = self.conn.execute("DELETE FROM generations WHERE id = ?", (gen_id,))
            self.conn.commit()
            deleted = cur.rowcount > 0
        for path in (wav, mp3):
            if path.exists():
                path.unlink()
        return deleted


def reference_wav(ref_id: str) -> Path:
    _safe_id(ref_id)
    return references() / f"{ref_id}.wav"


def generation_wav(gen_id: str) -> Path:
    _safe_id(gen_id)
    return audio() / f"{gen_id}.wav"


def preview_wav(gen_id: str) -> Path:
    _safe_id(gen_id)
    return audio() / f"{gen_id}.preview.wav"


def generation_mp3(gen_id: str) -> Path:
    _safe_id(gen_id)
    return audio() / f"{gen_id}.mp3"


def _safe_id(value: str) -> None:
    if not value or len(value) > 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("Invalid id")


def _voice_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "name": row["name"],
        "mode": row["mode"],
        "language": row["language"],
        "voice_description": row["voice_description"],
        "speaker": row["speaker"],
        "reference_id": row["reference_id"],
        "pace": row["pace"],
    }


def _reference_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "original_name": row["original_name"],
        "transcript": row["transcript"],
        "duration_sec": row["duration_sec"],
        "warning": row["warning"],
        "peaks": json.loads(row["peaks_json"]),
        "audio_url": f"/api/references/{row['id']}/audio",
    }


def _generation_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "mode": row["mode"],
        "script": row["script"],
        "language": row["language"],
        "language_note": row["language_note"],
        "voice_description": row["voice_description"],
        "speaker": row["speaker"],
        "instruct": row["instruct"],
        "model_id": row["model_id"],
        "reference_id": row["reference_id"],
        "duration_sec": row["duration_sec"],
        "sample_rate": row["sample_rate"],
        "load_sec": row["load_sec"],
        "first_audio_sec": row["first_audio_sec"],
        "generate_sec": row["generate_sec"],
        "peaks": json.loads(row["peaks_json"]),
        "markers": json.loads(row["markers_json"]) if row["markers_json"] else [],
        "wav_url": f"/api/audio/{row['id']}/wav",
        "mp3_url": f"/api/audio/{row['id']}/mp3",
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
