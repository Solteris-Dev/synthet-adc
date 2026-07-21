# envelope.py
"""Playhead-driven automation (Ask 2).

`Curve` is a time->value piecewise-linear breakpoint envelope (values in 0..1),
directly descended from the reference project's `gestures.Curve` but hardened.
`Automation` bundles several named lanes that a looping playhead samples to drive
engine parameters (master gain, brightness, reverb, ...).
"""
import bisect


class Curve:
    """A time -> value curve with linear segments. Times in seconds, values 0..1."""

    def __init__(self, default=0.5):
        self.t = []
        self.v = []
        self.default = float(default)

    def clear(self):
        self.t.clear()
        self.v.clear()

    def add_point(self, t, v):
        t = max(0.0, float(t))
        v = max(0.0, min(1.0, float(v)))
        i = bisect.bisect_left(self.t, t)
        # Replace a point at (nearly) the same time instead of stacking them.
        if i < len(self.t) and abs(self.t[i] - t) < 1e-4:
            self.v[i] = v
        else:
            self.t.insert(i, t)
            self.v.insert(i, v)

    def remove_range(self, t0, t1):
        """Delete breakpoints strictly inside (t0, t1). Used by the editor to
        'paint' a stroke: the swept span is cleared before the new endpoint is
        added, so dragging follows the cursor instead of tangling old points."""
        if t1 < t0:
            t0, t1 = t1, t0
        kept = [(t, v) for t, v in zip(self.t, self.v) if not (t0 < t < t1)]
        self.t = [t for t, _ in kept]
        self.v = [v for _, v in kept]

    def sample(self, t):
        if not self.t:
            return self.default
        if t <= self.t[0]:
            return self.v[0]
        if t >= self.t[-1]:
            return self.v[-1]
        i = bisect.bisect_right(self.t, t) - 1
        t0, v0 = self.t[i], self.v[i]
        t1, v1 = self.t[i + 1], self.v[i + 1]
        k = (t - t0) / max(1e-6, (t1 - t0))
        return v0 + k * (v1 - v0)

    def to_dict(self):
        return {"points": list(zip(self.t, self.v)), "default": self.default}

    def load_dict(self, d):
        self.clear()
        self.default = float(d.get("default", 0.5))
        for t, v in d.get("points", []):
            self.add_point(t, v)


class Automation:
    """Named automation lanes sharing one looping playhead.

    The engine advances `playhead` in its audio callback and reads each lane
    with `sample()`; the GUI edits the same `Curve` objects. Editing a Python
    list while the audio thread reads it is benign here (append/replace of
    floats) and mirrors the reference's simple approach — good enough for a
    single-user instrument, and easy to snapshot later if needed.
    """

    def __init__(self, seconds=8.0, lanes=("gain", "brightness", "reverb")):
        self.seconds = float(seconds)
        self.lanes = {name: Curve(default=0.5) for name in lanes}
        self.playhead = 0.0
        self.playing = False

    def lane(self, name) -> Curve:
        return self.lanes.setdefault(name, Curve())

    def advance(self, dt):
        if not self.playing:
            return
        self.playhead += dt
        if self.playhead >= self.seconds:
            self.playhead -= self.seconds

    def sample_all(self):
        return {name: c.sample(self.playhead) for name, c in self.lanes.items()}

    def to_dict(self):
        return {
            "seconds": self.seconds,
            "lanes": {k: c.to_dict() for k, c in self.lanes.items()},
        }

    def load_dict(self, d):
        self.seconds = float(d.get("seconds", self.seconds))
        for name, cd in d.get("lanes", {}).items():
            self.lane(name).load_dict(cd)
