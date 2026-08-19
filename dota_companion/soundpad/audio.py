
   
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

from ..core.logger import get_logger

log = get_logger("audio")

SUPPORTED = {".wav", ".mp3", ".ogg", ".flac"}


class AudioBackend:
    def __init__(
        self,
        device_name: str = "",
        master_volume: float = 0.8,
        voice_volume: float = 1.0,
    ) -> None:
        self._device_name = device_name
        self._master_volume = float(master_volume)
        self._voice_volume = float(voice_volume)
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[np.ndarray, int]] = {}
        self._cache_lock = threading.Lock()

                                                                          

    @property
    def device_name(self) -> str:
        return self._device_name

    def set_device(self, device_name: str) -> None:
        self._device_name = device_name

    def set_volumes(self, master: float, voice: float) -> None:
        with self._lock:
            self._master_volume = float(master)
            self._voice_volume = float(voice)

                                                                          

    def list_output_devices(self) -> list[str]:
                                                                    
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception as exc:                
            log.warning("sounddevice недоступен: %s", exc)
            return []
        result = []
        for i, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) > 0:
                result.append(f"{i}: {dev['name']}")
        return result

    def _resolve_device_index(self) -> int | None:
                                                                                   
        name = self._device_name.strip()
        if not name:
            return None
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception:                
            return None

                                                 
        if ":" in name:
            try:
                idx = int(name.split(":", 1)[0])
                if 0 <= idx < len(devices) and devices[idx].get("max_output_channels", 0) > 0:
                    return idx
            except ValueError:
                pass

                                                                               
        lowered = name.lower()
        for i, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) > 0 and lowered in dev["name"].lower():
                return i
        return None

                                                                          

    def play(self, path: str, volume: float | None = None) -> bool:
                                                                                  
        file_path = Path(path)
        if not file_path.is_file():
            log.warning("Файл не найден: %s", path)
            return False
        if file_path.suffix.lower() not in SUPPORTED:
            log.warning("Неподдерживаемый формат: %s", file_path.suffix)
            return False
        threading.Thread(
            target=self._play_sync,
            args=(str(file_path), volume),
            name="soundpad-play",
            daemon=True,
        ).start()
        return True

    def _play_sync(self, path: str, volume: float | None) -> None:
        with self._lock:
            gain = self._master_volume * self._voice_volume * (volume if volume is not None else 1.0)
        if gain <= 0.0:
            return

        with self._cache_lock:
            cached = self._cache.get(path)
        if cached is None:
            data, sample_rate = self._decode(path)
            if data is None or sample_rate <= 0:
                return
            with self._cache_lock:
                self._cache[path] = (data, sample_rate)
        else:
            data, sample_rate = cached

        try:
            import sounddevice as sd

            device = self._resolve_device_index()
            channels = data.shape[1] if data.ndim > 1 else 1
            stream = sd.OutputStream(
                samplerate=sample_rate,
                device=device,
                channels=channels,
                dtype="float32",
            )
            with stream:
                                                                           
                chunk = max(1024, int(sample_rate * 0.05))
                for start in range(0, len(data), chunk):
                    stream.write((data[start : start + chunk] * gain).astype("float32"))
        except Exception as exc:                
            log.warning("Ошибка воспроизведения %s: %s", path, exc)

                                                                          

    @staticmethod
    def _decode(path: str) -> tuple[np.ndarray | None, int]:

           
        suffix = Path(path).suffix.lower()
        decode_path, temp_copy = path, None

        if sys.platform == "win32" and any(ord(ch) > 127 for ch in path):
            try:
                fd, temp_name = tempfile.mkstemp(suffix=suffix, prefix="dota_sound_")
                os.close(fd)
                shutil.copyfile(path, temp_name)
                decode_path, temp_copy = temp_name, temp_name
            except OSError as exc:
                log.warning("Не удалось скопировать %s во временный файл: %s", path, exc)

        try:
            if suffix in {".wav", ".flac", ".ogg"}:
                import soundfile as sf

                data, sr = sf.read(decode_path, dtype="float32", always_2d=True)
                return np.asarray(data, dtype="float32"), int(sr)

            if suffix == ".mp3":
                try:
                    data, sr = AudioBackend._decode_mp3_ffmpeg(decode_path)
                    if data is not None:
                        return data, sr
                except Exception as exc:
                    log.warning("ffmpeg-декодирование не удалось (%s), fallback на miniaudio", exc)
                import miniaudio

                decoded = miniaudio.decode_file(
                    decode_path,
                    output_format=miniaudio.SampleFormat.FLOAT32,
                    nchannels=2,
                    sample_rate=48000,
                )
                samples = np.asarray(decoded.samples, dtype="float32")
                if samples.ndim == 1:
                    samples = samples.reshape(-1, 1)
                return samples, int(decoded.sample_rate) or 48000
        except ImportError as exc:
            log.warning("Нет декодера для %s (%s). Установи soundfile/miniaudio.", suffix, exc)
        except Exception as exc:                
            log.warning("Не удалось декодировать %s: %s", path, exc)
        finally:
            if temp_copy is not None:
                try:
                    os.remove(temp_copy)
                except OSError:
                    pass
        return None, 0

    @staticmethod
    def _decode_mp3_ffmpeg(path: str) -> tuple[np.ndarray | None, int]:
        """Декодирует mp3 через ffmpeg (imageio-ffmpeg) в float32 mono 48000.

        ffmpeg декодирует корректно даже файлы, которые dr_mp3 (miniaudio)
        читает с удвоением сэмплов — результат идентичен Windows Media Player.
        """
        try:
            import imageio_ffmpeg
        except ImportError:
            raise
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        raw_path = path + ".raw"
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "48000", raw_path],
                capture_output=True,
            )
            if result.returncode != 0 or not os.path.isfile(raw_path):
                log.warning("ffmpeg decode error: %s", (result.stderr or b"")[:200])
                return None, 0
            data = np.fromfile(raw_path, dtype="<f4")
            if data.size == 0:
                return None, 0
            return data.reshape(-1, 1), 48000
        finally:
            if os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except OSError:
                    pass
