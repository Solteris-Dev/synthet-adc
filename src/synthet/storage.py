# storage.py
"""JSON session save / load — now actually wired into the app.

A session captures the current input mode, the string tuning, and every drawn
automation lane so a performance patch can be recalled.
"""
import json


def save_session(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_session(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def session_dict(router, engine):
    """Snapshot the live app state into a JSON-serialisable dict."""
    return {
        "version": 2,
        "mode": router.mode.name,
        "quality": router.quality,
        "synth": engine.synth_name,
        "warble": engine.warble_on,
        "tuning": list(engine.tuning),           # the open tuning, not a held chord
        "params": dict(engine.param_targets),
        "automation": engine.automation.to_dict(),
        "settings": {
            "hover_strum": router.hover_strum,
            "snap": router.snap,
            "pitch_range": router.pitch_range,
        },
    }


def apply_session(data, router, engine):
    """Restore a session dict onto the live router + engine."""
    if "mode" in data:
        router.set_mode(data["mode"])
    if "quality" in data:
        router.quality = data["quality"]
    if data.get("synth") in engine.SYNTHS:
        engine.set_synth(data["synth"])
    if "warble" in data:
        engine.set_warble(bool(data["warble"]))
    if data.get("tuning"):
        engine.set_tuning(data["tuning"])
    for name, val in data.get("params", {}).items():
        engine.set_param(name, val)
    if "automation" in data:
        engine.automation.load_dict(data["automation"])
    s = data.get("settings", {})
    if "hover_strum" in s:
        router.hover_strum = bool(s["hover_strum"])
    if "snap" in s:
        router.set_snap(bool(s["snap"]))
    if "pitch_range" in s:
        router.pitch_range = float(s["pitch_range"])
