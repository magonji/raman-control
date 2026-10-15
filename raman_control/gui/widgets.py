"""Shared interface elements and visual theme.

Palette: neutral greys and a petrol-blue ink for a calm interface that reads
well through safety goggles. The 532 nm green is reserved exclusively for
showing that the laser is emitting; red, for the stop button and errors.
"""
from __future__ import annotations

import html
import math
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap,
                           QValidator)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QPushButton, QSizePolicy, QToolButton,
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
# Colours of the spectra on the plot, in the order they are handed out: the first six
# are the most distinct; twenty in all, chosen so that each one is as far as possible
# (OKLab) from those before it, the current spectrum's petrol blue, the greys and the
# error red, and stays visible as a thin line on white. After twenty they repeat.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7",
          "#c113ec", "#8e0b6b", "#776b22", "#d92691", "#773922", "#7813ec",
          "#178282", "#1322ec", "#a0970d", "#0b8e0b", "#39a6ef", "#10c610",
          "#9059cf", "#ef39d7")

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
QToolButton#stepButton {{
    background: white; border: 1px solid {LINE}; border-radius: 3px; font-weight: 700;
}}
QToolButton#stepButton:hover {{ background: {PANEL}; }}
QPushButton#quickValue {{ padding: 2px 6px; min-width: 0; font-size: 11px; }}
QToolButton#axisZoom {{
    background: white; border: 1px solid {LINE}; border-radius: 3px; padding: 0;
}}
QToolButton#axisZoom:hover {{ background: {PANEL}; }}
QToolButton#axisZoom:pressed {{ background: {LINE}; }}
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
CCD_ROWS_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="3" width="18" height="18" rx="2" stroke-width="2"/>
  <path d="M9 3v18M15 3v18" stroke-width="1" opacity="0.6"/>
  <rect x="3" y="10" width="18" height="4.5" fill="{color}" stroke-width="1.6"/></svg>"""
LIPID_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-linecap="round" stroke-linejoin="round">
  <rect x="11" y="3" width="8" height="17" fill="{color}" opacity="0.25" stroke="none"/>
  <path d="M2 19h5c2 0 2.5-13 4.5-13S14 19 16 19h6" stroke-width="2"/></svg>"""
ZOOM_FULL_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M3 8.5V3h5.5M15.5 3H21v5.5M21 15.5V21h-5.5M8.5 21H3v-5.5"/>
  <path d="M6.5 16h2.3l1.4-7 1.6 7.5 1.2-3.5 1.2 3h3.3" stroke-width="1.6"/></svg>"""
FIT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 3h16M4 21h16"/>
  <path d="M12 6.5v11M8.5 10 12 6.5 15.5 10M8.5 14 12 17.5 15.5 14"/></svg>"""
FIT_NO_RAYLEIGH_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 3h12M9 21h12"/>
  <path d="M15 6.5v11M12 10l3-3.5 3 3.5M12 14l3 3.5 3-3.5"/>
  <path d="M1.5 19c1.2 0 1.6-14 2.5-14s1.3 14 2.5 14" stroke-width="1.6"/>
  <path d="M1.5 6.5l5 10" stroke-width="1.8"/></svg>"""
# Axis zoom, as in Andor Solis: arrows out stretch the data (zoom in), arrows in squeeze
# it (zoom out). Drawn horizontal; {angle} 90 turns them for the vertical axis.
ZOOM_IN_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
  <g transform="rotate({angle} 12 12)"><path d="M12 5v14"/>
  <path d="M10 12H3M6 9l-3 3 3 3"/><path d="M14 12h7M18 9l3 3-3 3"/></g></svg>"""
ZOOM_OUT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
  <g transform="rotate({angle} 12 12)"><path d="M12 5v14"/>
  <path d="M2 12h7M6 9l3 3-3 3"/><path d="M22 12h-7M18 9l-3 3 3 3"/></g></svg>"""
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


def axis_zoom_button(svg: str, vertical: bool, tooltip: str) -> QToolButton:
    """Small button next to a plot axis, as in Andor Solis."""
    button = QToolButton()
    button.setObjectName("axisZoom")
    button.setIcon(svg_icon(svg.format(color=INK, angle=90 if vertical else 0), 18))
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(26, 24)
    set_tip(button, tooltip)
    return button


def set_tip(button: QToolButton, text: str) -> None:
    """Tooltip and accessible name: the only words an icon-only button has."""
    button.setToolTip(text)
    button.setAccessibleName(text)


CONTINUOUS_MARK = SERIES[1]  # orange: the continuous measurement's settings (◆)


def nice_values(lo: float, hi: float) -> list[float]:
    """Round values between lo and hi, in the 1-2-5 series (… 0.5, 1, 2, 5, 10 …),
    with lo and hi themselves."""
    values = {lo, hi}
    for k in range(int(math.floor(math.log10(lo))) - 1, int(math.ceil(math.log10(hi))) + 1):
        for m in (1, 2, 5):
            v = round(m * 10 ** k, 10)
            if lo <= v <= hi:
                values.add(v)
    return sorted(values)


class NiceSpinBox(QDoubleSpinBox):
    """A box for a quantity that steps through round values (1-2-5), with − and +, the
    arrow keys or the mouse wheel, and takes any value typed (on Enter). committed is
    emitted when the user sets a value; set_quiet shows one set elsewhere."""
    committed = Signal(float)

    def __init__(self, lo: float, hi: float, unit: str = "", integer: bool = False):
        super().__init__()
        self.unit, self.integer = unit, integer
        self._steps = nice_values(lo, hi)
        self.setRange(lo, hi)
        self.setDecimals(0 if integer else 3)
        self.setKeyboardTracking(False)  # typing commits on Enter, not on each key
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMinimumWidth(84)
        self.valueChanged.connect(self.committed.emit)

    def stepBy(self, steps: int) -> None:
        v = self.value()
        for _ in range(abs(steps)):
            if steps > 0:
                v = next((s for s in self._steps if s > v * (1 + 1e-9)), self.maximum())
            else:
                v = next((s for s in reversed(self._steps) if s < v * (1 - 1e-9)),
                         self.minimum())
        self.setValue(v)

    def textFromValue(self, value: float) -> str:
        text = f"{value:.0f}" if self.integer else f"{value:.4g}"
        return f"{text} {self.unit}".strip()

    def valueFromText(self, text: str) -> float:
        match = re.search(r"[-+]?\d*[.,]?\d+(?:[eE][-+]?\d+)?", text)
        return float(match.group().replace(",", ".")) if match else self.value()

    def validate(self, text: str, pos: int):
        ok = re.search(r"\d", text) is not None
        return (QValidator.Acceptable if ok else QValidator.Intermediate), text, pos

    def set_quiet(self, value: float) -> None:
        self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(False)


def stepper(box: NiceSpinBox, mark: str = "", mark_color: str = INK) -> QWidget:
    """The box between − and + buttons, with an optional mark before it (● or ◆)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(2)
    if mark:
        lbl = QLabel(mark)
        lbl.setStyleSheet(f"color:{mark_color};")
        h.addWidget(lbl)
    for text, steps, tip in (("−", -1, "Down to the previous round value"), (None, 0, ""),
                             ("+", 1, "Up to the next round value")):
        if text is None:
            h.addWidget(box)
            continue
        b = QToolButton()
        b.setText(text)
        b.setObjectName("stepButton")
        b.setFixedSize(24, 24)
        b.setToolTip(tip)
        b.setAutoRepeat(True)
        b.clicked.connect(lambda _=False, s=steps: box.stepBy(s))
        h.addWidget(b)
    return w


def quick_values(values, box: NiceSpinBox) -> QWidget:
    """A row of small buttons that set box to a usual value."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(2)
    for v in values:
        b = QPushButton(box.textFromValue(v).replace(f" {box.unit}", "") if box.unit else str(v))
        b.setObjectName("quickValue")
        b.setToolTip(box.textFromValue(v))
        b.clicked.connect(lambda _=False, v=v: box.setValue(v))
        h.addWidget(b)
    return w


class AccumulationProgress(QWidget):
    """Progress of a measurement: one segment per accumulation, in two alternating
    shades so that each one can be told apart, the current one filling as its exposure
    goes, and the estimated time left.

    The time per accumulation is learnt from those already done (exposure plus the
    CCD readout); before the first one ends, the nominal exposure is used. Nothing is
    drawn while idle, so that the bar does not shift the buttons around it.
    """
    SHADES = (TEAL, "#4f93a0")

    def __init__(self, width: int = 360):
        super().__init__()
        # Narrows down to 240 px when the button row needs the room.
        self.setMinimumWidth(240)
        self.setMaximumWidth(width)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._total = 0
        self._done = 0
        self._exposure = 0.0
        self._started: float | None = None   # when the current accumulation started
        self._durations: list[float] = []
        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(self.update)

    def start_frame(self, index: int, total: int, exposure_s: float) -> None:
        """Accumulation index (from 0) of total has just started."""
        now = time.monotonic()
        if index == 0:
            self._durations = []
        elif self._started is not None and index == self._done + 1:
            self._durations.append(now - self._started)
        self._total, self._done, self._exposure = total, index, exposure_s
        self._started = now
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._total = 0
        self._started = None
        self._timer.stop()
        self.update()

    def _per_frame(self) -> float:
        return (sum(self._durations) / len(self._durations)) if self._durations \
            else self._exposure

    def remaining_s(self) -> float:
        if not self._total or self._started is None:
            return 0.0
        per = self._per_frame()
        current = max(0.0, per - (time.monotonic() - self._started))
        return current + per * (self._total - self._done - 1)

    def paintEvent(self, event) -> None:
        if not self._total:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(LINE))
        p.drawRoundedRect(QRectF(0, 0, w, h), 5, 5)
        gap = 2 if w / self._total >= 6 else 0  # no gaps once segments get too thin
        seg = (w - gap * (self._total - 1)) / self._total
        per = self._per_frame()
        fraction = min(1.0, (time.monotonic() - self._started) / per) if per > 0 else 0.0
        p.setClipRect(QRectF(0, 0, w, h))
        for i in range(self._done + 1):
            filled = 1.0 if i < self._done else fraction
            if filled <= 0:
                continue
            p.setBrush(QColor(self.SHADES[i % 2]))
            p.drawRect(QRectF(i * (seg + gap), 0, seg * filled, h))
        left = self.remaining_s()
        text = f"Exposure {self._done + 1}/{self._total} · " + (
            f"{format_seconds(left)} left" if left >= 0.5 else "finishing")
        p.setClipping(False)
        font = p.font()
        font.setBold(True)
        p.setFont(font)
        # White text with a dark outline reads both on the filled and the empty part.
        p.setPen(QPen(QColor(INK)))
        rect = QRectF(0, 0, w, h)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(rect.translated(dx, dy), Qt.AlignCenter, text)
        p.setPen(QPen(QColor("white")))
        p.drawText(rect, Qt.AlignCenter, text)
        p.end()


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
        self.label.setMinimumWidth(200)
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


def titled_box(title: str, widget: QWidget, extra: QWidget | None = None) -> QWidget:
    """A widget under a title line; extra, if given, sits at the right of that line."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(6, 4, 6, 4)
    label = QLabel(title)
    if extra is None:
        label.setStyleSheet(f"color:{TEAL}; font-weight:600; border-bottom:1px solid {LINE};")
        v.addWidget(label)
    else:
        label.setStyleSheet(f"color:{TEAL}; font-weight:600;")
        header = QWidget()
        header.setObjectName("titleRow")
        header.setStyleSheet(f"#titleRow {{ border-bottom:1px solid {LINE}; }}")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 2)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(extra)
        v.addWidget(header)
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
        self.chk_attach = QCheckBox("Also save the camera image")
        self.chk_attach.setChecked(True)
        self.btn_save = QPushButton("Save latest continuous spectrum")
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
        grid.addWidget(self.chk_attach, 2, 0, 1, 3)
        grid.addWidget(self.btn_save, 3, 0, 1, 3)
        grid.addWidget(hint("Measurements are always saved, in a folder per day (yyyymmdd) as "
                            "sample_001, sample_002…, each with a JSON holding all the "
                            "measurement parameters. Continuous spectra only by hand."), 4, 0, 1, 3)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Data folder", self.edit_folder.text())
        if folder:
            self.edit_folder.setText(folder)

    def sample(self) -> str:
        return self.edit_sample.text().strip() or "sample"

    def folder(self) -> str:
        return self.edit_folder.text().strip() or "."


SPECTRUM_MODES = ("normal", "dim", "hide")


def blend_with_white(color: str, amount: float) -> QColor:
    """The colour faded towards white (amount 0: unchanged, 1: white)."""
    c = QColor(color)
    return QColor(round(c.red() + (255 - c.red()) * amount),
                  round(c.green() + (255 - c.green()) * amount),
                  round(c.blue() + (255 - c.blue()) * amount))


class SpectrumSquare(QWidget):
    """A square in the colour of a spectrum on the plot, to control it.

    A click steps through normal → dimmed → hidden → normal: the square is the full
    colour, faded, or white with a border in the colour. Hovering shows its tooltip and
    emits hovered (to highlight the spectrum); removable ones offer Remove on a right
    click. dashed draws it like a dashed line (the measurement in progress, the
    background).
    """
    clicked = Signal()
    hovered = Signal(bool)
    remove_requested = Signal()
    SIZE = 18

    def __init__(self, color: str, tooltip: str, removable: bool = False,
                 dashed: bool = False):
        super().__init__()
        self.color, self.removable, self.dashed = color, removable, dashed
        self.mode = "normal"
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip.split("\n")[0])
        self.setCursor(Qt.PointingHandCursor)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1.5, 1.5, self.SIZE - 3, self.SIZE - 3)
        fill = {"normal": QColor(self.color), "dim": blend_with_white(self.color, 0.7),
                "hide": QColor("white")}[self.mode]
        p.setPen(QPen(QColor(self.color), 1.5))
        p.setBrush(fill)
        p.drawRoundedRect(rect, 3, 3)
        if self.dashed and self.mode != "hide":
            # Two white dashes across: it stands for a dashed line.
            p.setPen(QPen(QColor("white"), 2))
            y = self.SIZE / 2
            p.drawLine(QPointF(4, y), QPointF(7.5, y))
            p.drawLine(QPointF(10.5, y), QPointF(14, y))
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.RightButton and self.removable:
            menu = QMenu(self)
            menu.addAction("Remove this spectrum", self.remove_requested.emit)
            menu.exec(event.globalPosition().toPoint())

    def enterEvent(self, event) -> None:
        self.hovered.emit(True)

    def leaveEvent(self, event) -> None:
        self.hovered.emit(False)
