# Trust model

synthet-adc is a **local, offline desktop instrument**. This document states plainly
what running it does and does not expose, so you know what you're getting.

## The short version

There is **no network surface** — no server, no sockets, no telemetry, no auto-update,
no accounts. The app makes sound and reads/writes files you point it at. The only trust
boundaries worth naming are (1) audio plugins you choose to load run as native code, and
(2) session files are data you should treat as data.

## Trust boundaries

- **VST3 / AU plugins (optional `fx` extra).** `AudioEngine.set_vst3(path)` calls
  `pedalboard.load_plugin`, which loads and runs a **native binary in-process** — that
  plugin is arbitrary code with the app's privileges, exactly as in any DAW. Load only
  plugins you trust, from sources you trust. Without the `fx` extra installed there is
  no plugin-loading path at all.
- **Session files (`*.json`).** Save/Load reads JSON and applies it to synth parameters
  and drawn envelopes. It is parsed with the standard-library `json` (no `eval`, no
  `pickle`, no code execution), and every value is clamped/validated where it is used —
  so a malformed or hostile session **cannot run code**; the worst case is nonsense
  state or a load that does nothing. Still, only open sessions you obtained honestly.
- **MIDI input (optional `midi` extra).** Incoming messages only select the input mode
  or trigger notes. They are treated as data, never as shell/host commands.
- **Rendered audio (`take.wav`, demo renders).** Ordinary file writes to the path you
  choose or the current directory. Nothing is uploaded anywhere.

## What there isn't

No network I/O, no background services, no analytics, no credentials, no persistence
beyond files you explicitly save. "Uninstalling" is deleting the folder / the venv.

## Reporting

If you find a way to make synthet-adc **execute code from a session file** (the JSON
path becoming code execution), or any other memory-safety / code-execution issue in the
app itself, that's a real bug — open an issue or a PR with a runnable reproduction.
Native code running because you loaded a plugin you chose is by design, not a
vulnerability.
