# synthet-adc

A **computer-native instrument** — played like a real instrument, but the
strings live in software and your mouse, tablet, and keyboard are the hands. Super WIP right now.  
All feedback/testing is welcome! 

Three pillars:

1. **A switchable articulation matrix** — continuous pointer motion and
   discrete keys trade the roles of *strumming* and *pitch* depending on a mode
   you can flip live (from the UI or from a MIDI device).
   
   **Input is handled in a specifically opinionated way that's primarily meant for having fun with no special hardware.**
   
3. **Playhead envelopes** — draw multi-lane automation that loops over
   the performance and drives the sound in real time.
   
   **There's settings for the synths in the software, and they can be drawn on screen/played to modulate the instrument's properties over time.**
   
5. **Impossible instruments** — the voice banks are *physical models of objects
   that cannot exist*, designed so that every impossible law produces a sound
   native to digital machines. The flagship is the **Modulo Koto**.
   
   **They make fun sounds that aren't super realistic.**
   

## Provenance and guarantees

synthet-adc's code is **substantially AI-generated** — mostly-to-entirely, by design and
out in the open; we'd rather say so than pretend otherwise. The author mostly worked with Claude models/GPT-5.6/Gemini/Cursor models.
We stand behind it and test
what matters (28 headless tests, bit-exact DSP equivalence checks, a human in the loop),
and we treat AI-written code as first-class — but it is provided **as-is, with no
guarantees** (see [`LICENSE`](LICENSE) and [`SECURITY.md`](SECURITY.md)). Run it at your
own risk, and if something's off, [open an issue](../../issues/new/choose). 

**This isn't a particularly hardened app, which is due to it being local/LLM generated code.**

## Install & run

Works on **macOS, Windows, and Linux**. You need **Python 3.9+** (3.10+
recommended) and `pip`. Everything installs from PyPI wheels — no compiler
needed for the core app.

### 1. Get the code and make a virtual environment

```bash
cd synthet-adc
python -m venv .venv
```

Activate it (this is the only step that differs per platform):

| Platform | Activate |
| --- | --- |
| macOS / Linux (bash/zsh) | `source .venv/bin/activate` |
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |
| Windows (cmd.exe) | `.venv\Scripts\activate.bat` |

> On Windows PowerShell, if activation is blocked, run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2. Install and launch — pick one path

**A. Install as a package (recommended)** — also gives you a `synthet-adc` command:

```bash
pip install -e .            # add extras: pip install -e ".[fx,midi]"
synthet-adc                 # or:  python -m synthet
```

**B. No install, run from source** — just the dependencies:

```bash
pip install -r requirements.txt
python run.py
```

Both paths are equivalent; use whichever you prefer. The optional extras
(`fx` = pedalboard reverb/VST3, `midi` = MIDI mode switching) are not required —
the app runs fine without them (dry synth, UI-only mode switching).

### Platform notes

- **macOS** — works out of the box (CoreAudio). Nothing extra to install.
- **Windows** — works out of the box (WASAPI/MME). Nothing extra to install.
- **Linux** — the `sounddevice`/`soundfile` wheels need two system libraries,
  and Qt needs a few X/Wayland libs for the window. On Debian/Ubuntu:
  ```bash
  sudo apt install libportaudio2 libsndfile1 libxcb-cursor0 libxkbcommon0 libegl1
  ```
  (Fedora: `sudo dnf install portaudio libsndfile libxcb libxkbcommon`.)
  For the optional `midi` extra, building `python-rtmidi` from source also needs
  `libasound2-dev` (or a prebuilt wheel, when available).

### Try it without a soundcard or screen

```bash
python examples/render_demo.py        # renders a ~30 s audio tour to a WAV
python tests/test_core.py             # 28 headless self-tests (no pytest needed)
```

`render_demo.py` needs no audio device or GUI — handy for a headless box or a
quick check that the install works. (The tests also run under `pytest` if you
have it: `pip install pytest && python -m pytest tests/`.)

### Troubleshooting

- **`PortAudioError: Error opening OutputStream`** — no output device is
  available (common on headless/SSH sessions or in a container). The GUI still
  opens; `render_demo.py` still works. Plug in / select an output device, or run
  on a machine with audio.
- **Linux: the window doesn't appear / `xcb` plugin error** — install the Qt
  libs listed above (`libxcb-cursor0` is the usual missing one on newer distros).
- **`No module named synthet` when using `python run.py`** — run it from the
  repo root (the folder containing `run.py`), or use path A (`pip install -e .`).

## The Modulo Koto

> *A six-string koto strung with two's-complement integers: friction is
> truncation, the ceiling is wraparound, and silence is a hard gate one
> quantum wide.*

Physical modelling usually simulates a real object. The Modulo Koto keeps the
machinery — a travelling wave on a string, reflecting through a bridge — but
the string's physics are integer arithmetic, and each impossible law is the
direct cause of a chip-native sound:

| Impossible law | What you hear |
| --- | --- |
| Displacement is quantised to 2^B rungs (B = 3–8, the **bits** lane, live) | staircase waves: pulse-flavoured spectra; buzz that grows *relative* to the note as it decays — tails get grittier, not smoother |
| Displacement space is a ring: past +max **wraps** to −max (integer overflow as law) | hard plucks (velocity ≳ 0.66) snarl with wrap-folded polarity flips, then self-clean; diving the bits lane wrap-folds ringing notes live |
| The only friction is **truncation-toward-zero** at the bridge — a narrow gate touching 1 cell in K per pass (K = the sustain lane, a 2-bit register: 2/4/8/closed) | terraced, digit-dependent decay: two identical notes die slightly differently; longer strings ring longer; at sustain = 1 the gate closes and rounding **freezes the wave into an immortal drone** |
| **The existence threshold**: motion under one quantum does not exist | notes never fade out — they gate off hard, like a chip channel |
| Space is gridded and the world clock is slow: pitch = sr/(H·(L+0.5)), integer L; the pickup holds values between ticks (ZOH) | the exact NES period-register pitch grid (machine temperament up high); bends **zipper** in integer steps; alias shimmer on high notes |
| One **multiplexed pickup** wire for six strings (~93 Hz slot scan above 4 ringing strings) | dense chords flicker into hardware arpeggios; single lines stay solid |
| **The lattice remembers**: cells beyond the current length are never erased | re-fretting longer pulls ghosts of old notes back into the loop |
| **Quantum evaporation** (the choke gesture): strip one quantum per block | a terraced NES-style release staircase ending in the gate |

Also aboard: **warble** (C64-style arpeggio flicker of the string lengths at
~16 Hz, following the current chord quality — toggle in the control bar) and an
LFSR **noise pick** (`synth.pick_material = "noise"` — the NES noise channel as
pick material).

The design came out of a judged panel of independent "impossible instrument"
proposals; the Koto (digital-artifact-physics lens) won, and the best ideas
from the losers were grafted in. The **bitstring** bank (the runner-up: a
quantised waveguide with error-feedback "rounding debts", smooth continuous
pitch, and resample-preserving re-fretting) ships alongside it — its glassy
theremin-like bends complement the Koto's zipper grid in `MOUSE_PITCH` mode —
plus **strings**, a clean plucked-string bank, as the control group. Each bank
carries **per-synth sane defaults** for the five envelope lanes, applied on
switch. Bitstring in particular caps its `bits` lane to a low **1–5 bit** range
(vs. 1–8) so the whole lane travel — and the editor's readout — lands in the
crunchy zone where it sounds best; a hard strum on low bits is a highlight.

## Ask 1 — the input matrix

Two articulation **roles**, mapped onto **devices** by the current `Mode`:

| Mode | Mouse / tablet (analog) | Keyboard (discrete) |
| ---- | ----------------------- | ------------------- |
| `MOUSE_STRUM` | **strum** — sweep the cursor across the string lanes (bare hover strums); speed = velocity, direction = up/down-stroke | **pitch** — `a s d f g h j k` chord roots; `q w e r t y` quality |
| `MOUSE_PITCH` | **pitch** — drag left↔right to bend (koto: zipper steps; bitstring: smooth glide); **right-click resets** to 0 | **strum** — `1–6` pluck, `space` strums all |
| `DUAL_ANALOG` | **tablet = pitch**, **mouse = strum** | **choke** — `space` damps all strings (koto: evaporation staircase) |

`MOUSE_PITCH` paints a **semitone grid** with a live marker so you can see where
a bend lands; the **Snap** toggle quantises to in-tune semitones (off =
continuous / microtonal).

The mode switch is itself an input: MIDI `note_on` selects a mode by note
number, CC 80 cycles them — plug in a footswitch and flip roles hands-free.

**Emergency kill switch:** the **Mute** button (or **`Esc`**) instantly silences
the output and chokes every ringing string — hammer it while trying settings so
a drone can't blast your ears.

**Settings** (the ⚙ button): live-applying input preferences — toggle
**strum-on-hover vs. click-drag**, set the **pitch bend range**. Preferences and
per-synth defaults are saved with the session.

**Tuning** (the *Tuning…* button): set each open string's pitch independently of
chord fretting, with common presets (Standard, Drop D, Open G/D, DADGAD,
half-step down). This is a persistent **open tuning** — chord input overrides the
sounding pitches but the tuning survives, and *Drop chord → tuning* restores it.
Because it's independent of the chord keys, **every mode plays a properly tuned
instrument** — including `MOUSE_PITCH` and `DUAL_ANALOG`, which otherwise have no
per-string pitch control (only a global bend).

## Ask 2 — playhead envelopes

The lower panel edits five automation lanes — `gain`, `brightness`, `reverb`,
`bits`, `sustain`. Left-drag **paints** a lane (the stroke overwrites the span
it sweeps, so the curve follows the cursor cleanly); right-click clears it.
**Play** loops the playhead over them; on the Koto, drawing the `bits` lane is
live wrap-folding and the `sustain` lane opens and closes the friction gate.
**Rec** captures the output to `take.wav`. Sessions (mode, synth, warble,
tuning, params, all lanes) save/load as JSON.

## Layout

| File | Role |
| ---- | ---- |
| `src/synthet/app.py` | Qt window; wiring; MIDI mode switch; save/load |
| `src/synthet/input_router.py` | **Ask 1** — the strum/pitch role matrix |
| `src/synthet/modulokoto.py` | **the Modulo Koto** — integer-arithmetic physics |
| `src/synthet/bitstring.py` | the Bitstring — quantised waveguide, rounding debts |
| `src/synthet/synth.py` | clean plucked strings + music-theory helpers |
| `src/synthet/audio_engine.py` | RT output stream, bank switching, optional FX, recording |
| `src/synthet/envelope.py` | **Ask 2** — multi-lane playhead automation |
| `src/synthet/surfaces.py` | Qt widgets: performance surface, envelope editor, control bar |
| `src/synthet/settings.py` / `tuning.py` | Settings dialog; the open-tuning editor |
| `src/synthet/midi_io.py` / `storage.py` | optional MIDI; JSON sessions |
| `run.py` · `requirements.txt` · `pyproject.toml` | launch from source · deps · package/install config |
| `tests/` | 28 headless tests, incl. **bit-exact** equivalence of both banks' chunk-vectorised loops against naive per-sample references |
| `examples/render_demo.py` | offline audio tour, no device needed |

## Engineering notes

* Both impossible banks run the classic block-vectorised waveguide trick
  (gather → vector math → scatter in chunks bounded by the loop length); the
  bound proofs live in the docstrings and the tests pin bit-exactness.
* Real bugs found and fixed en route, in case you're curious what the physics
  cost: a `round()`-quantiser freezes decay at ~1/(2(1−a)) rungs (fixed with
  per-slot error feedback — "each rung remembers what it owes"); pure
  truncation friction bleeds ~0.5 quantum/pass (far too dead — fixed with the
  narrow-gate law); and a fixed-phase gate resonates number-theoretically with
  the loop length, making some notes immortal (fixed by rotating the gate
  phase per block).
* Descends from the `python-instrument-thingie` prototype; its bugs (a local
  `pedalboard.py` shadowing the pip package, unwired MIDI/storage, a broken
  duplex path) are fixed here, and FX/MIDI are optional extras.
