"""Full-screen drag-select overlay + screen-region grab, for OCR capture.

:class:`RegionSelectOverlay` dims the screen and lets the user drag a rectangle; it returns
the chosen rectangle in global screen coordinates (or ``None`` on Escape / empty drag).
:func:`grab_region` captures that rectangle as PNG bytes for the OCR engine.
"""

from __future__ import annotations

from PyQt6.QtCore import QBuffer, QEventLoop, QIODevice, QRect, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget


class RegionSelectOverlay(QWidget):
    """A translucent, always-on-top overlay for drag-selecting a screen rectangle."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(self._virtual_geometry())
        self._origin: tuple[int, int] | None = None
        self._rect = QRect()
        self._result: QRect | None = None
        self._loop: QEventLoop | None = None

    @staticmethod
    def _virtual_geometry() -> QRect:
        """The union of all screen geometries (covers a multi-monitor desktop)."""
        geometry = QRect()
        for screen in QApplication.screens():
            geometry = geometry.united(screen.geometry())
        return geometry

    def select_region(self) -> tuple[int, int, int, int] | None:
        """Show the overlay and block until the user drags a rectangle or cancels.

        Returns:
            ``(x, y, width, height)`` in global screen coordinates, or ``None`` when the
            user pressed Escape or made an empty selection.
        """
        self._result = None
        self.show()
        self.activateWindow()
        self.raise_()
        self._loop = QEventLoop()
        self._loop.exec()
        rect = self._result
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        return (rect.x(), rect.y(), rect.width(), rect.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        point = event.globalPosition().toPoint()
        self._origin = (point.x(), point.y())
        self._rect = QRect(point, point)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if self._origin is not None:
            self._rect = QRect(
                QRect(
                    *self._origin, 0, 0
                ).topLeft(),  # origin as a QPoint via a 0-size QRect
                event.globalPosition().toPoint(),
            ).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if self._origin is not None:
            self._result = QRect(
                QRect(*self._origin, 0, 0).topLeft(),
                event.globalPosition().toPoint(),
            ).normalized()
        self._finish()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape:
            self._result = None
            self._finish()

    def _finish(self) -> None:
        self.close()
        if self._loop is not None:
            self._loop.quit()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 70))  # dim the whole screen
        if not self._rect.isNull():
            local = self._rect.translated(-self.geometry().topLeft())
            # Punch the selection back to fully transparent, then outline it.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(local, Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setPen(QPen(QColor(80, 140, 255), 2))
            painter.drawRect(local)


def grab_region(region: tuple[int, int, int, int]) -> bytes:
    """Grab the screen rectangle ``(x, y, width, height)`` as PNG bytes (``b""`` on failure)."""
    x, y, width, height = region
    screen = QApplication.primaryScreen()
    if screen is None:  # pragma: no cover - a desktop session always has a screen
        return b""
    pixmap = screen.grabWindow(0, x, y, width, height)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())
