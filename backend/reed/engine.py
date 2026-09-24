from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from queue import Queue

import numpy as np

from .audio import apply_pace, join_segments, peaks, read_wav, scale_markers, write_wav
from .catalog import get_model
from .paths import benchmarks_path, model_dir
from .store import Store, generation_wav, preview_wav, reference_wav
from .textutil import SAMPLE_ID, SAMPLE_TEXT, ValidationError, apply_say_as, plan_speech
from .validate import validate_generation

log = logging.getLogger("reed.engine")

DESIGN_BENCH = (
    "A woman in her forties, close to the microphone, soft northern English accent, "
    "low and unhurried, with dry warmth and no smile in the voice."
)
PRESET_BENCH = "Speak evenly, with quiet confidence."


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, publish, downloads: object, store: Store) -> None:
        self.publish = publish
        self.downloads = downloads
        self.store = store
        self.jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._queue: Queue[str] = Queue()
        self._model = None
        self.loaded_model_id: str | None = None
        self._thread = threading.Thread(target=self._loop, name="reed-inference", daemon=True)
        self._thread.start()

    def submit(self, body: dict) -> dict:
        try:
            model = get_model(body.get("model_id") or "")
        except KeyError as exc:
            raise ValidationError("That model is not in Reed's catalog.") from exc
        spec = validate_generation(body, model)
        if not self.downloads.installed(model.id):
            raise ValidationError("Download this checkpoint before generating. An incomplete download is not ready.")
        if spec["mode"] == "clone":
            reference = self.store.get_reference(spec["reference_id"])
            if reference is None or not reference_wav(spec["reference_id"]).is_file():
                raise ValidationError("That reference clip is no longer on this Mac.")
            if not reference["transcript"].strip():
                raise ValidationError("The reference clip needs a transcript of the words it speaks.")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "state": "queued",
            "mode": spec["mode"],
            "model_id": model.id,
            "script": spec["script"],
            "language": spec["requested_language"],
            "language_note": spec["language_note"],
            "speaker": spec["speaker"],
            "instruct": spec["instruct"],
            "voice_description": spec["voice_description"],
            "reference_id": spec["reference_id"],
            "temperature": spec["temperature"],
            "pace": spec["pace"],
            "resolved_language": spec["language"],
            "error": "",
            "audio_sec": 0.0,
            "elapsed_sec": 0.0,
            "load_sec": None,
            "first_audio_sec": None,
            "generate_sec": None,
            "chunks_total": 0,
            "chunks_done": 0,
            "markers": [],
            "progress": None,
            "history_id": None,
            "created_at": time.time(),
            "started_at": None,
            "benchmark": bool(body.get("benchmark")),
        }
        with self._lock:
            self.jobs[job_id] = job
            self._cancel[job_id] = threading.Event()
        self._queue.put(job_id)
        self._emit(job)
        return self.public(job_id)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.jobs.get(job_id)
            event = self._cancel.get(job_id)
        if job is None or event is None:
            raise KeyError(job_id)
        event.set()
        if job["state"] == "queued":
            job["state"] = "cancelled"
            job["error"] = "Cancelled before generation started."
            self._emit(job)
        return self.public(job_id)

    def public(self, job_id: str) -> dict:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            data = dict(job)
        if data.get("started_at") and data["state"] in {"loading", "generating"}:
            data["elapsed_sec"] = round(time.time() - data["started_at"], 2)
        data.pop("resolved_language", None)
        return data

    def list_active(self) -> list[dict]:
        with self._lock:
            jobs = list(self.jobs.values())
        active = []
        for job in jobs:
            if job["state"] in {"queued", "loading", "generating"}:
                active.append(self.public(job["id"]))
        return active

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self.jobs.get(job_id)
            if job is None or job["state"] == "cancelled":
                continue
            try:
                self._run(job)
            except Cancelled:
                job["state"] = "cancelled"
                job["error"] = "Cancelled."
                self._cleanup_partial(job["id"])
                self._emit(job)
            except Exception as exc:
                log.exception("generation failed")
                job["state"] = "failed"
                job["error"] = _friendly_error(exc)
                self._cleanup_partial(job["id"])
                self._emit(job)

    def _run(self, job: dict) -> None:
        cancel = self._cancel[job["id"]]
        job["state"] = "loading"
        job["started_at"] = time.time()
        self._emit(job)
        load_started = time.time()
        model = self._load(job["model_id"])
        if cancel.is_set():
            raise Cancelled()
        job["load_sec"] = round(time.time() - load_started, 3)
        job["state"] = "generating"
        self._emit(job)
        plan = plan_speech(job["script"], dialogue=job["mode"] == "preset")
        replacements = [(item["written"], item["spoken"]) for item in self.store.list_say_as()]
        for item in plan:
            if item["kind"] == "speech":
                item["text"] = apply_say_as(item["text"], replacements).strip()
        plan = [item for item in plan if item["kind"] != "speech" or item["text"]]
        speech = [item for item in plan if item["kind"] == "speech"]
        if not speech:
            raise ValidationError("Write the words to speak.")
        job["chunks_total"] = len(speech)
        sample_rate = int(getattr(model, "sample_rate", 24000) or 24000)
        segments: list[np.ndarray] = []
        markers: list[dict] = []
        generate_started = time.time()
        first_audio = None
        produced = 0.0
        spoken = 0
        reference = None
        if job["mode"] == "clone":
            reference = self.store.get_reference(job["reference_id"])
        for item in plan:
            if cancel.is_set():
                raise Cancelled()
            if item["kind"] == "pause":
                segments.append(np.zeros(int(sample_rate * item["seconds"]), dtype=np.float32))
                produced += float(item["seconds"])
                job["audio_sec"] = round(produced, 2)
                self._emit(job)
                continue
            label = (item.get("speaker") or "").replace("_", " ") or f"Part {spoken + 1}"
            markers.append({"label": label, "start": round(produced, 3)})
            job["markers"] = markers if len(markers) > 1 else []
            audio = self._speak_chunk(model, job, item["text"], reference, cancel, item["speaker"])
            if first_audio is None:
                first_audio = time.time() - generate_started
                job["first_audio_sec"] = round(first_audio, 3)
            segments.append(audio)
            produced += float(audio.size) / sample_rate
            spoken += 1
            job["chunks_done"] = spoken
            job["audio_sec"] = round(produced, 2)
            ready = join_segments(segments, sample_rate) if len(segments) > 1 else segments[0]
            write_wav(preview_wav(job["id"]), ready, sample_rate)
            self._emit(job)
        if cancel.is_set():
            raise Cancelled()
        mixed = join_segments(segments, sample_rate) if len(segments) > 1 else segments[0]
        generate_sec = time.time() - generate_started
        wav_path = generation_wav(job["id"])
        write_wav(wav_path, mixed, sample_rate)
        pace = float(job.get("pace") or 1)
        if abs(pace - 1) >= 0.01:
            apply_pace(wav_path, pace)
            mixed, sample_rate = read_wav(wav_path)
        markers = scale_markers(markers, pace)
        job["markers"] = markers
        duration = float(mixed.size) / sample_rate
        partial = preview_wav(job["id"])
        if partial.exists():
            partial.unlink()
        record = self.store.add_generation(
            {
                "id": job["id"],
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "mode": job["mode"],
                "script": job["script"],
                "language": job["language"],
                "language_note": job["language_note"],
                "voice_description": job["voice_description"],
                "speaker": job["speaker"],
                "instruct": job["instruct"] if job["mode"] == "preset" else "",
                "model_id": job["model_id"],
                "reference_id": job["reference_id"],
                "duration_sec": round(duration, 3),
                "sample_rate": sample_rate,
                "load_sec": job["load_sec"],
                "first_audio_sec": job["first_audio_sec"],
                "generate_sec": round(generate_sec, 3),
                "peaks": peaks(mixed),
                "markers": markers,
            }
        )
        job["state"] = "completed"
        job["history_id"] = record["id"]
        job["audio_sec"] = record["duration_sec"]
        job["generate_sec"] = round(generate_sec, 3)
        job["elapsed_sec"] = round(time.time() - job["started_at"], 2)
        if job["benchmark"]:
            _save_benchmark(job, len(SAMPLE_TEXT))
        self._emit(job)

    def _speak_chunk(
        self,
        model,
        job: dict,
        text: str,
        reference: dict | None,
        cancel: threading.Event,
        speaker: str = "",
    ) -> np.ndarray:
        text = " ".join(text.split())
        language = job["resolved_language"]
        temperature = job["temperature"]
        if job["mode"] == "design":
            generator = model.generate_voice_design(
                text=text,
                instruct=job["instruct"],
                language=language,
                temperature=temperature,
                stream=True,
                streaming_interval=0.4,
            )
        elif job["mode"] == "preset":
            instruct = job["instruct"] or None
            generator = model.generate_custom_voice(
                text=text,
                speaker=speaker or job["speaker"],
                language=language,
                instruct=instruct,
                temperature=temperature,
                stream=True,
                streaming_interval=0.4,
            )
        else:
            assert reference is not None
            generator = model.generate(
                text=text,
                ref_audio=str(reference_wav(reference["id"])),
                ref_text=reference["transcript"],
                lang_code=language,
                temperature=temperature,
                stream=True,
                streaming_interval=0.4,
            )
        pieces = [_to_numpy(result.audio) for result in _iter_audio(generator, cancel)]
        pieces = [piece for piece in pieces if piece.size]
        if not pieces:
            raise RuntimeError("The model returned no audio.")
        return np.concatenate(pieces) if len(pieces) > 1 else pieces[0]

    def _load(self, model_id: str):
        if self._model is not None and self.loaded_model_id == model_id:
            return self._model
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model

        self._model = None
        self.loaded_model_id = None
        mx.clear_cache()
        path = model_dir(model_id)
        if not path.is_dir():
            raise RuntimeError("The checkpoint directory is missing.")
        model = load_model(str(path))
        self._model = model
        self.loaded_model_id = model_id
        return model

    def unload(self, model_id: str | None = None) -> None:
        if model_id is not None and self.loaded_model_id != model_id:
            return
        self._model = None
        self.loaded_model_id = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            log.debug("mlx cache clear skipped", exc_info=True)

    def _emit(self, job: dict) -> None:
        self.publish({"type": "job", "job": self.public(job["id"])})

    def _cleanup_partial(self, job_id: str) -> None:
        path = generation_wav(job_id)
        if path.exists() and self.store.get_generation(job_id) is None:
            path.unlink()
        partial = preview_wav(job_id)
        if partial.exists():
            partial.unlink()


def load_benchmarks() -> dict:
    path = benchmarks_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def estimate(model_id: str, script: str, loaded: bool) -> dict:
    bench = load_benchmarks().get(model_id)
    sample = {
        "id": SAMPLE_ID,
        "text": SAMPLE_TEXT,
        "sentences": SAMPLE_TEXT.split(". "),
    }
    if not bench:
        return {
            "kind": "rough",
            "sample": sample,
            "load_sec": None,
            "first_audio_sec": None,
            "generate_sec": None,
            "detail": (
                "Rough estimate: this Mac has not been measured yet. "
                "Reed will not invent a synthesis time. Run the three-sentence sample locally, "
                "and later estimates use that measurement."
            ),
        }
    chars = max(len(script.strip()), 1)
    scale = chars / max(int(bench["sample_chars"]), 1)
    return {
        "kind": "measured",
        "sample": sample,
        "measured_at": bench.get("measured_at"),
        "chip": bench.get("chip"),
        "load_sec": None if loaded else bench.get("load_sec"),
        "first_audio_sec": bench.get("first_audio_sec"),
        "generate_sec": round(float(bench["generate_sec"]) * scale, 2),
        "scale": round(scale, 3),
        "detail": (
            "Based on a local measurement of the fixed three-sentence sample on this Mac. "
            "Model loading is separate. First audio is the measured time until the first sound, not scaled. "
            "Total generation is scaled by script length."
        ),
    }


def _save_benchmark(job: dict, sample_chars: int) -> None:
    from .hardware import detect

    hardware = detect()
    data = load_benchmarks()
    previous = data.get(job["model_id"]) or {}
    load_sec = job["load_sec"]
    if load_sec is not None and load_sec < 0.05 and (previous.get("load_sec") or 0) >= 0.05:
        load_sec = previous["load_sec"]
    data[job["model_id"]] = {
        "sample_id": SAMPLE_ID,
        "sample_chars": sample_chars,
        "mode": job["mode"],
        "load_sec": load_sec,
        "first_audio_sec": job["first_audio_sec"],
        "generate_sec": job["generate_sec"],
        "audio_sec": job["audio_sec"],
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chip": hardware.get("chip"),
        "unified_memory_bytes": hardware.get("unified_memory_bytes"),
    }
    benchmarks_path().write_text(json.dumps(data, indent=2))


def _iter_audio(generator, cancel: threading.Event):
    try:
        for result in generator:
            if cancel.is_set():
                raise Cancelled()
            yield result
    finally:
        close = getattr(generator, "close", None)
        if close is not None:
            close()


def _to_numpy(audio) -> np.ndarray:
    array = np.array(audio, dtype=np.float32)
    return np.ascontiguousarray(array).reshape(-1)


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "metal" in lowered or "out of memory" in lowered or "malloc" in lowered:
        return (
            message
            + " This checkpoint may be competing with other apps for unified memory."
        )
    return message[:900]
