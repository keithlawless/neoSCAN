"""
Generate PNG icon files from the SVG source.
Run from the project root:  python tools/generate_icons.py
Requires PyQt6 and PyQt6-Qt6 (already a project dependency).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG_PATH = ROOT / "resources" / "icons" / "neoscan.svg"
ICONS_DIR = ROOT / "resources" / "icons"


def main() -> None:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtGui import QImage, QPainter, QPixmap
    from PyQt6.QtCore import Qt, QSize, QRectF

    app = QApplication.instance() or QApplication(sys.argv)

    svg_data = SVG_PATH.read_bytes()
    renderer = QSvgRenderer(svg_data)

    sizes = [16, 32, 48, 64, 128, 256, 512]
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)  # transparent
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()

        out_path = ICONS_DIR / f"neoscan_{size}.png"
        image.save(str(out_path))
        print(f"  Wrote {out_path.name}")

    # Also write a plain neoscan.png at 256x256 for general use
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    out_path = ICONS_DIR / "neoscan.png"
    image.save(str(out_path))
    print(f"  Wrote {out_path.name}")

    print("Icon generation complete.")


if __name__ == "__main__":
    main()
