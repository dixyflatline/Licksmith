"""Clean up OMR output (homr) of a single-staff treble solo and merge pages into one score.

OMR sometimes reads a treble line as alto or bass clef; every note in those bars is then
shifted by a fixed number of staff steps. Since a trumpet solo is all treble clef, each bar
read under another clef is moved back by the clef's staff-step offset (keeping each note's
sharp/flat) and the clef is reset to treble.

Usage:  python -m lick4myhorn.omrfix out.musicxml page1.musicxml page2.musicxml ...
"""
from __future__ import annotations

import sys
from pathlib import Path

from music21 import clef, converter, note, pitch, stream

_STEPS = "CDEFGAB"
# staff steps to add so a note read under this clef lands where it sits on a treble staff
_CLEF_SHIFT = {("G", 2): 0, ("C", 3): 6, ("C", 4): 8, ("F", 4): 12}


def _shift(p: pitch.Pitch, steps: int) -> pitch.Pitch:
    idx = _STEPS.index(p.step) + p.octave * 7 + steps
    q = pitch.Pitch(_STEPS[idx % 7] + str(idx // 7))
    if p.accidental is not None:
        q.accidental = p.accidental.name
    return q


def fix_and_merge(pages: list[Path], out: Path, title: str = "Book reference (OMR)") -> dict:
    merged = stream.Part()
    report = {"bars": 0, "fixed_bars": []}
    first = True
    for page in pages:
        part = converter.parse(str(page)).parts[0]
        shift = 0
        for m in part.getElementsByClass(stream.Measure):
            for c in m.recurse().getElementsByClass(clef.Clef):
                key = (c.sign, c.line)
                shift = _CLEF_SHIFT.get(key, 0)
                m.replace(c, clef.TrebleClef()) if not first or c.sign != "G" else None
            report["bars"] += 1
            if shift:
                report["fixed_bars"].append(report["bars"])
                for n in m.recurse().notes:
                    if isinstance(n, note.Note):
                        n.pitch = _shift(n.pitch, shift)
                    else:  # chord
                        n.pitches = tuple(_shift(p, shift) for p in n.pitches)
            m.number = report["bars"]
            if not first:
                for c in list(m.recurse().getElementsByClass(clef.Clef)):
                    m.remove(c, recurse=True)
            merged.append(m)
            first = False
    s = stream.Score()
    from music21 import metadata

    s.metadata = metadata.Metadata()
    s.metadata.title = title
    s.insert(0, merged)
    s.write("musicxml", fp=str(out))
    return report


if __name__ == "__main__":
    out, *pages = sys.argv[1:]
    r = fix_and_merge([Path(p) for p in pages], Path(out))
    print(f"{r['bars']} bars -> {out}; bars moved back to treble: {r['fixed_bars']}")
