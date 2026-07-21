# audio_engine.py
"""Real-time audio: synth voices -> (optional) FX -> speakers, with recording.

Improvements over the reference `python-instrument-thingie` engine:
* Output-only `sounddevice` stream (it's a synth — no broken duplex input path).
* pedalboard FX is *optional*: the instrument makes sound with just NumPy +
  sounddevice; reverb/plugins layer on only if the `pedalboard` package is
  installed. (The reference crashed because an empty local `pedalboard.py`
  shadowed the real package — that file does not exist here.)
* The playhead-driven `Automation` is sampled inside the callback, so drawn
  envelopes automate gain / brightness / reverb in real time.

Control flows in from the GUI thread through a lock-free queue; the audio
callback drains it, so no mutation happens on the audio thread from outside.
"""
import queue
import numpy as np
import sounddevice as sd
import soundfile as sf

from .synth import StringSynth
from .bitstring import BitStringSynth
from .modulokoto import ModuloKotoSynth
from .envelope import Automation

# pedalboard is optional — degrade gracefully if it (or a plugin host) is absent.
try:
    from pedalboard import Pedalboard, Reverb, load_plugin
    _HAVE_PEDALBOARD = True
except Exception:  # pragma: no cover - environment dependent
    Pedalboard = Reverb = load_plugin = None
    _HAVE_PEDALBOARD = False


class AudioEngine:
    # Switchable voice banks sharing one interface (pluck/set_chord/set_bend/render).
    SYNTHS = {"strings": StringSynth, "bitstring": BitStringSynth,
              "modulokoto": ModuloKotoSynth}

    def __init__(self, samplerate=48000, blocksize=256, channels=2, n_strings=6,
                 synth="strings"):
        self.sr = samplerate
        self.block = blocksize
        self.ch = channels
        self.n_strings = n_strings

        self.synth_name = synth if synth in self.SYNTHS else "strings"
        self.synth = self.SYNTHS[self.synth_name](samplerate=samplerate, n=n_strings)
        # The persistent open-string tuning (like a guitar's), independent of
        # chord fretting. Chords override the sounding pitches; this survives so
        # every mode can be tuned and restored to it.
        self.tuning = list(self.synth.base)
        self.warble_on = False
        self.automation = Automation(
            seconds=8.0, lanes=("gain", "brightness", "reverb", "bits", "sustain"))

        self.q_cmd = queue.Queue()
        self.q_rec = queue.Queue()
        self.recording = False
        self.muted = False         # emergency kill switch: silence the output

        # Live parameter targets (0..1) — the automation and UI write these.
        # Seeded from the active bank's per-synth defaults just below.
        self.param_targets = {"gain": 0.8, "brightness": 0.7, "reverb": 0.15,
                              "bits": 0.85, "sustain": 0.6}
        self._apply_synth_defaults()

        # Optional FX chain.
        self.board = None
        self.reverb = None
        if _HAVE_PEDALBOARD:
            self.reverb = Reverb(room_size=0.35, wet_level=self.param_targets["reverb"])
            self.board = Pedalboard([self.reverb])

        self.stream = sd.OutputStream(
            samplerate=self.sr, blocksize=self.block, channels=self.ch,
            dtype="float32", callback=self._callback,
        )
        self.stream.start()

    # -- command plumbing (called from the GUI thread) --------------------
    def _push(self, kind, payload=None):
        self.q_cmd.put((kind, payload))

    def pluck(self, string, velocity=1.0):
        self._push("pluck", (int(string), float(velocity)))

    def set_chord(self, root_midi, quality="maj"):
        self._push("chord", (int(root_midi), str(quality)))

    def set_tuning(self, midi_notes):
        """Set the open-string tuning. Records it as the persistent baseline
        (so it survives chord overrides and synth swaps) and applies it now."""
        self.tuning = [int(m) for m in midi_notes]
        self._push("tuning", list(self.tuning))

    def restore_tuning(self):
        """Drop any chord override and return the strings to the open tuning."""
        self._push("tuning", list(self.tuning))

    def set_bend(self, semitones):
        self._push("bend", float(semitones))

    def set_param(self, name, value):
        self._push("param", (str(name), float(value)))

    def set_synth(self, name):
        """Swap the voice bank. The instance is built on the GUI thread and
        handed to the audio thread through the queue (it isn't audible until
        the callback installs it, so this is race-free)."""
        if name not in self.SYNTHS:
            return False
        inst = self.SYNTHS[name](samplerate=self.sr, n=self.n_strings)
        inst.set_tuning(list(self.synth.base))     # keep the current fretting
        if hasattr(inst, "set_warble"):
            inst.set_warble(self.warble_on)
        self.synth_name = name
        self._push("synth", inst)
        return True

    def set_warble(self, on: bool):
        self.warble_on = bool(on)
        self._push("warble", self.warble_on)

    def damp_all(self):
        """Choke every ringing string (the banks decide what dying means:
        instant for strings/bitstring, quantum evaporation for the koto)."""
        self._push("damp", None)

    # -- transport --------------------------------------------------------
    def play(self, on: bool):
        self.automation.playing = bool(on)

    def start_record(self):
        self.recording = True

    def stop_record(self):
        self.recording = False

    def set_mute(self, on: bool):
        """Emergency kill switch. Read live in the callback (an atomic bool),
        so it silences the output on the very next block."""
        self.muted = bool(on)

    def _apply_synth_defaults(self):
        """Seed the live params and the empty automation lanes' resting values
        from the active bank's DEFAULTS, so each synth sounds sane out of the
        box. Drawn lanes keep their curves (default only affects empty lanes);
        a playing envelope overwrites the live value each block regardless."""
        defaults = getattr(type(self.synth), "DEFAULTS", None)
        if not defaults:
            return
        for name, val in defaults.items():
            self.param_targets[name] = float(val)
            lane = self.automation.lanes.get(name)
            if lane is not None:
                lane.default = float(val)

    # -- FX ---------------------------------------------------------------
    def set_vst3(self, path):
        """Insert a VST3/AU plugin ahead of the reverb (no-op without pedalboard)."""
        if not _HAVE_PEDALBOARD:
            return False
        plugin = load_plugin(path)
        chain = list(self.board) if self.board else []
        chain.insert(0, plugin)
        self.board = Pedalboard(chain)
        return True

    # -- the audio callback ----------------------------------------------
    def _apply_commands(self):
        try:
            while True:
                kind, payload = self.q_cmd.get_nowait()
                if kind == "pluck":
                    i, vel = payload
                    self.synth.pluck(i, vel, brightness=self.param_targets["brightness"])
                elif kind == "chord":
                    self.synth.set_chord(*payload)
                elif kind == "tuning":
                    self.synth.set_tuning(payload)
                elif kind == "bend":
                    self.synth.set_bend(payload)
                elif kind == "param":
                    name, value = payload
                    self.param_targets[name] = float(np.clip(value, 0.0, 1.0))
                elif kind == "synth":
                    self.synth = payload
                    self._apply_synth_defaults()   # sane resting values per bank
                elif kind == "warble":
                    if hasattr(self.synth, "set_warble"):
                        self.synth.set_warble(payload)
                elif kind == "damp":
                    if hasattr(self.synth, "damp_all"):
                        self.synth.damp_all()
        except queue.Empty:
            pass

    def _callback(self, outdata, frames, time, status):
        self._apply_commands()

        # Advance the playhead and let drawn envelopes drive live parameters.
        self.automation.advance(frames / self.sr)
        if self.automation.playing:
            for name, val in self.automation.sample_all().items():
                if name in self.param_targets:
                    self.param_targets[name] = val

        # Emergency kill switch: hard-silence the output immediately. Checked
        # after the command drain (so state stays consistent) but before any
        # synthesis, so a stuck drone cannot leak through while muted.
        if self.muted:
            outdata.fill(0.0)
            return

        # Continuous timbre params flow to the voice bank every block (the
        # Bitstring's lattice depth / tone / sustain are live, not pluck-time).
        pt = self.param_targets
        if hasattr(self.synth, "set_params"):
            self.synth.set_params(brightness=pt["brightness"], bits=pt["bits"],
                                  sustain=pt["sustain"])

        mono = self.synth.render(frames)

        if self.board is not None:
            if self.reverb is not None:
                self.reverb.wet_level = float(self.param_targets["reverb"])
            mono = self.board(mono.reshape(-1, 1), self.sr, reset=False).reshape(-1)

        mono = mono * float(self.param_targets["gain"])
        stereo = np.repeat(mono.reshape(-1, 1), self.ch, axis=1).astype(np.float32)
        np.clip(stereo, -1.0, 1.0, out=stereo)
        outdata[:] = stereo

        if self.recording:
            try:
                self.q_rec.put_nowait(stereo.copy())
            except queue.Full:
                pass

    # -- rendering out ----------------------------------------------------
    def render_wav(self, path):
        chunks = []
        try:
            while True:
                chunks.append(self.q_rec.get_nowait())
        except queue.Empty:
            pass
        if not chunks:
            return False
        sf.write(path, np.concatenate(chunks, axis=0), self.sr)
        return True

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
