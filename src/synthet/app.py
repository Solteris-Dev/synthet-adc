# app.py
"""synthet-adc — a computer-native instrument.

Wires the pieces together:
    PerformanceSurface / ControlBar / EnvelopeEditor   (Qt, surfaces.py)
        -> InputRouter  (the strum/pitch role matrix, input_router.py)
            -> AudioEngine  (synth + FX + recording, audio_engine.py)

The input mode can be flipped from the ControlBar *or* from MIDI (a separate IO
device) — see `_on_midi`.
"""
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from .audio_engine import AudioEngine
from .input_router import InputRouter, Mode
from .surfaces import PerformanceSurface, EnvelopeEditor, ControlBar
from .settings import SettingsDialog
from .tuning import TuningDialog
from .midi_io import MidiIn
from . import storage

N_STRINGS = 6


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("synthet-adc — computer instrument")

        self.engine = AudioEngine(n_strings=N_STRINGS)
        self.router = InputRouter(self.engine, n_strings=N_STRINGS)

        self.surface = PerformanceSurface(self.router, n_strings=N_STRINGS)
        self.envelope = EnvelopeEditor(self.engine)
        self.controls = ControlBar()

        # Keep the ControlBar's mode combo in sync when the router changes mode
        # from anywhere (UI or MIDI).
        self.router.on_mode_change = self._on_mode_change

        # ControlBar -> app.
        self.controls.playToggled.connect(self.engine.play)
        self.controls.recordToggled.connect(self._on_record)
        self.controls.modeChanged.connect(self.router.set_mode)
        self.controls.laneChanged.connect(self.envelope.set_active_lane)
        self.controls.synthChanged.connect(self._on_synth)
        self.controls.warbleToggled.connect(self.engine.set_warble)
        self.controls.muteToggled.connect(self._on_mute)
        self.controls.snapToggled.connect(self.router.set_snap)
        self.controls.settingsRequested.connect(self._on_settings)
        self.controls.tuningRequested.connect(self._on_tuning)
        self.controls.saveRequested.connect(self._on_save)
        self.controls.loadRequested.connect(self._on_load)

        # Esc = panic: toggle the kill switch from anywhere in the window.
        panic = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        panic.activated.connect(self.controls.btnMute.toggle)

        central = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(central)
        lay.addWidget(self.controls)
        lay.addWidget(self.surface, stretch=3)
        lay.addWidget(self.envelope, stretch=2)
        self.setCentralWidget(central)
        self.resize(1060, 620)

        # Optional MIDI: notes 0..2 (or any note) cycle/select the mode.
        self.midi = MidiIn(self._on_midi)
        opened = self.midi.open()
        ports = MidiIn.available()
        self.controls.status.setText(
            f"MIDI: {ports[0]}" if opened and ports else "MIDI: none")

        self.surface.setFocus()

    # -- transport --------------------------------------------------------
    def _on_record(self, rec: bool):
        if rec:
            self.engine.start_record()
        else:
            self.engine.stop_record()
            ok = self.engine.render_wav("take.wav")
            self.controls.status.setText(
                "Rendered take.wav" if ok else "Nothing recorded")

    # -- mode sync --------------------------------------------------------
    def _on_mode_change(self, mode: Mode):
        self.controls.set_mode_display(mode)

    def _on_synth(self, name: str):
        if self.engine.set_synth(name):
            self.controls.status.setText(f"Voice bank: {name}")

    def _on_settings(self):
        SettingsDialog(self.router, self).exec()

    def _on_tuning(self):
        TuningDialog(self.engine, self).exec()

    def _on_mute(self, on: bool):
        """Emergency kill switch: silence output and choke every ringing string
        so nothing blares back when unmuted."""
        self.engine.set_mute(on)
        if on:
            self.engine.damp_all()
        self.controls.status.setText("MUTED — press Esc / Mute to resume" if on else "")

    def _on_midi(self, msg):
        """A separate IO device drives the mode switch.

        note_on maps note number -> mode (mod 3); control_change 80 also cycles.
        Runs on the MIDI thread; Qt updates are marshalled by set_mode's callback
        touching only plain Python + a thread-safe combo update, which is fine
        for this single control. For heavier UI work, post a QTimer/queued call.
        """
        if msg.type == "note_on" and msg.velocity > 0:
            modes = list(Mode)
            self.router.set_mode(modes[msg.note % len(modes)])
        elif msg.type == "control_change" and msg.control == 80 and msg.value > 0:
            self.router.cycle_mode()

    # -- session persistence ---------------------------------------------
    def _on_save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save session", "session.json", "JSON (*.json)")
        if path:
            storage.save_session(path, storage.session_dict(self.router, self.engine))
            self.controls.status.setText(f"Saved {path.split('/')[-1]}")

    def _on_load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load session", "", "JSON (*.json)")
        if path:
            data = storage.load_session(path)
            storage.apply_session(data, self.router, self.engine)
            self.controls.set_synth_display(self.engine.synth_name)
            self.controls.set_warble_display(self.engine.warble_on)
            self.controls.set_snap_display(self.router.snap)
            self.controls.status.setText(f"Loaded {path.split('/')[-1]}")

    def closeEvent(self, ev):
        self.midi.close()
        self.engine.close()
        return super().closeEvent(ev)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = Main()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
