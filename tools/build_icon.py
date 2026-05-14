"""Generate assets/icon.ico from the bundled 'guitar' SVG (amber)."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODeviceBase, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsdlc.icons import _SVG  # noqa: E402

SIZES = [16, 24, 32, 48, 64, 128, 256]
COLOR = "#f5c518"


def _rasterize(svg: str, size: int) -> Image.Image:
    """Render the SVG into a PIL RGBA image at exactly (size, size)."""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    painter.end()

    buf = QBuffer()
    buf.open(QIODeviceBase.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png_bytes = bytes(buf.data())
    return Image.open(BytesIO(png_bytes)).convert("RGBA")


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    svg = _SVG["guitar"].replace("currentColor", COLOR)
    rasters = [_rasterize(svg, s) for s in SIZES]
    out_dir = ROOT / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "icon.ico"
    base = rasters[-1]  # 256x256 as primary
    base.save(
        out,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=rasters[:-1],
    )
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
