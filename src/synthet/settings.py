# settings.py
"""The Settings panel — a home for input/behaviour preferences.

Deliberately small and live-applying: each control writes straight to the
router (or engine) so changes are audible immediately, no OK/Apply round-trip.
Add new rows here as more toggles earn their place.
"""
from PySide6 import QtWidgets


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, router, parent=None):
        super().__init__(parent)
        self.router = router
        self.setWindowTitle("synthet-adc · Settings")
        self.setMinimumWidth(360)

        form = QtWidgets.QFormLayout(self)
        form.setSpacing(10)

        # --- Mouse strum: hover vs. click ---------------------------------
        self.hover = QtWidgets.QCheckBox("Strum on hover")
        self.hover.setToolTip(
            "MOUSE_STRUM: sweep the cursor across the strings to strum.\n"
            "Off = hold the left button and drag to strum (click-strum).")
        self.hover.setChecked(bool(router.hover_strum))
        self.hover.toggled.connect(lambda v: setattr(self.router, "hover_strum", bool(v)))
        form.addRow("Mouse strum", self.hover)

        # --- Pitch bend range ---------------------------------------------
        self.bend = QtWidgets.QSpinBox()
        self.bend.setRange(2, 48)
        self.bend.setSuffix(" semitones")
        self.bend.setValue(int(round(router.pitch_range)))
        self.bend.setToolTip("MOUSE_PITCH: how many semitones span the full "
                             "width of the surface (also sets the marker grid).")
        self.bend.valueChanged.connect(lambda v: setattr(self.router, "pitch_range", float(v)))
        form.addRow("Pitch bend range", self.bend)

        note = QtWidgets.QLabel("Changes apply live.")
        note.setStyleSheet("color:#8a8a96; font-style:italic;")
        form.addRow("", note)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.accept)
        form.addRow(btns)
