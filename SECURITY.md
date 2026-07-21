# Trust model

synthet-adc is a **local, offline desktop instrument**. This document states plainly
what running it does and does not expose, so you know what you're getting.

## The short version

**Today** there is no network surface — no server, no sockets, no telemetry, no
auto-update, no accounts. The app makes sound and reads/writes files you point it at.
The only trust boundaries worth naming right now are (1) audio plugins you choose to
load run as native code, and (2) session files are data you should treat as data. A few
capabilities are on the roadmap — see [Planned capabilities](#planned-capabilities) for
the boundaries they'll respect.

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

## What there isn't (today)

No network I/O, no background services, no analytics, no credentials, and no persistence
beyond files you explicitly save. When the capabilities below land they'll stay inside
the boundaries stated there. "Uninstalling" is deleting the folder / the venv.

## Planned capabilities

None of these are shipped yet. This section states up front what they will and won't do,
so the trust model stays honest as the app grows.

- **Optional analytics (opt-out).** Any usage analytics will be optional and **opt-out**
  — turning it off is one setting, and the app stays fully functional without it. It
  would cover product usage (what's used, what breaks), never your audio, your sessions,
  or the contents of your files.
- **Local, self-contained persistence.** Saved state — settings, recent sessions, small
  caches — stays **alongside the files the app already uses** (its own folder, or paths
  you choose), not scattered across your system, and never anything hidden or hostile.
  Plain files you can read and delete.
- **Opt-in interaction with other apps.** The app may talk to other running software over
  protocols like **MIDI** — to sync with, or drive, another instrument — but only when
  **you configure and allow it**. Nothing reaches out to anything else by default.

## Reporting

If you find a way to make synthet-adc **execute code from a session file** (the JSON
path becoming code execution), or any other memory-safety / code-execution issue in the
app itself, that's a real bug — open an issue or a PR with a runnable reproduction.
Native code running because you loaded a plugin you chose is by design, not a
vulnerability.
