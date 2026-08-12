"""A layout that wraps its items onto new lines when they run out of width.

Qt ships no wrapping layout, and a ``QHBoxLayout`` silently CLIPS what does not fit — which is
how the lookup panel's metadata row ended up reading "Unit 06-1", "1d interva". The chips there
are user data (deck names, tag lists) whose width cannot be predicted, so they need to wrap.

A compact version of Qt's own FlowLayout example: lay items left-to-right, break to a new row on
overflow, and report a height-for-width so the panel can size itself correctly.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Lays widgets out in rows, wrapping to the next row when the width runs out."""

    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        spacing: int = 6,
    ) -> None:
        """Initialise the layout.

        Args:
            parent: Optional parent widget.
            margin: Margin on all four sides.
            spacing: Gap between items, horizontally and vertically.
        """
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    # -- QLayout plumbing ----------------------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt's API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt's API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt's API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt's API
        return Qt.Orientation(0)

    # -- sizing --------------------------------------------------------------------------

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's API
        """Height depends on width: that is the whole point of wrapping."""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's API
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt's API
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt's API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        """Place the items inside ``rect`` (or just measure) and return the total height."""
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        top = rect.y() + margins.top()
        right = rect.right() - margins.right()
        x, y, row_height = left, top, 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self.spacing()
            if next_x - self.spacing() > right and row_height > 0:
                x = left  # wrap
                y += row_height + self.spacing()
                next_x = x + hint.width() + self.spacing()
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


def flow_widget(spacing: int = 6) -> tuple[QWidget, FlowLayout]:
    """Return a widget wired with a :class:`FlowLayout`, sized by its wrapped content."""
    holder = QWidget()
    layout = FlowLayout(holder, margin=0, spacing=spacing)
    holder.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return holder, layout
