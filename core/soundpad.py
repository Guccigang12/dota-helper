"""
Soundpad Module for DotaHelper.
Virtual soundboard / audio mixer for playing sound clips into in-game microphone (VAC)
and simultaneous monitoring into headphones.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("DotaHelper.Soundpad")

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac"}


class ActiveSound:
    """Active audio stream track in mixer."""
    __slots__ = ("audio", "cursor", "volume")

    def __init__(self, audio: np.ndarray, volume: float = 1.0) -> None:
        self.audio = audio
        self.cursor = 0
        self.volume = volume


class AudioBackend:
    """Low-latency audio mixer with dual outputs (VAC/Mic + Headphones monitor)."""

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

        self._cache: Dict[str, Tuple[np.ndarray, int]] = {}
        self._cache_lock = threading.Lock()

        self._active_sounds: List[ActiveSound] = []
        self._active_lock = threading.Lock()

        self._wakeup_event = threading.Event()
        self._stop_event = threading.Event()

        self._main_stream = None
        self._main_stream_key = None
        self._main_sr = 48000

        self._monitor_stream = None
        self._monitor_stream_key = None
        self._monitor_sr = 48000

        self._is_vac_mode = False
        self._stream_lock = threading.Lock()

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
            logger.info("Changed soundpad output device: %s", device_name or "Default Output")
            self._reset_streams()

    def set_volumes(self, master: float, voice: float) -> None:
        with self._lock:
            self._master_volume = max(0.0, min(2.0, float(master)))
            self._voice_volume = max(0.0, min(2.0, float(voice)))

    def set_monitor_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._monitor_enabled != enabled:
                self._monitor_enabled = bool(enabled)
                logger.info("Headphone monitoring: %s", "Enabled" if enabled else "Disabled")
                self._reset_monitor_stream()

    def list_output_devices(self) -> List[str]:
        """Query available audio output devices."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as exc:
            logger.warning("sounddevice query failed: %s", exc)
            return []

        result = []
        for i, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) <= 0:
                continue
            api_idx = dev.get("hostapi", -1)
            api_name = ""
            if 0 <= api_idx < len(hostapis):
                api_name = hostapis[api_idx].get("name", "")
            if "WDM-KS" in api_name:
                continue
            tag = f" [{api_name}]" if api_name else ""
            result.append(f"{i}: {dev['name']}{tag}")
        return result

    def _resolve_device_index(self) -> Optional[int]:
        name = self._device_name.strip()
        if not name:
            return None
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception:
            return None

        if ":" in name:
            try:
                idx = int(name.split(":", 1)[0].strip())
                if 0 <= idx < len(devices):
                    dev = devices[idx]
                    if dev.get("max_output_channels", 0) > 0:
                        return idx
            except ValueError:
                pass

        clean_label = name.lower()
        for i, dev in enumerate(devices):
            if dev.get("max_output_channels", 0) <= 0:
                continue
            if clean_label in str(dev.get("name", "")).lower():
                return i
        return None

    @staticmethod
    def _get_default_output_index() -> Optional[int]:
        try:
            import sounddevice as sd
            defaults = sd.default.device
            if defaults and len(defaults) > 1 and defaults[1] is not None and defaults[1] >= 0:
                return int(defaults[1])
        except Exception:
            pass
        return None

    @staticmethod
    def _open_stream(device_idx: Optional[int]) -> Tuple[Any, int]:
        try:
            import sounddevice as sd
        except ImportError:
            return None, 0

        rates = [48000, 44100]
        if device_idx is not None:
            try:
                dev = sd.query_devices(device_idx)
                native = int(dev.get("default_samplerate", 0))
                if native and native not in rates:
                    rates.insert(0, native)
            except Exception:
                pass

        for sr in rates:
            try:
                block = int(sr * 0.05)  # 50ms chunks
                stream = sd.OutputStream(
                    samplerate=sr,
                    device=device_idx,
                    channels=2,
                    dtype="float32",
                    blocksize=block,
                )
                stream.start()
                return stream, sr
            except Exception:
                continue
        return None, 0

    def play(self, path: str, volume: Optional[float] = None) -> bool:
        """Play sound file from disk."""
        file_path = Path(path)
        if not file_path.is_file():
            logger.warning("Audio file not found: %s", path)
            return False
        if file_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            logger.warning("Unsupported audio format: %s", file_path.suffix)
            return False

        path_str = str(file_path.resolve())
        vol = 1.0 if volume is None else float(volume)
        logger.info("Playing audio: %s (vol=%.2f)", file_path.name, vol)

        with self._cache_lock:
            cached = self._cache.get(path_str)

        if cached is not None:
            audio, _ = cached
            with self._active_lock:
                self._active_sounds.append(ActiveSound(audio, vol))
            self._wakeup_event.set()
            return True

        threading.Thread(
            target=self._decode_and_enqueue,
            args=(path_str, vol),
            name="soundpad-decode",
            daemon=True,
        ).start()
        return True

    def stop(self) -> None:
        """Stop all active audio playbacks."""
        with self._active_lock:
            self._active_sounds.clear()
        logger.info("Audio playback stopped.")

    def _decode_and_enqueue(self, path: str, volume: float) -> None:
        data, sample_rate = self._decode(path)
        if data is None or sample_rate <= 0:
            logger.warning("Failed to decode audio file: %s", path)
            return

        audio = self._resample_48k_stereo(data, sample_rate)
        with self._cache_lock:
            self._cache[path] = (audio, 48000)

        with self._active_lock:
            self._active_sounds.append(ActiveSound(audio, volume))
        self._wakeup_event.set()

    def _playback_loop(self) -> None:
        chunk_size = 2400  # 50ms at 48000 Hz

        while not self._stop_event.is_set():
            with self._active_lock:
                has_sounds = len(self._active_sounds) > 0

            if not has_sounds:
                self._wakeup_event.wait(timeout=0.1)
                self._wakeup_event.clear()
                continue

            if not self._ensure_streams():
                self._wakeup_event.wait(timeout=0.2)
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
                    mixed[:n] += s.audio[s.cursor : s.cursor + n] * s.volume
                    s.cursor += n
                    if s.cursor >= len(s.audio):
                        to_remove.append(s)

                for s in to_remove:
                    self._active_sounds.remove(s)

            with self._lock:
                m_vol = self._master_volume
                v_vol = self._voice_volume
                monitor_on = self._monitor_enabled

            # Main output
            if self._main_stream is not None:
                gain_main = (m_vol * v_vol) if self._is_vac_mode else m_vol
                buf_main = (mixed * gain_main) if gain_main != 1.0 else mixed.copy()
                np.clip(buf_main, -1.0, 1.0, out=buf_main)
                if self._main_sr != 48000:
                    buf_main = self._resample(buf_main, 48000, self._main_sr)
                try:
                    self._main_stream.write(buf_main)
                except Exception:
                    self._reset_main_stream()

            # Monitor headphones output
            if monitor_on and self._monitor_stream is not None:
                buf_mon = (mixed * m_vol) if m_vol != 1.0 else mixed.copy()
                np.clip(buf_mon, -1.0, 1.0, out=buf_mon)
                if self._monitor_sr != 48000:
                    buf_mon = self._resample(buf_mon, 48000, self._monitor_sr)
                try:
                    self._monitor_stream.write(buf_mon)
                except Exception:
                    self._reset_monitor_stream()

    def _ensure_streams(self) -> bool:
        with self._stream_lock:
            main_idx = self._resolve_device_index()
            default_idx = self._get_default_output_index()
            self._is_vac_mode = (main_idx is not None and (default_idx is None or main_idx != default_idx))

            if self._main_stream is None or self._main_stream_key != main_idx:
                if self._main_stream is not None:
                    try:
                        self._main_stream.close()
                    except Exception:
                        pass
                s, sr = self._open_stream(main_idx)
                self._main_stream = s
                self._main_sr = sr
                self._main_stream_key = main_idx

            should_monitor = self._is_vac_mode and self._monitor_enabled
            if should_monitor:
                if self._monitor_stream is None:
                    s, sr = self._open_stream(None)
                    self._monitor_stream = s
                    self._monitor_sr = sr
            else:
                if self._monitor_stream is not None:
                    try:
                        self._monitor_stream.close()
                    except Exception:
                        pass
                    self._monitor_stream = None

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

    def _reset_streams(self) -> None:
        self._reset_main_stream()
        self._reset_monitor_stream()

    def close(self) -> None:
        self._stop_event.set()
        self._wakeup_event.set()
        try:
            self._worker_thread.join(timeout=1.0)
        except Exception:
            pass
        self._reset_streams()

    @staticmethod
    def _resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
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
    def _decode(path: str) -> Tuple[Optional[np.ndarray], int]:
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            return np.asarray(data, dtype="float32"), int(sr)
        except Exception as e:
            logger.debug("soundfile direct read failed for %s: %s", path, e)

        # Unicode path copy fallback
        try:
            import soundfile as sf
            suffix = Path(path).suffix.lower()
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
            shutil.copy2(path, tmp_path)
            try:
                data, sr = sf.read(tmp_path, dtype="float32", always_2d=True)
                return np.asarray(data, dtype="float32"), int(sr)
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        except Exception as e2:
            logger.warning("Audio decoding failed for %s: %s", path, e2)

        return None, 0


class SoundpadManager:
    """Manages audio sounds directory, triggers, and playback routing."""

    def __init__(
        self,
        sounds_dir: Path = Path("sounds"),
        device_name: str = "",
        master_volume: float = 0.8,
        voice_volume: float = 1.0,
        monitor_enabled: bool = True,
    ) -> None:
        self.sounds_dir = sounds_dir
        self.sounds_dir.mkdir(parents=True, exist_ok=True)

        self.audio = AudioBackend(
            device_name=device_name,
            master_volume=master_volume,
            voice_volume=voice_volume,
            monitor_enabled=monitor_enabled,
        )

    def scan_sounds(self) -> List[Dict[str, str]]:
        """Scan sound files in sounds_dir."""
        sounds = []
        if not self.sounds_dir.exists():
            return sounds

        for f in sorted(self.sounds_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                sounds.append({
                    "name": f.stem,
                    "filename": f.name,
                    "path": str(f.resolve()),
                })
        return sounds

    def play(self, sound_path: str, volume: Optional[float] = None) -> bool:
        return self.audio.play(sound_path, volume)

    def stop(self) -> None:
        self.audio.stop()

    def close(self) -> None:
        self.audio.close()
