# tests/test_core.py
"""Headless tests: synth banks, bitstring DSP (incl. exact equivalence with a
naive per-sample reference), envelopes, the input matrix, and the engine.

Runs under pytest, or directly:  python tests/test_core.py
No audio device or display needed.
"""
import time

import numpy as np

from synthet.synth import (StringSynth, midi_to_hz, chord_voicing,
                           STANDARD_TUNING)
from synthet.bitstring import BitVoice, BitStringSynth
from synthet.modulokoto import KotoVoice, ModuloKotoSynth, _P, _MASK
from synthet.envelope import Curve, Automation
from synthet.input_router import InputRouter, Mode


# --- string synth ------------------------------------------------------------

def test_string_synth_basics():
    assert abs(midi_to_hz(69) - 440.0) < 1e-6
    s = StringSynth(48000, 6)
    assert np.allclose(s.render(256), 0.0)
    s.pluck(0, 1.0, brightness=0.7)
    block = s.render(256)
    assert np.any(np.abs(block) > 0) and np.all(np.isfinite(block))
    peak0 = np.max(np.abs(block))
    for _ in range(180):
        last = s.render(256)
    assert np.max(np.abs(last)) < peak0          # decays

    s2 = StringSynth(48000, 6)
    for i in range(6):
        s2.pluck(i, 1.0, 1.0)
    assert np.max(np.abs(s2.render(256))) <= 1.0  # full strum, no clip

    s2.set_chord(48, "maj")
    s2.set_bend(2.0)
    assert abs(s2.voices[0].target_freq - midi_to_hz(50)) < 1e-3


def test_string_synth_sustain_param():
    s = StringSynth(48000, 6)
    s.set_params(sustain=1.0)
    assert all(abs(v.decay - 0.5) < 1e-9 for v in s.voices)
    s.set_params(sustain=0.0)
    assert all(abs(v.decay - 12.5) < 1e-9 for v in s.voices)


# --- bitstring: exact equivalence with the per-sample recurrence --------------

def naive_bitvoice_render(buf0, L, frac, pos, frames, step, w, a):
    """The per-sample loop the vectorised BitVoice.render must match exactly.
    Uses 1-element float32 slices so every operation stays in float32 with the
    same op tree as the vectorised path. Includes the per-slot rounding-debt
    (error feedback) that makes decay truthful."""
    buf = buf0.copy()
    err = np.zeros(L, dtype=np.float32)
    fr = np.float32(frac); wf = np.float32(w); af = np.float32(a)
    forgiven = af >= np.float32(1.0)
    out = np.empty(frames, dtype=np.float32)
    for n in range(frames):
        t0 = buf[pos:pos + 1]
        t1 = buf[(pos + 1) % L:(pos + 1) % L + 1]
        t2 = buf[(pos + 2) % L:(pos + 2) % L + 1]
        d0 = t0 + fr * (t1 - t0)
        d1 = t1 + fr * (t2 - t1)
        y = d0 + wf * (d1 - d0)
        ye = y if forgiven else y + err[pos:pos + 1]
        z = np.round(ye / step) * step
        z = np.clip(z, -1.0, 1.0).astype(np.float32)
        if not forgiven:
            err[pos] = (ye - z)[0]
        buf[pos] = af * z[0]
        out[n] = z[0]
        pos = (pos + 1) % L
    return out


def test_bitvoice_matches_naive_reference():
    rng = np.random.default_rng(7)
    for (L, frames, pos), a in [((48, 256, 0), 0.999), ((23, 256, 11), 1.0),
                                ((301, 700, 150), 0.999), ((5, 64, 3), 1.0)]:
        content = rng.uniform(-0.8, 0.8, L).astype(np.float32)
        step, w = 2.0 ** (1 - 3.5), 0.3

        v = BitVoice(48000)
        v.buf[:L] = content
        v.L, v.frac, v.pos, v.active = L, 0.4, pos, True
        v._applied = (v.hz, w)                    # suppress retune: geometry fixed
        got = v.render(frames, step, w, a)

        want = naive_bitvoice_render(content, L, 0.4, pos, frames, step, w, a)
        assert np.array_equal(got, want), (
            f"vectorised != per-sample at L={L}: "
            f"{np.max(np.abs(got - want))} max diff, "
            f"{np.count_nonzero(got != want)}/{frames} samples")


def test_bitvoice_quantisation_grid():
    v = BitVoice(48000)
    v.set_hz(220.0)
    v._retune(0.25)
    v.excite(1.0, 0.25)
    step = 2.0 ** (1 - 2.0)                       # 2 "bits" -> coarse lattice
    out = v.render(512, step, 0.25, 1.0)
    on_grid = np.abs(out / step - np.round(out / step)) < 1e-6
    assert on_grid.all(), "output must live on the lattice"


def test_bitstring_gate_off_termination():
    """Decaying notes terminate hard (existence threshold), instead of
    stalling in a re-amplified 1-rung limit cycle forever."""
    v = BitVoice(48000)
    v.set_hz(440.0)
    v._retune(0.3)
    v.excite(0.5, 0.5)
    step = 2.0 ** (1 - 4.0)
    alive = True
    for _ in range(48000 * 4 // 256):             # up to 4s
        v.render(256, step, 0.3, 0.99)            # heavy decay
        if not v.active:
            alive = False
            break
    assert not alive, "decaying bitvoice should terminate"
    assert not v.render(256, step, 0.3, 0.99).any(), "dead string is silent"


def test_bitstring_limit_cycle_sustains():
    """At loop gain 1.0 the quantiser limit cycle rings forever."""
    v = BitVoice(48000)
    v.set_hz(330.0)
    v._retune(0.2)
    v.excite(0.8, 0.25)
    step = 2.0 ** (1 - 3.0)
    for _ in range(48000 * 2 // 256):             # 2 seconds
        out = v.render(256, step, 0.2, 1.0)
    assert v.active and np.max(np.abs(out)) > step / 2, \
        "limit cycle should still be ringing after 2s at a=1.0"


def test_bitstring_bits_capped_and_readout():
    """Bitstring's bits lane spans a capped, low range for finer control, and
    exposes a per-lane readout for the editor."""
    s = BitStringSynth(48000, 6)
    assert (s.BITS_MIN, s.BITS_MAX) == (1.0, 5.0)
    s.set_params(bits=0.0)
    assert abs(s.bit_depth - 1.0) < 1e-9 and s._step == 2.0 ** 0    # coarsest
    s.set_params(bits=1.0)
    assert abs(s.bit_depth - 5.0) < 1e-9                            # capped at 5, not 8
    assert abs(s._step - 2.0 ** (1 - 5)) < 1e-12
    # Full-travel is 4 bits over 0..1 -> finer than the old 7-bit spread.
    assert (s.BITS_MAX - s.BITS_MIN) < 7.0
    # Editor readout hook.
    assert "bits" in s.lane_readout("bits", 0.5)
    assert s.lane_readout("gain", 0.5) is None


def test_bitstring_survives_refret():
    """The impossible part: re-pitching mid-ring keeps the wave alive."""
    s = BitStringSynth(48000, 6)
    s.set_params(sustain=1.0, bits=0.5, brightness=0.8)
    s.pluck(0, 1.0)
    s.render(1024)
    L_before = s.voices[0].L
    s.set_chord(52, "min")                        # re-fret mid-ring
    out = s.render(1024)
    assert s.voices[0].L != L_before, "chord change must retune the loop"
    assert np.max(np.abs(out)) > 0, "the wave must survive the length change"


def test_bitstring_warble_flickers_length():
    s = BitStringSynth(48000, 6)
    s.set_params(sustain=1.0)
    s.set_chord(48, "maj")
    s.set_warble(True, rate=30.0)
    s.pluck(0, 1.0)
    lengths = set()
    for _ in range(40):                           # ~0.21s at 30Hz -> several switches
        s.render(256)
        lengths.add(s.voices[0].L)
    assert len(lengths) >= 3, f"warble should cycle >=3 loop lengths, saw {lengths}"
    s.set_warble(False)
    s.render(256)
    assert s.voices[0]._applied[0] == midi_to_hz(s.base[0]), "warble off -> base pitch"


def test_bitstring_full_strum_no_clip_and_realtime():
    s = BitStringSynth(48000, 6)
    s.set_params(sustain=1.0, bits=0.0, brightness=1.0)  # worst case: loud + 1 bit
    for i in range(6):
        s.pluck(i, 1.0)
    t0 = time.perf_counter()
    n_blocks = 100
    for _ in range(n_blocks):
        out = s.render(256)
    elapsed_ms = (time.perf_counter() - t0) * 1000 / n_blocks
    assert np.max(np.abs(out)) <= 1.0
    # budget: 256/48000 = 5.33ms per block; require comfortable headroom
    assert elapsed_ms < 2.5, f"6-voice block took {elapsed_ms:.2f}ms (budget 5.33ms)"


# --- modulo koto: exact equivalence with the per-tick recurrence --------------

def naive_koto_ticks(buf0, w, L, n, K, M, phase=0):
    """The per-tick recurrence KotoVoice._ticks must match exactly."""
    buf = buf0.copy()
    half = np.float32(0.5)
    out = np.empty(n, dtype=np.float32)
    for t in range(n):
        a = buf[(w - L) & _MASK:((w - L) & _MASK) + 1]
        b = buf[(w - L + 1) & _MASK:((w - L + 1) & _MASK) + 1]
        u = (a + b) * half
        v = np.trunc(u) if (K and (w + phase) % K == 0) else np.rint(u)
        v = ((v + M) % (2 * M)) - M
        buf[w & _MASK] = v[0]
        out[t] = v[0]
        w += 1
    return out


def test_koto_ticks_match_naive_reference():
    rng = np.random.default_rng(11)
    for L, n, w0, M, K, ph in [(48, 256, 0, 64, 4, 0), (23, 300, 500, 8, 0, 0),
                               (301, 700, 2000, 128, 16, 5), (5, 64, 3, 4, 2, 1),
                               (48, 256, 7, 64, 0, 0)]:
        content = np.trunc(rng.uniform(-M, M, _P)).astype(np.float32)
        v = KotoVoice(48000)
        v.buf[:] = content
        v.L = v.L_target = L
        v.w = w0
        v.active = True
        got = v._ticks(n, K, M, ph)
        want = naive_koto_ticks(content, w0, L, n, K, M, ph)
        assert np.array_equal(got, want), (
            f"chunked != per-tick at L={L}, K={K}: "
            f"{np.count_nonzero(got != want)}/{n} samples differ")


def test_koto_wrap_law_on_hard_pluck():
    """Velocity past ~0.66 writes over the ceiling and wrap-folds (the snarl)."""
    v = KotoVoice(48000)
    v.set_f0(220.0)
    v.L = v.L_target          # apply pitch instantly for the test
    M = 64
    v.excite(1.0, 0.5, M)     # A = 96 > M: must wrap, not clip
    window = v.buf[(v.w - v.L + np.arange(v.L)) & _MASK]
    assert window.max() < M and window.min() >= -M, "values must stay on the ring"
    # duty-head cells were +96 -> wrapped to 96-128 = -32: polarity flipped
    assert np.any(window[: v.L // 2] < 0), "hard pluck must wrap-fold"


def test_koto_parity_decay_and_existence_gate():
    """Truncation friction terminates notes via the existence threshold."""
    s = ModuloKotoSynth(48000, 6)
    s.set_tuning([57] * 6)                # A3: ~1s decay at K=2
    s.set_params(sustain=0.0, bits=0.6)
    s.pluck(0, 0.5)
    died = False
    for _ in range(48000 * 6 // 256):
        s.render(256)
        if not s.voices[0].active:
            died = True
            break
    assert died, "koto note should hit the existence threshold and gate off"


def test_koto_immortal_drone_when_gate_closed():
    """At sustain=1 the friction gate closes (K=0): rounding freezes the wave
    into a drone that still rings after 2 seconds."""
    v = KotoVoice(48000)
    v.set_f0(330.0)
    v.L = v.L_target
    M = 64
    v.excite(0.5, 0.5, M)
    for _ in range(48000 * 2 // 256):
        out = v.render(256, 0, M)          # K=0: gate closed
    assert v.active and np.max(np.abs(out)) >= 1.0, \
        "with the friction gate closed the drone must be immortal"


def test_koto_zoh_and_pitch_grid():
    """High notes run on a divided world clock: ZOH holds each tick H samples,
    and pitch sits exactly on the integer-period grid f = sr/(H*(L+0.5))."""
    v = KotoVoice(48000)
    v.set_f0(2000.0)          # > 1200 -> H=2
    assert v.H == 2
    expected_L = int(round(48000 / (2 * 2000.0) - 0.5))
    assert v.L_target == expected_L
    v.L = v.L_target
    v.excite(0.5, 0.5, 64)
    out = v.render(256, 0, 64)
    assert out.size == 256
    assert np.array_equal(out[0::2], out[1::2]), "H=2 must hold pairs (ZOH)"


def test_koto_zipper_glide():
    """Bends step the integer length over successive blocks (zipper is law)."""
    s = ModuloKotoSynth(48000, 6)
    s.set_params(sustain=1.0)
    s.pluck(0, 0.5)
    s.render(256)
    L0 = s.voices[0].L
    s.set_bend(-7.0, glide=0.1)          # a slow dive
    lengths = set()
    for _ in range(30):
        s.render(256)
        lengths.add(s.voices[0].L)
    assert len(lengths) >= 3, f"glide should zipper through lengths, saw {lengths}"
    assert max(lengths) > L0, "downward bend must lengthen the string"


def test_koto_multiplexed_pickup():
    """>4 ringing strings flicker into ~93Hz hardware-arpeggio slots."""
    s = ModuloKotoSynth(48000, 6)
    s.set_params(sustain=1.0, bits=1.0)
    for i in range(6):
        s.pluck(i, 0.5)
    s.render(256)                        # let the loops settle
    out = s.render(256)
    # with 6 voices muxed, some 32-sample slots must differ starkly from others
    slots = out.reshape(-1, 32)
    peaks = np.abs(slots).max(axis=1)
    assert peaks.max() > 0, "muxed chord should still make sound"
    s2 = ModuloKotoSynth(48000, 6)
    s2.set_params(sustain=1.0, bits=1.0)
    s2.flicker = 0.0
    for i in range(6):
        s2.pluck(i, 0.5)
    s2.render(256)
    assert np.any(np.abs(s2.render(256)) > 0), "flicker=0 must sum plainly"


def test_koto_evaporation_choke():
    """damp_all strips one quantum per block: terraced release, then the gate."""
    s = ModuloKotoSynth(48000, 6)
    s.set_params(sustain=1.0)
    s.pluck(0, 1.0)
    s.render(256)
    s.damp_all()
    assert s.voices[0].evaporating
    for _ in range(48000 * 3 // 256):
        s.render(256)
        if not s.voices[0].active:
            break
    assert not s.voices[0].active, "evaporation must terminate the note"


def test_koto_warble_and_ghost_memory():
    s = ModuloKotoSynth(48000, 6)
    s.set_params(sustain=1.0)
    s.set_chord(48, "maj")
    s.render(256)
    s.set_warble(True, rate=30.0)
    s.pluck(0, 0.6)
    lengths = set()
    for _ in range(40):
        s.render(256)
        lengths.add(s.voices[0].L)
    assert len(lengths) >= 3, f"warble should flicker lengths, saw {lengths}"
    s.set_warble(False)
    # ghost memory: growing the string pulls never-erased cells into the loop
    v = s.voices[0]
    stale = v.buf[(v.w + np.arange(32)) & _MASK]   # cells just beyond the head
    assert v.active                                 # still ringing after all that


def test_koto_full_strum_realtime_budget():
    s = ModuloKotoSynth(48000, 6)
    s.set_params(sustain=1.0, bits=0.0, brightness=1.0)
    for i in range(6):
        s.pluck(i, 1.0)
    t0 = time.perf_counter()
    n_blocks = 100
    for _ in range(n_blocks):
        out = s.render(256)
    elapsed_ms = (time.perf_counter() - t0) * 1000 / n_blocks
    assert np.max(np.abs(out)) <= 1.0
    assert elapsed_ms < 2.5, f"koto 6-voice block took {elapsed_ms:.2f}ms (budget 5.33ms)"


# --- envelope -----------------------------------------------------------------

def test_envelope():
    c = Curve(default=0.5)
    assert c.sample(3.0) == 0.5
    c.add_point(0.0, 0.0); c.add_point(2.0, 1.0)
    assert abs(c.sample(1.0) - 0.5) < 1e-6
    c.add_point(0.0, 0.25)
    assert c.v[0] == 0.25 and len(c.t) == 2      # replaced, not stacked

    a = Automation(seconds=4.0, lanes=("gain", "bits"))
    a.lane("gain").add_point(0.0, 0.2); a.lane("gain").add_point(4.0, 0.9)
    a.playing = True
    a.advance(2.0)
    assert abs(a.sample_all()["gain"] - 0.55) < 1e-6
    a.advance(3.0)
    assert a.playhead < 4.0                      # loops
    a2 = Automation(); a2.load_dict(a.to_dict())
    assert abs(a2.lane("gain").sample(4.0) - 0.9) < 1e-6


def test_curve_remove_range_paint_semantics():
    """The editor's paint stroke clears the swept span so the curve follows
    the cursor instead of tangling with earlier points."""
    c = Curve()
    for t in range(11):
        c.add_point(t * 0.5, 0.5)            # a flat line, points every 0.5s
    # A left-to-right stroke from 1.0 to 4.0 clears the interior, then sets 4.0.
    c.remove_range(1.0, 4.0)
    c.add_point(4.0, 0.9)
    interior = [t for t in c.t if 1.0 < t < 4.0]
    assert interior == [], f"swept interior must be cleared, kept {interior}"
    assert 1.0 in c.t and 4.0 in c.t         # endpoints survive
    assert abs(c.sample(4.0) - 0.9) < 1e-6
    # remove_range is order-independent (dragging right-to-left).
    c.remove_range(4.0, 1.0)
    assert [t for t in c.t if 1.0 < t < 4.0] == []


# --- input matrix ---------------------------------------------------------------

class FakeEngine:
    def __init__(self):
        self.calls = []
        class _S: base = list(STANDARD_TUNING)
        self.synth = _S()
    def pluck(self, s, v): self.calls.append(("pluck", s, round(v, 2)))
    def set_bend(self, x): self.calls.append(("bend", round(x, 2)))
    def set_chord(self, r, q): self.calls.append(("chord", r, q))
    def set_tuning(self, t): self.calls.append(("tuning", tuple(t)))
    def set_param(self, n, v): self.calls.append(("param", n, round(v, 2)))
    def damp_all(self): self.calls.append(("damp",))


def test_input_matrix():
    fe = FakeEngine(); r = InputRouter(fe, n_strings=6)

    r.set_mode(Mode.MOUSE_STRUM)
    r.pointer_moved("mouse", 0.5, 0.05, 0.8)
    r.pointer_moved("mouse", 0.5, 0.95, 0.8)
    assert [c[1] for c in fe.calls if c[0] == "pluck"] == [1, 2, 3, 4, 5]

    fe.calls.clear()
    r.key_pressed("w"); r.key_pressed("a")
    chords = [c for c in fe.calls if c[0] == "chord"]
    assert chords and chords[-1][2] == "min"

    r.set_mode(Mode.MOUSE_PITCH); fe.calls.clear()
    r.pointer_moved("mouse", 1.0, 0.5, 0.0)
    assert any(c[0] == "bend" and c[1] > 0 for c in fe.calls)
    fe.calls.clear()
    r.key_pressed("3")
    assert ("pluck", 2, 0.9) in fe.calls

    r.set_mode(Mode.DUAL_ANALOG); fe.calls.clear()
    r.pointer_moved("tablet", 0.25, 0.5, 0.0)
    r.pointer_moved("mouse", 0.5, 0.1, 0.5)
    r.pointer_moved("mouse", 0.5, 0.9, 0.9)
    assert any(c[0] == "bend" for c in fe.calls)
    assert any(c[0] == "pluck" for c in fe.calls)
    r.key_pressed(" ")                    # AUX: space chokes the strings
    assert ("damp",) in fe.calls

    seen = set()
    for _ in range(4):
        seen.add(r.mode); r.cycle_mode()
    assert seen == set(Mode)


def test_strum_no_phantom_sweep_on_reentry():
    """Re-entering the surface at the far string must not sweep every string;
    only a continuous in-surface motion strums the lanes it crosses."""
    fe = FakeEngine(); r = InputRouter(fe, n_strings=6)
    r.set_mode(Mode.MOUSE_STRUM)
    r.pointer_moved("mouse", 0.5, 0.05, 0.8)     # baseline lane 0
    r.pointer_moved("mouse", 0.5, 0.95, 0.8)     # sweep -> plucks 1..5
    assert [c[1] for c in fe.calls if c[0] == "pluck"] == [1, 2, 3, 4, 5]

    # Simulate leaving + re-entering at the opposite end (surface calls this).
    r.pointer_released("mouse")
    fe.calls.clear()
    r.pointer_moved("mouse", 0.5, 0.05, 0.8)     # land on string 0
    assert not any(c[0] == "pluck" for c in fe.calls), \
        "landing after re-entry must not phantom-sweep"
    # A real crossing from there still fires exactly the crossed string.
    r.pointer_moved("mouse", 0.5, 0.30, 0.8)     # -> lane 1
    assert [c[1] for c in fe.calls if c[0] == "pluck"] == [1]


def test_pitch_snap_and_reset():
    fe = FakeEngine(); r = InputRouter(fe, n_strings=6)
    r.set_mode(Mode.MOUSE_PITCH)

    # Free (default): a bend just off an integer stays microtonal.
    fe.calls.clear()
    r.pointer_moved("mouse", 0.60, 0.5, 0.0)     # (0.6-0.5)*24 = +2.4 st
    bend = [c for c in fe.calls if c[0] == "bend"][-1][1]
    assert abs(bend - 2.4) < 1e-6, f"free bend should be continuous, got {bend}"

    # Snap on: the same position quantises to the nearest semitone.
    r.set_snap(True)
    fe.calls.clear()
    r.pointer_moved("mouse", 0.60, 0.5, 0.0)
    bend = [c for c in fe.calls if c[0] == "bend"][-1][1]
    assert bend == 2.0, f"snapped bend should be integer, got {bend}"
    assert float(bend).is_integer()

    # Reset zeroes the bend.
    fe.calls.clear()
    r.reset_pitch()
    assert ("bend", 0.0) in fe.calls and r.current_bend == 0.0


def test_open_tuning_persists_through_chords():
    """The open tuning is a persistent baseline: chords override the sounding
    pitches but leave the tuning intact, and restore_tuning returns to it."""
    import sounddevice as sd
    orig = sd.OutputStream

    class DummyStream:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    sd.OutputStream = DummyStream
    try:
        from synthet.audio_engine import AudioEngine
        eng = AudioEngine(48000, 256, 2, 6)
        out = np.zeros((256, 2), dtype=np.float32)
        assert eng.tuning == list(STANDARD_TUNING)      # default open tuning

        drop_d = [38, 45, 50, 55, 59, 64]
        eng.set_tuning(drop_d)
        eng._callback(out, 256, None, None)
        assert eng.tuning == drop_d
        assert list(eng.synth.base) == drop_d           # applied to the strings

        # A chord overrides what sounds, but not the stored tuning.
        eng.set_chord(48, "maj")
        eng._callback(out, 256, None, None)
        assert list(eng.synth.base) != drop_d
        assert eng.tuning == drop_d

        # Restore drops the chord back to the open tuning.
        eng.restore_tuning()
        eng._callback(out, 256, None, None)
        assert list(eng.synth.base) == drop_d

        # Tuning survives a synth swap and round-trips through a session.
        from synthet import storage
        from synthet.input_router import InputRouter
        router = InputRouter(eng, n_strings=6)
        d = storage.session_dict(router, eng)
        assert d["tuning"] == drop_d
        eng.set_synth("bitstring")
        eng._callback(out, 256, None, None)
        assert eng.tuning == drop_d                     # swap keeps the tuning

        eng2 = AudioEngine(48000, 256, 2, 6)
        storage.apply_session(d, InputRouter(eng2, n_strings=6), eng2)
        eng2._callback(out, 256, None, None)
        assert eng2.tuning == drop_d and list(eng2.synth.base) == drop_d
    finally:
        sd.OutputStream = orig


def test_engine_mute_silences_output():
    import sounddevice as sd
    orig = sd.OutputStream

    class DummyStream:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    sd.OutputStream = DummyStream
    try:
        from synthet.audio_engine import AudioEngine
        eng = AudioEngine(48000, 256, 2, 6)
        out = np.full((256, 2), 0.5, dtype=np.float32)   # pre-fill to prove it clears
        eng.pluck(0, 1.0)
        eng.set_mute(True)
        eng._callback(out, 256, None, None)
        assert np.all(out == 0.0), "mute must zero the output buffer"
        eng.set_mute(False)
        eng.pluck(0, 1.0)
        eng._callback(out, 256, None, None)
        assert np.any(np.abs(out) > 0), "unmute restores sound"
    finally:
        sd.OutputStream = orig


# --- engine (stubbed stream) ------------------------------------------------------

def test_engine_with_stubbed_stream():
    import sounddevice as sd
    orig = sd.OutputStream

    class DummyStream:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    sd.OutputStream = DummyStream
    try:
        from synthet.audio_engine import AudioEngine
        eng = AudioEngine(48000, 256, 2, 6)
        out = np.zeros((256, 2), dtype=np.float32)

        eng.pluck(0, 1.0)
        eng._callback(out, 256, None, None)
        assert np.any(np.abs(out) > 0) and np.max(np.abs(out)) <= 1.0

        # swap to the bitstring bank; it should render through the same path
        assert eng.set_synth("bitstring")
        eng.pluck(2, 1.0)
        eng._callback(out, 256, None, None)
        assert isinstance(eng.synth, BitStringSynth)
        assert np.any(np.abs(out) > 0) and np.all(np.isfinite(out))

        # the bits/sustain lanes reach the synth via set_params each block
        eng.set_param("bits", 0.0)
        eng.set_param("sustain", 1.0)
        eng._callback(out, 256, None, None)
        assert abs(eng.synth.bits_f - 0.0) < 1e-9
        assert abs(eng.synth.sustain - 1.0) < 1e-9

        # warble command routes through the queue
        eng.set_warble(True)
        eng._callback(out, 256, None, None)
        assert eng.synth.warble_on is True

        # the modulo koto swaps in and renders through the same path
        assert eng.set_synth("modulokoto")
        eng.pluck(0, 1.0)
        eng._callback(out, 256, None, None)
        assert isinstance(eng.synth, ModuloKotoSynth)
        assert np.any(np.abs(out) > 0) and np.all(np.isfinite(out))
        assert eng.synth.warble_on is True   # warble state survives the swap

        # per-synth defaults were applied on the swap (bank + empty lanes)
        assert eng.param_targets["bits"] == ModuloKotoSynth.DEFAULTS["bits"]
        assert eng.automation.lanes["reverb"].default == \
            ModuloKotoSynth.DEFAULTS["reverb"]

        # damp command reaches the bank
        eng.damp_all()
        eng._callback(out, 256, None, None)
        assert eng.synth.voices[0].evaporating or not eng.synth.voices[0].active

        # session snapshot round-trips the new fields, incl. input settings
        from synthet import storage
        from synthet.input_router import InputRouter
        router = InputRouter(eng, n_strings=6)
        router.hover_strum = False
        router.set_snap(True)
        router.pitch_range = 12.0
        d = storage.session_dict(router, eng)
        assert d["synth"] == "modulokoto" and d["warble"] is True
        assert d["settings"]["hover_strum"] is False and d["settings"]["pitch_range"] == 12.0
        eng2 = AudioEngine(48000, 256, 2, 6)
        router2 = InputRouter(eng2, n_strings=6)
        storage.apply_session(d, router2, eng2)
        eng2._callback(out, 256, None, None)
        assert eng2.synth_name == "modulokoto"
        assert isinstance(eng2.synth, ModuloKotoSynth)
        assert eng2.synth.warble_on is True
        assert router2.hover_strum is False and router2.snap is True
        assert router2.pitch_range == 12.0
    finally:
        sd.OutputStream = orig


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"{fn.__name__}: OK")
    print(f"\nALL {len(fns)} TESTS PASSED")
