from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import aiohttp
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from .core.async_worker import AsyncWorker
from .core.hotkeys import HotkeyManager
from .core.logger import configure_logging, get_logger
from .core.settings import Settings, ChatRegion, get_config_dir, get_data_dir
from .dota.console_log import ConsoleLogTailer
from .dota.gsi_server import GsiServer
from .dota.match_state import MatchState, Player, STEAM_ID64_OFFSET, sanitize_name
from .dota import opendota
from .dota import steam_api
from .dota.steam_api import fetch_realtime_stats, search_player_by_name
from .market.client import MarketClient
from .market.models import InventoryValue
from .ocr.capturer import ScreenCapturer
from .ocr.engine import OcrEngine
from .ocr.translator import Translator
from .soundpad.audio import AudioBackend
from .soundpad.triggers import autofill_triggers, default_triggers
from .ui.chat_overlay import ChatOverlay
from .ui.chat_tab import ChatTab
from .ui.crop_overlay import CropOverlay
from .ui.main_window import MainWindow
from .ui.match_tab import MatchTab
from .ui.soundpad_overlay import SoundPadOverlay
from .ui.soundpad_tab import SoundPadTab
from .ui.translator_tab import TranslatorTab
from .ui.theme import apply_theme

log = get_logger("app")

GAME_STATE_ACTIVE = "DOTA_GAMERULES_STATE_HERO_SELECTION"


class _ResultBridge(QObject):
    done = pyqtSignal(object)


class DotaCompanionApplication(QObject):

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        apply_theme(qapp)

        self.settings = Settings()
        self.settings.load()
        configure_logging(get_config_dir())

        self.worker = AsyncWorker(self)
        self.worker.task_failed.connect(self._on_task_failed)

        self.audio = AudioBackend(
            device_name=self.settings.audio_device,
            master_volume=self.settings.master_volume,
            voice_volume=self.settings.voice_volume,
        )
        self.capturer = ScreenCapturer()
        self.engine = OcrEngine(lang=self.settings.ocr_lang)
        self.translator = Translator(
            provider=self.settings.translator_provider,
            deepl_api_key=self.settings.deepl_api_key,
            libretranslate_url=self.settings.libretranslate_url,
            source_lang=self.settings.source_lang,
            target_lang=self.settings.target_lang,
            gemini_api_key=self.settings.gemini_api_key,
            gemini_model=self.settings.gemini_model,
        )
        self.market = MarketClient(
            token=self.settings.market_token,
            currency=self.settings.market_currency,
            app_id=self.settings.market_app_id,
            ignore_cache=self.settings.market_ignore_cache,
            min_interval=self.settings.market_min_interval,
        )
        self.hotkeys = HotkeyManager(self)

        self.gsi: GsiServer | None = None
        self._dota_session: aiohttp.ClientSession | None = None
        self.heroes: dict[int, str] = {}
        self.match_state = MatchState()
        self._current_match_id: int | None = None
        self._server_steam_id: str | None = None
        self._pending_steam_ids: set[int] = set()  # Буфер: Steam ID из консоли до появления ростера

        self._last_ocr_text = ""
        self._last_notify_ts = 0.0
        self._bridges: set[_ResultBridge] = set()

        self.match_tab = MatchTab(self.settings)
        self.chat_tab = ChatTab(self.settings)
        self.translator_tab = TranslatorTab(self.settings)
        self.soundpad_tab = SoundPadTab(self.settings, self.audio.list_output_devices)
        self.window = MainWindow(
            self.settings, self.match_tab, self.chat_tab, self.translator_tab, self.soundpad_tab
        )

        self.chat_overlay = ChatOverlay(self.settings)
        self.soundpad_overlay = SoundPadOverlay(self.settings)
        self.crop_overlay: CropOverlay | None = None

        self._ocr_timer = QTimer(self)
        self._ocr_timer.timeout.connect(self._capture_frame)
        self._ocr_timer.setInterval(self.settings.ocr_interval_ms)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self.settings.save)

        self._wire_signals()
        self._reload_triggers()

    def _wire_signals(self) -> None:

        self.hotkeys.calibrate_region.connect(self.open_crop_overlay)
        self.hotkeys.toggle_pause.connect(self._on_pause_hotkey)
        self.hotkeys.exit_app.connect(self._on_exit)

        self.match_tab.evaluate_requested.connect(self.evaluate_player)
        self.match_tab.refresh_roster_requested.connect(self._on_refresh_roster)
        self.match_tab.auto_evaluate_toggled.connect(self._on_auto_evaluate_toggled)
        self.match_tab.settings_changed.connect(self._on_settings_changed)

        self.chat_tab.ocr_toggle_requested.connect(self._set_ocr_active)
        self.chat_tab.region_requested.connect(self.open_crop_overlay)
        self.chat_tab.settings_changed.connect(self._on_settings_changed)

        self.translator_tab.translate_requested.connect(self._on_manual_translate)
        self.translator_tab.settings_changed.connect(self._on_settings_changed)

        self.soundpad_tab.play_requested.connect(self.audio.play)
        self.soundpad_tab.refresh_requested.connect(self._reload_triggers)
        self.soundpad_tab.settings_changed.connect(self._on_settings_changed)
        self.soundpad_tab.show_overlay_requested.connect(self._show_soundpad_overlay)

        self.soundpad_overlay.play_requested.connect(self.audio.play)
        self.soundpad_overlay.refresh_requested.connect(self._reload_triggers)
        self.soundpad_overlay.volume_changed.connect(self._on_overlay_volume)
        self.soundpad_overlay.close_requested.connect(self._hide_soundpad_overlay)

        self.window.quit_requested.connect(self._on_exit)
        self.window.pause_toggled.connect(self._on_pause_hotkey)
        self.window.crop_requested.connect(self.open_crop_overlay)
        self.window.soundpad_overlay_toggled.connect(self._set_soundpad_overlay_visible)

        self.engine.signals.text_ready.connect(self._on_ocr_text)
        self.engine.signals.error.connect(lambda msg: self.window.set_status(msg))

    def start(self) -> None:
        log.info("Запуск Dota Companion")

        self.worker.start()
        if not self.worker.wait_ready(10):
            log.error("Event loop не запустился")
            return

        self.gsi = GsiServer(
            self.worker.loop,
            port=self.settings.gsi_port,
            auth_token=self.settings.gsi_auth_token,
        )
        self.gsi_signals = self.gsi.signals
        self.gsi.signals.match_started.connect(self._on_match_started)
        self.gsi.signals.match_ended.connect(self._on_match_ended)
        self.gsi.signals.game_state_changed.connect(self._on_game_state)
        self.gsi.signals.roster_update.connect(self._on_gsi_roster)
        self.gsi.signals.server_error.connect(self._on_gsi_error)
        self.gsi.signals.server_steam_id_found.connect(self._on_server_steam_id)
        self._submit("gsi-start", self.gsi.start(), lambda ok, _res: self._on_gsi_started())

        self.worker.submit("heroes", self._load_heroes())

        self.engine.start()
        self.tailer = ConsoleLogTailer(self._console_log_path)
        self.tailer.signals.chat_line.connect(self._on_log_chat)
        self.tailer.signals.steam_ids.connect(self._on_console_steam_ids)
        self.tailer.signals.status_players.connect(self._on_console_status_players)
        self.tailer.signals.status.connect(
            lambda msg, is_err: self.window.set_status(msg)
        )
        self.tailer.start()
        self.hotkeys.start()

        self.engine.set_paused(not (self.settings.ocr_enabled and not self.settings.ocr_paused))
        if self.settings.ocr_enabled and not self.settings.ocr_paused:
            self._ocr_timer.start()

        self.chat_overlay.apply_settings()
        if self.settings.soundpad_overlay_visible:
            self.soundpad_overlay.show()

        self.window.show()
        self.window.set_status("Dota Companion готов. Хоткеи: Ctrl+Shift+F8 — область чата, F9 — пауза, F10 — выход")

    def shutdown(self) -> None:
        log.info("Завершение работы")
        try:
            self.settings.save()
        except Exception:
            pass

        self._ocr_timer.stop()
        self.engine.stop()
        if getattr(self, "tailer", None) is not None:
            self.tailer.stop()
        self.hotkeys.stop()
        self.capturer.close()

        if self.gsi is not None and self.worker.isRunning():
            try:
                self.worker.submit("gsi-stop", self.gsi.stop())
            except RuntimeError:
                pass
        if self.worker.isRunning():
            self.worker.submit("close-sessions", self._close_sessions())
        self.worker.stop()
        self.window.force_close()

    def _on_gsi_started(self, _result=None) -> None:
        self.window.set_status(f"GSI-сервер активен на порту {self.settings.gsi_port}")

    def _on_gsi_error(self, message: str) -> None:
        self.window.set_status(message)
        self.window.show_tray_message("Dota Companion", message)

    def _on_game_state(self, state: str) -> None:
        log.debug("game_state: %s", state)
        self.window.set_status(f"Игровое состояние: {state}")

    def _on_match_started(self, match_id: int) -> None:
        self._current_match_id = match_id
        self._server_steam_id = None
        self._pending_steam_ids = set()  # Сбрасываем буфер при новом матче
        self.match_state = MatchState(match_id=match_id)
        if hasattr(self, '_realtime_timer'):
            self._realtime_timer.stop()
        if hasattr(self, 'tailer'):
            self.tailer.reset()
        status = f"Матч {match_id} · Автоматический подхват Steam ID…"
        self.match_tab.set_status(status)
        self.window.set_status(status)
        log.info("Новый матч %s: авто-загрузка ростера и Steam ID", match_id)
        self._submit(
            "match-fetch",
            self._load_match_and_live(match_id),
            lambda ok, res: self._on_roster_ready(ok, res, match_id),
        )

    def _on_live_match_ready(self, ok: bool, result, match_id: int) -> None:
        if match_id != self._current_match_id:
            return
        if ok and result:
            log.info("Live-данные матча %s: получено %d игроков с никами", match_id, len(result))
            self.match_state.set_players(result)
            self.match_state.sort_players()
            self.match_tab.set_players(self.match_state.players)
            status = f"Матч {match_id} · {len(self.match_state.players)} игроков с никами"
            self.match_tab.set_status(status)
            self.window.set_status(status)
            for player in self.match_state.players:
                if player.account_id and player.account_id > 0 and player.value_status != "ok":
                    self.evaluate_player(player)

    async def _load_heroes(self) -> None:
        session = await self._get_dota_session()
        self.heroes = await opendota.fetch_heroes(session)
        log.info("Загружено героев: %d", len(self.heroes))

    async def _load_match_and_live(self, match_id: int) -> list[Player]:
        session = await self._get_dota_session()
        if not self.heroes:
            self.heroes = await opendota.fetch_heroes(session)

        match_players = await opendota.fetch_match(session, match_id, self.heroes)
        if match_players and len(match_players) >= 2:
            return match_players

        for attempt in range(4):
            try:
                live_players = await opendota.fetch_live_match(session, match_id, self.heroes)
                if live_players and len(live_players) >= 2:
                    return live_players
            except Exception:
                pass
            await asyncio.sleep(2.5)

        return match_players or []

    async def _load_live_match(self, match_id: int) -> list[Player]:
        return await self._load_match_and_live(match_id)

    def _on_match_ended(self) -> None:
        """Игра закончилась (matchid сброшен в 0) — ростер и оценки остаются на экране."""
        log.info("Матч завершён по GSI")
        if hasattr(self, '_realtime_timer'):
            self._realtime_timer.stop()
        self._server_steam_id = None
        n = len(self.match_state.players)
        status = f"Матч окончен · {n} игроков в ростере"
        self.match_tab.set_status(status)
        self.window.set_status(status)

    def _on_server_steam_id(self, server_steam_id: str) -> None:
        """Получен server_steam_id из GSI — настраиваем периодический опрос GetRealtimeStats."""
        if server_steam_id == self._server_steam_id:
            return
        self._server_steam_id = server_steam_id
        log.info("server_steam_id: %s", server_steam_id)

        if not self.settings.steam_api_key:
            log.debug("steam_api_key не задан — пропускаем GetRealtimeStats")
            return

        if not hasattr(self, '_realtime_timer'):
            self._realtime_timer = QTimer(self)
            self._realtime_timer.timeout.connect(self._poll_realtime_stats)
        self._realtime_timer.setInterval(15_000)
        self._realtime_timer.start()
        # Первый опрос — сразу
        self._poll_realtime_stats()

    def _poll_realtime_stats(self) -> None:
        """Запрос GetRealtimeStats через Steam Web API для получения account_id всех игроков."""
        if not self._server_steam_id or not self.settings.steam_api_key:
            return

        async def _do_poll():
            session = await self._get_dota_session()
            return await fetch_realtime_stats(
                session, self._server_steam_id, self.settings.steam_api_key
            )

        def _on_result(ok: bool, players: list[Player]) -> None:
            if not ok or not players:
                return
            # Подставляем hero_name
            for p in players:
                if p.hero_id and not p.hero_name:
                    p.hero_name = self.heroes.get(p.hero_id, "")
            self.match_state.set_players(players)
            self.match_state.sort_players()
            self.match_tab.set_players(self.match_state.players)
            status = f"Матч {self._current_match_id or ''} · {len(self.match_state.players)} игроков (Steam API)"
            self.match_tab.set_status(status)
            self.window.set_status(status)

            # Оцениваем тех, у кого есть account_id и ещё нет оценки
            for player in self.match_state.players:
                if player.account_id and player.account_id > 0 and player.value_status != "ok":
                    self.evaluate_player(player)

        self._submit("realtime-stats", _do_poll(), _on_result)

    def _on_auto_evaluate_toggled(self, enabled: bool) -> None:
        """Включили «Авто-оценку» — оцениваем тех, у кого ещё нет стоимости."""
        if not enabled:
            return
        if not self.match_state.players:
            return
        for player in self.match_state.players:
            if player.value_status != "ok":
                self.evaluate_player(player)

    def _on_console_steam_ids(self, steam_ids: list[str]) -> None:
        """Обнаружение Steam ID из console.log и автоматическое привязывание к нераспределенным слотам."""
        if not steam_ids:
            return

        # Если ростер ещё не пришёл — буферизуем Steam ID для позднейшей обработки
        if not self.match_state.players:
            for s_id in steam_ids:
                try:
                    steam_id64 = int(s_id)
                    account_id = steam_id64 - STEAM_ID64_OFFSET
                    if account_id > 0:
                        self._pending_steam_ids.add(account_id)
                except ValueError:
                    pass
            log.debug("Буферизовано %d Steam ID (ростер ещё не готов)", len(self._pending_steam_ids))
            return

        local_p = next((pl for pl in self.match_state.players if pl.is_local), None)
        updated = False

        for s_id in steam_ids:
            try:
                steam_id64 = int(s_id)
                account_id = steam_id64 - STEAM_ID64_OFFSET
            except ValueError:
                continue

            if account_id <= 0:
                continue

            # Если это локальный игрок (вы) — привязываем строго к вашему слоту!
            if local_p and (local_p.account_id is None or local_p.account_id == account_id):
                if local_p.account_id != account_id:
                    local_p.account_id = account_id
                    updated = True
                if local_p.value_status != "ok":
                    self.evaluate_player(local_p)
                continue

            p = self.match_state.player_by_account(account_id)
            if p is None:
                # Сохраняем в буфер на случай позднейшей привязки
                self._pending_steam_ids.add(account_id)
                continue

            if p and p.steam_id64 and p.value_status != "ok":
                self.evaluate_player(p)
                updated = True

        if updated:
            self.match_state.sort_players()
            self.match_tab.set_players(self.match_state.players)
            status = f"Матч {self._current_match_id or ''} · {len(self.match_state.players)} игроков (Steam ID подтянуты)"
            self.match_tab.set_status(status)
            self.window.set_status(status)

    def _on_console_status_players(self, data: dict) -> None:
        """Обработка списка 10 игроков, выведенного командой status в консоли Dota 2."""
        match_id = data.get("match_id")
        parsed_players = data.get("players") or []
        if not parsed_players:
            return

        key = (match_id, tuple((p["slot"], p["name"]) for p in parsed_players))
        if hasattr(self, "_last_status_key") and self._last_status_key == key:
            return
        self._last_status_key = key

        if match_id and match_id != self._current_match_id:
            self._current_match_id = match_id

        # Определяем локального игрока по имени из существующего ростера
        local_name = None
        local_p_existing = next((pl for pl in self.match_state.players if pl.is_local), None)
        if local_p_existing:
            local_name = sanitize_name(local_p_existing.name).lower()

        # Строим новый ростер по слотам из status — это самый надёжный источник никнеймов
        # Слоты 1-5 → Radiant, 6-10 → Dire (стандартный порядок status в Dota 2)
        new_players: list[Player] = []
        for p_info in sorted(parsed_players, key=lambda x: x["slot"]):
            slot = p_info["slot"]
            name = p_info["name"]
            is_radiant = p_info["is_radiant"]
            parsed_acc_id = p_info.get("account_id")

            # Ищем этого игрока в существующем ростере чтобы сохранить account_id и оценку
            existing = self.match_state.player_by_name(name)
            if existing is None and parsed_acc_id:
                existing = self.match_state.player_by_account(parsed_acc_id)

            account_id = None
            hero_id = None
            hero_name = ""
            value_status = ""
            inventory_value = None
            currency = ""
            value_error = ""
            is_local = False

            if existing:
                account_id = existing.account_id
                hero_id = existing.hero_id
                hero_name = existing.hero_name
                value_status = existing.value_status
                inventory_value = existing.inventory_value
                currency = existing.currency
                value_error = existing.value_error
                is_local = existing.is_local

            # Если account_id пришёл из status — используем его
            if parsed_acc_id and (account_id is None or account_id <= 0):
                account_id = parsed_acc_id

            # Определяем локального игрока по имени
            if local_name and sanitize_name(name).lower() == local_name:
                is_local = True

            p = Player(
                account_id=account_id,
                name=name,
                hero_id=hero_id,
                hero_name=hero_name,
                is_radiant=is_radiant,
                is_local=is_local,
            )
            p.value_status = value_status
            p.inventory_value = inventory_value
            p.currency = currency
            p.value_error = value_error
            new_players.append(p)

        if not new_players:
            return

        # Полностью заменяем ростер — status даёт правильный порядок игроков
        self.match_state.players = new_players
        self.match_state.sort_players()
        self.match_tab.set_players(self.match_state.players)
        status = f"Матч {self._current_match_id or ''} · {len(new_players)} игроков из команды status"
        self.match_tab.set_status(status)
        self.window.set_status(status)

        # Запускаем оценку или поиск Steam ID для каждого игрока
        for p in self.match_state.players:
            if p.value_status == "ok":
                continue
            if p.account_id and p.account_id > 0:
                self.evaluate_player(p)
            elif not p.is_local and p.name and len(p.name) >= 2:
                self._submit(
                    f"search-{p.name}",
                    self._fetch_player_by_search(p.name),
                    lambda ok, acc_id, target_p=p: self._on_player_search_done(target_p, acc_id if ok else None),
                )

    def _on_player_search_done(self, player: Player, account_id: int | None) -> None:
        """Поиск по нику завершён — устанавливаем account_id и оцениваем инвентарь."""
        if account_id and account_id > 0:
            player.account_id = account_id
            log.info("Игрок '%s' найден через OpenDota Search: account_id = %d", player.name, account_id)
            self.match_tab.update_player(player)
            self.evaluate_player(player)
        else:
            log.warning("Игрок '%s' не найден через OpenDota Search", player.name)

    def _on_log_chat_roster(self, channel: str, name: str, text: str) -> None:
        """Вспомогательный метод: привязка игрока по нику из чата (вызывается из _on_log_chat)."""
        log.info("Чат [%s] %s: %s", channel, name, text)
        if name and not name.startswith("Игрок ") and not name.startswith("Player ") and name != "Вы":
            # Если игрок с таким ником уже есть в ростере — просто оцениваем
            existing = self.match_state.player_by_name(name)
            if existing and existing.account_id and existing.value_status != "ok":
                self.evaluate_player(existing)
                return
            # Иначе ищем по нику
            unassigned = [pl for pl in self.match_state.players if pl.account_id is None or pl.account_id <= 0]
            if unassigned:
                target_p = self.match_state.player_by_name(name) or unassigned[0]
                if not self.match_state.player_by_name(name):
                    target_p.name = name
                self._submit(
                    f"search-{name}",
                    self._fetch_player_by_search(name),
                    lambda ok, acc_id, p=target_p: self._on_player_search_done(p, acc_id if ok else None),
                )

    def _on_player_name_ready(self, player: Player, name: str) -> None:
        if name:
            player.name = name
            self.match_tab.update_player(player)

    def _on_gsi_roster(self, data: dict) -> None:
        """Ростер 10 игроков прямо из GSI (реальное время)."""
        roster = data.get("players") or []
        local_steam_id64 = data.get("local_steam_id64")
        players: list[Player] = []
        for info in roster:
            raw_steam_id = info.get("steam_id64") or 0
            account_id = (raw_steam_id - STEAM_ID64_OFFSET) if raw_steam_id > 0 else None
            hero_id = info.get("hero_id")
            name = sanitize_name(info.get("name")) or (
                f"Player {account_id}" if account_id else f"Игрок {info.get('slot', 0) + 1}")
            players.append(
                Player(
                    account_id=account_id,
                    name=name,
                    hero_id=hero_id,
                    hero_name=self.heroes.get(hero_id, "") if hero_id else "",
                    is_radiant=info.get("is_radiant", True),
                    is_local=local_steam_id64 is not None and raw_steam_id == local_steam_id64,
                )
            )
        if not players:
            return
        self.match_state.set_players(players)
        self.match_state.sort_players()
        self.match_tab.set_players(self.match_state.players)
        if self._current_match_id is not None:
            status = f"Матч {self._current_match_id} · {len(self.match_state.players)} игроков (GSI)"
        else:
            status = f"Ростер: {len(self.match_state.players)} игроков (GSI)"
        self.match_tab.set_status(status)
        self.window.set_status(status)

        # Применяем буферизованные Steam ID из консоли
        if self._pending_steam_ids:
            applied = False
            for account_id in list(self._pending_steam_ids):
                p = self.match_state.player_by_account(account_id)
                if p is not None and p.value_status != "ok":
                    self.evaluate_player(p)
                    self._pending_steam_ids.discard(account_id)
                    applied = True
            if applied:
                log.info("Применены буферизованные Steam ID после появления ростера GSI")

        # Оцениваем всех игроков — evaluate_player сам решит искать по ID или по нику
        for player in self.match_state.players:
            if player.value_status in ("ok", "loading", "error"):
                continue
            self.evaluate_player(player)

    async def _fetch_player_name(self, account_id: int) -> str | None:
        session = await self._get_dota_session()
        return await opendota.fetch_player_name(session, account_id)

    async def _find_player_account_id(self, name: str) -> int | None:
        "Поиск account_id: сначала Steam API (ResolveVanityURL), потом OpenDota."
        session = await self._get_dota_session()
        if self.settings.steam_api_key:
            acc_id = await search_player_by_name(session, name, self.settings.steam_api_key)
            if acc_id:
                log.info("Steam API нашёл '%s' -> account_id=%d", name, acc_id)
                return acc_id
            log.debug("Steam API не нашёл '%s', пробуем OpenDota", name)
        acc_id = await opendota.fetch_player_by_search(session, name)
        if acc_id:
            log.info("OpenDota нашёл '%s' -> account_id=%d", name, acc_id)
        return acc_id

    async def _fetch_player_by_search(self, name: str) -> int | None:
        session = await self._get_dota_session()
        return await opendota.fetch_player_by_search(session, name)

    # _load_heroes определён выше (строка ~294), дубликат удалён

    async def _load_match(self, match_id: int) -> list:
        session = await self._get_dota_session()
        if not self.heroes:
            self.heroes = await opendota.fetch_heroes(session)
        return await opendota.fetch_match(session, match_id, self.heroes)

    async def _get_dota_session(self) -> aiohttp.ClientSession:
        if self._dota_session is None or self._dota_session.closed:
            self._dota_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"User-Agent": "DotaCompanion/1.0"},
            )
        return self._dota_session

    async def _close_sessions(self) -> None:
        if self._dota_session is not None and not self._dota_session.closed:
            await self._dota_session.close()
        await self.translator.close()
        await self.market.close()

    def _on_roster_ready(self, ok: bool, result, match_id: int) -> None:
        if match_id != self._current_match_id:
            return
        if not ok:
            return
        players = result
        self.match_state.set_players(players)
        self.match_state.sort_players()
        self.match_tab.set_players(self.match_state.players)
        self.match_tab.set_status(f"Матч {self._current_match_id} · {len(self.match_state.players)} игроков")

        if self.match_tab.auto_evaluate_enabled():
            for player in self.match_state.players:
                self.evaluate_player(player)

    def _on_refresh_roster(self) -> None:
        if not self.match_state.players:
            msg = "Ростер пуст — дождитесь начала матча"
            self.match_tab.set_status(msg)
            self.window.set_status(msg)
            return

        n = len(self.match_state.players)
        msg = f"Переоценка всех {n} игроков…"
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        for player in self.match_state.players:
            player.value_status = ""  # сброс — разрешаем повторный поиск
            player.value_error = ""
            self.evaluate_player(player)

    def evaluate_player(self, player) -> None:
        """Оценка всего Steam-инвентаря игрока по его профилю."""
        # Не дублируем запрос если уже грузится или уже есть результат
        if player.value_status == "loading":
            return

        player.currency = self.settings.market_currency

        if player.steam_id64 is None or player.account_id is None or player.account_id <= 0:
            # Нет Steam ID — ищем через Steam API, потом OpenDota как fallback
            name = player.name
            generic = not name or name.startswith("Игрок ") or name.startswith("Player ") or name == "Вы"
            if not generic and len(name) >= 2:
                player.value_status = "loading"
                self.match_tab.update_player(player)
                msg = f"Поиск Steam ID для '{name}'..."
                self.match_tab.set_status(msg)
                self.window.set_status(msg)
                log.info(msg)
                self._submit(
                    f"search-eval-{name}",
                    self._find_player_account_id(name),
                    lambda ok, acc_id, p=player: self._on_search_then_evaluate(p, acc_id if ok else None),
                )
            else:
                player.value_status = ""
                player.value_error = ""
                self.match_tab.update_player(player)
                msg = f"Игрок '{name}': Steam ID не привязан (нет ника для поиска)"
                self.window.set_status(msg)
            return

        self.match_tab.set_player_loading(player)
        msg = f"Запрос инвентаря на Lolzteam Market для {player.name}..."
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        log.info(msg)

        self._submit(
            f"market-eval-{player.account_id}",
            self.market.get_inventory_value_by_link(player.steam_id64),
            lambda ok, result: self._on_value_ready(player, result) if ok else self._on_value_error(player, result),
        )

    def _on_search_then_evaluate(self, player: Player, account_id: int | None) -> None:
        """Получен account_id после поиска по нику — сразу оцениваем инвентарь."""
        if account_id and account_id > 0:
            player.account_id = account_id
            log.info("Найден account_id=%d для '%s', запускаем оценку инвентаря", account_id, player.name)
            self.match_tab.update_player(player)
            # Сбрасываем статус чтобы evaluate_player прошёл проверку
            player.value_status = ""
            self.evaluate_player(player)
        else:
            player.value_status = "error"
            player.value_error = (
                "Steam ID не найден автоматически. "
                "Введите 'status' в консоли Dota 2 (~) — все Steam ID подтянутся мгновенно."
            )
            self.match_tab.update_player(player)
            msg = f"'{player.name}': Steam ID не найден — введите 'status' в консоли Dota 2"
            self.match_tab.set_status(msg)
            self.window.set_status(msg)
            log.warning(msg)

    # Дубликат _on_player_search_done удалён — используется определение выше (~строка 463)

    def _on_value_ready(self, player, value: InventoryValue) -> None:
        player.inventory_value = value.amount
        player.currency = value.currency
        player.value_status = "ok"
        player.value_error = ""
        self.match_tab.update_player(player)
        msg = f"{player.name}: инвентарь ≈ {value.formatted()}"
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        log.info(msg)

    def _on_value_error(self, player, error: Exception) -> None:
        player.value_status = "error"
        player.value_error = str(error)
        self.match_tab.update_player(player)
        msg = f"Ошибка оценки {player.name}: {error}"
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        log.warning(msg)

    def _capture_frame(self) -> None:
        region: ChatRegion = self.settings.chat_region
        if not region.valid:
            return
        if not self.settings.ocr_enabled or self.settings.ocr_paused:
            return
        monitors = self.capturer.monitors()
        if not (1 <= region.monitor < len(monitors)):
            return
        mon = monitors[region.monitor]
        bbox = {
            "left": mon["left"] + region.x,
            "top": mon["top"] + region.y,
            "width": region.width,
            "height": region.height,
        }
        try:
            image = self.capturer.grab(bbox)
        except Exception:
            return
        self.engine.submit(image)

    def _set_ocr_active(self, active: bool) -> None:
        self.settings.ocr_enabled = active
        paused = not (active and not self.settings.ocr_paused)
        self.engine.set_paused(paused)
        if active and not self.settings.ocr_paused:
            self._ocr_timer.start()
        else:
            self._ocr_timer.stop()
        self.chat_tab.set_ocr_active(active)

    def _on_pause_hotkey(self) -> None:
        self.chat_tab.toggle_pause()
        self._sync_ocr_from_settings()

    def _sync_ocr_from_settings(self) -> None:
        paused = not (self.settings.ocr_enabled and not self.settings.ocr_paused)
        self.engine.set_paused(paused)
        if self.settings.ocr_enabled and not self.settings.ocr_paused:
            self._ocr_timer.start()
        else:
            self._ocr_timer.stop()

    def _on_ocr_text(self, text: str) -> None:
        if not text or text == self._last_ocr_text:
            return
        self._last_ocr_text = text
        self.chat_tab.add_message("OCR", text)
        if self.settings.quick_translation:
            self._translate_and_show("OCR", text)

    def _on_log_chat(self, channel: str, name: str, text: str) -> None:
        line = f"{name}: {text}"
        self.chat_tab.add_message("ЛОГ", line)
        if self.settings.quick_translation:
            self._translate_and_show("ЛОГ", line)
        # Попытка привязать игрока по нику из чата
        if self.match_state.players:
            self._on_log_chat_roster(channel, name, text)

    def _translate_and_show(self, source: str, text: str) -> None:
        self._submit(
            "translate",
            self.translator.translate(text),
            lambda ok, translated: self._on_translated(source, text, translated) if ok else None,
        )

    def _on_translated(self, source: str, original: str, translated: str) -> None:
        self.chat_tab.add_message(f"{source}→перевод", original, translated)
        if self.settings.overlay_over_game:
            self.chat_overlay.show_message(translated)
        if self.settings.notifications_enabled:
            now = time.monotonic()
            if now - self._last_notify_ts > 4.0:
                self._last_notify_ts = now
                self.window.show_tray_message("Dota Companion · чат", translated[:120])

    def open_crop_overlay(self) -> None:
        if self.crop_overlay is not None:
            return
        screens = self.qapp.screens()
        cursor = QCursor.pos()
        screen = next((s for s in screens if s.geometry().contains(cursor)), self.qapp.primaryScreen())
        monitor_index = screens.index(screen) + 1

        self.crop_overlay = CropOverlay(screen, monitor_index)
        self.crop_overlay.region_selected.connect(self._on_region_selected)
        self.crop_overlay.cancelled.connect(self._on_crop_cancelled)
        self.crop_overlay.destroyed.connect(self._on_crop_destroyed)
        self.crop_overlay.show()
        self.crop_overlay.raise_()
        self.crop_overlay.activateWindow()

    def _on_region_selected(self, monitor_index: int, bbox: dict) -> None:
        self.settings.chat_region = ChatRegion(
            monitor=monitor_index,
            x=bbox["left"],
            y=bbox["top"],
            width=bbox["width"],
            height=bbox["height"],
        )
        self.chat_tab.refresh_region_label()
        self._schedule_save()
        self.window.set_status(
            f"Область чата: {bbox['width']}×{bbox['height']} на мониторе {monitor_index}"
        )

    def _on_crop_cancelled(self) -> None:
        self.window.set_status("Выбор области отменён")

    def _on_crop_destroyed(self) -> None:
        self.crop_overlay = None

    def _reload_triggers(self) -> None:
        sound_dir = self.settings.sound_dir or str(get_data_dir() / "sounds")
        Path(sound_dir).mkdir(parents=True, exist_ok=True)
        if not self.settings.sound_dir:
            self.settings.sound_dir = sound_dir
        current = self.settings.sound_triggers or default_triggers()
        self.settings.sound_triggers = autofill_triggers(current, sound_dir)
        self.soundpad_tab.set_triggers(self.settings.sound_triggers)
        self.soundpad_overlay.set_triggers(self.settings.sound_triggers)
        self._schedule_save()

    def _on_overlay_volume(self, volume: float) -> None:
        self.settings.master_volume = volume
        self.audio.set_volumes(volume, self.settings.voice_volume)
        self._schedule_save()

    def _show_soundpad_overlay(self) -> None:
        self._set_soundpad_overlay_visible(True)

    def _hide_soundpad_overlay(self) -> None:
        self._set_soundpad_overlay_visible(False)

    def _set_soundpad_overlay_visible(self, visible: bool) -> None:
        self.settings.soundpad_overlay_visible = visible
        if visible:
            self.soundpad_overlay.show()
            self.soundpad_overlay.raise_()
        else:
            self.soundpad_overlay.hide()
        self.window.set_soundpad_overlay_checked(visible)
        self._schedule_save()

    def _on_settings_changed(self) -> None:
        s = self.settings
        self.market.update_config(
            token=s.market_token,
            currency=s.market_currency,
            app_id=s.market_app_id,
            ignore_cache=s.market_ignore_cache,
            min_interval=s.market_min_interval,
        )
        self.translator.update(
            s.translator_provider,
            s.deepl_api_key,
            s.libretranslate_url,
            s.source_lang,
            s.target_lang,
            s.gemini_api_key,
            s.gemini_model,
        )
        self.audio.set_volumes(s.master_volume, s.voice_volume)
        self.audio.set_device(s.audio_device)
        self.engine.set_lang(s.ocr_lang)
        self._ocr_timer.setInterval(s.ocr_interval_ms)
        self.chat_overlay.apply_settings()
        self.translator_tab.set_provider_info(s.translator_provider, s.gemini_model)
        self._sync_ocr_from_settings()
        if not s.overlay_over_game:
            self.chat_overlay.hide()
        self._schedule_save()

    def _schedule_save(self) -> None:
        if not self._save_timer.isActive():
            self._save_timer.start()

    def _on_manual_translate(self, text: str, source: str, target: str) -> None:
        self.translator_tab.set_busy(True)
        self._submit(
            "manual-translate",
            self.translator.translate(text, source_lang=source, target_lang=target),
            lambda ok, result: self._on_manual_done(ok, result),
        )

    def _on_manual_done(self, ok: bool, result) -> None:
        self.translator_tab.set_busy(False)
        if ok:
            self.translator_tab.show_result(str(result))
        else:
            self.translator_tab.show_error(str(result))

    def _on_task_failed(self, name: str, error: str) -> None:
        log.error("Задача %s упала: %s", name, error)
        self.window.set_status(f"Ошибка: {error}")

    def _console_log_path(self) -> str:
        if self.settings.console_log_path and Path(self.settings.console_log_path).is_file():
            return self.settings.console_log_path

        candidates = []
        rel_path = "steamapps/common/dota 2 beta/game/dota/console.log"
        drives = ("C", "D", "E", "F", "G")
        folders = (
            "Program Files (x86)/Steam",
            "Program Files/Steam",
            "Steam",
            "SteamLibrary",
            "Games/Steam",
            "Games/SteamLibrary",
        )
        for drive in drives:
            for folder in folders:
                candidates.append(Path(f"{drive}:/{folder}/{rel_path}"))

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return ""

    def _submit(self, name: str, coro, on_done) -> None:

        async def runner():
            try:
                return (True, await coro)
            except Exception as exc:
                log.exception("Задача %s упала", name)
                return (False, exc)

        bridge = _ResultBridge(self)
        bridge.done.connect(lambda res: self._deliver_bridge(bridge, on_done, res))
        self._bridges.add(bridge)
        self.worker.submit(name, runner(), lambda res: bridge.done.emit(res))

    def _deliver_bridge(self, bridge: _ResultBridge, on_done, result: tuple) -> None:

        self._bridges.discard(bridge)
        try:
            on_done(*result)
        except Exception:
            log.exception("Ошибка в колбэке фоновой задачи")

    def _on_exit(self) -> None:
        log.info("Выход по запросу")
        self.shutdown()
        self.qapp.quit()