# bitstring.py
"""The Bitstring — a physical model of an instrument that cannot exist.

Physical modelling usually simulates a real object (a plucked string, a struck
bar). The Bitstring keeps the *machinery* of physical modelling — a waveguide:
a wave travelling down a string, reflecting, and passing through the bridge —
but the string it models is impossible, and each impossible law is chosen so
its consequence is a sound native to digital machines (a chiptune's voice):

* **Its displacement lives on a lattice.** The string can only occupy 2^bits
  discrete rungs, so a quantiser sits *inside* the waveguide feedback loop and
  every reflection snaps to the grid. Waves on it are staircases: pulse-like,
  buzzy spectra that get crunchier as the note decays toward the step size.
* **Its length is a variable, not a fact.** No mass, no tension: it can be
  re-fretted mid-ring and the travelling wave survives (the loop is resampled
  onto the new length). Glissando and C64-style arpeggio flicker ("warble")
  without re-plucking — impossible for any physical string.
* **It does not resonate, it loops.** At loop gain 1.0 the quantiser locks the
  wave into a limit cycle that sustains forever: the classic DSP *bug* of
  quantised feedback, promoted to the instrument's sustain mechanism.
* **Notes do not fade, they terminate.** Motion smaller than one lattice rung
  does not exist: once the decaying wave goes sub-quantum (which the quantiser
  would otherwise re-amplify into a phantom 1-rung cycle forever), the string
  is deleted — the note gates off the way a chip channel stops, rather than
  the way a room falls silent.
* **The lattice remembers.** Slots beyond the current length are never erased;
  re-lengthening the string pulls ghosts of old notes back into the loop.

DSP: per-voice Karplus–Strong-style delay line with fractional-delay
interpolation, a delay-compensated one-zero tone filter, and the quantiser in
the loop. Rendered block-vectorised: chunks of ≤ L-2 samples are computed
gather → vector math → scatter, which is exactly equivalent to the per-sample
loop because within a chunk no read slot is ever written (see `BitVoice.render`).
"""
from __future__ import annotations  # keeps `float | None` annotations 3.9-safe

import numpy as np

from .synth import STANDARD_TUNING, chord_voicing, midi_to_hz

_MAXLEN = 4096          # lowest pitch ≈ 48000/4096 ≈ 11.7 Hz — plenty
_MINLEN = 4             # highest pitch ≈ 48000/4 = 12 kHz


class BitVoice:
    """One impossible string: a quantised waveguide loop."""

    def __init__(self, samplerate=48000):
        self.sr = float(samplerate)
        self.buf = np.zeros(_MAXLEN, dtype=np.float32)
        self.err = np.zeros(_MAXLEN, dtype=np.float32)  # per-slot rounding debt
        self.L = 64                 # integer loop length (slots in use)
        self.frac = 0.0             # fractional part of the delay
        self.pos = 0                # read/write head
        self.hz = 220.0             # target pitch
        self._applied = None        # (hz, w) the loop geometry currently matches
        self._preserve = True       # resample loop content on the next retune?
        self._floor = 0             # existence-threshold gate counter
        self.active = False

    # -- control ----------------------------------------------------------
    def set_hz(self, hz: float, preserve: bool = True):
        self.hz = float(min(max(hz, self.sr / _MAXLEN), self.sr / (_MINLEN + 2)))
        self._preserve = preserve

    def excite(self, velocity: float, duty: float):
        """Write one period of a DC-free pulse wave into the loop.

        A noise burst (classic Karplus-Strong) sounds like a guitar; a pulse
        period sounds like a chip channel keying on. Duty is the pulse width
        (12.5%..50% — the NES palette). Plucking a ringing string adds to it.
        """
        v = float(np.clip(velocity, 0.0, 1.0))
        if v <= 0.0:
            return
        L = self.L
        k = int(np.clip(round(duty * L), 1, L - 1))
        period = np.full(L, -v, dtype=np.float32)
        period[:k] = v
        period -= period.mean()                     # no DC pumping
        idx = (self.pos + np.arange(L)) % L
        if self.active:
            self.buf[idx] = np.clip(self.buf[idx] + period, -1.0, 1.0)
        else:
            self.buf[idx] = period
        self.err[idx] = 0.0                          # a pluck forgives all debts
        self.active = True
        self._floor = 0

    def damp(self):
        """Choke the string dead (a palm mute for an impossible string)."""
        self.buf[: self.L] = 0.0
        self.active = False

    # -- loop geometry ------------------------------------------------------
    def _retune(self, w: float):
        """Apply pending pitch/tone to the loop geometry.

        Total loop delay must equal sr/hz; the tone FIR contributes ~w samples
        of delay, the integer line L, and the interp frac the rest.
        """
        if self._applied == (self.hz, w):
            return
        total = self.sr / self.hz - w
        Li = int(np.clip(int(total), _MINLEN, _MAXLEN - 4))
        frac = float(np.clip(total - Li, 0.0, 1.0))

        if self.active and self._preserve and abs(Li - self.L) > 2:
            # The impossible part: the wave survives the length change.
            # Resample the loop (in time order from the head) onto the new length.
            old = self.buf[(self.pos + np.arange(self.L)) % self.L]
            xs = np.linspace(0.0, self.L, Li, endpoint=False)
            self.buf[:Li] = np.interp(xs, np.arange(self.L + 1),
                                      np.append(old, old[0])).astype(np.float32)
            self.err[:Li] = 0.0        # refretting voids the rounding debts
            self.pos = 0
        # Small changes (bends) and non-preserving changes (warble) just move
        # the loop boundary: shrinking truncates, growing pulls in whatever the
        # disused slots still hold — "the lattice remembers".
        self.L = Li
        self.frac = frac
        self.pos %= Li
        self._applied = (self.hz, w)

    # -- render -------------------------------------------------------------
    def render(self, frames: int, step: float, w: float, a: float) -> np.ndarray:
        """Render a block. step = quantiser step size, w = tone FIR weight
        [0..0.5], a = loop gain (sustain; 1.0 = infinite limit cycle).

        Chunked vectorisation: for a chunk of m ≤ L-2 samples starting at
        `pos`, sample i reads slots pos+i, pos+i+1, pos+i+2 and writes slot
        pos+i. Within the chunk, every slot a sample reads was last written at
        least L samples ago (a write from step j < i lands at pos+j, which a
        later read only revisits after wrapping the full loop — impossible
        while m ≤ L-2). So gather-all → compute → scatter-all is *exactly* the
        per-sample recurrence, with no per-sample Python.
        """
        if not self.active:
            return np.zeros(frames, dtype=np.float32)
        self._retune(w)

        buf, L, frac = self.buf, self.L, np.float32(self.frac)
        wf, af = np.float32(w), np.float32(a)
        # THE SUSTAIN LAW: at loop gain exactly 1 the lattice forgives all
        # rounding debts — the quantiser re-amplifies the filter's losses and
        # locks the wave into an immortal limit cycle. Below 1, every rung
        # remembers what it owes and the note truthfully decays.
        forgiven = af >= np.float32(1.0)
        out = np.empty(frames, dtype=np.float32)
        done = 0
        ypk = 0.0
        while done < frames:
            m = min(frames - done, L - 2)
            idx = self.pos + np.arange(m)
            idx[idx >= L] -= L
            i1 = idx + 1; i1[i1 >= L] -= L
            i2 = i1 + 1; i2[i2 >= L] -= L
            t0, t1, t2 = buf[idx], buf[i1], buf[i2]
            d0 = t0 + frac * (t1 - t0)          # fractional delay
            d1 = t1 + frac * (t2 - t1)
            y = d0 + wf * (d1 - d0)             # tone FIR (delay-compensated)
            ypk = max(ypk, float(np.max(np.abs(y))))
            # Quantise. With debts remembered (a < 1), per-slot error feedback
            # keeps decay truthful — otherwise round() re-amplifies every
            # sub-half-rung decrement and the level freezes forever.
            if forgiven:
                z = np.round(y / step) * step   # the lattice, debts forgiven
            else:
                ye = y + self.err[idx]
                z = np.round(ye / step) * step  # the lattice
            np.clip(z, -1.0, 1.0, out=z)
            z = z.astype(np.float32)
            if not forgiven:
                self.err[idx] = ye - z          # carry the rounding debt
            buf[idx] = af * z
            out[done:done + m] = z
            done += m
            self.pos = int(idx[-1] + 1) % L if m else self.pos

        # Termination: either the lattice rounded the whole block to zero, or
        # (the existence threshold) the underlying wave has gone sub-quantum
        # while decaying — at a < 1 the quantiser would otherwise re-amplify
        # a 1-rung limit cycle forever, so sub-quantum motion is *deleted*.
        # At a == 1.0 exactly, the limit cycle is the sustain law and lives.
        if not out.any():
            self.active = False
        elif af < np.float32(1.0) and ypk < step:
            self._floor += 1
            if self._floor >= 8:                # ~40ms of sub-quantum residue
                self.buf[:L] = 0.0              # ghosts beyond L survive
                self.err[:L] = 0.0
                self.active = False
        else:
            self._floor = 0
        return out


class BitStringSynth:
    """Six Bitstrings. Drop-in alternative voice bank to `synth.StringSynth`:
    same pluck / set_chord / set_tuning / set_bend / render interface, so the
    input router and audio engine can host either without knowing which."""

    # Bitstring lives in its low-bit crunch, so the `bits` lane is capped well
    # below a clean 8-bit: the full 0..1 travel spans BITS_MIN..BITS_MAX, giving
    # finer, more precise control down where the good sounds are (per the user;
    # "cap lower and make the editor range more precise, bitstring only").
    BITS_MIN, BITS_MAX = 1.0, 5.0

    # Sane resting values for the 5 automation lanes (0..1): a crunchy low-bit
    # lattice, a touch of reverb, medium decay.
    DEFAULTS = {"gain": 0.80, "brightness": 0.60, "reverb": 0.15,
                "bits": 0.45, "sustain": 0.55}

    def __init__(self, samplerate=48000, n=6, tuning=None):
        self.sr = samplerate
        self.n = n
        self.voices = [BitVoice(samplerate) for _ in range(n)]
        self.base = list((tuning or STANDARD_TUNING)[:n])
        if len(self.base) < n:
            self.base += [self.base[-1] + 5] * (n - len(self.base))
        self.bend = 0.0

        # Normalised 0..1 performance params (driven by the automation lanes).
        self.brightness = 0.7      # pulse duty at excite + loop tone
        self.bits_f = 0.45         # lattice depth over BITS_MIN..BITS_MAX
        self.sustain = 0.6         # loop gain: stair-decay .. infinite limit cycle

        # Warble: C64-style arpeggio flicker of the string lengths.
        self.warble_on = False
        self.warble_rate = 16.0    # switches per second
        self.warble_intervals = [0, 4, 7]
        self._warble_samples = 0   # sample-counted clock (no wall time in audio)
        self._warble_idx = 0

        self._apply_pitch()

    # -- parameter mapping --------------------------------------------------
    def set_params(self, brightness=None, bits=None, sustain=None):
        if brightness is not None:
            self.brightness = float(np.clip(brightness, 0.0, 1.0))
        if bits is not None:
            self.bits_f = float(np.clip(bits, 0.0, 1.0))
        if sustain is not None:
            self.sustain = float(np.clip(sustain, 0.0, 1.0))

    @classmethod
    def bits_to_depth(cls, value_0_1: float) -> float:
        return cls.BITS_MIN + (cls.BITS_MAX - cls.BITS_MIN) * float(value_0_1)

    def lane_readout(self, name, value_0_1):
        """Optional per-synth label for an automation-lane value (shown in the
        editor). Only `bits` is meaningful for the Bitstring."""
        if name == "bits":
            return (f"{self.bits_to_depth(value_0_1):.1f} bits "
                    f"(cap {self.BITS_MIN:.0f}–{self.BITS_MAX:.0f})")
        return None

    @property
    def bit_depth(self) -> float:
        """Continuous lattice depth in 'bits', capped to the crunchy zone."""
        return self.bits_to_depth(self.bits_f)

    @property
    def _step(self) -> float:
        return float(2.0 ** (1.0 - self.bit_depth))

    @property
    def _w(self) -> float:
        return 0.5 * (1.0 - self.brightness)    # bright = no damping filter

    @property
    def _a(self) -> float:
        # Per-*period* decay (the loop applies `a` once per trip). 1.0 exactly
        # -> the quantiser's limit cycle sustains forever.
        return 0.999 + 0.001 * self.sustain

    @property
    def _duty(self) -> float:
        return 0.125 + 0.375 * self.brightness  # 12.5%..50%, the chip palette

    # -- pitch ----------------------------------------------------------------
    def _apply_pitch(self, preserve: bool = True):
        off = (self.warble_intervals[self._warble_idx]
               if self.warble_on and self.warble_intervals else 0)
        for v, m in zip(self.voices, self.base):
            v.set_hz(midi_to_hz(m + self.bend + off), preserve=preserve)

    def set_tuning(self, midi_notes, glide: float = 0.0):
        self.base = list(midi_notes)[: self.n]
        self._apply_pitch()

    def set_chord(self, root_midi: int, quality: str = "maj", glide: float = 0.0):
        self.base = chord_voicing(root_midi, quality, self.n)
        # Warble follows the harmony: minor chords flicker minor arpeggios.
        self.warble_intervals = [0, 3, 7] if quality in ("min",) else [0, 4, 7]
        self._apply_pitch()

    def set_bend(self, semitones: float, glide: float = 0.0):
        self.bend = float(semitones)
        self._apply_pitch()

    # -- warble ---------------------------------------------------------------
    def set_warble(self, on: bool, rate: float = None):
        self.warble_on = bool(on)
        if rate is not None:
            self.warble_rate = float(np.clip(rate, 1.0, 60.0))
        if not self.warble_on:
            self._warble_idx = 0
            self._apply_pitch(preserve=False)

    def _advance_warble(self, frames: int):
        if not self.warble_on or not self.warble_intervals:
            return
        self._warble_samples += frames
        idx = int(self._warble_samples / self.sr * self.warble_rate) \
            % len(self.warble_intervals)
        if idx != self._warble_idx:
            self._warble_idx = idx
            # Raw length switch, no resample: the hard re-pitch (and whatever
            # ghosts the disused slots hold) IS the arpeggio-flicker sound.
            self._apply_pitch(preserve=False)

    # -- articulation -----------------------------------------------------------
    def pluck(self, string: int, velocity: float = 1.0,
              brightness: float | None = None):
        if not 0 <= string < self.n:
            return
        duty = (0.125 + 0.375 * float(np.clip(brightness, 0.0, 1.0))
                if brightness is not None else self._duty)
        v = self.voices[string]
        v._retune(self._w)                       # excite at the current length
        v.excite(velocity, duty)

    def damp_all(self):
        for v in self.voices:
            v.damp()

    # -- render -------------------------------------------------------------------
    def render(self, frames: int) -> np.ndarray:
        self._advance_warble(frames)
        step, w, a = self._step, self._w, self._a
        out = np.zeros(frames, dtype=np.float32)
        for v in self.voices:
            out += v.render(frames, step, w, a)
        return out * (0.9 / self.n)
