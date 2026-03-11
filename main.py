"""
NeoSCAN — main entry point.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

# Fix macOS dock/menu-bar showing "Python" instead of "NeoSCAN".
# This must happen before QApplication is created.
if sys.platform == "darwin":
    try:
        from Foundation import NSBundle  # type: ignore[import]
        bundle = NSBundle.mainBundle()
        if bundle:
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info and info.get("CFBundleName") == "Python":
                info["CFBundleName"] = "NeoSCAN"
    except Exception:
        pass  # pyobjc not installed — name stays as "Python" in dev; fine in packaged app

from app.ui.main_window import MainWindow
from app.ui.settings.preferences_dialog import load_prefs, apply_theme

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
# Suppress verbose debug output from the serial protocol layer so that
# audio transcription debug messages are easier to see.
logging.getLogger("app.serial.protocol").setLevel(logging.WARNING)

_ROOT = Path(__file__).resolve().parent
_ICON_PATH = _ROOT / "resources" / "icons" / "neoscan.png"


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("NeoSCAN")
    app.setOrganizationName("NeoSCAN")
    app.setApplicationDisplayName("NeoSCAN")

    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    # Apply saved theme before the window is shown so colours are consistent
    # from the first paint.
    apply_theme(load_prefs().value("appearance/theme", "System default"))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
