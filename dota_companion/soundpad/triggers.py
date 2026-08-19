                                                                              
from __future__ import annotations

from pathlib import Path

from ..core.logger import get_logger
from ..core.settings import SoundTrigger

log = get_logger("triggers")

DEFAULT_LABELS = [
    "good job",
    "ni hao",
    "nt",
    "thanks for a game",
    "sorry my bad",
    "gl hf",
    "gg wp",
    "nice try",
]

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac"}


def default_triggers() -> list[SoundTrigger]:
    return [SoundTrigger(label=label) for label in DEFAULT_LABELS]


def scan_sounds_dir(sound_dir: str | Path | None) -> dict[str, str]:
                                                                        
    result: dict[str, str] = {}
    if not sound_dir:
        return result
    path = Path(sound_dir)
    if not path.is_dir():
        return result
    for file_path in sorted(path.iterdir()):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            result[file_path.stem.lower()] = str(file_path)
    return result


def autofill_triggers(triggers: list[SoundTrigger], sound_dir: str | Path | None) -> list[SoundTrigger]:

       
    available = scan_sounds_dir(sound_dir)
    updated: list[SoundTrigger] = []
    for t in triggers:
        if not t.path:
            candidate = available.get(t.label.lower())
            if candidate:
                updated.append(SoundTrigger(label=t.label, path=candidate))
                continue
        updated.append(SoundTrigger(label=t.label, path=t.path))
    return updated
