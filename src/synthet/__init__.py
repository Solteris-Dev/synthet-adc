# synthet — a computer-native instrument.
#
# Keep this import-light: pulling in Qt or sounddevice here would make
# `import synthet` heavy and audio-device-dependent. The synth voice banks are
# pure NumPy, so they are safe (and useful) to expose directly.
__version__ = "0.1.0"

from .synth import StringSynth, midi_to_hz, chord_voicing  # noqa: F401
from .bitstring import BitStringSynth  # noqa: F401
from .modulokoto import ModuloKotoSynth  # noqa: F401
