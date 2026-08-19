
                             
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings
from ..dota.match_state import Player
from ..market.models import CURRENCIES, format_value
from .theme import ACCENT, DIRE, RADIANT, TEXT_DIM
from .widgets import Card, GhostButton, NeonButton, dim_label


class PlayerRow(QWidget):

    evaluate_clicked = pyqtSignal(object)

    def __init__(self, player: Player, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._player = player
        self._settings = settings

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        self._name_label = QLabel(player.name)
        self._name_label.setStyleSheet("font-weight: 600;")
        self._name_label.setMaximumWidth(170)
        self._hero_label = dim_label(player.hero_name or "—")
        self._hero_label.setMaximumWidth(170)
        name_col.addWidget(self._name_label)
        name_col.addWidget(self._hero_label)
        layout.addLayout(name_col, 1)

        self._value_label = QLabel()
        self._value_label.setObjectName("value")
        self._value_label.setMinimumWidth(90)
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._value_label)

        evaluate_btn = NeonButton("Оценить")
        evaluate_btn.setFixedWidth(86)
        evaluate_btn.clicked.connect(self._on_evaluate_clicked)
        layout.addWidget(evaluate_btn)

        self.refresh()

    def _on_evaluate_clicked(self) -> None:
        self.evaluate_clicked.emit(self._player)

                                                                          

    def set_team_color(self, is_radiant: bool) -> None:
        color = RADIANT if is_radiant else DIRE
        self._name_label.setStyleSheet(f"font-weight: 600; color: {color};")

    def set_local(self) -> None:
        """Подсветка локального игрока (вы)."""
        self._name_label.setText(f"{self._player.name} (вы)")
        self._name_label.setStyleSheet(f"font-weight: 700; color: {ACCENT};")

    def refresh(self) -> None:
        if self._player.value_status == "loading":
            self._value_label.setText("…")
            self._value_label.setStyleSheet("color: " + TEXT_DIM + "; font-weight: 600;")
        elif self._player.value_status == "error":
            self._value_label.setText("✕")
            self._value_label.setToolTip(self._player.value_error or "Ошибка оценки")
            self._value_label.setStyleSheet("color: " + DIRE + "; font-weight: 600;")
        elif self._player.inventory_value is not None:
            self._value_label.setText(format_value(self._player.inventory_value, self._player.currency))
            self._value_label.setObjectName("value")
            self._value_label.setStyleSheet("")
            self._value_label.setToolTip("")
        else:
            self._value_label.setText("—")
            self._value_label.setStyleSheet("color: " + TEXT_DIM + ";")
            self._value_label.setToolTip("")


class MatchTab(QWidget):
                                  

    evaluate_requested = pyqtSignal(object)              
    refresh_roster_requested = pyqtSignal()
    auto_evaluate_toggled = pyqtSignal(bool)
    settings_changed = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._rows: dict[int | str, PlayerRow] = {}
        self._auto_evaluate = True

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

                                     
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        currency_label = dim_label("Валюта:")
        self._currency_combo = QComboBox()
        for code in CURRENCIES:
            self._currency_combo.addItem(code.upper(), code)
        idx = self._currency_combo.findData(self._settings.market_currency)
        self._currency_combo.setCurrentIndex(max(idx, 0))
        self._currency_combo.currentIndexChanged.connect(self._on_currency_changed)

        self._auto_check = QCheckBox("Авто-оценка при старте матча")
        self._auto_check.setChecked(True)
        self._auto_check.setToolTip("Автоматически оценивать весь Steam-инвентарь всех игроков")
        self._auto_check.toggled.connect(self.auto_evaluate_toggled.emit)

        self._status_label = dim_label("Ожидание матча (GSI)…")

        refresh_btn = GhostButton("⟳ Обновить ростер")
        refresh_btn.clicked.connect(self.refresh_roster_requested.emit)

        toolbar.addWidget(currency_label)
        toolbar.addWidget(self._currency_combo)
        toolbar.addWidget(self._auto_check)
        toolbar.addStretch(1)
        toolbar.addWidget(self._status_label)
        toolbar.addWidget(refresh_btn)
        root.addLayout(toolbar)

                         
        teams = QHBoxLayout()
        teams.setSpacing(12)

        self._radiant_card = Card("⚔️ RADIANT — Силы Света")
        self._radiant_header = QLabel("5 игроков")
        self._radiant_header.setStyleSheet(f"color: {RADIANT}; font-weight: 600;")
        self._radiant_card.content_layout().addWidget(self._radiant_header)
        self._radiant_container = QVBoxLayout()
        self._radiant_container.setSpacing(6)
        self._radiant_card.content_layout().addLayout(self._radiant_container)
        self._radiant_card.content_layout().addStretch(1)

        self._dire_card = Card("🔥 DIRE — Силы Тьмы")
        self._dire_header = QLabel("5 игроков")
        self._dire_header.setStyleSheet(f"color: {DIRE}; font-weight: 600;")
        self._dire_card.content_layout().addWidget(self._dire_header)
        self._dire_container = QVBoxLayout()
        self._dire_container.setSpacing(6)
        self._dire_card.content_layout().addLayout(self._dire_container)
        self._dire_card.content_layout().addStretch(1)

        teams.addWidget(self._radiant_card, 1)
        teams.addWidget(self._dire_card, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_widget = QWidget()
        scroll_widget.setLayout(teams)
        scroll.setWidget(scroll_widget)
        root.addWidget(scroll, 1)

                           
        hint = dim_label(
            "⚡ Авто-подхват 10 игроков: При старте матча ростер запрашивается с сервера OpenDota. "
            "В рейтинговых матчах введите 'status' в консоли Dota 2 (~) — логгер мгновенно подтянет Steam ID всех 10 игроков!"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

                                                                          

    def _on_currency_changed(self) -> None:
        self._settings.market_currency = self._currency_combo.currentData()
        self.settings_changed.emit()

                                                                          

    def set_auto_evaluate(self, enabled: bool) -> None:
        self._auto_evaluate = enabled
        self._auto_check.blockSignals(True)
        self._auto_check.setChecked(enabled)
        self._auto_check.blockSignals(False)

    def auto_evaluate_enabled(self) -> bool:
        return self._auto_check.isChecked()

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

                                                                          

    def set_players(self, players: list[Player]) -> None:
        self._rows.clear()
        self._clear_container(self._radiant_container)
        self._clear_container(self._dire_container)

        radiant = [p for p in players if p.is_radiant]
        dire = [p for p in players if p.is_radiant is False]

        self._radiant_header.setText(f"{len(radiant)} игроков")
        self._dire_header.setText(f"{len(dire)} игроков")

        for team_players, container, is_radiant in (
            (radiant, self._radiant_container, True),
            (dire, self._dire_container, False),
        ):
            for player in team_players:
                row = self._make_row(player, is_radiant)
                container.addWidget(row)

    def _make_row(self, player: Player, is_radiant: bool) -> PlayerRow:
        row = PlayerRow(player, self._settings)
        row.set_team_color(is_radiant)
        if player.is_local:
            row.set_local()
        row.evaluate_clicked.connect(self.evaluate_requested)
        key = player.account_id if player.account_id is not None else player.name.lower()
        self._rows[key] = row
        return row

    def update_player(self, player: Player) -> None:
                                                   
        key = player.account_id if player.account_id is not None else player.name.lower()
        row = self._rows.get(key)
        if row is not None:
            row.refresh()

    def set_player_loading(self, player: Player) -> None:
        player.value_status = "loading"
        self.update_player(player)

    def set_player_error(self, player: Player, message: str) -> None:
        player.value_status = "error"
        player.value_error = message
        self.update_player(player)

    @staticmethod
    def _clear_container(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
