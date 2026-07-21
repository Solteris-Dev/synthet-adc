# synth.py
"""The sound source: a small bank of plucked-string voices.

Design goals
------------
* Real-time safe in *pure NumPy* — every voice renders a whole block with
  vectorised ops, no per-sample Python loops, so the audio callback stays cheap.
* Instrument-like — each "string" is an oscillator with a fast attack and an
  exponential decay (a plucked/picked shape), re-pitchable with optional glide.

A `StringSynth` owns N voices (6 by default = a guitar). "Strumming" plucks
individual strings; "pitch" re-frets every string to a chord or bends them all.
The audio engine drives it from its callback via a lock-free command queue.
"""
from __future__ import annotations  # keeps `float | None` annotations 3.9-safe

import numpy as np

# --- music theory helpers ---------------------------------------------------

# Standard guitar tuning as MIDI note numbers: E2 A2 D3 G3 B3 E4
STANDARD_TUNING = [40, 45, 50, 55, 59, 64]

# Chord "shapes" as semitone offsets stacked across the 6 strings, low -> high.
CHORD_SHAPES = {
    "maj":  [0, 7, 12, 16, 19, 24],
    "min":  [0, 7, 12, 15, 19, 24],
    "7":    [0, 7, 10, 16, 19, 22],
    "maj7": [0, 7, 11, 16, 19, 23],
    "sus2": [0, 7, 12, 14, 19, 24],
    "sus4": [0, 7, 12, 17, 19, 24],
    "5":    [0, 7, 12, 19, 24, 31],  # power chord, spread wide
}

# Major scale degrees (semitones) for mapping keyboard "pitch" keys to roots.
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11, 12]

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: int) -> str:
    """MIDI note number -> scientific pitch name, e.g. 40 -> 'E2'."""
    return f"{NOTE_NAMES[int(midi) % 12]}{int(midi) // 12 - 1}"


def midi_to_hz(note: float) -> float:
    """Convert a (possibly fractional) MIDI note number to frequency in Hz."""
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


def chord_voicing(root_midi: int, quality: str = "maj", n: int = 6):
    """Return `n` MIDI notes voicing `quality` on `root_midi`, low -> high."""
    shape = CHORD_SHAPES.get(quality, CHORD_SHAPES["maj"])
    return [root_midi + shape[i % len(shape)] for i in range(n)]


# --- synthesis --------------------------------------------------------------

class Voice:
    """One monophonic string: an oscillator + plucked amplitude envelope.

    The waveform crossfades sine <-> saw by `brightness` (0 = dark/mellow,
    1 = bright/twangy), which is a cheap, alias-friendly stand-in for a real
    pick-position/tone control.
    """

    def __init__(self, samplerate=48000):
        self.sr = float(samplerate)
        self.freq = 110.0
        self.target_freq = 110.0
        self.glide = 0.0            # seconds to reach target_freq (0 = instant)
        self.phase = 0.0            # normalised [0, 1)
        self.decay = 5.0            # exponential decay rate (1/sec); higher = shorter
        self.attack_samples = max(1, int(0.003 * self.sr))  # ~3 ms click-free attack
        self.vel = 0.0             # velocity of the current pluck
        self.brightness = 0.7
        self.age = 0               # samples since the last pluck
        self.active = False

    # -- control ----------------------------------------------------------
    def set_freq(self, hz: float, glide: float = 0.0):
        self.target_freq = max(1.0, float(hz))
        self.glide = max(0.0, float(glide))
        if self.glide <= 0.0:
            self.freq = self.target_freq

    def pluck(self, velocity: float = 1.0, brightness: float | None = None):
        self.vel = float(np.clip(velocity, 0.0, 1.0))
        if brightness is not None:
            self.brightness = float(np.clip(brightness, 0.0, 1.0))
        self.age = 0
        self.active = self.vel > 1e-4

    # -- render -----------------------------------------------------------
    def _advance_freq(self, frames: int):
        if self.freq != self.target_freq and self.glide > 0.0:
            alpha = min(1.0, (frames / self.sr) / self.glide)
            self.freq += (self.target_freq - self.freq) * alpha
            if abs(self.freq - self.target_freq) < 1e-3:
                self.freq = self.target_freq

    def _envelope(self, ages: np.ndarray) -> np.ndarray:
        att = self.attack_samples
        decay_tail = np.exp(-self.decay * np.maximum(0, ages - att) / self.sr)
        env = np.where(ages < att, ages / att, decay_tail)
        return self.vel * env.astype(np.float32)

    def render(self, frames: int) -> np.ndarray:
        if not self.active:
            return np.zeros(frames, dtype=np.float32)
        self._advance_freq(frames)

        n = np.arange(frames)
        inc = self.freq / self.sr
        ph = self.phase + inc * n
        sine = np.sin(2.0 * np.pi * ph)
        saw = 2.0 * (ph - np.floor(ph + 0.5))          # naive saw in [-1, 1]
        b = self.brightness
        wave = ((1.0 - b) * sine + b * saw).astype(np.float32)

        env = self._envelope(self.age + n)
        self.phase = float((self.phase + inc * frames) % 1.0)
        self.age += frames

        # Retire the voice once it has decayed below the noise floor.
        tail = self.vel * np.exp(-self.decay * max(0, self.age - self.attack_samples) / self.sr)
        if self.age > self.attack_samples and tail < 1e-4:
            self.active = False

        return wave * env


class StringSynth:
    """A bank of `n` string voices with tuning / chord / bend control."""

    # Sane resting values for the 5 automation lanes (0..1). `bits` is inert
    # for a real string; a little reverb suits its natural tone.
    DEFAULTS = {"gain": 0.80, "brightness": 0.60, "reverb": 0.22,
                "bits": 0.50, "sustain": 0.55}

    def __init__(self, samplerate=48000, n=6, tuning=None):
        self.sr = samplerate
        self.n = n
        self.voices = [Voice(samplerate) for _ in range(n)]
        self.base = list((tuning or STANDARD_TUNING)[:n])
        if len(self.base) < n:                          # pad if fewer than n given
            self.base += [self.base[-1] + 5] * (n - len(self.base))
        self.bend = 0.0            # global pitch bend in semitones
        self._apply_pitch()

    # -- pitch ------------------------------------------------------------
    def _apply_pitch(self, glide: float = 0.0):
        for v, m in zip(self.voices, self.base):
            v.set_freq(midi_to_hz(m + self.bend), glide=glide)

    def set_tuning(self, midi_notes, glide: float = 0.02):
        self.base = list(midi_notes)[: self.n]
        self._apply_pitch(glide)

    def set_chord(self, root_midi: int, quality: str = "maj", glide: float = 0.03):
        self.base = chord_voicing(root_midi, quality, self.n)
        self._apply_pitch(glide)

    def set_bend(self, semitones: float, glide: float = 0.01):
        """Continuous pitch (mouse-as-pitch): slide every string together."""
        self.bend = float(semitones)
        self._apply_pitch(glide)

    # -- live params (same protocol as bitstring.BitStringSynth) ----------
    def set_params(self, brightness=None, bits=None, sustain=None):
        """`sustain` maps to pluck decay rate; `bits` has no meaning for a
        physical string and is ignored; `brightness` is applied per-pluck by
        the engine, so it needs no handling here."""
        if sustain is not None:
            decay = 12.0 * (1.0 - float(np.clip(sustain, 0.0, 1.0))) + 0.5
            for v in self.voices:
                v.decay = decay

    # -- articulation -----------------------------------------------------
    def pluck(self, string: int, velocity: float = 1.0, brightness: float | None = None):
        if 0 <= string < self.n:
            self.voices[string].pluck(velocity, brightness)

    def damp_all(self):
        """Choke every ringing string dead."""
        for v in self.voices:
            v.vel = 0.0
            v.active = False

    # -- render -----------------------------------------------------------
    def render(self, frames: int) -> np.ndarray:
        out = np.zeros(frames, dtype=np.float32)
        for v in self.voices:
            out += v.render(frames)
        # Soft headroom so a full 6-string strum can't clip hard.
        return out * (0.9 / self.n)
