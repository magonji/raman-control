"""Shared interface elements and visual theme.

Palette: neutral greys and a petrol-blue ink for a calm interface that reads
well through safety goggles. The 532 nm green is reserved exclusively for
showing that the laser is emitting; red, for the stop button and errors.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

INK = "#1f2933"
MUTED = "#5b6673"
PANEL = "#f4f5f6"
LINE = "#d5d9de"
TEAL = "#0b5563"
LASER = "#2fb344"      # only for "laser emitting"
DANGER = "#c0392b"
WARN = "#b7791f"
OK = "#1e7a46"

# Colours for overlaid spectra, always assigned in this order. It is the reference
# categorical palette without its green and red, which this program reserves for
# "laser emitting" and for stop/errors; the order keeps neighbouring colours
# distinguishable for colour-blind readers.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7")

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
QToolButton#estopSquare {{
    background: {DANGER}; border: 2px solid #8e2a20; border-radius: 6px; padding: 0;
}}
QToolButton#estopSquare:hover {{ background: #a93226; }}
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


# IEC power symbol, white, for the square laser-off button.
POWER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="white" stroke-width="2.6" stroke-linecap="round">
  <path d="M12 3v8"/><path d="M6.6 6.6a7.5 7.5 0 1 0 10.8 0"/></svg>"""


def svg_icon(svg: str, size: int) -> QIcon:
    """An icon drawn from SVG text, sharp on high-resolution screens too."""
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


def estop_square_button(tooltip: str, size: int = 44) -> QToolButton:
    """Square laser-off button with the power symbol instead of text."""
    button = QToolButton()
    button.setObjectName("estopSquare")
    button.setIcon(svg_icon(POWER_SVG, 26))
    button.setIconSize(QSize(26, 26))
    button.setFixedSize(size, size)
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


class TrafficLight(QWidget):
    """Laser state as a traffic light, with the state also in words beside it.

    Red: disconnected (the program cannot tell whether it emits). Amber: connected,
    not emitting. Green: emitting, the 532 nm green used for that across the program.
    """
    LIT = {"disconnected": DANGER, "idle": "#e8a317", "emitting": LASER}
    DIM = {"disconnected": "#5c3330", "idle": "#5c4c2c", "emitting": "#2e4d34"}
    ORDER = ("disconnected", "idle", "emitting")

    def __init__(self, lamp: int = 16):
        super().__init__()
        self._lamp = lamp
        housing = QWidget()
        housing.setObjectName("trafficHousing")
        housing.setStyleSheet("#trafficHousing { background:#2b3036; border-radius:8px; }")
        lamps = QHBoxLayout(housing)
        lamps.setContentsMargins(7, 5, 7, 5)
        lamps.setSpacing(6)
        self._lamps = {}
        for state in self.ORDER:
            lamp_label = QLabel()
            lamp_label.setFixedSize(lamp, lamp)
            lamps.addWidget(lamp_label)
            self._lamps[state] = lamp_label
        self.label = QLabel()
        # Room for the longest text, so that the lights do not shift when it changes.
        self.label.setMinimumWidth(380)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(10)
        row.addWidget(self.label)
        row.addWidget(housing)
        self.set_state("disconnected", "Laser disconnected")

    def set_state(self, state: str, text: str) -> None:
        for name, lamp in self._lamps.items():
            color = self.LIT[name] if name == state else self.DIM[name]
            lamp.setStyleSheet(f"background:{color}; border-radius:{self._lamp // 2}px;")
        self.label.setText(text)
        weight = 700 if state == "emitting" else 600
        self.label.setStyleSheet(f"color:{INK}; font-weight:{weight}; font-size:13px;")
        self.setToolTip(text)
        self.setAccessibleName(text)


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
        super().__init__("Sample and saving")
        self.edit_sample = QLineEdit("sample")
        self.edit_folder = QLineEdit(str(Path(default_folder)))
        browse = QPushButton("…")
        browse.setFixedWidth(30)
        browse.clicked.connect(self._browse)
        self.chk_autosave = QCheckBox("Save every spectrum automatically")
        self.chk_attach = QCheckBox("Also save the camera image")
        self.chk_attach.setChecked(True)
        self.btn_save = QPushButton("Save latest spectrum")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_clicked)

        grid = QGridLayout(self)
        grid.addWidget(QLabel("Sample"), 0, 0)
        grid.addWidget(self.edit_sample, 0, 1, 1, 2)
        grid.addWidget(QLabel("Folder"), 1, 0)
        row = QHBoxLayout()
        row.addWidget(self.edit_folder)
        row.addWidget(browse)
        grid.addLayout(row, 1, 1, 1, 2)
        grid.addWidget(self.chk_autosave, 2, 0, 1, 3)
        grid.addWidget(self.chk_attach, 3, 0, 1, 3)
        grid.addWidget(self.btn_save, 4, 0, 1, 3)
        grid.addWidget(hint("Every file comes with a JSON holding all the measurement parameters."), 5, 0, 1, 3)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Data folder", self.edit_folder.text())
        if folder:
            self.edit_folder.setText(folder)

    def sample(self) -> str:
        return self.edit_sample.text().strip() or "sample"

    def folder(self) -> str:
        return self.edit_folder.text().strip() or "."
