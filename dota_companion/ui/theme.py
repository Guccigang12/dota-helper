
                                              
from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

                                                                          
BG = "#12161d"                            
BG_SOFT = "#181e27"
CARD = "#1d2430"                    
CARD_BORDER = "#2a3342"
TEXT = "#e6ebf2"
TEXT_DIM = "#8b95a7"
ACCENT = "#4fa3ff"                       
ACCENT_HOVER = "#6cb5ff"
RADIANT = "#4ade80"                             
DIRE = "#f2504c"                               
GOLD = "#d4a843"
DANGER = "#ff5c5c"
INPUT_BG = "#141a23"

QSS = f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{
    background-color: {BG};
}}
QWidget#root {{
    background-color: {BG};
}}
QTabWidget::pane {{
    border: 1px solid {CARD_BORDER};
    border-radius: 10px;
    background-color: {BG_SOFT};
    top: -1px;
}}
QTabBar::tab {{
    background: {CARD};
    color: {TEXT_DIM};
    border: 1px solid {CARD_BORDER};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 22px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    background: {BG_SOFT};
    color: {ACCENT};
    border-top: 2px solid {ACCENT};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}

QFrame#card {{
    background-color: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 10px;
}}
QLabel#cardTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#dim {{
    color: {TEXT_DIM};
}}
QLabel#accent {{
    color: {ACCENT};
}}
QLabel#radiant {{
    color: {RADIANT};
    font-weight: 600;
}}
QLabel#dire {{
    color: {DIRE};
    font-weight: 600;
}}
QLabel#gold {{
    color: {GOLD};
}}
QLabel#value {{
    color: {ACCENT};
    font-weight: 700;
    font-size: 14px;
}}
QLabel#error {{
    color: {DANGER};
}}

QPushButton {{
    background-color: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {CARD_BORDER};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {CARD_BORDER};
}}
QPushButton#neon {{
    background-color: {ACCENT};
    color: #0b1018;
    font-weight: 600;
    border: none;
}}
QPushButton#neon:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid {CARD_BORDER};
}}
QPushButton#ghost:hover {{
    color: {ACCENT};
    border-color: {ACCENT};
}}
QPushButton#play {{
    background-color: {ACCENT};
    color: #0b1018;
    font-weight: 700;
    border: none;
    border-radius: 6px;
    padding: 4px 12px;
}}
QPushButton#play:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#play:disabled {{
    background-color: {CARD};
    color: {TEXT_DIM};
}}
QPushButton#danger {{
    color: {DANGER};
    border-color: {DIRE};
}}
QPushButton#danger:hover {{
    background-color: rgba(242, 80, 76, 0.12);
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {INPUT_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: #0b1018;
}}

QListWidget {{
    background-color: {INPUT_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 6px;
    color: {TEXT};
}}
QListWidget::item:selected {{
    background-color: rgba(79, 163, 255, 0.15);
    color: {ACCENT};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {CARD_BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {CARD_BORDER};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {CARD_BORDER};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #ffffff;
    border: 2px solid {ACCENT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QCheckBox {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 40px;
    height: 20px;
    border-radius: 10px;
    background: {CARD_BORDER};
    border: 1px solid {CARD_BORDER};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QStatusBar {{
    background: {BG_SOFT};
    color: {TEXT_DIM};
    border-top: 1px solid {CARD_BORDER};
}}
QStatusBar QLabel {{
    color: {TEXT_DIM};
}}

QMenu {{
    background-color: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px;
    border-radius: 6px;
    color: {TEXT};
}}
QMenu::item:selected {{
    background-color: {ACCENT};
    color: #0b1018;
}}

QToolTip {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {ACCENT};
    border-radius: 4px;
    padding: 4px 8px;
}}
QMessageBox {{
    background-color: {BG};
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyleSheet(QSS)


def app_icon() -> QIcon:
                                                                  
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#16202e"))
    gradient.setColorAt(1.0, QColor("#0d1220"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor(CARD_BORDER), 2))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 14, 14)

                                          
    pen = QPen(QColor(ACCENT), 3)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = QPointF(size / 2, size / 2)
    r = 16.0
    painter.drawPolygon(
        [
            QPointF(center.x(), center.y() - r),
            QPointF(center.x() + r, center.y()),
            QPointF(center.x(), center.y() + r),
            QPointF(center.x() - r, center.y()),
        ]
    )
    painter.setPen(QPen(QColor(GOLD), 2))
    painter.drawEllipse(center, 4.0, 4.0)
    painter.end()

    return QIcon(pixmap)
