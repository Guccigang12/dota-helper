                                                       
from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = "dota_companion"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
                                                                             
    root = logging.getLogger(_ROOT)
    if root.handlers:
        return root
    root.setLevel(level)
    root.propagate = False

    if sys.stdout is not None:
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
        root.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "dota_companion.log", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
            root.addHandler(file_handler)
        except OSError:
            root.warning("Не удалось открыть файл лога в %s", log_dir)

    return root


def get_logger(name: str) -> logging.Logger:
                                                                
    return logging.getLogger(f"{_ROOT}.{name}")
