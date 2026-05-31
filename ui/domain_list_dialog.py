from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton,
)


class DomainListDialog(QDialog):
    """Popup for entering multiple search domains (one per line)."""

    def __init__(self, domains: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Domain List")
        self.setModal(True)
        self.setFixedSize(400, 320)
        self._saved: list[str] = list(domains or [])
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint = QLabel("Enter one domain per line. These run in addition to the main input.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("restaurants\ncafes\ngyms\nhotels")
        if self._saved:
            self.text_edit.setPlainText("\n".join(self._saved))
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("outlined")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self._save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self):
        lines = self.text_edit.toPlainText().splitlines()
        self._saved = [line.strip() for line in lines if line.strip()]
        self.accept()

    def domains(self) -> list[str]:
        return list(self._saved)
