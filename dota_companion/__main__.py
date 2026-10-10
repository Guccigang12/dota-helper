"""
Entry point for Dota Companion.
Run via:
    python -m dota_companion
"""

from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication

from .app import DotaCompanionApplication


def main() -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("Dota Companion")
    app.setOrganizationName("DotaCompanion")

    companion = DotaCompanionApplication(app)
    app.aboutToQuit.connect(companion.shutdown)

    companion.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
