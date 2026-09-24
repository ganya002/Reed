from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import AudioError, ffmpeg_available, mp3_supported, prepare_reference, to_mp3
from .catalog import BY_ID, describe, load_byte_table, refresh_byte_table
from .downloads import DownloadError, DownloadManager
from .engine import DESIGN_BENCH, PRESET_BENCH, Engine, estimate, load_benchmarks
from .events import hub
from .hardware import detect
from .paths import ROOT, ensure, tmp
from .store import Store, generation_mp3, generation_wav, preview_wav, reference_wav
from .textutil import (
    DESIGN_EXAMPLES,
    SAMPLE_ID,
    SAMPLE_SENTENCES,
    SAMPLE_TEXT,
    SPEAKER_IDS,
    SPEAKERS,
    ValidationError,
    apply_say_as,
    language_choices,
    plan_speech,
    resolve_language,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("reed")

ensure()
store = Store()
downloads = DownloadManager(hub.publish)
engine = Engine(hub.publish, downloads, store)
_ID = re.compile(r"^[0-9a-f]{32}$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.attach(asyncio.get_running_loop())
    downloads.reconcile()
    threading.Thread(target=refresh_byte_table, name="reed-manifest", daemon=True).start()
    yield


app = FastAPI(title="Reed", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _bad(exc: Exception):
    if isinstance(exc, (ValidationError, DownloadError, AudioError)):
        raise HTTPException(status_code=400, detail=exc.message) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="Not found.") from exc
    raise exc


def _model_or_400(model_id: str):
    model = BY_ID.get(model_id)
    if model is None:
        raise HTTPException(status_code=400, detail="That model is not in Reed's catalog.")
    return model


def _check_id(value: str) -> str:
    if not _ID.match(value or ""):
        raise HTTPException(status_code=400, detail="Invalid id.")
    return value


@app.get("/api/health")
def health():
    return {"ok": True, "service": "reed", "version": __version__}


@app.get("/api/hardware")
def hardware():
    return detect()


@app.get("/api/models")
def models():
    return {"models": _models_payload()}


@app.get("/api/models/status")
def model_status(model_id: str):
    _model_or_400(model_id)
    return downloads.snapshot(model_id)


@app.post("/api/models/download")
def model_download(body: dict):
    model = _model_or_400(body.get("model_id") or "")
    table = load_byte_table()
    entry = table.get(model.id, {})
    try:
        if not entry.get("files"):
            table = refresh_byte_table(timeout=25)
            entry = table.get(model.id, {})
        status = downloads.start(
            model.id,
            int(entry.get("bytes") or 0),
            list(entry.get("files") or []),
        )
    except (DownloadError, ValidationError) as exc:
        _bad(exc)
    return status


@app.post("/api/models/pause")
def model_pause(body: dict):
    model = _model_or_400(body.get("model_id") or "")
    return downloads.pause(model.id)


@app.post("/api/models/remove")
def model_remove(body: dict):
    model = _model_or_400(body.get("model_id") or "")
    active = [job for job in engine.list_active() if job["model_id"] == model.id]
    if active:
        raise HTTPException(status_code=409, detail="Wait for the current generation to finish before removing this checkpoint.")
    engine.unload(model.id)
    return downloads.remove(model.id)


@app.post("/api/generate")
def generate(body: dict):
    try:
        job = engine.submit(body)
    except (ValidationError, KeyError) as exc:
        _bad(exc)
    return JSONResponse(job, status_code=202)


@app.post("/api/benchmark")
def benchmark(body: dict):
    model = _model_or_400(body.get("model_id") or "")
    payload = {
        "mode": model.task,
        "model_id": model.id,
        "script": SAMPLE_TEXT,
        "language": "english",
        "benchmark": True,
        "temperature": 0.9,
        "owns_voice": body.get("owns_voice") is True,
        "reference_id": body.get("reference_id") or "",
        "speaker": "Ryan" if model.task == "preset" else "",
        "instruct": "",
    }
    if model.task == "design":
        payload["instruct"] = DESIGN_BENCH
    elif model.supports_instruct:
        payload["instruct"] = PRESET_BENCH
    try:
        job = engine.submit(payload)
    except (ValidationError, KeyError) as exc:
        _bad(exc)
    return JSONResponse(job, status_code=202)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    _check_id(job_id)
    try:
        return engine.public(job_id)
    except KeyError as exc:
        _bad(exc)


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str):
    _check_id(job_id)
    path = preview_wav(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The start of this take is not ready.")
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    _check_id(job_id)
    try:
        return engine.cancel(job_id)
    except KeyError as exc:
        _bad(exc)


@app.get("/api/history")
def history():
    return {"items": store.list_generations()}


@app.delete("/api/history/{gen_id}")
def history_delete(gen_id: str):
    _check_id(gen_id)
    if not store.delete_generation(gen_id):
        raise HTTPException(status_code=404, detail="That take is not in the local history.")
    return {"ok": True}


@app.get("/api/references")
def reference_list():
    return {"items": store.list_references()}


@app.post("/api/references")
async def reference_upload(file: UploadFile = File(...), transcript: str = Form("")):
    spoken = transcript.strip()
    if len(spoken) < 2:
        raise HTTPException(status_code=400, detail="Add a transcript of the words spoken in the clip.")
    if len(spoken) > 1200:
        raise HTTPException(status_code=400, detail="Keep the reference transcript under 1200 characters.")
    name = Path(file.filename or "reference").name
    suffix = Path(name).suffix.lower() or ".webm"
    raw = await file.read(25 * 1024 * 1024 + 1)
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Keep the reference clip under 25 MB.")
    safe_name = re.sub(r"[^\w.\- ]+", "", name)[:80] or "reference"
    token = uuid.uuid4().hex
    source = tmp() / f"upload-{token}.src{suffix}"
    source.write_bytes(raw)
    dest = tmp() / f"upload-{token}.wav"
    try:
        prepared = prepare_reference(source, dest, suffix, len(raw))
        record = store.add_reference(safe_name, spoken, prepared["duration"], prepared["warning"], prepared["peaks"])
        target = reference_wav(record["id"])
        dest.replace(target)
    except AudioError as exc:
        _bad(exc)
    finally:
        source.unlink(missing_ok=True)
        if dest.exists():
            dest.unlink()
    return record


@app.delete("/api/references/{ref_id}")
def reference_delete(ref_id: str):
    _check_id(ref_id)
    if not store.delete_reference(ref_id):
        raise HTTPException(status_code=404, detail="That reference is not stored on this Mac.")
    return {"ok": True}


@app.get("/api/references/{ref_id}/audio")
def reference_audio(ref_id: str):
    _check_id(ref_id)
    path = reference_wav(ref_id)
    if store.get_reference(ref_id) is None or not path.is_file():
        raise HTTPException(status_code=404, detail="That reference audio is not on this Mac.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/audio/{gen_id}/wav")
def audio_wav(gen_id: str, download: int = 0):
    _check_id(gen_id)
    path = generation_wav(gen_id)
    if store.get_generation(gen_id) is None or not path.is_file():
        raise HTTPException(status_code=404, detail="That audio is not on this Mac.")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"reed-{gen_id[:8]}.wav",
        content_disposition_type=disposition,
    )


@app.get("/api/audio/{gen_id}/mp3")
def audio_mp3(gen_id: str):
    _check_id(gen_id)
    wav = generation_wav(gen_id)
    if store.get_generation(gen_id) is None or not wav.is_file():
        raise HTTPException(status_code=404, detail="That audio is not on this Mac.")
    if not mp3_supported():
        raise HTTPException(status_code=503, detail="MP3 export needs ffmpeg with libmp3lame.")
    mp3 = generation_mp3(gen_id)
    if not mp3.is_file():
        try:
            to_mp3(wav, mp3)
        except AudioError as exc:
            _bad(exc)
    return FileResponse(mp3, media_type="audio/mpeg", filename=f"reed-{gen_id[:8]}.mp3")


@app.get("/api/benchmarks")
def benchmarks():
    return {"items": load_benchmarks(), "sample": _sample_payload()}


@app.get("/api/events")
async def events(request: Request):
    queue = hub.subscribe()

    async def stream():
        try:
            yield "event: hello\ndata: {\"ok\": true}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/voices")
def voice_create(body: dict):
    name = re.sub(r"\s+", " ", (body.get("name") or "").strip())
    mode = (body.get("mode") or "").strip()
    if not name or len(name) > 40:
        raise HTTPException(status_code=400, detail="Name the voice in 40 characters or fewer.")
    if mode not in {"design", "clone", "preset"}:
        raise HTTPException(status_code=400, detail="Choose design, clone, or preset.")
    requested = (body.get("language") or "auto").strip().lower()
    try:
        resolve_language(requested)
    except ValidationError as exc:
        _bad(exc)
    description = (body.get("voice_description") or "").strip()
    speaker = (body.get("speaker") or "").strip()
    reference_id = (body.get("reference_id") or "").strip()
    try:
        pace = float(1 if body.get("pace") is None else body.get("pace"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Pace must be a number between 0.80 and 1.25.") from exc
    if pace < 0.8 or pace > 1.25:
        raise HTTPException(status_code=400, detail="Pace stays between 0.80 and 1.25.")
    if mode == "design":
        if len(description) < 8:
            raise HTTPException(status_code=400, detail="Describe the voice before saving it.")
        speaker = ""
        reference_id = ""
    elif mode == "preset":
        if speaker not in SPEAKER_IDS:
            raise HTTPException(status_code=400, detail="Choose one of the published speakers before saving.")
        description = ""
        reference_id = ""
    else:
        if not reference_id or store.get_reference(reference_id) is None:
            raise HTTPException(status_code=404, detail="That reference clip is no longer on this Mac.")
        description = ""
        speaker = ""
    try:
        return store.add_voice(name, mode, requested, description, speaker, reference_id, round(pace, 2))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/voices/{voice_id}")
def voice_delete(voice_id: str):
    _check_id(voice_id)
    if not store.delete_voice(voice_id):
        raise HTTPException(status_code=404, detail="That voice is not saved on this Mac.")
    return {"ok": True}


@app.post("/api/say-as")
def say_as_create(body: dict):
    written = re.sub(r"\s+", " ", (body.get("written") or "").strip())
    spoken = re.sub(r"\s+", " ", (body.get("spoken") or "").strip())
    if not written or len(written) > 40 or not spoken or len(spoken) > 80:
        raise HTTPException(status_code=400, detail="Keep the written word under 40 characters and the spoken form under 80.")
    if "[" in written or "]" in written:
        raise HTTPException(status_code=400, detail="Pronunciations replace words. Pauses stay in the script.")
    try:
        return store.add_say_as(written, spoken)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/say-as/{say_id}")
def say_as_delete(say_id: str):
    _check_id(say_id)
    if not store.delete_say_as(say_id):
        raise HTTPException(status_code=404, detail="That pronunciation is not saved on this Mac.")
    return {"ok": True}


@app.get("/api/studio")
def studio(script: str = "", mode: str = "design", speaker: str = ""):
    hardware_info = detect()
    models_payload = _models_payload()
    loaded = engine.loaded_model_id
    estimates = {
        model["id"]: estimate(model["id"], script or SAMPLE_TEXT, loaded == model["id"])
        for model in models_payload
    }
    if mode not in {"design", "clone", "preset"}:
        mode = "design"
    plan_error = ""
    plan: list[dict] = []
    if script.strip():
        try:
            plan = plan_speech(script, dialogue=mode == "preset")
        except ValidationError as exc:
            plan_error = exc.message
    speech = [item for item in plan if item["kind"] == "speech"]
    cast: list[str] = []
    replacements = [(item["written"], item["spoken"]) for item in store.list_say_as()]
    spoken_lines: list[str] = []
    for item in speech:
        name = (item.get("speaker") or "").replace("_", " ")
        if name and name not in cast:
            cast.append(name)
        heard = apply_say_as(item["text"], replacements).strip()
        if not heard:
            continue
        spoken_lines.append(f"{name}: {heard}" if name else heard)
    return {
        "hardware": hardware_info,
        "models": models_payload,
        "languages": language_choices(),
        "speakers": SPEAKERS,
        "examples": DESIGN_EXAMPLES,
        "sample": _sample_payload(),
        "history": store.list_generations(),
        "references": store.list_references(),
        "voices": store.list_voices(),
        "say_as": store.list_say_as(),
        "benchmarks": load_benchmarks(),
        "estimates": estimates,
        "jobs": engine.list_active(),
        "loaded_model_id": loaded,
        "mp3": mp3_supported(),
        "ffmpeg": ffmpeg_available(),
        "parts": len(speech),
        "pauses": sum(1 for item in plan if item["kind"] == "pause"),
        "cast": cast,
        "spoken": "\n".join(spoken_lines),
        "plan_error": plan_error,
        "preset_note": "These are the published CustomVoice speakers. They are not voices you designed or cloned.",
        "clone_guidance": [
            "Use a quiet room and one speaker.",
            "Say the transcript naturally. The words should match the clip.",
            "Three to twelve seconds is the useful range for these clone checkpoints.",
            "The clip stays on this Mac. Reed does not upload it.",
        ],
    }


def _models_payload() -> list[dict]:
    hardware_info = detect()
    table = load_byte_table()
    memory = int(hardware_info.get("unified_memory_bytes") or 0)
    payload = []
    for model in BY_ID.values():
        payload.append(
            describe(
                model,
                memory,
                table,
                downloads.installed(model.id),
                downloads.snapshot(model.id),
            )
        )
    return payload


def _sample_payload() -> dict:
    return {"id": SAMPLE_ID, "text": SAMPLE_TEXT, "sentences": SAMPLE_SENTENCES}


dist = ROOT / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="studio")
