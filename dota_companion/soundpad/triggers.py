                                                                              
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
    """Сканирует папку sound_dir и возвращает словарь: stem.lower() -> str(full_path)."""
    result: dict[str, str] = {}
    if not sound_dir:
        return result
    path = Path(sound_dir)
    if not path.is_dir():
        return result
    try:
        for file_path in sorted(path.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                result[file_path.stem.lower()] = str(file_path)
    except Exception as exc:
        log.warning("Ошибка сканирования папки звуков %s: %s", sound_dir, exc)
    return result


def autofill_triggers(
    triggers: list[SoundTrigger],
    sound_dir: str | Path | None,
    ignored_sounds: list[str] | None = None,
) -> list[SoundTrigger]:
    """Автозаполнение путей и добавление аудиофайлов из sound_dir, исключая ignored_sounds."""
    available = scan_sounds_dir(sound_dir)
    ignored_set = {str(s).strip().lower() for s in (ignored_sounds or []) if str(s).strip()}
    used_paths: set[str] = set()
    updated: list[SoundTrigger] = []

    # 1. Обрабатываем существующие триггеры
    for t in triggers:
        label = t.label.strip()
        stem_key = label.lower()
        if stem_key in ignored_set:
            continue

        path = t.path
        # Если путь пустой или файл отсутствует по старому пути, пробуем найти в sound_dir
        if not path or not Path(path).is_file():
            if stem_key in available:
                path = available[stem_key]

        in_overlay = getattr(t, "in_overlay", True)

        if path and Path(path).is_file():
            try:
                norm = str(Path(path).resolve())
            except Exception:
                norm = str(path)
            used_paths.add(norm)
            updated.append(SoundTrigger(label=label, path=path, in_overlay=in_overlay))
        else:
            # Если в папке вообще ничего нет, оставляем заготовку
            if not available:
                updated.append(SoundTrigger(label=label, path="", in_overlay=in_overlay))

    # 2. Автоматически добавляем все файлы из sound_dir, которых ещё нет в триггерах и не в ignored
    if sound_dir and Path(sound_dir).is_dir():
        try:
            for file_path in sorted(Path(sound_dir).iterdir()):
                if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    stem = file_path.stem.lower()
                    if stem in ignored_set:
                        continue
                    try:
                        norm = str(file_path.resolve())
                    except Exception:
                        norm = str(file_path)
                    if norm not in used_paths:
                        used_paths.add(norm)
                        updated.append(SoundTrigger(label=file_path.stem, path=str(file_path), in_overlay=True))
        except Exception as exc:
            log.warning("Ошибка чтения файлов папки звуков %s: %s", sound_dir, exc)

    if not updated and not ignored_set:
        return default_triggers()

    return updated
