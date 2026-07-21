# midi_io.py
"""MIDI input — the optional "separate IO device" that can drive the mode switch.

Threaded reader (from the reference project), degrading gracefully if `mido`
or a backend isn't available so the app still runs MIDI-less.
"""
import threading

try:
    import mido
    _HAVE_MIDI = True
except Exception:  # pragma: no cover - environment dependent
    mido = None
    _HAVE_MIDI = False


class MidiIn:
    def __init__(self, on_message):
        self.on_message = on_message
        self._stop = False
        self.port = None
        self.thread = None

    @staticmethod
    def available():
        if not _HAVE_MIDI:
            return []
        try:
            return mido.get_input_names()
        except Exception:
            return []

    def open(self, name=None):
        if not _HAVE_MIDI:
            return False
        names = self.available()
        port_name = name or (names[0] if names else None)
        if not port_name:
            return False
        self.port = mido.open_input(port_name)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def _loop(self):
        while not self._stop:
            for msg in self.port.iter_pending():
                try:
                    self.on_message(msg)
                except Exception:
                    pass

    def close(self):
        self._stop = True
        if self.port:
            self.port.close()
