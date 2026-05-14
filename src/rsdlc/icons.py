"""Lucide icons bundled as raw SVG strings.

We render them into a tinted :class:`QIcon` so they pick up the current theme's
text color. Lucide is MIT-licensed; sources at https://lucide.dev/.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

_SVG: dict[str, str] = {
    "eye-off": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/>'
        '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/>'
        '<path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/>'
        '<line x1="2" x2="22" y1="2" y2="22"/></svg>'
    ),
    "eye": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3"/></svg>'
    ),
    "trash-2": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 1 1 1 2v2"/>'
        '<line x1="10" x2="10" y1="11" y2="17"/>'
        '<line x1="14" x2="14" y1="11" y2="17"/></svg>'
    ),
    "flame": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 '
        '2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 '
        '1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>'
    ),
    "refresh-cw": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/></svg>'
    ),
    "undo-2": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M9 14 4 9l5-5"/>'
        '<path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5v0a5.5 5.5 0 0 1-5.5 5.5H11"/></svg>'
    ),
    "folder-open": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m6 14 1.45-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 '
        '6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.93a2 2 0 0 1 '
        '1.66.9l.82 1.2a2 2 0 0 0 1.66.9H18a2 2 0 0 1 2 2v2"/></svg>'
    ),
    "bar-chart-3": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 3v18h18"/>'
        '<path d="M18 17V9"/>'
        '<path d="M13 17V5"/>'
        '<path d="M8 17v-3"/></svg>'
    ),
    "star": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 '
        '5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    ),
    "star-fill": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="currentColor" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 '
        '5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    ),
    "list": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="8" x2="21" y1="6" y2="6"/>'
        '<line x1="8" x2="21" y1="12" y2="12"/>'
        '<line x1="8" x2="21" y1="18" y2="18"/>'
        '<line x1="3" x2="3.01" y1="6" y2="6"/>'
        '<line x1="3" x2="3.01" y1="12" y2="12"/>'
        '<line x1="3" x2="3.01" y1="18" y2="18"/></svg>'
    ),
    "guitar": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m11.9 12.1 4.514-4.514"/>'
        '<path d="M20.1 2.3a1 1 0 0 0-1.4 0l-1.114 1.114A2 2 0 0 0 17 4.828v1.344'
        'a2 2 0 0 1-.586 1.414A2 2 0 0 1 17.828 7h1.344a2 2 0 0 0 1.414-.586'
        'L21.7 5.3a1 1 0 0 0 0-1.4Z"/>'
        '<path d="m6 16 2 2"/>'
        '<path d="M8.2 9.9C8.7 8.8 9.8 8 11 8c2.8 0 5 2.2 5 5 0 1.2-.8 2.3-1.9 '
        '2.8l-.9.4A2 2 0 0 0 12 18a4 4 0 0 1-4 4c-3.3 0-6-2.7-6-6a4 4 0 0 1 4-4 '
        '2 2 0 0 0 1.8-1.2z"/></svg>'
    ),
}


def app_icon() -> QIcon:
    """Build the application icon (multiple sizes embedded for crisp scaling)."""
    # Bright amber on transparent — readable on both light and dark taskbars.
    color = QColor("#f5c518")
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        # Re-render the SVG at each size for crisp rasters
        rendered = icon("guitar", size=size, color=color).pixmap(size, size)
        ic.addPixmap(rendered)
    return ic


def _palette_color() -> QColor:
    """Best foreground color for icons under the current Qt theme."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return QColor("#dcdcdc")
    return QColor(app.palette().windowText().color())


def padded_icon(name: str, outer_size: int, inner_size: int,
                color: QColor | None = None) -> QIcon:
    """Render a Lucide icon at ``inner_size`` centered inside a transparent
    ``outer_size`` × ``outer_size`` pixmap.

    Useful when the icon will land in a slot whose iconSize is fixed (e.g. a
    table column that also displays bigger thumbnails) but the icon itself
    should look smaller.
    """
    svg = _SVG.get(name)
    if svg is None:
        return QIcon()
    fg = color or _palette_color()
    svg_data = svg.replace("currentColor", fg.name())
    renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
    scale = 4
    pix = QPixmap(outer_size * scale, outer_size * scale)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    inner_px = inner_size * scale
    offset = (outer_size * scale - inner_px) // 2
    renderer.render(painter, QRectF(float(offset), float(offset),
                                    float(inner_px), float(inner_px)))
    painter.end()
    return QIcon(pix)


def icon(name: str, size: int = 20, color: QColor | None = None) -> QIcon:
    """Return a :class:`QIcon` for one of the bundled Lucide icons.

    The SVG's ``stroke="currentColor"`` is replaced by the palette window-text
    color (or a custom color if provided) so icons follow the theme.

    We render at 4x the requested size so Qt can downscale crisply when Qt
    paints the icon at its target slot (toolbar, button, etc).
    """
    svg = _SVG.get(name)
    if svg is None:
        return QIcon()
    fg = color or _palette_color()
    svg_data = svg.replace("currentColor", fg.name())
    renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
    render_size = max(size, 1) * 4
    pix = QPixmap(render_size, render_size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(render_size), float(render_size)))
    painter.end()
    return QIcon(pix)


__all__ = ["icon", "padded_icon", "app_icon"]
