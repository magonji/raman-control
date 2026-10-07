"""Shared interface elements and visual theme.

Palette: neutral greys and a petrol-blue ink for a calm interface that reads
well through safety goggles. The 532 nm green is reserved exclusively for
showing that the laser is emitting; red, for the stop button and errors.
"""
from __future__ import annotations

import html
import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider,
                               QStyle, QStyleOptionSlider, QToolButton, QVBoxLayout, QWidget)

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
QToolButton#square {{
    background: white; border: 2px solid {LINE}; border-radius: 6px; padding: 0;
}}
QToolButton#square:hover {{ background: {PANEL}; }}
QToolButton#square:checked {{ background: {TEAL}; border-color: #083f49; }}
QToolButton#square:disabled {{ background: {PANEL}; }}
QSlider::groove:horizontal {{ height: 6px; background: {LINE}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {TEAL}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -7px 0; border-radius: 10px;
    background: white; border: 2px solid {TEAL};
}}
QSlider::handle:horizontal:hover {{ background: {PANEL}; }}
QSlider::sub-page:horizontal:disabled {{ background: #b8c0c8; }}
QSlider::handle:horizontal:disabled {{ border-color: #b8c0c8; }}
QToolButton#squareLaser {{
    background: white; border: 2px solid {LINE}; border-radius: 6px; padding: 0;
}}
QToolButton#squareLaser:hover {{ background: {PANEL}; }}
QToolButton#squareLaser:checked {{ background: {LASER}; border-color: #23843a; }}
QToolButton#squareLaser:disabled {{ background: {PANEL}; }}
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


# Emergency stop: white octagon with an exclamation mark, for the laser-off button.
EMERGENCY_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path d="M8.3 2h7.4L22 8.3v7.4L15.7 22H8.3L2 15.7V8.3z" fill="none" stroke="white"
    stroke-width="2.2" stroke-linejoin="round"/>
  <path d="M12 6.6v6.8" stroke="white" stroke-width="2.8" stroke-linecap="round"/>
  <circle cx="12" cy="17.2" r="1.6" fill="white"/></svg>"""

# Symbols for the square buttons of the image window; {color} is filled in.
POWER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.6" stroke-linecap="round">
  <path d="M12 3v8"/><path d="M6.6 6.6a7.5 7.5 0 1 0 10.8 0"/></svg>"""
PLAY_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <path d="M7 4.5v15l12.5-7.5z" fill="{color}" stroke="{color}" stroke-width="1.5"
    stroke-linejoin="round"/></svg>"""
STOP_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <rect x="5.5" y="5.5" width="13" height="13" rx="1.5" fill="{color}"/></svg>"""
LASER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round">
  <circle cx="7.5" cy="12" r="2.4" fill="{color}"/>
  <path d="M7.5 4.5v2.5M7.5 17v2.5M2 12h1.6M2.3 6.8l1.9 1.9M2.3 17.2l1.9-1.9
    M12.7 6.8l-1.9 1.9M12.7 17.2l-1.9-1.9"/>
  <path d="M11 12h11" stroke-width="2.6"/></svg>"""
CONTINUOUS_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4.5 11a7.5 7.5 0 0 1 13-4.6"/><path d="M18 2.6v4.2h-4.2"/>
  <path d="M19.5 13a7.5 7.5 0 0 1-13 4.6"/><path d="M6 21.4v-4.2h4.2"/></svg>"""
SPECTRUM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M2 19h3.5c1.6 0 1.9-14 3.5-14s1.9 14 3.5 14h1c1.2 0 1.5-8 2.6-8s1.4 8 2.6 8H22"/>
  </svg>"""
PHOTO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linejoin="round">
  <path d="M3 8.5a2 2 0 0 1 2-2h2.5l1.6-2.5h5.8l1.6 2.5H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5
    a2 2 0 0 1-2-2z"/>
  <circle cx="12" cy="13" r="3.8"/></svg>"""


def svg_icon(svg: str, size: int, icon: QIcon | None = None,
             state: QIcon.State = QIcon.Off, mode: QIcon.Mode = QIcon.Normal) -> QIcon:
    """An icon drawn from SVG text, sharp on high-resolution screens too. Given an
    icon, adds the drawing to it for that state (on/off) and mode (normal/disabled)
    instead of making a new one."""
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    icon = icon if icon is not None else QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap, mode, state)
    return icon


def estop_square_button(tooltip: str, size: int = 44) -> QToolButton:
    """Square laser-off button with the power symbol instead of text."""
    button = QToolButton()
    button.setObjectName("estopSquare")
    button.setIcon(svg_icon(EMERGENCY_SVG, 26))
    button.setIconSize(QSize(26, 26))
    button.setFixedSize(size, size)
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


def square_button(svg: str, checkable: bool = False, size: int = 44,
                  laser: bool = False, svg_checked: str | None = None) -> QToolButton:
    """Square button with a symbol: dark on white, white on petrol blue while checked
    (on the laser green for the emission button), light grey when disabled. svg is
    one of the *_SVG templates above; svg_checked, another one to show while checked."""
    button = QToolButton()
    button.setObjectName("squareLaser" if laser else "square")
    button.setCheckable(checkable)
    svg_on = svg_checked or svg
    icon = svg_icon(svg.format(color=INK), 26, state=QIcon.Off)
    svg_icon(svg_on.format(color="white"), 26, icon, QIcon.On)
    for state, drawing in ((QIcon.Off, svg), (QIcon.On, svg_on)):
        svg_icon(drawing.format(color="#b8c0c8"), 26, icon, state, QIcon.Disabled)
    button.setIcon(icon)
    button.setIconSize(QSize(26, 26))
    button.setFixedSize(size, size)
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    return button


def set_tip(button: QToolButton, text: str) -> None:
    """Tooltip and accessible name: the only words an icon-only button has."""
    button.setToolTip(text)
    button.setAccessibleName(text)


class _MarkedSlider(QSlider):
    """Horizontal slider that can also draw a vertical mark at another position, such as
    the measured value next to the requested one."""

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.mark: int | None = None  # slider position of the mark, or None
        self.mark_color = LASER

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.mark is None:
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        x = groove.left() + handle.width() / 2 + QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), self.mark, span)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self.mark_color), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(int(x), 2, int(x), self.height() - 3)
        painter.end()


class ValueSlider(QWidget):
    """Name, horizontal slider and value, for a quantity between lo and hi.

    With log=True the slider is logarithmic, which gives fine control at the low end
    of wide ranges (a few mW for aligning, up to 500 mW for measuring). step rounds
    the values it produces (1 = whole numbers). moved is emitted while dragging;
    released when the slider is let go or moved with the keyboard or a click.
    """
    moved = Signal(float)
    released = Signal(float)
    TICKS = 1000

    def __init__(self, name: str, lo: float, hi: float, fmt, log: bool = False,
                 step: float = 0.0):
        super().__init__()
        self.lo, self.hi, self.log, self.step, self.fmt = lo, hi, log, step, fmt
        self._value = lo
        self.slider = _MarkedSlider()
        self.slider.setRange(0, self.TICKS)
        self.slider.setMinimumWidth(160)
        self.slider.setAccessibleName(name)
        self.lbl_value = QLabel()
        self.lbl_value.setMinimumWidth(70)
        self.lbl_value.setStyleSheet(f"color:{INK}; font-weight:600;")
        name_label = QLabel(name)
        name_label.setStyleSheet(f"color:{MUTED};")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(name_label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.lbl_value)
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderReleased.connect(lambda: self.released.emit(self._value))
        self.set_value(lo)

    def _to_value(self, tick: int) -> float:
        f = tick / self.TICKS
        v = (self.lo * (self.hi / self.lo) ** f) if self.log else self.lo + f * (self.hi - self.lo)
        if self.step:
            v = round(v / self.step) * self.step
        elif self.log:  # two significant figures: 0.52, 1.3, 27, 140...
            v = round(v, 1 - int(math.floor(math.log10(v))))
        return min(max(v, self.lo), self.hi)

    def _to_tick(self, value: float) -> int:
        v = min(max(value, self.lo), self.hi)
        f = math.log(v / self.lo) / math.log(self.hi / self.lo) if self.log else \
            (v - self.lo) / (self.hi - self.lo)
        return round(f * self.TICKS)

    def _on_slider(self, tick: int) -> None:
        self._value = self._to_value(tick)
        self.lbl_value.setText(self.fmt(self._value))
        self.moved.emit(self._value)
        if not self.slider.isSliderDown():  # keyboard, wheel or click on the track
            self.released.emit(self._value)

    def value(self) -> float:
        return self._value

    def set_mark(self, value: float | None) -> None:
        """Draws a vertical mark at value (None, or below the range, removes it)."""
        mark = None if value is None or value < self.lo else self._to_tick(value)
        if mark != self.slider.mark:
            self.slider.mark = mark
            self.slider.update()

    def set_value(self, value: float) -> None:
        """Shows a value set elsewhere, without emitting anything. A value outside the
        range is shown as it is, with the slider at that end."""
        self._value = value
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_tick(value))
        self.slider.blockSignals(False)
        self.lbl_value.setText(self.fmt(value))


def format_seconds(s: float) -> str:
    if s < 60:
        return f"{s:.2g} s" if s < 10 else f"{s:.0f} s"
    minutes, seconds = divmod(round(s), 60)
    return f"{minutes} min {seconds:02d} s" if seconds else f"{minutes} min"


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
        self.chk_autosave = QCheckBox("Save every spectrum automatically (not continuous ones)")
        self.chk_autosave.setChecked(True)
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
