
from __future__ import annotations

import sys

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from dota_companion.app import DotaCompanionApplication
from dota_companion.ui.theme import app_icon


def log_uncaught_exceptions(exctype, value, tb):
    import traceback
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    logging.getLogger("dota_companion").critical("Uncaught Exception:\n%s", err_msg)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = log_uncaught_exceptions


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Dota Companion")
    app.setOrganizationName("DotaCompanion")
    app.setWindowIcon(app_icon())

    companion = DotaCompanionApplication(app)
    companion.start()

    exit_code = app.exec()
    companion.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
