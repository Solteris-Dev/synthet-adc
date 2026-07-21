# examples/render_demo.py
"""Offline demo render — no audio device or GUI needed.

    python examples/render_demo.py [out.wav]

Plays the same phrase on all three voice banks, then tours the Modulo Koto's
impossible laws: the wrap-snarl attack, warble, a zipper dive, the bits-lane
wrap-fold sweep, and the evaporation choke.
"""
import sys

import numpy as np
import soundfile as sf

from synthet.synth import StringSynth
from synthet.bitstring import BitStringSynth
from synthet.modulokoto import ModuloKotoSynth

SR = 48000
BLOCK = 256


def run(synth, seconds, events=(), params=None):
    """Render `seconds`, firing (time, fn) events; yields the mono result."""
    total = int(seconds * SR)
    out = np.empty(total, dtype=np.float32)
    ev = sorted(events, key=lambda e: e[0])
    i = pos = 0
    while pos < total:
        t = pos / SR
        while i < len(ev) and ev[i][0] <= t:
            ev[i][1](synth)
            i += 1
        if params:
            synth.set_params(**params(t))
        n = min(BLOCK, total - pos)
        out[pos:pos + n] = synth.render(n)
        pos += n
    return out


def phrase(bank_cls, **kw):
    """The comparison phrase: strum a chord, pick, then re-fret."""
    s = bank_cls(SR, 6)
    s.set_params(**kw)
    ev = [(0.0, lambda s: s.set_chord(45, "min"))]
    for i in range(6):                       # a slowish strum
        ev.append((0.05 + 0.04 * i, lambda s, i=i: s.pluck(i, 0.8)))
    ev += [(1.2, lambda s: s.pluck(3, 0.6)),
           (1.5, lambda s: s.pluck(4, 0.6)),
           (1.8, lambda s: s.set_chord(48, "maj")),
           (1.85, lambda s: [s.pluck(i, 0.7) for i in range(6)])]
    return run(s, 3.2, ev)


def koto_tour():
    parts = []

    # 1. wrap-snarl: same note, soft then hard (past the overflow ceiling)
    s = ModuloKotoSynth(SR, 6)
    s.set_params(sustain=0.6, bits=0.85, brightness=0.6)
    parts.append(run(s, 3.0, [(0.0, lambda s: s.set_tuning([52] * 6)),
                              (0.1, lambda s: s.pluck(0, 0.45)),
                              (1.5, lambda s: s.pluck(0, 1.0))]))

    # 2. warble: a held chord flickering through its own quality
    s = ModuloKotoSynth(SR, 6)
    s.set_params(sustain=1.0, bits=0.85, brightness=0.7)
    ev = [(0.0, lambda s: s.set_chord(52, "min")),
          (0.05, lambda s: [s.pluck(i, 0.7) for i in range(4)]),
          (0.3, lambda s: s.set_warble(True, rate=14.0))]
    parts.append(run(s, 3.0, ev))

    # 3. zipper dive: a slow bend stepping down the integer pitch grid
    s = ModuloKotoSynth(SR, 6)
    s.set_params(sustain=1.0, bits=0.85, brightness=0.5)
    ev = [(0.0, lambda s: s.set_tuning([64] * 6)),
          (0.1, lambda s: s.pluck(0, 0.6)),
          (0.6, lambda s: s.set_bend(-12.0, glide=1.8))]
    parts.append(run(s, 3.0, ev))

    # 4. the bits lane dives: live wrap-folding of a ringing chord
    s = ModuloKotoSynth(SR, 6)
    s.set_params(sustain=1.0, brightness=0.7)
    ev = [(0.0, lambda s: s.set_chord(57, "maj")),
          (0.05, lambda s: [s.pluck(i, 0.7) for i in range(3)])]
    parts.append(run(s, 3.0, ev,
                     params=lambda t: {"bits": max(0.0, 0.9 - 0.35 * t)}))

    # 5. evaporation choke: terraced staircase release, then the hard gate
    s = ModuloKotoSynth(SR, 6)
    s.set_params(sustain=1.0, bits=0.7, brightness=0.8)
    ev = [(0.0, lambda s: s.set_chord(50, "min")),
          (0.05, lambda s: [s.pluck(i, 0.9) for i in range(6)]),
          (1.2, lambda s: s.damp_all())]
    parts.append(run(s, 2.6, ev))

    return parts


def main(path="synthet_demo.wav"):
    gap = np.zeros(int(0.4 * SR), dtype=np.float32)
    segs = []
    for name, cls, kw in [("strings", StringSynth, dict(sustain=0.6)),
                          ("bitstring", BitStringSynth,
                           dict(sustain=0.6, bits=0.6, brightness=0.7)),
                          ("modulokoto", ModuloKotoSynth,
                           dict(sustain=0.6, bits=0.85, brightness=0.7))]:
        print(f"rendering phrase on {name} ...")
        segs += [phrase(cls, **kw), gap]
    print("rendering the koto tour ...")
    for part in koto_tour():
        segs += [part, gap]
    audio = np.concatenate(segs)
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio *= 0.9 / peak
    sf.write(path, audio, SR)
    print(f"wrote {path} ({audio.size / SR:.1f}s)")


if __name__ == "__main__":
    main(*sys.argv[1:2])
