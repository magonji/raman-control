"""Elementos de interfaz compartidos y tema visual.

Paleta: grises neutros y tinta azul petróleo para una interfaz tranquila que se
lee bien con gafas de protección. El verde 532 nm se reserva exclusivamente
para indicar que el láser está emitiendo; el rojo, para el paro y los errores.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

INK = "#1f2933"
MUTED = "#5b6673"
PANEL = "#f4f5f6"
LINE = "#d5d9de"
TEAL = "#0b5563"
LASER = "#2fb344"      # solo para «láser emitiendo»
DANGER = "#c0392b"
WARN = "#b7791f"
OK = "#1e7a46"

STYLESHEET = f"""
QMainWindow, QWidget {{ color: {INK}; }}
QMainWindow {{ background: {PANEL}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {PANEL}; border: none; }}
QGroupBox {{
    background: white; border: 1px solid {LINE}; border-radius: 6px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEAL}; }}
QPushButton {{
    background: white; border: 1px solid #b8c0c8; border-radius: 4px; padding: 5px 10px;
}}
QPushButton:hover {{ border-color: {TEAL}; }}
QPushButton:disabled {{ color: #9aa3ad; border-color: {LINE}; }}
QPushButton:checked {{ background: #e3eef0; border-color: {TEAL}; }}
QPushButton#primary {{ background: {TEAL}; color: white; border-color: {TEAL}; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #9fb8bc; border-color: #9fb8bc; color: #eef3f4; }}
QToolButton#estop {{
    background: {DANGER}; color: white; font-weight: 700; font-size: 13px;
    border: 2px solid #8e2a20; border-radius: 6px; padding: 6px 16px;
}}
QToolButton#estop:hover {{ background: #a93226; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 6px 14px; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {TEAL}; border-bottom: 2px solid {TEAL}; }}
QLabel#reading {{ font-size: 20px; font-weight: 600; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#simbadge {{ color: {WARN}; font-weight: 600; padding: 0 8px; }}
QProgressBar {{ border: 1px solid {LINE}; border-radius: 3px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {TEAL}; }}
QPlainTextEdit {{ background: white; border: 1px solid {LINE}; }}
"""


def mono_font(size: int = 9) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    return font


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def reading(text: str = "—") -> QLabel:
    label = QLabel(text)
    label.setObjectName("reading")
    return label


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primary")
    return button


def estop_button(text: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("estop")
    button.setText(text)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    return button


def titled_box(title: str, widget: QWidget) -> QWidget:
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(6, 4, 6, 4)
    label = QLabel(title)
    label.setStyleSheet(f"color:{TEAL}; font-weight:600; border-bottom:1px solid {LINE};")
    v.addWidget(label)
    v.addWidget(widget, 1)
    return box


class Led(QLabel):
    COLORS = {"off": "#b8c0c8", "ok": OK, "warn": WARN, "error": DANGER,
              "busy": TEAL, "laser": LASER}

    def __init__(self, size: int = 12):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.set_state("off")

    def set_state(self, state: str) -> None:
        color = self.COLORS.get(state, state)
        self.setStyleSheet(f"background:{color}; border-radius:{self._size // 2}px;")


class LogView(QPlainTextEdit):
    COLORS = {"info": INK, "ok": OK, "warn": WARN, "error": DANGER}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(3000)
        self.setFont(mono_font(9))

    def add(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        color = self.COLORS.get(level, INK)
        self.appendHtml(f'<span style="color:{MUTED}">{stamp}</span>&nbsp; '
                        f'<span style="color:{color}">{html.escape(message)}</span>')


class SavePanel(QGroupBox):
    save_clicked = Signal()

    def __init__(self, default_folder: str):
        super().__init__("Muestra y guardado")
        self.edit_sample = QLineEdit("muestra")
        self.edit_folder = QLineEdit(str(Path(default_folder)))
        browse = QPushButton("…")
        browse.setFixedWidth(30)
        browse.clicked.connect(self._browse)
        self.chk_autosave = QCheckBox("Guardar cada espectro automáticamente")
        self.chk_attach = QCheckBox("Guardar también la imagen de la cámara")
        self.chk_attach.setChecked(True)
        self.btn_save = QPushButton("Guardar último espectro")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_clicked)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("Muestra"), 0, 0)
        grid.addWidget(self.edit_sample, 0, 1, 1, 2)
        grid.addWidget(QLabel("Carpeta"), 1, 0)
        row = QHBoxLayout()
        row.addWidget(self.edit_folder)
        row.addWidget(browse)
        grid.addLayout(row, 1, 1, 1, 2)
        grid.addWidget(self.chk_autosave, 2, 0, 1, 3)
        grid.addWidget(self.chk_attach, 3, 0, 1, 3)
        grid.addWidget(self.btn_save, 4, 0, 1, 3)
        grid.addWidget(hint("Cada archivo lleva un JSON con todos los parámetros de la medida."), 5, 0, 1, 3)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Carpeta de datos", self.edit_folder.text())
        if folder:
            self.edit_folder.setText(folder)

    def sample(self) -> str:
        return self.edit_sample.text().strip() or "muestra"

    def folder(self) -> str:
        return self.edit_folder.text().strip() or "."
