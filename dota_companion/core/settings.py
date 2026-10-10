from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .logger import get_logger

log = get_logger("settings")

APP_DIR_NAME = "DotaCompanion"
SETTINGS_FILE = "settings.json"


def _load_env_file() -> None:
    env_file = Path(".env")
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_env_file()


def get_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def get_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


@dataclass
class ChatRegion:
    monitor: int = 1
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def valid(self) -> bool:
        return self.width >= 20 and self.height >= 20


@dataclass
class SoundTrigger:
    label: str = ""
    path: str = ""
    in_overlay: bool = True

    @property
    def available(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()


@dataclass
class Settings:
    # OCR / Распознавание чата
    ocr_enabled: bool = False
    ocr_paused: bool = False
    ocr_interval_ms: int = 800
    ocr_lang: str = "all"
    chat_region: ChatRegion = field(default_factory=ChatRegion)

    # Переводчик (общие настройки)
    translator_provider: str = "groq"
    deepl_api_key: str = ""
    libretranslate_url: str = "https://libretranslate.com/translate"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    source_lang: str = "auto"
    target_lang: str = "ru"

    # Неограниченные / LLM провайдеры
    custom_api_url: str = "http://localhost:11434/v1"
    custom_api_key: str = ""
    custom_model: str = "qwen2.5:7b"

    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    groq_model: str = "qwen/qwen3.8-27b"

    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # Сленг и мультимодальный перевод по фото
    use_vision_for_crop: bool = True
    slang_enhancement: bool = True

    # Ручной переводчик
    manual_source_lang: str = "auto"
    manual_target_lang: str = "en"

    # Оверлей чата и уведомления
    quick_translation: bool = True
    overlay_over_game: bool = False
    notifications_enabled: bool = False
    font_size: int = 14
    showtime_ms: int = 6000

    # Саундпад
    sound_dir: str = ""
    audio_device: str = ""
    master_volume: float = 0.8
    voice_volume: float = 1.0
    soundpad_monitor: bool = True  # Дублировать звук в системные наушники при использовании VAC
    sound_triggers: list[SoundTrigger] = field(default_factory=list)
    ignored_sounds: list[str] = field(default_factory=list)  # Список скрытых/удаленных звуков

    # Оверлеи
    soundpad_overlay_visible: bool = False
    soundpad_overlay_scale: float = 1.0
    soundpad_overlay_geometry: list[int] = field(default_factory=lambda: [])
    chat_overlay_geometry: list[int] = field(default_factory=lambda: [])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        s = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name == "chat_region" and isinstance(value, dict):
                s.chat_region = ChatRegion(**{k: v for k, v in value.items() if k in ChatRegion.__dataclass_fields__})
            elif f.name == "sound_triggers" and isinstance(value, list):
                s.sound_triggers = [
                    SoundTrigger(
                        label=str(t.get("label", "")),
                        path=str(t.get("path", "")),
                        in_overlay=bool(t.get("in_overlay", True)),
                    )
                    for t in value if isinstance(t, dict)
                ]
            else:
                # Type validation and safe conversion
                target_type = f.type
                try:
                    if target_type in (bool, "bool"):
                        value = bool(value)
                    elif target_type in (int, "int"):
                        value = int(value)
                    elif target_type in (float, "float"):
                        value = float(value)
                    elif target_type in (str, "str"):
                        value = str(value)
                except (ValueError, TypeError):
                    continue
                setattr(s, f.name, value)
        return s

    def load(self, path: Path | None = None) -> None:
        path = path or get_config_dir() / SETTINGS_FILE
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Не удалось прочитать настройки %s: %s", path, exc)
            return
        try:
            loaded = self.from_dict(data)
            self.__dict__.update(loaded.__dict__)
        except Exception as exc:
            log.warning("Ошибка применения настроек из %s: %s", path, exc)

    def save(self, path: Path | None = None) -> None:
        path = path or get_config_dir() / SETTINGS_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix="settings_", suffix=".tmp")
            try:
                with open(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
        except OSError as exc:
            log.warning("Не удалось сохранить настройки в %s: %s", path, exc)
