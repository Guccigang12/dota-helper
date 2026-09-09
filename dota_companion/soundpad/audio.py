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


class ActiveSound:
    """Активный воспроизводимый трек в микшере."""

    __slots__ = ("audio", "cursor", "volume")

    def __init__(self, audio: np.ndarray, volume: float = 1.0) -> None:
        self.audio = audio
        self.cursor = 0
        self.volume = volume


class AudioBackend:
    def __init__(
        self,
        device_name: str = "",
        master_volume: float = 0.8,
        voice_volume: float = 1.0,
        monitor_enabled: bool = True,
    ) -> None:
        self._device_name = device_name
        self._master_volume = float(master_volume)
        self._voice_volume = float(voice_volume)
        self._monitor_enabled = bool(monitor_enabled)
        self._lock = threading.Lock()

        self._cache: dict[str, tuple[np.ndarray, int]] = {}
        self._cache_lock = threading.Lock()

        self._active_sounds: list[ActiveSound] = []
        self._active_lock = threading.Lock()

        self._wakeup_event = threading.Event()
        self._stop_event = threading.Event()

        # Двойной вывод: основной (VAC или по умолчанию) и мониторный (наушники)
        self._main_stream = None
        self._main_stream_key = None
        self._main_sr = 48000

        self._monitor_stream = None
        self._monitor_stream_key = None
        self._monitor_sr = 48000

        self._is_vac_mode = False
        self._stream_lock = threading.Lock()

        # Выделенный фоновый поток микшера и вывода звука
        self._worker_thread = threading.Thread(
            target=self._playback_loop,
            name="soundpad-mixer",
            daemon=True,
        )
        self._worker_thread.start()

    @property
    def device_name(self) -> str:
        return self._device_name

    def set_device(self, device_name: str) -> None:
        if self._device_name != device_name:
            self._device_name = device_name
            log.info("Смена аудиоустройства: %s", device_name or "Системный вывод (по умолчанию)")
            self._reset_streams()

    def set_volumes(self, master: float, voice: float) -> None:
        with self._lock:
            self._master_volume = float(master)
            self._voice_volume = float(voice)

    def set_monitor_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._monitor_enabled != enabled:
                self._monitor_enabled = bool(enabled)
                log.info("Мониторинг в наушники: %s", "включен" if enabled else "выключен")
                self._reset_monitor_stream()

    def list_output_devices(self) -> list[str]:
        """Возвращает список устройств вывода, исключая несовместимый Windows WDM-KS."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as exc:
            log.warning("sounddevice недоступен: %s", exc)
            return []

        result = []
        for i, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) <= 0:
                continue
            api_idx = dev.get("hostapi", -1)
            api_name = ""
            if 0 <= api_idx < len(hostapis):
                api_name = hostapis[api_idx].get("name", "")
            # Исключаем WDM-KS, так как blocking API PortAudio не поддерживается на Windows WDM-KS
            if "WDM-KS" in api_name:
                continue
            tag = f" [{api_name}]" if api_name else ""
            result.append(f"{i}: {dev['name']}{tag}")
        return result

    def _resolve_device_index(self) -> int | None:
        name = self._device_name.strip()
        if not name:
            return None
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception:
            return None

        # 1. Если имя начинается с "ID: ...", проверяем этот точный индекс в первую очередь
        if ":" in name:
            try:
                idx = int(name.split(":", 1)[0].strip())
                if 0 <= idx < len(devices):
                    dev = devices[idx]
                    if dev.get("max_output_channels", 0) > 0:
                        api_idx = dev.get("hostapi", -1)
                        api_name = hostapis[api_idx].get("name", "") if 0 <= api_idx < len(hostapis) else ""
                        if "WDM-KS" not in api_name:
                            label_part = name.split(":", 1)[1].strip().lower()
                            clean_label = label_part.split("[")[0].strip() if "[" in label_part else label_part
                            if not clean_label or clean_label in str(dev.get("name", "")).lower():
                                return idx
            except ValueError:
                pass

        # 2. Поиск по имени и API, исключая WDM-KS
        lowered = name.lower()
        label = lowered.split(":", 1)[1].strip() if ":" in lowered else lowered
        clean_label = label.split("[")[0].strip() if "[" in label else label
        expected_api = ""
        if "[" in label and "]" in label:
            expected_api = label[label.find("[") + 1 : label.find("]")].strip().lower()

        if clean_label:
            if expected_api:
                for i, dev in enumerate(devices):
                    if dev.get("max_output_channels", 0) <= 0:
                        continue
                    api_idx = dev.get("hostapi", -1)
                    api_name = hostapis[api_idx].get("name", "").lower() if 0 <= api_idx < len(hostapis) else ""
                    if "wdm-ks" in api_name:
                        continue
                    if clean_label in str(dev.get("name", "")).lower() and expected_api in api_name:
                        return i

            for i, dev in enumerate(devices):
                if dev.get("max_output_channels", 0) <= 0:
                    continue
                api_idx = dev.get("hostapi", -1)
                api_name = hostapis[api_idx].get("name", "").lower() if 0 <= api_idx < len(hostapis) else ""
                if "wdm-ks" in api_name:
                    continue
                if clean_label in str(dev.get("name", "")).lower():
                    return i

        return None

    @staticmethod
    def _get_default_output_index() -> int | None:
        try:
            import sounddevice as sd
            defaults = sd.default.device
            if defaults and len(defaults) > 1 and defaults[1] is not None and defaults[1] >= 0:
                return int(defaults[1])
        except Exception:
            pass
        return None

    @staticmethod
    def _open_stream(device_idx: int | None) -> tuple[any, int]:
        """Открывает поток воспроизведения с надёжным подбором поддерживаемой частоты дискретизации."""
        try:
            import sounddevice as sd
        except ImportError:
            return None, 0

        rates_to_try = [48000]
        if device_idx is not None:
            try:
                dev = sd.query_devices(device_idx)
                native = int(dev.get("default_samplerate", 0))
                if native and native not in rates_to_try:
                    rates_to_try.append(native)
            except Exception:
                pass
        if 44100 not in rates_to_try:
            rates_to_try.append(44100)

        for sr in rates_to_try:
            try:
                block = int(sr * 0.05)  # 50 мс
                stream = sd.OutputStream(
                    samplerate=sr,
                    device=device_idx,
                    channels=2,
                    dtype="float32",
                    blocksize=block,
                )
                stream.start()
                return stream, sr
            except Exception as exc:
                log.debug("Не удалось открыть поток на устройстве %s (%d Гц): %s", device_idx, sr, exc)
                continue

        log.warning("Не удалось открыть аудиопоток на устройстве %s ни с одной из частот %s", device_idx, rates_to_try)
        return None, 0

    def play(self, path: str, volume: float | None = None) -> bool:
        file_path = Path(path)
        if not file_path.is_file():
            log.warning("Файл не найден: %s", path)
            return False
        if file_path.suffix.lower() not in SUPPORTED:
            log.warning("Неподдерживаемый формат: %s", file_path.suffix)
            return False

        path_str = str(file_path)
        vol = 1.0 if volume is None else float(volume)
        log.info("▶ Воспроизведение звука: %s (громкость: %.2f)", file_path.name, vol)

        # Если звук уже в кэше — добавляем в очередь мгновенно в этом же потоке
        with self._cache_lock:
            cached = self._cache.get(path_str)

        if cached is not None:
            audio, _ = cached
            with self._active_lock:
                self._active_sounds.append(ActiveSound(audio, vol))
            self._wakeup_event.set()
            return True

        # Если не закэширован — декодируем в фоне и запускаем
        threading.Thread(
            target=self._decode_and_enqueue,
            args=(path_str, vol),
            name="soundpad-decode",
            daemon=True,
        ).start()
        return True

    def stop(self) -> None:
        """Останавливает воспроизведение всех текущих звуков."""
        with self._active_lock:
            self._active_sounds.clear()
        log.info("⏹ Воспроизведение звуков остановлено")

    def preload(self, paths: list[str]) -> None:
        """Предзагружает звуковые файлы в кэш в фоновом потоке для мгновенного старта."""
        def _loader():
            for p in paths:
                if self._stop_event.is_set():
                    break
                try:
                    p_str = str(p)
                    with self._cache_lock:
                        if p_str in self._cache:
                            continue
                    data, sr = self._decode(p_str)
                    if data is not None and sr > 0:
                        audio = self._resample_48k_stereo(data, sr)
                        with self._cache_lock:
                            self._cache[p_str] = (audio, 48000)
                except Exception as exc:
                    log.debug("Ошибка предзагрузки %s: %s", p, exc)

        threading.Thread(target=_loader, name="soundpad-preload", daemon=True).start()

    def _decode_and_enqueue(self, path: str, volume: float | None) -> None:
        with self._cache_lock:
            cached = self._cache.get(path)

        if cached is None:
            data, sample_rate = self._decode(path)
            if data is None or sample_rate <= 0:
                log.warning("Не удалось декодировать аудиофайл: %s", path)
                return
            audio = self._resample_48k_stereo(data, sample_rate)
            with self._cache_lock:
                self._cache[path] = (audio, 48000)
        else:
            audio, _ = cached

        vol = 1.0 if volume is None else float(volume)
        with self._active_lock:
            self._active_sounds.append(ActiveSound(audio, vol))
        self._wakeup_event.set()

    def _playback_loop(self) -> None:
        """Постоянный цикл микширования и потокового вывода с поддержкой дублирования в наушники."""
        chunk_size = 2400  # 50 мс при 48000 Гц

        while not self._stop_event.is_set():
            with self._active_lock:
                has_sounds = len(self._active_sounds) > 0

            if not has_sounds:
                self._wakeup_event.wait(timeout=0.2)
                self._wakeup_event.clear()
                continue

            if not self._ensure_streams():
                self._stop_event.wait(0.1)
                continue

            mixed = np.zeros((chunk_size, 2), dtype="float32")

            with self._active_lock:
                to_remove = []
                for s in self._active_sounds:
                    rem = len(s.audio) - s.cursor
                    if rem <= 0:
                        to_remove.append(s)
                        continue
                    n = min(chunk_size, rem)
                    piece = s.audio[s.cursor : s.cursor + n]
                    if s.volume != 1.0:
                        mixed[:n] += piece * s.volume
                    else:
                        mixed[:n] += piece
                    s.cursor += n
                    if s.cursor >= len(s.audio):
                        to_remove.append(s)

                for s in to_remove:
                    self._active_sounds.remove(s)

            with self._lock:
                m_vol = self._master_volume
                v_vol = self._voice_volume
                monitor_on = self._monitor_enabled

            # 1. Основной вывод (микрофон / VAC или системный вывод по умолчанию)
            if self._main_stream is not None:
                gain_main = (m_vol * v_vol) if self._is_vac_mode else m_vol
                buf_main = (mixed * gain_main) if gain_main != 1.0 else mixed.copy()
                np.clip(buf_main, -1.0, 1.0, out=buf_main)
                if self._main_sr != 48000:
                    buf_main = self._resample(buf_main, 48000, self._main_sr)
                try:
                    self._main_stream.write(buf_main)
                except Exception as exc:
                    log.warning("Ошибка записи в основной аудиопоток (%s), пересоздаю", exc)
                    self._reset_main_stream()

            # 2. Мониторный вывод в наушники (при включенном мониторинге и режиме VAC)
            if monitor_on and self._monitor_stream is not None:
                gain_mon = m_vol
                buf_mon = (mixed * gain_mon) if gain_mon != 1.0 else mixed.copy()
                np.clip(buf_mon, -1.0, 1.0, out=buf_mon)
                if self._monitor_sr != 48000:
                    buf_mon = self._resample(buf_mon, 48000, self._monitor_sr)
                try:
                    self._monitor_stream.write(buf_mon)
                except Exception as exc:
                    log.warning("Ошибка записи в мониторный поток (%s), пересоздаю", exc)
                    self._reset_monitor_stream()

    def _ensure_streams(self) -> bool:
        """Открывает или подтверждает рабочий постоянный выходной поток (основной + наушники)."""
        with self._stream_lock:
            main_idx = self._resolve_device_index()
            default_idx = self._get_default_output_index()
            is_vac_mode = (main_idx is not None and (default_idx is None or main_idx != default_idx))
            self._is_vac_mode = is_vac_mode

            # 1. Основной поток
            if self._main_stream is None or self._main_stream_key != main_idx:
                if self._main_stream is not None:
                    try:
                        self._main_stream.close()
                    except Exception:
                        pass
                    self._main_stream = None
                s, sr = self._open_stream(main_idx)
                self._main_stream = s
                self._main_sr = sr
                self._main_stream_key = main_idx
                if s is not None:
                    log.info("Основной аудиопоток открыт: устройство %s, частота %d Гц (VAC mode: %s)", main_idx, sr, is_vac_mode)
                else:
                    log.warning("Не удалось открыть основной аудиопоток для устройства %s", main_idx)

            # 2. Мониторный поток в системные наушники
            should_monitor = is_vac_mode and self._monitor_enabled
            if should_monitor:
                if self._monitor_stream is None or self._monitor_stream_key != "default":
                    if self._monitor_stream is not None:
                        try:
                            self._monitor_stream.close()
                        except Exception:
                            pass
                        self._monitor_stream = None
                    s, sr = self._open_stream(None)
                    self._monitor_stream = s
                    self._monitor_sr = sr
                    self._monitor_stream_key = "default"
                    if s is not None:
                        log.info("Мониторный поток (наушники) открыт: частота %d Гц", sr)
                    else:
                        log.warning("Не удалось открыть мониторный поток (наушники)")
            else:
                if self._monitor_stream is not None:
                    try:
                        self._monitor_stream.close()
                    except Exception:
                        pass
                    self._monitor_stream = None
                    self._monitor_stream_key = None

            return (self._main_stream is not None) or (self._monitor_stream is not None)

    def _reset_main_stream(self) -> None:
        with self._stream_lock:
            if self._main_stream is not None:
                try:
                    self._main_stream.close()
                except Exception:
                    pass
                self._main_stream = None
                self._main_stream_key = None

    def _reset_monitor_stream(self) -> None:
        with self._stream_lock:
            if self._monitor_stream is not None:
                try:
                    self._monitor_stream.close()
                except Exception:
                    pass
                self._monitor_stream = None
                self._monitor_stream_key = None

    def _reset_streams(self) -> None:
        self._reset_main_stream()
        self._reset_monitor_stream()

    def close(self) -> None:
        """Завершение работы аудио подсистемы."""
        self._stop_event.set()
        self._wakeup_event.set()
        try:
            self._worker_thread.join(timeout=1.0)
        except Exception:
            pass
        self._reset_streams()

    @staticmethod
    def _resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
        """Универсальный быстрый ресемплинг между любыми частотами дискретизации."""
        if sr_in == sr_out or data.shape[0] == 0:
            return np.ascontiguousarray(data, dtype="float32")
        duration = data.shape[0] / sr_in
        n_out = max(1, int(round(duration * sr_out)))
        x_old = np.linspace(0.0, duration, data.shape[0], endpoint=False)
        x_new = np.linspace(0.0, duration, n_out, endpoint=False)
        out = np.empty((n_out, data.shape[1]), dtype="float32")
        for ch in range(data.shape[1]):
            out[:, ch] = np.interp(x_new, x_old, data[:, ch])
        return np.ascontiguousarray(out, dtype="float32")

    @staticmethod
    def _resample_48k_stereo(data: np.ndarray, sample_rate: int) -> np.ndarray:
        """Приводит декодированный буфер к формату потока: 48000 Гц, стерео float32."""
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:
            data = data[:, :2]

        sr_in = int(sample_rate) or 48000
        if sr_in == 48000 or data.shape[0] == 0:
            return np.ascontiguousarray(data, dtype="float32")

        duration = data.shape[0] / sr_in
        n_out = max(1, int(duration * 48000))
        x_old = np.linspace(0.0, duration, data.shape[0], endpoint=False)
        x_new = np.linspace(0.0, duration, n_out, endpoint=False)
        out = np.empty((n_out, 2), dtype="float32")
        for ch in range(2):
            out[:, ch] = np.interp(x_new, x_old, data[:, ch])
        return out

    @staticmethod
    def _decode(path: str) -> tuple[np.ndarray | None, int]:
        """Декодирует аудиофайл любого поддерживаемого формата без всплывающих консолей."""
        # 1. Прямое чтение через soundfile (поддерживает WAV, MP3, FLAC, OGG в памяти)
        try:
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32", always_2d=True)
            return np.asarray(data, dtype="float32"), int(sr)
        except Exception as sf_exc:
            log.debug("soundfile прямое чтение не удалось для %s: %s", path, sf_exc)

        # 2. Обработка путей с кириллицей/спецсимволами для внешних библиотек
        suffix = Path(path).suffix.lower()
        decode_path, temp_copy = path, None
        if sys.platform == "win32" and any(ord(ch) > 127 for ch in path):
            try:
                fd, temp_name = tempfile.mkstemp(suffix=suffix, prefix="dota_sound_")
                os.close(fd)
                shutil.copyfile(path, temp_name)
                decode_path, temp_copy = temp_name, temp_name
            except OSError as exc:
                log.warning("Не удалось создать временную копию %s: %s", path, exc)

        try:
            if temp_copy is not None:
                try:
                    import soundfile as sf

                    data, sr = sf.read(decode_path, dtype="float32", always_2d=True)
                    return np.asarray(data, dtype="float32"), int(sr)
                except Exception:
                    pass

            # 3. Fallback через ffmpeg (imageio-ffmpeg) без создания окна консоли и без записи на диск
            try:
                data, sr = AudioBackend._decode_via_ffmpeg(decode_path)
                if data is not None and sr > 0:
                    return data, sr
            except Exception as ffmpeg_exc:
                log.debug("ffmpeg fallback не удался (%s)", ffmpeg_exc)

            # 4. Fallback через miniaudio
            try:
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
            except Exception as mini_exc:
                log.warning("miniaudio fallback не удался (%s)", mini_exc)

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
    def _decode_via_ffmpeg(path: str) -> tuple[np.ndarray | None, int]:
        """Декодирует аудио через ffmpeg в память (float32 СТЕРЕО 48000) БЕЗ открытия консольных окон."""
        try:
            import imageio_ffmpeg
        except ImportError:
            return None, 0

        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None, 0

        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        cmd = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            path,
            "-f",
            "f32le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=creationflags,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout:
                if result.stderr:
                    log.debug("ffmpeg decode error: %s", result.stderr[:200])
                return None, 0
            data = np.frombuffer(result.stdout, dtype="<f4")
            if data.size == 0:
                return None, 0
            return data.reshape(-1, 2), 48000
        except Exception as exc:
            log.warning("Ошибка выполнения ffmpeg: %s", exc)
            return None, 0