"""Chord symbols: iReal Pro syntax -> plain text symbols, transposition, music21 objects.

Plain text symbols used throughout the project look like "Eb7", "Fm7", "Bbmaj7",
"Cm7b5", "Ddim7", "G7b9", "C7/E". They are stored in concert pitch.
"""
from __future__ import annotations

import re

from music21 import harmony, interval, pitch

# One chord inside an iReal measure string, e.g. "F-7Bb7" -> ["F-7", "Bb7"].
_IREAL_CHORD = re.compile(r"([A-GW][b#]?[^A-GW/,\s]*(?:/[A-G][b#]?)?)")

# iReal quality spellings -> plain spellings (order matters: longest first).
_IREAL_QUALITY = [
    ("-^7", "mmaj7"),
    ("-^", "mmaj7"),
    ("^9", "maj9"),
    ("^13", "maj13"),
    ("^7", "maj7"),
    ("^", "maj7"),
    ("h7", "m7b5"),
    ("h9", "m9b5"),
    ("h", "m7b5"),
    ("o7", "dim7"),
    ("o", "dim"),
    ("-", "m"),
]


def split_ireal_measure(measure: str) -> list[str]:
    """Split one iReal measure string into iReal chord tokens."""
    if measure.strip() == "n":
        return ["N.C."]
    return [c for c in _IREAL_CHORD.findall(measure) if not c.startswith("W")]


def ireal_to_plain(token: str) -> str:
    """Convert one iReal chord token ("F-7", "Bb^7", "Eh7", "C7/E") to plain text."""
    if token == "N.C.":
        return token
    m = re.match(r"^([A-G][b#]?)(.*?)(/[A-G][b#]?)?$", token)
    if not m:
        return token
    root, quality, bass = m.group(1), m.group(2) or "", m.group(3) or ""
    for src, dst in _IREAL_QUALITY:
        if quality.startswith(src):
            quality = dst + quality[len(src):]
            break
    quality = quality.replace("(", "").replace(")", "")
    return f"{root}{quality}{bass}"


def _transpose_note_name(name: str, semitones_interval: str) -> str:
    p = pitch.Pitch(name.replace("b", "-"))
    q = p.transpose(semitones_interval)
    return q.name.replace("-", "b")


def transpose_symbol(symbol: str, intv: str = "M2") -> str:
    """Transpose a plain chord symbol by a music21 interval name (default: major 2nd up)."""
    if symbol == "N.C.":
        return symbol
    m = re.match(r"^([A-G][b#]?)(.*?)(?:/([A-G][b#]?))?$", symbol)
    if not m:
        return symbol
    root = _transpose_note_name(m.group(1), intv)
    bass = "/" + _transpose_note_name(m.group(3), intv) if m.group(3) else ""
    return f"{root}{m.group(2)}{bass}"


def to_music21(symbol: str):
    """Return a music21 ChordSymbol (or NoChord) for a plain symbol; falls back to simpler forms."""
    if symbol == "N.C.":
        return harmony.NoChord()
    figure = symbol.replace("b", "-") if len(symbol) > 1 and symbol[1] == "b" else symbol
    # music21 wants "-" for a flat root only; alterations keep "b".
    root_part = re.match(r"^[A-G][-#]?", figure).group(0) if re.match(r"^[A-G][-#]?", figure) else ""
    rest = figure[len(root_part):]
    rest = rest.replace("alt", "7#9") if rest == "7alt" else rest
    if root_part and re.match(r"^(m7b5|m7-5|min7b5|mi7b5|ø7?|h7?)$", rest):
        try:  # a real half-diminished chord, so it survives a MusicXML round trip
            return harmony.ChordSymbol(root=root_part, kind="half-diminished-seventh")
        except Exception:
            pass
    candidates = [root_part + rest, root_part + re.sub(r"[b#]\d+", "", rest), root_part + "7", root_part]
    for fig in candidates:
        try:
            cs = harmony.ChordSymbol(fig)
            if cs.pitches:
                return cs
        except Exception:  # music21 raises various errors on unknown figures
            continue
    return harmony.NoChord()


def written_interval_for(instrument: str) -> str:
    """Interval from concert to written pitch for a transposing instrument."""
    return {"trumpet": "M2", "tenor_sax": "M9", "alto_sax": "M6", "clarinet": "M2"}.get(instrument, "P1")


def interval_obj(name: str) -> interval.Interval:
    return interval.Interval(name)
