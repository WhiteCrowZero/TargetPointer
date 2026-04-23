from __future__ import annotations


DEFAULT_PERSON_VOICE_NAME = "后端默认"
DEFAULT_PERSON_VOICE_ID = ""


CUSTOM_PERSON_VOICE_ID_MAP: dict[str, str] = {
    "外国绅士": "JBFqnCBsd6RMkjVDRZzb",
    "唯美女性": "jBpfuIE2acCO8z3wKNLl",
    "粤语女性": "Xb7hH8MSUJpSbSDYk0k2",
    "旁白（男）": "2EiwWnXFnvU5JabPnv8n",
    "旁白（女）": "piTKgcLEGmPE4e6mEKli",
}


def voice_name_for_id(voice_id: str) -> str | None:
    normalized = str(voice_id or "").strip()
    if not normalized:
        return DEFAULT_PERSON_VOICE_NAME
    for name, mapped_id in CUSTOM_PERSON_VOICE_ID_MAP.items():
        if mapped_id == normalized:
            return name
    return None


def voice_choices(current_voice_id: str | None = None) -> list[tuple[str, str]]:
    normalized = str(current_voice_id or "").strip()
    choices: list[tuple[str, str]] = [(DEFAULT_PERSON_VOICE_NAME, DEFAULT_PERSON_VOICE_ID)]
    choices.extend(CUSTOM_PERSON_VOICE_ID_MAP.items())
    if normalized and normalized not in {voice_id for _name, voice_id in choices}:
        return [(DEFAULT_PERSON_VOICE_NAME, normalized), *choices[1:]]
    return choices
