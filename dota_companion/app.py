
   
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
from .dota.steam_api import fetch_realtime_stats
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
        if not steam_ids or not self.match_state.players:
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
                # Не назначаем случайный Steam ID на первый попавшийся слот —
                # ждём подтверждения от GSI-ростера или Steam API
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

        # Если сетка ростера ещё не создана (0 игроков) — создаём 10 слотов
        if len(self.match_state.players) < 10:
            players: list[Player] = []
            for p in parsed_players:
                name = p["name"]
                is_radiant = p["is_radiant"]
                players.append(Player(account_id=None, name=name, is_radiant=is_radiant))
            if len(players) > 0:
                self.match_state.set_players(players)

        # Подставляем полученные ники и автоматически запускаем поиск Steam ID
        for p_info in parsed_players:
            name = p_info["name"]
            is_radiant = p_info["is_radiant"]
            parsed_acc_id = p_info.get("account_id")

            p = self.match_state.player_by_name(name)
            if p is None and parsed_acc_id:
                p = self.match_state.player_by_account(parsed_acc_id)

            if p is None:
                unassigned = [pl for pl in self.match_state.players if (pl.account_id is None or pl.account_id <= 0) and not pl.is_local and pl.is_radiant == is_radiant]
                if not unassigned:
                    unassigned = [pl for pl in self.match_state.players if (pl.account_id is None or pl.account_id <= 0) and not pl.is_local]
                if unassigned:
                    p = unassigned[0]
                    p.name = name
                    p.is_radiant = is_radiant

            if p:
                if parsed_acc_id and (p.account_id is None or p.account_id <= 0):
                    p.account_id = parsed_acc_id

                self.match_tab.update_player(p)

                if p.account_id and p.account_id > 0:
                    if p.value_status != "ok":
                        self.evaluate_player(p)
                elif not p.is_local:
                    self._submit(
                        f"search-{name}",
                        self._fetch_player_by_search(name),
                        lambda ok, acc_id, target_p=p: self._on_player_search_done(target_p, acc_id if ok else None),
                    )

        self.match_state.sort_players()
        self.match_tab.set_players(self.match_state.players)
        status = f"Матч {self._current_match_id or ''} · Игроки подтянуты из команды status"
        self.match_tab.set_status(status)
        self.window.set_status(status)

    def _on_player_search_done(self, player: Player, account_id: int | None) -> None:
        """Поиск по нику завершён — устанавливаем account_id и оцениваем инвентарь."""
        if account_id and account_id > 0:
            player.account_id = account_id
            log.info("Игрок '%s' найден через OpenDota Search: account_id = %d", player.name, account_id)
            self.match_tab.update_player(player)
            self.evaluate_player(player)
        else:
            log.warning("Игрок '%s' не найден через OpenDota Search", player.name)

    def _on_log_chat(self, channel: str, name: str, text: str) -> None:
        log.info("Чат [%s] %s: %s", channel, name, text)
        if name and not name.startswith("Игрок ") and not name.startswith("Player ") and name != "Вы":
            unassigned = [pl for pl in self.match_state.players if pl.account_id is None or pl.account_id <= 0]
            if unassigned:
                target_p = unassigned[0]
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
            name = sanitize_name(info.get("name")) or (f"Player {account_id}" if account_id else f"Игрок {info.get('slot', 0)+1}")
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

        # Оцениваем игроков со Steam ID, либо автоматически ищем по нику из GSI (для Турбо и всех режимов)
        for player in self.match_state.players:
            if player.value_status == "ok":
                continue
            if player.account_id and player.account_id > 0:
                self.evaluate_player(player)
            elif (
                player.name
                and len(player.name) >= 3
                and not player.name.startswith("Игрок ")
                and not player.name.startswith("Player ")
                and player.name != "Вы"
            ):
                self._submit(
                    f"search-{player.name}",
                    self._fetch_player_by_search(player.name),
                    lambda ok, acc_id, p=player: self._on_player_search_done(p, acc_id if ok else None),
                )

    async def _fetch_player_name(self, account_id: int) -> str | None:
        session = await self._get_dota_session()
        return await opendota.fetch_player_name(session, account_id)

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
        evaluable = [p for p in self.match_state.players if p.account_id and p.account_id > 0]
        if not evaluable:
            msg = "Нет игроков со Steam ID для оценки (нажмите 'Оценить' возле игрока)"
            self.match_tab.set_status(msg)
            self.window.set_status(msg)
            return

        msg = f"Запуск оценки инвентарей ({len(evaluable)} игроков)…"
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        for player in evaluable:
            player.value_status = ""
            self.evaluate_player(player)

    def evaluate_player(self, player) -> None:
        """Оценка всего Steam-инвентаря игрока по его профилю."""
        player.currency = self.settings.market_currency

        if player.steam_id64 is None or player.account_id is None or player.account_id <= 0:
            player.value_status = ""
            player.value_error = ""
            self.match_tab.update_player(player)
            msg = f"Игрок '{player.name}': Steam ID ещё не привязан"
            self.window.set_status(msg)
            return

        self.match_tab.set_player_loading(player)
        msg = f"Запрос инвентаря на Lolzteam Market для {player.name}..."
        self.match_tab.set_status(msg)
        self.window.set_status(msg)
        log.info(msg)

        self._submit(
            "market-eval",
            self.market.get_inventory_value_by_link(player.steam_id64),
            lambda ok, result: self._on_value_ready(player, result) if ok else self._on_value_error(player, result),
        )

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
