import os
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from reed.audio import join_segments, prepare_reference, scale_markers, write_wav
from reed.catalog import BY_ID
from reed.hardware import fit_for
from reed.paths import model_dir
from reed.textutil import SAMPLE_SENTENCES, ValidationError, apply_say_as, eta_seconds, plan_speech, split_script
from reed.validate import validate_generation


def test_script_chunks_keep_order_and_cover_the_text():
    text = "One stays. Two follows. " + ("A longer clause, with a pause, " * 30)
    parts = split_script(text, limit=80)
    assert parts[0].startswith("One stays.")
    assert "Two follows." in parts[0] or parts[1].startswith("Two follows.")
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_pauses_and_speaker_lines_plan_in_order():
    plan = plan_speech(
        "Ryan: The harbor stays quiet.\n\nAiden: The street is awake. [pause 1.2] Listen.",
        dialogue=True,
    )
    kinds = [(item["kind"], item.get("speaker", ""), item.get("text", ""), item.get("seconds")) for item in plan]
    assert kinds == [
        ("speech", "Ryan", "The harbor stays quiet.", None),
        ("pause", "", "", 0.4),
        ("speech", "Aiden", "The street is awake.", None),
        ("pause", "", "", 1.2),
        ("speech", "Aiden", "Listen.", None),
    ]


def test_a_bad_pause_is_rejected():
    with pytest.raises(ValidationError, match="0.1 and 3"):
        plan_speech("Hello [pause 9]")
    with pytest.raises(ValidationError, match=r"\[pause\]"):
        plan_speech("Hello [pause forever]")


def test_say_as_replaces_whole_words():
    assert apply_say_as("The GIF is not a GIFter.", [("GIF", "jif")]) == "The jif is not a GIFter."


def test_sample_is_three_sentences():
    assert len(SAMPLE_SENTENCES) == 3


def test_eta_needs_real_movement():
    assert eta_seconds([(0, 0), (0.2, 10)], 1000) is None
    eta = eta_seconds([(0, 0), (2, 200), (4, 400)], 400)
    assert eta is not None
    assert 3.5 < eta < 4.5


def test_fit_labels_are_guidance_not_a_cutoff():
    memory = 16 * 1024**3
    light = fit_for(1_900_000_000, memory)
    mid = fit_for(3_080_000_000, memory)
    heavy = fit_for(4_520_000_000, memory)
    assert light["level"] == "comfortable"
    assert mid["level"] == "can_load"
    assert heavy["level"] == "competes"
    assert "official" not in mid["detail"].lower()


def test_catalog_rejects_unknown_ids():
    assert "org/not-a-model" not in BY_ID
    assert len({model.task for model in BY_ID.values()}) == 3


def test_controls_are_not_silently_dropped():
    preset = BY_ID["mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"]
    with pytest.raises(ValidationError, match="delivery instructions"):
        validate_generation(
            {
                "mode": "preset",
                "script": "Hello there.",
                "language": "english",
                "speaker": "Ryan",
                "instruct": "Whisper this.",
            },
            preset,
        )
    slowed = validate_generation(
        {
            "mode": "preset",
            "script": "Hello there.",
            "language": "english",
            "speaker": "Ryan",
            "pace": 0.85,
        },
        preset,
    )
    assert slowed["pace"] == 0.85
    with pytest.raises(ValidationError, match="Pace stays"):
        validate_generation(
            {
                "mode": "preset",
                "script": "Hello there.",
                "language": "english",
                "speaker": "Ryan",
                "pace": 2,
            },
            preset,
        )
    design = BY_ID["mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"]
    with pytest.raises(ValidationError, match="preset speakers"):
        validate_generation(
            {
                "mode": "design",
                "script": "Hello there.",
                "language": "english",
                "instruct": "A calm older voice with a dry wit.",
                "speaker": "Ryan",
            },
            design,
        )
    clone = BY_ID["mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"]
    with pytest.raises(ValidationError, match="permission"):
        validate_generation(
            {
                "mode": "clone",
                "script": "Hello there.",
                "reference_id": "abc",
                "owns_voice": False,
            },
            clone,
        )


def test_norwegian_is_explicit():
    model = BY_ID["mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"]
    result = validate_generation(
        {
            "mode": "design",
            "script": "Dette er en test.",
            "language": "norwegian",
            "instruct": "A quiet woman speaking slowly and carefully.",
        },
        model,
    )
    assert result["language"] == "auto"
    assert "experimental" in result["language_note"].lower()
    assert result["requested_language"] == "norwegian"


def test_join_keeps_order_and_adds_a_short_gap():
    sample_rate = 24000
    first = np.ones(sample_rate, dtype=np.float32)
    second = np.full(sample_rate, 0.25, dtype=np.float32)
    mixed = join_segments([first, second], sample_rate, gap_ms=70, fade_ms=8)
    assert mixed.shape[0] > first.shape[0] + second.shape[0]
    assert mixed[100] == pytest.approx(1.0)
    assert mixed[-100] == pytest.approx(0.25)


def test_invalid_reference_and_short_clip(tmp_path: Path):
    text = tmp_path / "notes.wav"
    text.write_text("not audio")
    with pytest.raises(Exception, match="readable audio"):
        prepare_reference(text, tmp_path / "out.wav", ".wav", text.stat().st_size)
    short = tmp_path / "short.wav"
    write_wav(short, np.zeros(2400, dtype=np.float32), 24000)
    with pytest.raises(Exception, match="too short"):
        prepare_reference(short, tmp_path / "out2.wav", ".wav", short.stat().st_size)


def test_hardware_reports_this_mac():
    os.environ["REED_DATA"] = "/tmp/reed-test-data"
    from reed.hardware import detect

    info = detect(force=True)
    assert info["chip"] == "Apple M4"
    assert info["unified_memory_bytes"] == 16 * 1024**3
    assert info["model_name"] == "MacBook Air"
    assert info["apple_silicon"] is True
    assert info["disk_free_bytes"] > 0


def test_api_rejects_unknown_model_and_bad_upload(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REED_DATA", str(tmp_path / "data"))
    # The app captured REED_DATA at import. Point new requests at the existing store
    # and just exercise validation that does not depend on the data directory.
    from reed.main import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    refused = client.post("/api/models/download", json={"model_id": "../secrets"})
    assert refused.status_code == 400
    upload = client.post(
        "/api/references",
        files={"file": ("clip.txt", b"hello", "text/plain")},
        data={"transcript": "hello there"},
    )
    assert upload.status_code == 400
    assert "readable" in upload.json()["detail"].lower() or "wav" in upload.json()["detail"].lower()
    short = tmp_path / "short.wav"
    write_wav(short, np.zeros(2400, dtype=np.float32), 24000)
    short_upload = client.post(
        "/api/references",
        files={"file": ("short.wav", short.read_bytes(), "audio/wav")},
        data={"transcript": "too short to clone"},
    )
    assert short_upload.status_code == 400
    assert "too short" in short_upload.json()["detail"].lower()


def test_progress_ignores_temporary_copies_of_finished_files(tmp_path: Path):
    import json

    from reed.downloads import measure_progress

    dest = tmp_path / "model"
    (dest / "speech_tokenizer").mkdir(parents=True)
    (dest / "model.safetensors").write_bytes(b"x" * 100)
    (dest / "speech_tokenizer" / "model.safetensors").write_bytes(b"y" * 50)
    cache = dest / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "weights.incomplete").write_bytes(b"z" * 80)
    (dest / "reed-expected.json").write_text(
        json.dumps(
            {
                "bytes": 150,
                "files": [
                    {"path": "model.safetensors", "bytes": 100},
                    {"path": "speech_tokenizer/model.safetensors", "bytes": 50},
                ],
            }
        )
    )
    assert measure_progress(dest) == 150

    path = model_dir("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit")
    assert path.parent.name == "models"
    with pytest.raises(ValueError):
        model_dir("../etc/passwd")


def test_markers_follow_pace_and_hide_a_single_part():
    marks = [{"label": "Ryan", "start": 0.0}, {"label": "Aiden", "start": 4.0}]
    assert scale_markers(marks, 1) == marks
    assert scale_markers(marks, 1.25) == [
        {"label": "Ryan", "start": 0.0},
        {"label": "Aiden", "start": 3.2},
    ]
    assert scale_markers([{"label": "Part 1", "start": 0.0}], 1) == []
