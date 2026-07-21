# tests/test_gui.py
"""Constructs the full Qt app offscreen with a stubbed audio stream and
exercises the ControlBar wiring, including the synth switch and warble toggle.

Runs under pytest, or directly:  python tests/test_gui.py
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402


class DummyStream:
    def __init__(self, *a, **k): self.callback = k.get("callback")
    def start(self): pass
    def stop(self): pass
    def close(self): pass


def test_app_constructs_and_wires():
    orig = sd.OutputStream
    sd.OutputStream = DummyStream
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        from synthet import app as app_module
        from synthet.bitstring import BitStringSynth

        qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        win = app_module.Main()
        win.show()
        for _ in range(5):
            qapp.processEvents()

        # ControlBar mode -> router
        win.controls.modeChanged.emit("MOUSE_PITCH")
        assert win.router.mode.name == "MOUSE_PITCH"

        # MIDI note flips the mode and syncs the combo
        class Msg:
            type = "note_on"; note = 0; velocity = 100
        win._on_midi(Msg())
        assert win.router.mode.name == "MOUSE_STRUM"
        assert win.controls.mode.currentData() == "MOUSE_STRUM"

        # Synth switch via the combo installs the bitstring bank
        idx = win.controls.synth.findText("bitstring")
        win.controls.synth.setCurrentIndex(idx)     # emits synthChanged
        out = np.zeros((256, 2), dtype=np.float32)
        win.engine._callback(out, 256, None, None)  # drain the swap command
        assert isinstance(win.engine.synth, BitStringSynth)

        # Warble checkbox reaches the synth through the queue
        win.controls.warble.setChecked(True)
        win.engine._callback(out, 256, None, None)
        assert win.engine.synth.warble_on is True

        # A key pluck makes it to the audio path
        win.surface.keyPressEvent(QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress, QtCore.Qt.Key_A, QtCore.Qt.NoModifier, "a"))
        win.engine._callback(out, 256, None, None)

        # Lane combo includes the new lanes
        lanes = [win.controls.lane.itemText(i)
                 for i in range(win.controls.lane.count())]
        assert "bits" in lanes and "sustain" in lanes

        # Transport
        win.controls.playToggled.emit(True)
        assert win.engine.automation.playing is True
        win._on_record(True)
        assert win.engine.recording is True
        win._on_record(False)

        # Emergency mute wiring + the output actually goes silent
        win.controls.btnMute.setChecked(True)
        assert win.engine.muted is True
        out[:] = 0.5
        win.engine._callback(out, 256, None, None)
        assert np.all(out == 0.0), "muted engine must silence output"
        win.controls.btnMute.setChecked(False)
        assert win.engine.muted is False

        # Snap toggle reaches the router
        win.controls.snap.setChecked(True)
        assert win.router.snap is True

        # Hover-strum: a bare mouse-move (no button) plucks in MOUSE_STRUM
        win.router.set_mode("MOUSE_STRUM")

        def hover(y):
            win.surface.mouseMoveEvent(QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove, QtCore.QPointF(40, y),
                QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))

        hover(10); hover(260)
        win.engine._callback(out, 256, None, None)   # drain the plucks
        assert any(f > 0 for f in win.surface._flash), "hover should strum strings"

        # Settings dialog constructs and its controls drive the router live.
        from synthet.settings import SettingsDialog
        dlg = SettingsDialog(win.router)
        dlg.hover.setChecked(False)
        assert win.router.hover_strum is False
        dlg.bend.setValue(7)
        assert win.router.pitch_range == 7.0

        # With hover-strum off, a bare hover no longer strums.
        win.surface._flash = [0.0] * win.surface.n
        win.router.pointer_released("mouse")
        hover(10); hover(260)
        win.engine._callback(out, 256, None, None)
        assert all(f == 0 for f in win.surface._flash), \
            "hover must not strum when hover-strum is disabled"

        # ...but a click-drag still does.
        win.surface.mousePressEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress, QtCore.QPointF(40, 10),
            QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        win.surface.mouseMoveEvent(QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(40, 260),
            QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        win.engine._callback(out, 256, None, None)
        assert any(f > 0 for f in win.surface._flash), "click-drag should still strum"

        # Tuning editor: preset and per-string edits reach the engine live.
        assert hasattr(win.controls, "btnTuning")
        from synthet.tuning import TuningDialog, PRESETS
        tdlg = TuningDialog(win.engine)
        tdlg.preset.setCurrentIndex(tdlg.preset.findText("Drop D (DADGBE)"))
        tdlg._on_preset(0)                            # activated is user-only; call slot
        win.engine._callback(out, 256, None, None)
        assert win.engine.tuning == PRESETS["Drop D (DADGBE)"]
        assert list(win.engine.synth.base) == PRESETS["Drop D (DADGBE)"]
        low = tdlg.combos[0]
        low.setCurrentIndex(low.findData(win.engine.tuning[0] + 1))   # +1 semitone
        win.engine._callback(out, 256, None, None)
        assert win.engine.tuning[0] == PRESETS["Drop D (DADGBE)"][0] + 1
        assert tdlg.preset.currentText() == "Custom"  # no longer a named preset

        win.close()
    finally:
        sd.OutputStream = orig


if __name__ == "__main__":
    test_app_constructs_and_wires()
    print("test_app_constructs_and_wires: OK")
