# tuning.py
"""The Tuning editor — set each open string's pitch, independent of chord
fretting, so every mode plays a properly tuned instrument.

The engine keeps this as its persistent open tuning (`engine.tuning`); chord
input in MOUSE_STRUM overrides the sounding pitches but leaves the tuning
intact, and "Drop chord → tuning" restores it.
"""
from PySide6 import QtWidgets

from .synth import note_name

# Common guitar tunings, low string -> high (MIDI note numbers).
PRESETS = {
    "Standard (EADGBE)": [40, 45, 50, 55, 59, 64],
    "Drop D (DADGBE)":   [38, 45, 50, 55, 59, 64],
    "Open G (DGDGBD)":   [38, 43, 50, 55, 59, 62],
    "Open D (DADF#AD)":  [38, 45, 50, 54, 57, 62],
    "DADGAD":            [38, 45, 50, 55, 57, 62],
    "Half-step down":    [39, 44, 49, 54, 58, 63],
}
_LO, _HI = 24, 84                      # C1 .. C6 selectable per string


class TuningDialog(QtWidgets.QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.n = engine.n_strings
        self.setWindowTitle("synthet-adc · Tuning")
        self.setMinimumWidth(340)
        self._building = False

        lay = QtWidgets.QVBoxLayout(self)

        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(QtWidgets.QLabel("Preset:"))
        self.preset = QtWidgets.QComboBox()
        for name in PRESETS:
            self.preset.addItem(name)
        self.preset.addItem("Custom")
        self.preset.activated.connect(self._on_preset)     # user selection only
        prow.addWidget(self.preset, 1)
        lay.addLayout(prow)

        grid = QtWidgets.QFormLayout()
        self.combos = []
        for _ in range(self.n):
            c = QtWidgets.QComboBox()
            for m in range(_LO, _HI + 1):
                c.addItem(note_name(m), m)
            c.currentIndexChanged.connect(self._on_string)
            self.combos.append(c)
        for i in reversed(range(self.n)):                  # high string on top
            tag = " (low)" if i == 0 else " (high)" if i == self.n - 1 else ""
            grid.addRow(f"String {i + 1}{tag}", self.combos[i])
        lay.addLayout(grid)

        btns = QtWidgets.QHBoxLayout()
        self.btnRestore = QtWidgets.QPushButton("Drop chord → tuning")
        self.btnRestore.setToolTip("Clear any held chord and return every string "
                                   "to this tuning")
        self.btnRestore.clicked.connect(self.engine.restore_tuning)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(self.btnRestore)
        btns.addStretch(1)
        btns.addWidget(close)
        lay.addLayout(btns)

        self._load(list(self.engine.tuning))

    # -- helpers ----------------------------------------------------------
    def _notes(self):
        return [self.combos[i].currentData() for i in range(self.n)]

    def _load(self, notes):
        self._building = True
        for i, c in enumerate(self.combos):
            m = int(notes[i]) if i < len(notes) else _LO
            c.setCurrentIndex(max(_LO, min(_HI, m)) - _LO)
        self._sync_preset(self._notes())
        self._building = False

    def _sync_preset(self, notes):
        self.preset.blockSignals(True)
        match = "Custom"
        for name, ns in PRESETS.items():
            if list(ns[: self.n]) == list(notes[: self.n]):
                match = name
                break
        idx = self.preset.findText(match)
        if idx >= 0:
            self.preset.setCurrentIndex(idx)
        self.preset.blockSignals(False)

    # -- slots ------------------------------------------------------------
    def _on_preset(self, _idx):
        name = self.preset.currentText()
        if name in PRESETS:
            self._load(PRESETS[name][: self.n])
            self.engine.set_tuning(self._notes())

    def _on_string(self, _idx):
        if self._building:
            return
        notes = self._notes()
        self.engine.set_tuning(notes)
        self._sync_preset(notes)
