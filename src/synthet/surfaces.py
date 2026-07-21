# surfaces.py
"""Qt widgets. These only capture input and paint — all musical decisions live
in `input_router.InputRouter`, so the surface stays a thin, replaceable skin.

* PerformanceSurface — the play area. Emits normalised pointer (mouse + tablet)
  and keyboard events to the router; draws string lanes, pluck flashes and the
  current pitch readout.
* EnvelopeEditor — draw multi-lane automation; shows the engine's playhead.
* ControlBar — transport, mode switch, envelope-lane picker, save/load.
"""
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .input_router import Mode, Role
from .synth import midi_to_hz  # noqa: F401  (handy for future string labels)

LANE_COLORS = {
    "gain": QtGui.QColor(120, 210, 255),
    "brightness": QtGui.QColor(255, 200, 120),
    "reverb": QtGui.QColor(180, 160, 255),
    "bits": QtGui.QColor(140, 255, 170),
    "sustain": QtGui.QColor(255, 140, 190),
}
LANE_NAMES = tuple(LANE_COLORS)


class PerformanceSurface(QtWidgets.QWidget):
    def __init__(self, router, n_strings=6, parent=None):
        super().__init__(parent)
        self.router = router
        self.n = n_strings
        self.setMinimumSize(680, 300)
        self.setMouseTracking(True)
        self.setTabletTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._flash = [0.0] * n_strings          # per-string decaying highlight
        self._pitch_label = ""
        self._cursor_nx = 0.5                     # last mouse x (for the pitch marker)
        self._last = {"mouse": None, "tablet": None}   # (x, y, t) per device

        router.on_strum = self._on_strum
        router.on_pitch = self._on_pitch

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)                     # ~60 fps

    # -- feedback from the router ----------------------------------------
    def _on_strum(self, string, vel):
        if 0 <= string < self.n:
            self._flash[string] = min(1.0, max(self._flash[string], 0.4 + vel))

    def _on_pitch(self, label):
        self._pitch_label = label

    def _tick(self):
        self._flash = [max(0.0, f - 0.06) for f in self._flash]
        self.update()

    # -- pointer handling -------------------------------------------------
    def _emit_pointer(self, device, pos, extra_speed=0.0):
        w, h = max(1, self.width()), max(1, self.height())
        nx, ny = pos.x() / w, pos.y() / h
        now = time.perf_counter()
        prev = self._last[device]
        speed = extra_speed
        if prev is not None:
            dx, dy = pos.x() - prev[0], pos.y() - prev[1]
            dt = max(1e-3, now - prev[2])
            diag = (w * w + h * h) ** 0.5
            speed = max(speed, ((dx * dx + dy * dy) ** 0.5 / diag) / dt / 6.0)
        self._last[device] = (pos.x(), pos.y(), now)
        self.router.pointer_moved(device, nx, min(1.0, max(0.0, ny)), min(1.0, speed))

    def mouseMoveEvent(self, ev):
        self._cursor_nx = ev.position().x() / max(1, self.width())
        # Strumming can respond to a bare hover (running the cursor across the
        # strings plucks them) — toggleable in Settings. When hover-strum is
        # off, strumming needs a held button (click-drag). Pitch always wants a
        # deliberate drag, so it only ever fires while the button is held.
        strumming = self.router.role_of("mouse") is Role.STRUM
        hover_ok = strumming and self.router.hover_strum
        if hover_ok or (ev.buttons() & QtCore.Qt.LeftButton):
            self._emit_pointer("mouse", ev.position())

    def enterEvent(self, ev):
        # Fresh gesture on (re-)entry: clear the velocity tracker AND the strum
        # baseline, so re-entering at the far end can't sweep every string in
        # one phantom stroke. A deliberate fast sweep from within still fires.
        self._last["mouse"] = None
        self.router.pointer_released("mouse")

    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == QtCore.Qt.LeftButton:
            self._last["mouse"] = None
            self._emit_pointer("mouse", ev.position())
        elif ev.button() == QtCore.Qt.RightButton and \
                self.router.role_of("mouse") is Role.PITCH:
            self.router.reset_pitch()    # right-click zeroes the bend

    def mouseReleaseEvent(self, ev):
        self.router.pointer_released("mouse")
        self._last["mouse"] = None

    def tabletEvent(self, ev):
        # A pen/Wacom is a *second* analog device — this is what enables the
        # DUAL_ANALOG mode (tablet=pitch while the mouse strums).
        pos = ev.position()
        pressure = float(ev.pressure())
        if ev.type() == QtCore.QEvent.TabletRelease:
            self.router.pointer_released("tablet")
            self._last["tablet"] = None
        else:
            self._emit_pointer("tablet", pos, extra_speed=pressure)
        ev.accept()

    # -- keyboard handling ------------------------------------------------
    def keyPressEvent(self, ev):
        if ev.isAutoRepeat():
            return
        self.router.key_pressed(ev.text() or _key_name(ev.key()))

    def keyReleaseEvent(self, ev):
        if ev.isAutoRepeat():
            return
        self.router.key_released(ev.text() or _key_name(ev.key()))

    # -- painting ---------------------------------------------------------
    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        r = self.rect()
        p.fillRect(r, QtGui.QColor(16, 16, 20))

        if self.router.role_of("mouse") is Role.PITCH:
            self._paint_pitch_guides(p, r)

        lane_h = r.height() / self.n
        for i in range(self.n):
            y = r.top() + (i + 0.5) * lane_h
            glow = self._flash[i]
            base = 70 + int(120 * glow)
            pen = QtGui.QPen(QtGui.QColor(base, base, min(255, base + 40)),
                             1 + 2 * glow)
            p.setPen(pen)
            p.drawLine(r.left() + 8, int(y), r.right() - 8, int(y))

        # Mode + pitch readout.
        p.setPen(QtGui.QColor(150, 210, 255))
        f = p.font(); f.setPointSize(10); p.setFont(f)
        p.drawText(r.left() + 12, r.top() + 20, f"Mode: {self.router.mode.name}")
        if self._pitch_label:
            p.setPen(QtGui.QColor(255, 220, 150))
            p.drawText(r.left() + 12, r.top() + 40, f"Pitch: {self._pitch_label}")

        hint = _mode_hint(self.router.mode)
        p.setPen(QtGui.QColor(110, 110, 125))
        p.drawText(r.left() + 12, r.bottom() - 12, hint)

    def _paint_pitch_guides(self, p, r):
        """Vertical semitone grid + a live marker to coordinate bends.

        Centre (x = 0.5) is bend 0; each in-tune semitone gets a tick, brighter
        at the octaves. The amber marker shows where the current cursor maps —
        snapped to the grid when Snap is on."""
        rng = float(self.router.pitch_range)
        half = int(rng / 2)
        for s in range(-half, half + 1):
            nx = 0.5 + s / rng
            x = int(r.left() + nx * r.width())
            if s == 0:
                p.setPen(QtGui.QPen(QtGui.QColor(120, 210, 255), 2))
            elif s % 12 == 0:
                p.setPen(QtGui.QPen(QtGui.QColor(95, 150, 190), 1))
            else:
                p.setPen(QtGui.QColor(46, 50, 60))
            p.drawLine(x, r.top() + 26, x, r.bottom() - 26)

        semis = (self._cursor_nx - 0.5) * rng
        if self.router.snap:
            semis = round(semis)
        mx = int(r.left() + (0.5 + semis / rng) * r.width())
        p.setPen(QtGui.QPen(QtGui.QColor(255, 210, 130),
                            2 if self.router.snap else 1,
                            QtCore.Qt.SolidLine if self.router.snap
                            else QtCore.Qt.DashLine))
        p.drawLine(mx, r.top(), mx, r.bottom())
        tag = "SNAP" if self.router.snap else "free"
        p.setPen(QtGui.QColor(255, 210, 130))
        p.drawText(r.right() - 120, r.top() + 20, f"{semis:+.1f} st · {tag}")


class EnvelopeEditor(QtWidgets.QWidget):
    """Draw automation lanes; the engine's playhead sweeps them (Ask 2)."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.automation = engine.automation
        self.active_lane = "gain"
        self._stroke_t = None            # last painted time within the drag
        self.setMinimumHeight(160)
        self.setMouseTracking(True)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def set_active_lane(self, name):
        self.active_lane = name

    def _edit(self, pos):
        w, h = max(1, self.width()), max(1, self.height())
        t = min(self.automation.seconds, max(0.0, pos.x() / w * self.automation.seconds))
        v = 1.0 - pos.y() / h
        lane = self.automation.lane(self.active_lane)
        # Paint semantics: clear whatever the drag swept over since the last
        # point, then set the new endpoint. The curve then follows the cursor
        # exactly instead of tangling with points from earlier passes.
        if self._stroke_t is not None:
            lane.remove_range(self._stroke_t, t)
        lane.add_point(t, v)
        self._stroke_t = t

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self._stroke_t = None        # begin a fresh stroke
            self._edit(ev.position())
        elif ev.button() == QtCore.Qt.RightButton:
            self.automation.lane(self.active_lane).clear()

    def mouseMoveEvent(self, ev):
        if ev.buttons() & QtCore.Qt.LeftButton:
            self._edit(ev.position())

    def mouseReleaseEvent(self, ev):
        self._stroke_t = None

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, QtGui.QColor(22, 22, 28))

        for i in range(1, 8):                    # time grid
            x = r.left() + i * r.width() / 8
            p.setPen(QtGui.QColor(45, 45, 55))
            p.drawLine(int(x), r.top(), int(x), r.bottom())

        for name, curve in self.automation.lanes.items():
            color = LANE_COLORS.get(name, QtGui.QColor(200, 200, 200))
            active = name == self.active_lane
            pen = QtGui.QPen(color, 2.5 if active else 1.2)
            if not active:
                color = QtGui.QColor(color); color.setAlpha(110); pen.setColor(color)
            p.setPen(pen)
            path = QtGui.QPainterPath()
            steps = max(2, r.width())
            for sx in range(steps):
                t = (sx / steps) * self.automation.seconds
                val = curve.sample(t)
                x = r.left() + sx
                y = r.bottom() - val * r.height()
                path.moveTo(x, y) if sx == 0 else path.lineTo(x, y)
            p.drawPath(path)

        # Playhead.
        ph = self.automation.playhead / max(1e-6, self.automation.seconds)
        x = r.left() + ph * r.width()
        p.setPen(QtGui.QPen(QtGui.QColor(160, 220, 255), 1))
        p.drawLine(int(x), r.top(), int(x), r.bottom())

        p.setPen(LANE_COLORS.get(self.active_lane, QtGui.QColor(220, 220, 220)))
        f = p.font(); f.setPointSize(9); p.setFont(f)
        label = f"editing: {self.active_lane}  (L-drag draw · R-click clear)"
        # Optional per-synth readout of the value under the playhead (e.g. the
        # Bitstring shows the current bit depth on its `bits` lane).
        readout = getattr(self.engine.synth, "lane_readout", None)
        if readout is not None:
            val = self.automation.lane(self.active_lane).sample(self.automation.playhead)
            tag = readout(self.active_lane, val)
            if tag:
                label += f"   →  {tag}"
        p.drawText(r.left() + 8, r.top() + 16, label)


class ControlBar(QtWidgets.QWidget):
    playToggled = QtCore.Signal(bool)
    recordToggled = QtCore.Signal(bool)
    saveRequested = QtCore.Signal()
    loadRequested = QtCore.Signal()
    modeChanged = QtCore.Signal(str)            # Mode.name
    laneChanged = QtCore.Signal(str)
    synthChanged = QtCore.Signal(str)           # voice bank name
    warbleToggled = QtCore.Signal(bool)
    muteToggled = QtCore.Signal(bool)
    snapToggled = QtCore.Signal(bool)
    settingsRequested = QtCore.Signal()
    tuningRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)

        self.btnPlay = QtWidgets.QPushButton("▶ Play"); self.btnPlay.setCheckable(True)
        self.btnRec = QtWidgets.QPushButton("● Rec"); self.btnRec.setCheckable(True)
        self.btnPlay.toggled.connect(self.playToggled.emit)
        self.btnRec.toggled.connect(self.recordToggled.emit)

        # Emergency kill switch. Red when engaged; also bound to Esc in app.py.
        self.btnMute = QtWidgets.QPushButton("🔇 Mute"); self.btnMute.setCheckable(True)
        self.btnMute.setToolTip("Kill the audio instantly (Esc) — silences output "
                                "and chokes every ringing string")
        self.btnMute.setStyleSheet("QPushButton:checked{background:#a03040;color:white;}")
        self.btnMute.toggled.connect(self.muteToggled.emit)

        self.mode = QtWidgets.QComboBox()
        for m in Mode:
            self.mode.addItem(m.name, m.name)
        self.mode.currentIndexChanged.connect(
            lambda _i: self.modeChanged.emit(self.mode.currentData()))

        self.synth = QtWidgets.QComboBox()
        for name in ("strings", "bitstring", "modulokoto"):
            self.synth.addItem(name)
        self.synth.currentTextChanged.connect(self.synthChanged.emit)

        self.warble = QtWidgets.QCheckBox("Warble")
        self.warble.setToolTip("Bitstring only: C64-style arpeggio flicker of "
                               "the string lengths, following the chord quality")
        self.warble.toggled.connect(self.warbleToggled.emit)

        self.btnTuning = QtWidgets.QPushButton("Tuning…")
        self.btnTuning.setToolTip("Set each open string's pitch (independent of "
                                  "chord fretting) — tune it like a guitar")
        self.btnTuning.clicked.connect(self.tuningRequested.emit)

        self.snap = QtWidgets.QCheckBox("Snap")
        self.snap.setToolTip("MOUSE_PITCH: snap bends to in-tune semitones "
                             "(off = continuous / microtonal)")
        self.snap.toggled.connect(self.snapToggled.emit)

        self.lane = QtWidgets.QComboBox()
        for name in LANE_NAMES:
            self.lane.addItem(name)
        self.lane.currentTextChanged.connect(self.laneChanged.emit)

        self.btnSettings = QtWidgets.QPushButton("⚙")
        self.btnSettings.setToolTip("Settings")
        self.btnSettings.setFixedWidth(34)
        self.btnSettings.clicked.connect(self.settingsRequested.emit)

        self.btnSave = QtWidgets.QPushButton("Save…")
        self.btnLoad = QtWidgets.QPushButton("Load…")
        self.btnSave.clicked.connect(self.saveRequested.emit)
        self.btnLoad.clicked.connect(self.loadRequested.emit)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#8a8a96;")

        lay.addWidget(self.btnPlay)
        lay.addWidget(self.btnRec)
        lay.addWidget(self.btnMute)
        lay.addSpacing(12)
        lay.addWidget(QtWidgets.QLabel("Mode:")); lay.addWidget(self.mode)
        lay.addWidget(self.snap)
        lay.addSpacing(8)
        lay.addWidget(QtWidgets.QLabel("Synth:")); lay.addWidget(self.synth)
        lay.addWidget(self.warble)
        lay.addWidget(self.btnTuning)
        lay.addSpacing(8)
        lay.addWidget(QtWidgets.QLabel("Envelope:")); lay.addWidget(self.lane)
        lay.addStretch(1)
        lay.addWidget(self.status)
        lay.addWidget(self.btnSettings)
        lay.addWidget(self.btnSave)
        lay.addWidget(self.btnLoad)

    def set_mode_display(self, mode: Mode):
        """Reflect a mode change that came from elsewhere (e.g. MIDI) without
        re-emitting modeChanged."""
        self.mode.blockSignals(True)
        idx = self.mode.findData(mode.name)
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        self.mode.blockSignals(False)

    def set_synth_display(self, name: str):
        """Reflect a synth change from elsewhere (e.g. session load) without
        re-emitting synthChanged."""
        self.synth.blockSignals(True)
        idx = self.synth.findText(name)
        if idx >= 0:
            self.synth.setCurrentIndex(idx)
        self.synth.blockSignals(False)

    def set_warble_display(self, on: bool):
        self.warble.blockSignals(True)
        self.warble.setChecked(bool(on))
        self.warble.blockSignals(False)

    def set_snap_display(self, on: bool):
        self.snap.blockSignals(True)
        self.snap.setChecked(bool(on))
        self.snap.blockSignals(False)


def _mode_hint(mode: Mode) -> str:
    if mode is Mode.MOUSE_STRUM:
        return "Sweep the cursor across strings to strum · keys a-k pick chord root, q/w/e/r/t/y set quality"
    if mode is Mode.MOUSE_PITCH:
        return "Drag left↔right to bend · right-click resets · Snap = in-tune · keys 1-6 pluck, space strums"
    return "Tablet frets pitch · mouse strums · space chokes the strings"


def _key_name(qt_key: int) -> str:
    if qt_key == QtCore.Qt.Key_Space:
        return " "
    return ""
