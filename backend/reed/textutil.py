from __future__ import annotations

import re

OFFICIAL_LANGUAGES = [
    ("auto", "Auto"),
    ("chinese", "Chinese"),
    ("english", "English"),
    ("japanese", "Japanese"),
    ("korean", "Korean"),
    ("german", "German"),
    ("french", "French"),
    ("russian", "Russian"),
    ("portuguese", "Portuguese"),
    ("spanish", "Spanish"),
    ("italian", "Italian"),
]

NORWEGIAN_NOTE = (
    "Norwegian is experimental. The official Qwen3-TTS language list does not include it, "
    "and these checkpoints have no Norwegian language token. Reed still speaks the script, "
    "using automatic language handling. Pronunciation and dialect are not reliable."
)

SAMPLE_ID = "harbor-v1"
SAMPLE_SENTENCES = [
    "The harbor stays quiet after the rain.",
    "A brass bell marks the hour, and the street begins to wake.",
    "Speak this line as if you were standing beside the water.",
]
SAMPLE_TEXT = " ".join(SAMPLE_SENTENCES)

DESIGN_EXAMPLES = [
    {
        "id": "close-calm",
        "label": "Close and calm",
        "text": (
            "A woman in her forties, close to the microphone, soft northern English accent, "
            "low and unhurried, with dry warmth and no smile in the voice."
        ),
    },
    {
        "id": "bright-young",
        "label": "Bright and young",
        "text": (
            "A young man with a light American accent, clear midrange, energetic but not loud, "
            "as if explaining something he cares about."
        ),
    },
    {
        "id": "older-story",
        "label": "Older storyteller",
        "text": (
            "An older storyteller, some gravel in the low end, slow pace, gentle melancholy, "
            "quiet and close."
        ),
    },
]

SPEAKERS = [
    {
        "id": "Vivian",
        "description": "Bright, slightly edgy young female voice.",
        "native": "Chinese",
    },
    {
        "id": "Serena",
        "description": "Warm, gentle young female voice.",
        "native": "Chinese",
    },
    {
        "id": "Uncle_Fu",
        "description": "Seasoned male voice with a low, mellow timbre.",
        "native": "Chinese",
    },
    {
        "id": "Dylan",
        "description": "Youthful Beijing male voice with a clear, natural timbre.",
        "native": "Chinese (Beijing dialect)",
    },
    {
        "id": "Eric",
        "description": "Lively Chengdu male voice with a slightly husky brightness.",
        "native": "Chinese (Sichuan dialect)",
    },
    {
        "id": "Ryan",
        "description": "Dynamic male voice with strong rhythmic drive.",
        "native": "English",
    },
    {
        "id": "Aiden",
        "description": "Sunny American male voice with a clear midrange.",
        "native": "English",
    },
    {
        "id": "Ono_Anna",
        "description": "Playful Japanese female voice with a light, nimble timbre.",
        "native": "Japanese",
    },
    {
        "id": "Sohee",
        "description": "Warm Korean female voice with rich emotion.",
        "native": "Korean",
    },
]

SPEAKER_IDS = {item["id"] for item in SPEAKERS}

_SENTENCE = re.compile(
    r".+?(?:[.!?。！？…]+(?:[\"'”’)\]]+)?)(?=\s+|$)|.+$",
    re.DOTALL,
)


class ValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def language_choices() -> list[dict]:
    choices = [
        {"id": code, "label": label, "experimental": False, "note": ""}
        for code, label in OFFICIAL_LANGUAGES
    ]
    choices.append(
        {
            "id": "norwegian",
            "label": "Norwegian",
            "experimental": True,
            "note": NORWEGIAN_NOTE,
        }
    )
    return choices


def resolve_language(language: str) -> tuple[str, str]:
    code = (language or "auto").strip().lower()
    known = {item["id"] for item in language_choices()}
    if code not in known:
        raise ValidationError("Choose a language from the list.")
    if code == "norwegian":
        return "auto", NORWEGIAN_NOTE
    return code, ""


_PAUSE = re.compile(r"\[pause(?:\s+([0-9]+(?:\.[0-9]+)?))?\]", re.IGNORECASE)
_LINE_SPEAKER = re.compile(r"^([A-Za-z][A-Za-z ]{0,24}):\s+(\S.*)$")
_SPEAKER_INDEX = {
    alias: item["id"]
    for item in SPEAKERS
    for alias in {item["id"].lower(), item["id"].replace("_", " ").lower()}
}


def plan_speech(text: str, *, dialogue: bool = False, limit: int = 380) -> list[dict]:
    """Split a script into speech and silence.

    A blank line is a 0.4s breath. ``[pause]`` is 0.6s. ``[pause 1.2]`` waits
    that many seconds, between 0.1 and 3. In dialogue mode, a line that starts
    with a published speaker name, such as ``Ryan:``, switches speaker.
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    blocks = [block.strip() for block in re.split(r"\n[ \t]*\n", cleaned) if block.strip()]
    raw: list[dict] = []
    for index, block in enumerate(blocks):
        if index:
            raw.append({"kind": "pause", "seconds": 0.4})
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            speaker = ""
            body = line
            if dialogue:
                matched = _LINE_SPEAKER.match(line)
                if matched:
                    resolved = _SPEAKER_INDEX.get(matched.group(1).strip().lower())
                    if resolved:
                        speaker = resolved
                        body = matched.group(2).strip()
            for piece in _pause_pieces(body):
                if piece["kind"] == "speech":
                    piece["speaker"] = speaker
                raw.append(piece)
    merged: list[dict] = []
    for item in raw:
        if (
            item["kind"] == "speech"
            and merged
            and merged[-1]["kind"] == "speech"
            and merged[-1]["speaker"] == item["speaker"]
        ):
            merged[-1]["text"] = f"{merged[-1]['text']} {item['text']}".strip()
        elif item["kind"] == "pause" and merged and merged[-1]["kind"] == "pause":
            merged[-1]["seconds"] = round(min(3.0, merged[-1]["seconds"] + item["seconds"]), 2)
        else:
            merged.append(dict(item))
    planned: list[dict] = []
    for item in merged:
        if item["kind"] == "pause":
            planned.append(item)
            continue
        for part in split_script(item["text"], limit):
            planned.append({"kind": "speech", "text": part, "speaker": item["speaker"]})
    if not any(item["kind"] == "speech" for item in planned):
        raise ValidationError("Write the words to speak. A pause needs words around it.")
    return planned


def apply_say_as(text: str, pairs: list[tuple[str, str]]) -> str:
    spoken = text
    for written, replacement in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if not written or not replacement:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(written)}(?!\w)", re.IGNORECASE)
        spoken = pattern.sub(replacement, spoken)
    return spoken


def _pause_pieces(text: str) -> list[dict]:
    pieces: list[dict] = []
    cursor = 0
    for match in _PAUSE.finditer(text):
        before = text[cursor : match.start()].strip()
        if before:
            pieces.append({"kind": "speech", "text": before})
        raw = match.group(1)
        seconds = 0.6 if raw is None else float(raw)
        if seconds < 0.1 or seconds > 3:
            raise ValidationError("Keep a pause between 0.1 and 3 seconds. [pause] is about half a second.")
        pieces.append({"kind": "pause", "seconds": round(seconds, 2)})
        cursor = match.end()
    rest = text[cursor:].strip()
    if rest:
        if re.search(r"\[pause\b", rest, re.IGNORECASE):
            raise ValidationError("Write a pause as [pause] or [pause 0.8].")
        pieces.append({"kind": "speech", "text": rest})
    return pieces


def split_script(text: str, limit: int = 380) -> list[str]:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    sentences: list[str] = []
    for block in re.split(r"\n+", cleaned):
        block = block.strip()
        if not block:
            continue
        sentences.extend(_sentences(block))
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(_split_long(sentence, limit))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _sentences(block: str) -> list[str]:
    found = [match.group(0).strip() for match in _SENTENCE.finditer(block)]
    found = [item for item in found if item]
    return found or [block]


def _split_long(sentence: str, limit: int) -> list[str]:
    pieces = [part.strip() for part in re.split(r"(?<=[,;:—–])\s+", sentence) if part.strip()]
    if len(pieces) == 1:
        return [sentence[index : index + limit].strip() for index in range(0, len(sentence), limit)]
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(
                [piece[index : index + limit].strip() for index in range(0, len(piece), limit)]
            )
            continue
        candidate = f"{current} {piece}".strip()
        if current and len(candidate) > limit:
            parts.append(current)
            current = piece
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def eta_seconds(samples: list[tuple[float, int]], remaining: int) -> float | None:
    if remaining <= 0 or len(samples) < 2:
        return None
    now_t, now_b = samples[-1]
    cutoff = now_t - 5
    earlier = samples[0]
    for sample in samples:
        if sample[0] >= cutoff:
            earlier = sample
            break
    elapsed = now_t - earlier[0]
    received = now_b - earlier[1]
    if elapsed < 1 or received <= 0:
        return None
    return remaining / (received / elapsed)
