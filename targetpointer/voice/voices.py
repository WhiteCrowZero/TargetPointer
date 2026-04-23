from __future__ import annotations


PERSON_VOICE_ID_MAP: dict[str, str] = {
    "默认音色": "l7kNoIfnJKPg7779LI2t",
    "外国绅士": "JBFqnCBsd6RMkjVDRZzb",
    "唯美女性": "jBpfuIE2acCO8z3wKNLl",
    "粤语女性": "Xb7hH8MSUJpSbSDYk0k2",
    "旁白（男）": "2EiwWnXFnvU5JabPnv8n",
    "旁白（女）": "piTKgcLEGmPE4e6mEKli",
}

def voice_name_for_id(voice_id: str) -> str | None:
    for name, mapped_id in PERSON_VOICE_ID_MAP.items():
        if mapped_id == voice_id:
            return name
    return None


def voice_choices(current_voice_id: str | None = None) -> list[tuple[str, str]]:
    choices = list(PERSON_VOICE_ID_MAP.items())
    if current_voice_id and current_voice_id not in {voice_id for _, voice_id in choices}:
        choices.insert(0, ("环境默认音色", current_voice_id))
    return choices
