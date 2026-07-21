# Contributing

Thanks for looking. synthet-adc is a small, **opinionated** project — a computer-native
instrument, not a neutral synth — so contributions are welcome but land best when they
fit that grain. When in doubt, open an issue before a big change.

## Design stance

The instrument is meant to have a point of view and gently steer the player, and the
synth banks are *physical models of things that cannot exist*, tuned so the impossible
law is the **source** of the sound. "More options" is not automatically better here;
"sharper character, still musical" usually is. Keep that in mind for feature work — see
the design notes in the [README](README.md).

## Development setup

Python **3.9+** (see the README for the per-platform system libraries).

```sh
git clone https://github.com/Solteris-Dev/synthet-adc
cd synthet-adc
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[fx,midi]"
```

## Running the tests

The suite is head-less — no audio device or display needed:

```sh
python tests/test_core.py                              # DSP, envelopes, input matrix, engine
QT_QPA_PLATFORM=offscreen python tests/test_gui.py     # Qt wiring, offscreen
python examples/render_demo.py                         # offline audio render, sanity check
```

CI runs both test files across Ubuntu (3.9–3.13), macOS, and Windows on every push and
pull request; the **`CI OK`** check must be green before a PR merges. Run them locally
first.

## Code conventions

- **Match the surrounding code** — comment density, naming, and the module-level
  docstrings that explain *why*, not just *what*. The DSP files earn their length in
  explanation; keep that.
- **Real-time safety.** Anything on the audio path (`*.render`, the engine callback)
  must be pure NumPy with no per-sample Python loops and no per-block allocation
  surprises. Block-vectorise. If you touch the impossible-synth loops, keep the
  **bit-exact equivalence tests** passing — they compare the vectorised loop against a
  naive per-sample reference, and that's the contract.
- **3.9-compatible.** Use `from __future__ import annotations` rather than newer typing
  syntax that would break on 3.9.
- Keep a change focused — a PR that does one thing is easier to reason about (and to
  hear).

## Adding a synth bank

A voice bank is any class with this drop-in interface (see `synth.py`, `bitstring.py`,
`modulokoto.py` for three worked examples):

```python
__init__(samplerate=48000, n=6, tuning=None)
pluck(string, velocity, brightness=None)
set_chord(root_midi, quality, glide=...)
set_tuning(midi_notes, glide=...)
set_bend(semitones, glide=...)
render(frames) -> np.ndarray        # mono float32 in [-1, 1]
# optional: set_params(brightness, bits, sustain), set_warble(on), damp_all(),
#           lane_readout(name, value), and a DEFAULTS = {lane: 0..1} table
```

Register it in `AudioEngine.SYNTHS` and add it to the Synth combo in `surfaces.py`. If
it has an impossible-physics story, tell it in the module docstring the way the others
do — that story is half the point.

## Pull requests

Branch or fork, keep commits tidy, and describe **what changed and how you tested it**
(for anything about sound, a short WAV helps). The PR template covers the rest. Green CI
plus a clear description is most of the battle.
