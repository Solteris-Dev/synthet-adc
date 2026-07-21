# input_router.py
"""Ask 1: the keyboard / pointer articulation matrix.

Two articulation *roles* — STRUM (attack/picking) and PITCH (fretting) — are
mapped onto input *devices* by a switchable `Mode`. The keyboard is always the
counterpart of whatever the pointer is doing:

    Mode            pointer (mouse / tablet)     keyboard
    -----------------------------------------------------------------
    MOUSE_STRUM     strum (drag across strings)  pitch  (pick chord/root)
    MOUSE_PITCH     pitch (x -> continuous glide) strum  ("less analog": keys pluck)
    DUAL_ANALOG     tablet=pitch, mouse=strum     AUX (TBD placeholder)

The mode can be flipped from the UI or from a separate IO device (MIDI) — see
`app.py`, which lets a MIDI note/CC call `set_mode`.

The router is pure logic: it receives normalised device events from the GUI
surface and calls the audio engine. It holds no Qt types.
"""
from enum import Enum

from .synth import MAJOR_SCALE

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[int(midi) % 12]}{int(midi) // 12 - 1}"


class Mode(Enum):
    MOUSE_STRUM = "mouse=strum, keyboard=pitch"
    MOUSE_PITCH = "mouse=pitch, keyboard=strum"
    DUAL_ANALOG = "tablet=pitch, mouse=strum, keyboard=aux(TBD)"


class Role(Enum):
    STRUM = "strum"
    PITCH = "pitch"
    AUX = "aux"


# Keyboard layouts.
PITCH_KEYS = "asdfghjk"           # diatonic scale degrees -> chord roots
STRUM_KEYS = "123456"             # pluck an individual string
CHORD_QUALITY_KEYS = {"q": "maj", "w": "min", "e": "7", "r": "maj7",
                      "t": "sus4", "y": "5"}


class InputRouter:
    def __init__(self, engine, n_strings=6, pitch_range=24.0, root_octave=48):
        self.engine = engine
        self.n = n_strings
        self.pitch_range = pitch_range     # semitones swept across the surface (mouse-pitch)
        self.root = root_octave            # MIDI root for the keyboard pitch row (C3)
        self.quality = "maj"

        self.snap = False                  # quantise mouse-pitch to semitones?
        self.current_bend = 0.0            # last applied bend, for the UI marker
        self.hover_strum = True            # MOUSE_STRUM: strum on hover vs. click-drag

        self.mode = Mode.MOUSE_STRUM
        self.on_mode_change = None         # optional callback(Mode) for the UI
        self.on_strum = None               # optional callback(string, velocity) for visuals
        self.on_pitch = None               # optional callback(label:str) for visuals
        # Last string-lane each analog device was in, to detect crossings.
        self._last_lane = {"mouse": None, "tablet": None}

    # -- pitch options ----------------------------------------------------
    def set_snap(self, on: bool):
        """Toggle snapping mouse-pitch bends to in-tune semitones."""
        self.snap = bool(on)

    def reset_pitch(self):
        """Return the bend to zero (bound to right-click in MOUSE_PITCH)."""
        self.current_bend = 0.0
        self.engine.set_bend(0.0)
        if self.on_pitch:
            self.on_pitch("reset · 0.0 st")

    # -- mode -------------------------------------------------------------
    def set_mode(self, mode: Mode):
        if isinstance(mode, str):
            mode = Mode[mode]
        self.mode = mode
        self._last_lane = {"mouse": None, "tablet": None}
        if self.on_mode_change:
            self.on_mode_change(mode)

    def _fire_strum(self, string, vel):
        self.engine.pluck(string, vel)
        if self.on_strum:
            self.on_strum(string, vel)

    def cycle_mode(self):
        order = list(Mode)
        self.set_mode(order[(order.index(self.mode) + 1) % len(order)])

    def role_of(self, device: str) -> Role:
        """Which articulation role a device currently performs."""
        if device in ("mouse", "tablet"):
            if self.mode is Mode.MOUSE_STRUM:
                return Role.STRUM
            if self.mode is Mode.MOUSE_PITCH:
                return Role.PITCH
            # DUAL_ANALOG: tablet frets, mouse strums.
            return Role.PITCH if device == "tablet" else Role.STRUM
        # keyboard is the counterpart of the pointer.
        if self.mode is Mode.MOUSE_STRUM:
            return Role.PITCH
        if self.mode is Mode.MOUSE_PITCH:
            return Role.STRUM
        return Role.AUX

    # -- pointer (analog) events -----------------------------------------
    def pointer_moved(self, device, nx, ny, speed):
        """nx, ny in 0..1 (top-left origin); speed in 0..1 (normalised)."""
        role = self.role_of(device)
        if role is Role.STRUM:
            self._strum_pointer(device, ny, speed)
        elif role is Role.PITCH:
            self._pitch_pointer(nx, ny)

    def pointer_released(self, device):
        self._last_lane[device] = None

    def _strum_pointer(self, device, ny, speed):
        """Dragging across the horizontal string lanes plucks each crossed string."""
        lane = min(self.n - 1, max(0, int(ny * self.n)))
        prev = self._last_lane[device]
        self._last_lane[device] = lane
        if prev is None or prev == lane:
            return
        step = 1 if lane > prev else -1
        vel = max(0.25, min(1.0, speed))       # faster drag -> louder pluck
        for s in range(prev + step, lane + step, step):
            self._fire_strum(s, vel)

    def _pitch_pointer(self, nx, ny):
        """x -> pitch bend across `pitch_range` semitones (theremin-like).
        With snap on, lands on in-tune semitones; otherwise continuous."""
        semis = (nx - 0.5) * self.pitch_range
        if self.snap:
            semis = float(round(semis))
        self.current_bend = semis
        self.engine.set_bend(semis)
        if self.on_pitch:
            note = _note_name(self.root + int(round(semis)))
            self.on_pitch(f"{semis:+.2f} st → {note}")

    # -- keyboard (discrete) events --------------------------------------
    def key_pressed(self, key: str):
        key = (key or "").lower()
        role = self.role_of("keyboard")
        if role is Role.PITCH:
            self._pitch_key(key)
        elif role is Role.STRUM:
            self._strum_key(key)
        else:
            self._aux_key(key)

    def key_released(self, key: str):
        pass  # notes ring out and decay naturally; nothing to release yet

    def _pitch_key(self, key):
        if key in CHORD_QUALITY_KEYS:
            self.quality = CHORD_QUALITY_KEYS[key]
            return
        if key in PITCH_KEYS:
            degree = PITCH_KEYS.index(key)
            root = self.root + MAJOR_SCALE[degree % len(MAJOR_SCALE)]
            self.engine.set_bend(0.0)          # keyboard pitch is discrete, not bent
            self.engine.set_chord(root, self.quality)
            if self.on_pitch:
                self.on_pitch(f"{_note_name(root)} {self.quality}")

    def _strum_key(self, key):
        # "less analog" strumming: number keys pluck strings, space strums all.
        if key in STRUM_KEYS:
            self._fire_strum(STRUM_KEYS.index(key), 0.9)
        elif key == " ":
            for s in range(self.n):
                self._fire_strum(s, 0.85)

    def _aux_key(self, key):
        # --- DUAL_ANALOG keyboard role: choke / palm-mute ----------------
        # With both hands on analog devices (tablet=pitch, mouse=strum), the
        # keyboard becomes the damping hand: space chokes every ringing
        # string (on the Modulo Koto this is quantum evaporation — a terraced
        # staircase release). Still open to a richer design; see README.
        if key == " ":
            self.engine.damp_all()
