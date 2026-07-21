<!-- Thanks for the PR. Keep it focused; delete any section that doesn't apply. -->

## What this changes

<!-- One or two sentences. Link the issue if there is one (e.g. Closes #12). -->

## Why

<!-- The problem or the itch. For a synth/behaviour change: what does it sound or feel like? -->

## How it was tested

- [ ] `python tests/test_core.py` passes
- [ ] `QT_QPA_PLATFORM=offscreen python tests/test_gui.py` passes
- [ ] Drove the actual change in the app (or said why not)
- [ ] Audio-path code stays real-time-safe — pure NumPy, no per-sample loops; impossible-synth loops keep their bit-exact tests green

## Notes

<!-- Tradeoffs, follow-ups, a screenshot, or a short audio clip if it's about sound. -->
