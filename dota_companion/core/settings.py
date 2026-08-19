
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

    @property
    def available(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()


@dataclass
class Settings:
                             
    market_token: str = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjEwODY1MjQ4LCJpc3MiOiJsenQiLCJpYXQiOjE3ODY5OTE2NDYsImp0aSI6IjEwMDY0NTYiLCJzY29wZSI6ImJhc2ljIHJlYWQgcG9zdCBjb252ZXJzYXRlIHBheW1lbnQgaW52b2ljZSBjaGF0Ym94IG1hcmtldCIsImV4cCI6MTk0NDY3MTY0Nn0.AluJS9sdL1ReFQwkPTkORrxBJzOLaGPSLW8zob4Y03v-KPQiXFYsB5iElnhkQDo8lgJlIyYqWffgbI-4a5gWXnGhOoiODRp7Y6nnQc-55ICyJ_CPyo227y2tVH_o4Qyxe8ANYv4XSpEE-Ecf82pQzNTf6jNF4_xK44DLrJVVN_Q"
    market_currency: str = "usd"                                                   
    market_app_id: int = 570                      
    market_ignore_cache: bool = False
    market_min_interval: float = 0.8                                           

                    
    gsi_port: int = 34567
    gsi_auth_token: str = ""
    steam_api_key: str = ""
    console_log_path: str = ""                                     

                 
    ocr_enabled: bool = False
    ocr_paused: bool = False
    ocr_interval_ms: int = 800
    ocr_lang: str = "ch"                                   
    chat_region: ChatRegion = field(default_factory=ChatRegion)

                     
    translator_provider: str = "gemini"                                             
    deepl_api_key: str = ""
    libretranslate_url: str = "https://libretranslate.com/translate"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    source_lang: str = "auto"
    target_lang: str = "ru"

                                                      
    manual_source_lang: str = "auto"
    manual_target_lang: str = "en"

                           
    quick_translation: bool = True
    overlay_over_game: bool = False
    notifications_enabled: bool = False
    font_size: int = 14
    showtime_ms: int = 6000

                      
    sound_dir: str = ""                                              
    audio_device: str = ""                                                                               
    master_volume: float = 0.8
    voice_volume: float = 1.0
    sound_triggers: list[SoundTrigger] = field(default_factory=list)

                                                                 
                                                                
    account_mappings: dict[str, str] = field(default_factory=dict)

                    
    soundpad_overlay_visible: bool = False
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
                    SoundTrigger(label=str(t.get("label", "")), path=str(t.get("path", "")))
                    for t in value if isinstance(t, dict)
                ]
            else:
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
            path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Не удалось сохранить настройки в %s: %s", path, exc)
