from __future__ import annotations

from .catalog import ModelSpec
from .textutil import SPEAKER_IDS, ValidationError, plan_speech, resolve_language


def validate_generation(body: dict, model: ModelSpec) -> dict:
    mode = (body.get("mode") or "").strip()
    if mode not in {"design", "clone", "preset"}:
        raise ValidationError("Choose design, clone, or preset.")
    if mode != model.task:
        raise ValidationError("That checkpoint belongs to a different creation mode.")

    script = (body.get("script") or "").strip()
    if not script:
        raise ValidationError("Write the words to speak.")
    if len(script) > 5000:
        raise ValidationError("Keep the script under 5000 characters. Split a longer piece into a few takes.")

    language, note = resolve_language(body.get("language") or "auto")
    instruct = (body.get("instruct") or "").strip()
    speaker = (body.get("speaker") or "").strip()
    reference_id = (body.get("reference_id") or "").strip()
    temperature = _temperature(body.get("temperature", 0.9))

    if mode == "design":
        if len(instruct) < 8:
            raise ValidationError("Describe the voice. Age, accent, texture, energy, and emotion all help.")
        if speaker:
            raise ValidationError("Voice design does not use preset speakers. The description defines the voice.")
        if reference_id:
            raise ValidationError("Voice design does not use a reference clip.")
    elif mode == "clone":
        if not reference_id:
            raise ValidationError("Add a reference clip to clone.")
        if body.get("owns_voice") is not True:
            raise ValidationError("Confirm that you own this voice or have permission to use it.")
        if instruct:
            raise ValidationError(
                "Base checkpoints clone the reference clip. They do not take a written voice description or delivery instruction."
            )
        if speaker:
            raise ValidationError("Clone mode does not use preset speakers.")
    else:
        if speaker not in SPEAKER_IDS:
            raise ValidationError("Choose one of the published CustomVoice speakers.")
        if reference_id:
            raise ValidationError("Preset voices are not clones. Switch to Clone a voice to use a reference clip.")
        if instruct and not model.supports_instruct:
            raise ValidationError(
                "This 0.6B CustomVoice checkpoint does not support delivery instructions. "
                "Switch to the 1.7B CustomVoice checkpoint, or clear the instruction."
            )
        if len(instruct) > 400:
            raise ValidationError("Keep the delivery instruction under 400 characters.")

    plan_speech(script, dialogue=mode == "preset")
    pace = _pace(body.get("pace", 1))

    return {
        "mode": mode,
        "script": script,
        "language": language,
        "requested_language": (body.get("language") or "auto").strip().lower(),
        "language_note": note,
        "instruct": instruct,
        "speaker": speaker,
        "reference_id": reference_id,
        "temperature": temperature,
        "pace": pace,
        "voice_description": instruct if mode == "design" else "",
        "delivery": instruct if mode == "preset" else "",
    }


def _pace(value) -> float:
    if value is None or value == "":
        return 1.0
    try:
        pace = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Pace must be a number between 0.80 and 1.25.") from exc
    if pace < 0.8 or pace > 1.25:
        raise ValidationError(
            "Pace stays between 0.80 and 1.25. It changes the length after synthesis and keeps the pitch."
        )
    return round(pace, 2)


def _temperature(value) -> float:
    try:
        temperature = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Temperature must be a number between 0.1 and 1.5.") from exc
    if temperature < 0.1 or temperature > 1.5:
        raise ValidationError("Temperature must stay between 0.1 and 1.5.")
    return temperature
