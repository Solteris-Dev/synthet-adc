# modulokoto.py
"""The Modulo Koto (MK-6) — a koto strung with two's-complement integers.

A physical model of an instrument that cannot exist. Where `bitstring` quantises
a string's displacement, the Modulo Koto goes further: *integer arithmetic is
its physics*. Its laws (each impossible, each the direct cause of a sound
native to chip hardware):

* **Quantised matter** — displacement occupies 2^B discrete levels; the space
  between levels does not exist. Waves are staircases: pulse-flavoured spectra
  with quantisation buzz that grows *relative* to the note as it decays, so
  tails get grittier instead of smoother.
* **Ring-shaped displacement space** — pushing the string past +max does not
  clip it, it *wraps* to −max (integer overflow as a law of nature). Hard
  plucks (velocity ≳ 0.66) write past the ceiling: the first few hundred ms
  snarl with wrap-folded polarity flips, then self-clean into a hollow pulse.
* **Arithmetic friction only** — the string is thermodynamically lossless; the
  sole decay mechanism is truncation-toward-zero at the bridge, and the bridge
  is a *narrow gate*: each pass it truncates only one cell in K (K set by
  sustain) while every other cell rounds to the nearest rung. Decay is
  terraced and depends on the digit content of the wave, not its energy: two
  identical notes decay slightly differently. At sustain = 1 the gate closes
  entirely and rounding freezes the wave into an immortal drone.
* **The existence threshold** — motion smaller than one quantum is not small,
  it is *nonexistent*. A string whose whole block stays ≤ 1 quantum is deleted:
  notes end with a chip-style hard gate, never a fade.
* **Gridded space, slow world clock** — the string is exactly L integer cells
  long and its wave equation ticks at sr/H; between ticks the pickup holds the
  last value (zero-order hold). Pitch is locked to f = sr/(H·(L+0.5)) — the
  exact NES-APU period-register grid, machine temperament included — bends
  zipper in integer steps, and ZOH imaging sprinkles alias shimmer on high notes.
* **Single-wire pickup** — all six strings share one output wire; above four
  ringing strings it time-multiplexes in ~93 Hz slots: dense chords flicker
  into hardware arpeggios while single lines stay solid.
* **The lattice remembers** (ghost memory) — cells beyond the current length
  are never erased; re-fretting longer pulls ghosts of old notes into the loop.

DSP: a delay-line loop over a power-of-two ring buffer holding integer quanta
as float32 (exact below 2^24). Per world-clock tick:

    a = buf[w-L],  b = buf[w-L+1]
    u = (a + b) / 2
    v = trunc(u) if w mod K == 0 else rint(u)  # the narrow friction gate
    v = ((v + M) mod 2M) − M                   # two's-complement wrap, M = 2^(B−1)
    buf[w] = v;  output v/M held for H samples

Block-vectorised in chunks of ≤ L−1 ticks (gather → compute → scatter), which
is exactly the per-sample recurrence: within a chunk, tick i reads lags L and
L−1, so it could only see an intra-chunk write from tick i−L+1 ≥ 0 — impossible
while the chunk is shorter than L−1. `tests/test_core.py` verifies bit-exact
equality against a naive per-tick reference.
"""
from __future__ import annotations  # keeps `float | None` annotations 3.9-safe

import numpy as np

from .synth import STANDARD_TUNING, chord_voicing, midi_to_hz

_P = 2048                  # ring size (power of two -> & masking)
_MASK = _P - 1
_SLOT = 32                 # multiplexer slot, samples (~93 Hz scan at 48k)

_lfsr_cache = None


def _lfsr_table():
    """The NES noise channel's 15-bit LFSR as a ±1 table (pick material)."""
    global _lfsr_cache
    if _lfsr_cache is None:
        reg, seq = 1, np.empty(32767, dtype=np.float32)
        for i in range(32767):
            bit = (reg ^ (reg >> 1)) & 1
            reg = (reg >> 1) | (bit << 14)
            seq[i] = 1.0 if reg & 1 else -1.0
        _lfsr_cache = seq
    return _lfsr_cache


def _wrap(v, M):
    """Two's-complement wrap onto [-M, M). In-place-friendly on float arrays."""
    return ((v + M) % (2 * M)) - M


class KotoVoice:
    """One integer string: delay loop + truncation friction + wrap law."""

    def __init__(self, samplerate=48000):
        self.sr = float(samplerate)
        self.buf = np.zeros(_P, dtype=np.float32)
        self.w = 0                  # monotonic write head (masked on use)
        self.L = 64                 # active length, integer cells
        self.L_target = 64
        self.glide_step = 1 << 30   # cells per block toward L_target
        self.H = 1                  # world-clock hold factor
        self.f0 = 220.0
        self.hold_val = np.float32(0.0)   # ZOH carry across block boundaries
        self.hold_left = 0
        self.active = False
        self.evaporating = False    # commanded quantum evaporation (mute)
        self._patience = 0          # existence-threshold gate counter
        self._gatephase = 0         # rotating friction-gate phase (see _ticks)

    # -- pitch: the integer grid -------------------------------------------
    def set_f0(self, hz: float, glide: float = 0.0, block: int = 256):
        hz = float(min(max(hz, 24.0), 3000.0))
        H = 1 if hz <= 1200.0 else (2 if hz <= 2400.0 else 4)
        L_t = int(np.clip(round(self.sr / (H * hz) - 0.5), 4, _P - 8))
        self.f0 = hz
        if H != self.H:             # register change: retune atomically
            self.H = H
            self.hold_left = 0
        self.L_target = L_t
        if glide <= 0.0:
            self.glide_step = 1 << 30
        else:
            blocks = max(1.0, glide * self.sr / block)
            self.glide_step = max(1, int(np.ceil(abs(L_t - self.L) / blocks)))

    # -- excitation: a square (or LFSR-noise) wavefront, written with wrap --
    def excite(self, velocity: float, duty: float, M: int,
               material: str = "square", noise_offset: int = 0):
        A = float(round(np.clip(velocity, 0.0, 1.0) * 1.5 * M))
        if A < 1.0:
            return
        if not self.active:
            self.L = self.L_target    # a fresh pluck starts at the target fret
        L = self.L
        if material == "noise":
            tbl = _lfsr_table()
            h = max(1, round(L / 48))              # clock-divided noise colour
            need = -(-L // h)
            o = noise_offset % (tbl.size - need)
            wave = np.repeat(tbl[o:o + need], h)[:L]
        else:
            k = int(np.clip(round(duty * L), 1, L - 1))
            wave = np.full(L, -1.0, dtype=np.float32)
            wave[:k] = 1.0
        idx = (self.w - L + np.arange(L)) & _MASK
        # Additive mod-2^B write: velocity past the ceiling engages the wrap
        # law (the snarl); re-plucking a ringing string overflows likewise.
        self.buf[idx] = _wrap(self.buf[idx] + np.float32(A) * wave, M)
        self.active = True
        self.evaporating = False
        self._patience = 0

    def damp(self):
        """Commanded quantum evaporation: strip one quantum per block — a
        terraced NES-style release staircase ending in the existence gate."""
        self.evaporating = True

    # -- the world-clock loop -----------------------------------------------
    def _ticks(self, n: int, K: int, M: int, phase: int = 0) -> np.ndarray:
        """Run n world-clock ticks, chunk-vectorised (chunk ≤ L−1: exact).

        K is the friction gate: the cell written at tick w is truncated toward
        zero when (w + phase) mod K == 0, rounded to the nearest rung
        otherwise. K = 0 means the gate is closed (pure rounding: the
        immortal-drone law). The caller rotates `phase` each block: with a
        fixed phase, L and K can share a factor that parks the gate on a
        subset of cells forever, letting a wave hide on the ungated cells and
        ring immortally when it should decay."""
        out = np.empty(n, dtype=np.float32)
        done = 0
        half = np.float32(0.5)
        while done < n:
            C = min(n - done, self.L - 1)
            off = np.arange(C)
            idx = (self.w - self.L + np.arange(C + 1)) & _MASK
            gth = self.buf[idx]
            u = (gth[:C] + gth[1:]) * half
            v = np.rint(u)
            if K:
                gate = ((self.w + off + phase) % K) == 0   # arithmetic friction
                np.copyto(v, np.trunc(u), where=gate)
            v += M
            np.mod(v, 2 * M, out=v)                      # the wrap law
            v -= M
            self.buf[(self.w + off) & _MASK] = v
            out[done:done + C] = v
            self.w += C
            done += C
        return out

    def render(self, frames: int, K: int, M: int) -> "np.ndarray | None":
        """Render `frames` output samples (quanta, caller normalises by M)."""
        if not self.active:
            return None

        # Glide: L steps toward L_target once per block — the zipper is law.
        if self.L != self.L_target:
            d = self.L_target - self.L
            step = min(abs(d), self.glide_step)
            self.L += step if d > 0 else -step
            # Growth pulls never-erased cells into the loop: ghost memory.

        if self.evaporating:
            idx = (self.w - self.L + np.arange(self.L)) & _MASK
            vals = self.buf[idx]
            self.buf[idx] = np.sign(vals) * np.maximum(np.abs(vals) - 1.0, 0.0)

        H = self.H
        parts = []
        consumed = min(self.hold_left, frames)
        if consumed:
            parts.append(np.full(consumed, self.hold_val, dtype=np.float32))
            self.hold_left -= consumed
        need = frames - consumed
        tv = None
        if need > 0:
            n_ticks = -(-need // H)
            if K:
                self._gatephase = (self._gatephase + 1) % K
            tv = self._ticks(n_ticks, K, M, self._gatephase)
            rep = np.repeat(tv, H) if H > 1 else tv    # zero-order hold
            parts.append(rep[:need])
            extra = rep.size - need
            if extra > 0:
                self.hold_val = tv[-1]
                self.hold_left = extra
        out = parts[0] if len(parts) == 1 else np.concatenate(parts)

        # The existence threshold: sub-quantum motion is nonexistence. A block
        # sees at most frames/H of the string, so a long string needs enough
        # consecutive quiet blocks to cover a full loop traversal before its
        # silence proves it is dead rather than merely sparse.
        if tv is not None:
            if float(np.max(np.abs(tv))) <= 1.0:
                self._patience += 1
                needed = (self.L * H) // frames + 2
                if self._patience >= needed:
                    idx = (self.w - self.L + np.arange(self.L)) & _MASK
                    self.buf[idx] = 0.0        # delete ONLY the active window:
                    self.active = False        # ghosts beyond L survive
                    self.evaporating = False
                    self.hold_left = 0
            else:
                self._patience = 0
        return out


class ModuloKotoSynth:
    """Six integer strings sharing one multiplexed pickup. Drop-in voice bank:
    same interface as `synth.StringSynth` / `bitstring.BitStringSynth`."""

    # Sane resting values for the 5 automation lanes (0..1). Dry (reverb muddies
    # the chip character), ~7-bit lattice, sustain 0.4 -> friction gate K=4 so
    # notes ring plucky instead of drone-immortal.
    DEFAULTS = {"gain": 0.80, "brightness": 0.65, "reverb": 0.10,
                "bits": 0.80, "sustain": 0.40}

    def __init__(self, samplerate=48000, n=6, tuning=None):
        self.sr = samplerate
        self.n = n
        self.voices = [KotoVoice(samplerate) for _ in range(n)]
        self.base = list((tuning or STANDARD_TUNING)[:n])
        if len(self.base) < n:
            self.base += [self.base[-1] + 5] * (n - len(self.base))
        self.bend = 0.0

        # Normalised 0..1 performance params (driven by the automation lanes).
        self.brightness = 0.7      # pluck duty (pick position / hollowness)
        self.bits_f = 0.85         # bit depth B = 3 + round(5*bits): live —
                                   #   lowering it mid-note wrap-folds the wave
        self.sustain = 0.6         # friction gate width K = 2^(2+6·s);
                                   #   s ≥ 0.995 closes the gate (immortal)
        self.flicker = 1.0         # single-wire pickup depth
        self.pick_material = "square"    # or "noise" (the LFSR pick)

        # Warble (grafted from bitstring): chord-quality arpeggio flicker.
        self.warble_on = False
        self.warble_rate = 16.0
        self.warble_intervals = [0, 4, 7]
        self._warble_samples = 0
        self._warble_idx = 0

        self._dc = 0.0             # output-side DC tracker (the lattice keeps
        self._slot0 = 0            #   its domain DC; only the wire is blocked)
        self._noise_off = 0
        self._apply_pitch(glide=0.0)

    # -- parameter mapping ----------------------------------------------------
    def set_params(self, brightness=None, bits=None, sustain=None):
        if brightness is not None:
            self.brightness = float(np.clip(brightness, 0.0, 1.0))
        if bits is not None:
            self.bits_f = float(np.clip(bits, 0.0, 1.0))
        if sustain is not None:
            self.sustain = float(np.clip(sustain, 0.0, 1.0))

    @property
    def _M(self) -> int:
        return 1 << (3 + int(round(5.0 * self.bits_f)) - 1)   # B in [3, 8]

    @property
    def _K(self) -> int:
        """The friction register (2 bits, like everything else here): gate
        spacing K ∈ {2, 4, 8, closed}. Measured decay at K=8 ranges ~3s (A4)
        to ~22s (A2) — longer strings ring longer, as strings do. Beyond K=8
        the rounding attractor heals the sparse truncation and notes go
        immortal, so K=0 (gate closed) is reserved for sustain ≥ 0.995."""
        s = self.sustain
        if s >= 0.995:
            return 0
        if s >= 0.75:
            return 8
        if s >= 0.25:
            return 4
        return 2

    @property
    def _duty(self) -> float:
        return 0.125 + 0.375 * self.brightness

    # -- pitch ------------------------------------------------------------------
    def _apply_pitch(self, glide: float = 0.02):
        off = (self.warble_intervals[self._warble_idx]
               if self.warble_on and self.warble_intervals else 0)
        for v, m in zip(self.voices, self.base):
            v.set_f0(midi_to_hz(m + self.bend + off), glide=glide)

    def set_tuning(self, midi_notes, glide: float = 0.0):
        self.base = list(midi_notes)[: self.n]
        self._apply_pitch(glide)

    def set_chord(self, root_midi: int, quality: str = "maj", glide: float = 0.02):
        self.base = chord_voicing(root_midi, quality, self.n)
        self.warble_intervals = [0, 3, 7] if quality in ("min",) else [0, 4, 7]
        self._apply_pitch(glide)

    def set_bend(self, semitones: float, glide: float = 0.01):
        self.bend = float(semitones)
        self._apply_pitch(glide)          # bends zipper on the integer grid

    # -- warble -------------------------------------------------------------------
    def set_warble(self, on: bool, rate: float = None):
        self.warble_on = bool(on)
        if rate is not None:
            self.warble_rate = float(np.clip(rate, 1.0, 60.0))
        if not self.warble_on:
            self._warble_idx = 0
            self._apply_pitch(glide=0.0)

    def _advance_warble(self, frames: int):
        if not self.warble_on or not self.warble_intervals:
            return
        self._warble_samples += frames
        idx = int(self._warble_samples / self.sr * self.warble_rate) \
            % len(self.warble_intervals)
        if idx != self._warble_idx:
            self._warble_idx = idx
            self._apply_pitch(glide=0.0)  # hard integer re-fret: the flicker

    # -- articulation ------------------------------------------------------------------
    def pluck(self, string: int, velocity: float = 1.0,
              brightness: float | None = None):
        if not 0 <= string < self.n:
            return
        duty = (0.125 + 0.375 * float(np.clip(brightness, 0.0, 1.0))
                if brightness is not None else self._duty)
        self.voices[string].excite(velocity, duty, self._M,
                                   self.pick_material, self._noise_off)
        self._noise_off += 4097

    def damp_all(self):
        """Choke: commanded evaporation on every ringing string."""
        for v in self.voices:
            if v.active:
                v.damp()

    # -- render ---------------------------------------------------------------------------
    def render(self, frames: int) -> np.ndarray:
        self._advance_warble(frames)
        K, M = self._K, self._M
        outs, idxs = [], []
        for i, v in enumerate(self.voices):
            o = v.render(frames, K, M)
            if o is not None:
                outs.append(o * np.float32(1.0 / M))
                idxs.append(i)
        mix = np.zeros(frames, dtype=np.float32)
        if not outs:
            return mix

        # The single-wire pickup: >4 ringing strings time-multiplex in
        # ~93 Hz slots — dense chords become hardware arpeggios.
        if len(outs) > 4 and self.flicker > 0.0:
            dim = np.float32(1.0 - self.flicker)
            n_slots = frames // _SLOT
            for s in range(n_slots):
                owner = idxs[(self._slot0 + s) % len(idxs)]
                a, b = s * _SLOT, (s + 1) * _SLOT
                for i, o in zip(idxs, outs):
                    if i != owner:
                        o[a:b] *= dim
        self._slot0 += frames // _SLOT

        for o in outs:
            mix += o
        mix *= np.float32(0.9 / self.n)

        # Output-side DC block (the pulse's domain DC stays in the string).
        self._dc += 0.2 * (float(mix.mean()) - self._dc)
        mix -= np.float32(self._dc)
        np.clip(mix, -1.0, 1.0, out=mix)
        return mix
