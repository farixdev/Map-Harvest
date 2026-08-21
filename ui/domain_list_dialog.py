"""The one popup for typing a list of things, one per line.

It was `setFixedSize(400, 320)` and could not be resized at any window size or
text scale, which is two defects rather than one. A user pasting forty search
terms got a 240px well with a scrollbar and no way to see more than eight of
them at once, on a 2560px monitor with room for all forty. And a user running
Windows at 150% text scaling got the same 400x320 box with type half again as
large in it, so the hint above the well wrapped to four lines and the two
buttons under it went off the bottom edge — a dialog whose Save cannot be
reached is a dialog that cannot be used.

So it opens at a size measured off the text it holds rather than written down,
and carries a minimum instead of a maximum. Everything it paints comes from
`ui/theme.py` through `ui/components.py`.
"""

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QTextEdit, QVBoxLayout,
)

from ui import components as C

# What the well is sized to open at: enough for a readable line of the hint
# above it, and enough rows that a list reads as a list. In characters and rows
# rather than in pixels, because both of those are what the box is actually
# being asked for and both scale with the font that draws them.
COLUMNS, ROWS = 46, 10


class ListDialog(QDialog):
    """A list of items, one per line, entered by hand."""

    def __init__(
        self,
        items: list | None = None,
        parent=None,
        title: str = "List",
        hint: str = "Enter one per line.",
        placeholder: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._saved: list = list(items or [])
        self._hint = hint
        self._placeholder = placeholder
        self._build()

    def _build(self):
        t = C.active_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.space["5"], t.space["5"], t.space["5"],
                                  t.space["5"])
        layout.setSpacing(t.space["3"])

        # Through `body_label`, so the sentence is capped at the 80 characters
        # the type scale asks for instead of running the whole width of whatever
        # the dialog has been dragged to.
        layout.addWidget(C.body_label(self._hint, tone="secondary"))

        self.text_edit = QTextEdit()
        if self._placeholder:
            self.text_edit.setPlaceholderText(self._placeholder)
        if self._saved:
            self.text_edit.setPlainText("\n".join(self._saved))
        layout.addWidget(self.text_edit, stretch=1)

        row = QHBoxLayout()
        row.setContentsMargins(t.space["0"], t.space["0"], t.space["0"],
                               t.space["0"])
        row.setSpacing(t.space["2"])
        row.addStretch()
        self.cancel_btn = C.button("Cancel", kind="secondary", size="md",
                                   on_click=self.reject)
        self.save_btn = C.button("Save", kind="primary", size="md",
                                 on_click=self._save)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        self.resize(self._wanted())
        # A floor and no ceiling. The floor is one row of the well plus the
        # chrome around it, so the two buttons are always reachable however far
        # the dialog is dragged down; there is no ceiling because a long list is
        # exactly what a big screen is for.
        self.setMinimumSize(QSize(self._wanted().width() // 2,
                                  self.sizeHint().height()))

    def _wanted(self) -> QSize:
        """The opening size, measured in the font that is drawing the box."""
        fm = self.text_edit.fontMetrics()
        t = C.active_theme()
        width = fm.horizontalAdvance("n" * COLUMNS) + 2 * t.space["5"]
        rows = ROWS * fm.lineSpacing()
        return QSize(max(width, self.sizeHint().width()),
                     self.sizeHint().height() + rows)

    def _save(self):
        lines = self.text_edit.toPlainText().splitlines()
        self._saved = [line.strip() for line in lines if line.strip()]
        self.accept()

    def items(self) -> list:
        return list(self._saved)


class DomainListDialog(ListDialog):
    """Backward-compatible dialog for entering multiple search domains."""

    def __init__(self, domains: list | None = None, parent=None):
        super().__init__(
            items=domains,
            parent=parent,
            title="Domain List",
            hint="Enter one domain per line. These run in addition to the main "
                 "input.",
            placeholder="restaurants\ncafes\ngyms\nhotels",
        )

    def domains(self) -> list:
        return self.items()
