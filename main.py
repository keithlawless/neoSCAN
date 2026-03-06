"""
NeoSCAN — main entry point.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app.ui.main_window import MainWindow

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("NeoSCAN")
    app.setOrganizationName("NeoSCAN")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
