"""Self-test without audio or models: python -m lick4myhorn.selftest

1. Builds an iReal link for a blues, imports it, and checks the chords.
2. Writes a known swing solo (the 'book'), turns it into a humanised performance
   (swung eighths, triplets, sixteenths, timing jitter), quantizes it back, writes
   MusicXML and benchmarks it against the original. Agreement should be ~100%.
3. Lick library: imports a two-lick sheet, auto-tags it, transposes to 12 keys, fits the
   licks into an iReal tune, writes the practice MusicXML and records a review.
"""
from __future__ import annotations

import json
import random
import tempfile
import urllib.parse
from fractions import Fraction as F
from pathlib import Path

import numpy as np

from .bench import run_bench
from .form import Form, expand_sections
from .grid import constant_grid
from .quantize import QNote, quantize_notes
from .xmlout import build_score, write_musicxml

BLUES = "{*AT44Eb7XyQ|Ab7XyQ|Eb7XyQ|Eb7XyQ|Ab7XyQ|Ab7XyQ|Eb7XyQ|C7XyQ|F-7XyQ|Bb7XyQ|Eb7 C7XyQ|F-7 Bb7 Z"


def _scramble(s: str) -> str:
    from pyRealParser import Tune

    out = ""
    while len(s) > 50:
        block, s = s[:50], s[50:]
        out += block if len(s) < 2 else Tune._obfusc50(block)
    return out + s


def test_ireal(tmp: Path) -> list[list[str]]:
    from .ireal import find_tune, import_playlist

    body = f"Test Blues=Tester=Medium Swing=Eb=1r34LbKcu7{_scramble(BLUES)}=Jazz-Medium Swing=132=3"
    html = f'<a href="irealb://{urllib.parse.quote(body)}">Test</a>'
    src = tmp / "playlist.html"
    src.write_text(html)
    charts = import_playlist(src, tmp / "charts")
    tune = find_tune("test blues", charts)
    bars = tune["bars"]
    assert len(bars) == 12, bars
    assert bars[0] == ["Eb7"] and bars[8] == ["Fm7"] and bars[11] == ["Fm7", "Bb7"], bars
    print("iReal import OK:", " | ".join(" ".join(b) for b in bars))
    return bars


def make_solo(n_bars: int, bpb: int, seed: int = 7) -> list[QNote]:
    """A deterministic 'book' line: each beat is a rest, quarter, swung 8ths, triplet or 16ths."""
    rnd = random.Random(seed)
    notes, pitch = [], 65
    patterns = [[], [F(0)], [F(0), F(1, 2)], [F(0), F(1, 2)], [F(0), F(1, 3), F(2, 3)],
                [F(0), F(1, 4), F(1, 2), F(3, 4)], [F(1, 2)]]
    for beat in range(n_bars * bpb):
        for pos in rnd.choice(patterns):
            pitch = max(58, min(82, pitch + rnd.choice([-3, -2, -1, 1, 2, 4])))
            notes.append(QNote(F(beat) + pos, F(beat) + pos + F(1, 4), pitch))
    for a, b in zip(notes, notes[1:]):  # book style: legato unless a rest of half a beat or more
        if b.start - a.end < F(1, 2):
            a.end = b.start
    return notes


def perform(notes: list[QNote], grid: np.ndarray, seed: int = 3) -> list[dict]:
    """Written -> performed seconds: swing the off-beat eighths, add jitter."""
    rnd = random.Random(seed)
    beat_len = grid[1] - grid[0]
    out = []
    for n in notes:
        beat, frac = int(n.start), float(n.start - int(n.start))
        siblings = [float(m.start - beat) for m in notes if int(m.start) == beat]
        if frac == 0.5 and len(siblings) == 2:
            frac = rnd.uniform(0.62, 0.69)  # swung eighth
        on = grid[0] + (beat + frac) * beat_len + rnd.gauss(0, 0.012)
        off = grid[0] + float(n.end) * beat_len - 0.03
        out.append({"onset": on, "offset": max(off, on + 0.05), "pitch": n.pitch, "velocity": 90})
    return out


def test_roundtrip(tmp: Path, chorus: list[list[str]]):
    data = {
        "title": "Selftest Blues", "artist": "Robot", "beats_per_bar": 4, "bpm": 132, "feel": "swing",
        "key": "Eb", "chorus": chorus, "grid": {"first_downbeat": 2.0},
        "sections": [{"label": "head", "choruses": 1}, {"label": "trumpet solo", "choruses": 2, "transcribe": True}],
    }
    form = Form(data=data, bars=expand_sections(data))
    grid = constant_grid(form.bpm, 2.0, len(form.bars) * 4)
    solo_bars = [b.number for b in form.transcribed_bars()]
    first_beat = (solo_bars[0] - 1) * 4
    truth = make_solo(len(solo_bars), 4)
    for n in truth:
        n.start += first_beat
        n.end += first_beat

    book = write_musicxml(build_score(truth, form, solo_bars, renumber=True), tmp / "book.musicxml")
    q = quantize_notes(perform(truth, grid), grid, form)
    trans = write_musicxml(build_score(q, form, solo_bars, renumber=True), tmp / "solo.musicxml")
    from .xmlout import validate_musicxml
    for f in (book, trans):
        problems = validate_musicxml(f)
        assert not problems, f"{f.name}: " + "; ".join(problems[:5])
    print("MusicXML check OK (MuseScore-style measure and tuplet rules)")
    report = run_bench(book, trans, form.chorus_len, tmp / "bench", engine="selftest")
    text = report.read_text()
    print(text)
    agree = float(text.split("Agreement with the book: ")[1].split("%")[0])
    assert agree >= 90.0, f"round-trip agreement too low: {agree}%"
    print(f"Round trip OK: {agree}% agreement, files in {tmp}")


def _lick_sheet(path: Path) -> None:
    """Two licks written for B-flat trumpet, as MuseScore would export them."""
    from music21 import bar, expressions, harmony, instrument, meter, note, stream

    def m(notes, chords, text=None, right=None, ts=False):
        me = stream.Measure()
        if ts:
            me.insert(0, meter.TimeSignature("4/4"))
        for o, c in chords:
            me.insert(o, harmony.ChordSymbol(c))
        if text:
            me.insert(0, expressions.TextExpression(text))
        o = 0
        for pch, d in notes:
            me.insert(o, note.Rest(quarterLength=d) if pch is None else note.Note(pch, quarterLength=d))
            o += d
        if right:
            me.rightBarline = bar.Barline(right)
        return me

    p = stream.Part()
    p.insert(0, instrument.Trumpet())
    # written Gm7 C7 | Fmaj7 = concert Fm7 Bb7 | Ebmaj7
    p.append(m([("F5", .5), ("G5", .5), ("A5", .5), ("C6", .5), ("B-5", .5), ("G5", .5), ("E5", .5), ("C5", .5)],
               [(0, "Gm7"), (2, "C7")], "Lick 7, Major II V I, taken from Chet Baker's 1956 Tenderly", ts=True))
    p.append(m([("D5", .5), ("E5", .5), ("A5", 2), (None, 1)], [(0, "Fmaj7")], right="double"))
    # written F7 = concert Eb7 blues lick
    p.append(m([("A-4", .5), ("A4", .5), ("C5", .5), ("E-5", .5), ("D5", 1), ("C5", 1)], [(0, "F7")],
               "Lick 8, Blues, from Lee Morgan's 1957 Blue Train", right="final"))
    s = stream.Score()
    s.insert(0, p)
    s.write("musicxml", fp=str(path))


def test_library(tmp: Path) -> None:
    from .library import Library
    from . import srs

    sheet = tmp / "licks.musicxml"
    _lick_sheet(sheet)
    lib = Library(tmp / "project")
    prev = lib.preview_import(sheet, sheet.name)
    items = {it["number"]: it for it in prev["items"]}
    assert set(items) == {7, 8}, items.keys()
    assert items[7]["function"] == "Major II-V-I" and items[7]["tonic_name"] == "Eb", items[7]
    assert items[7]["artist"] == "Chet Baker" and items[7]["year"] == 1956
    assert items[8]["function"] == "Blues", items[8]["function"]
    ids = lib.save_import(prev["token"], prev["items"])
    keys = lib.keys_for(ids[0])
    assert len({k["pc"] for k in keys}) == 12 and keys[0]["label"] == "F", keys[:2]  # written F = concert Eb
    body = f"Two Five=Tester=Medium Swing=Eb=1r34LbKcu7{_scramble(TWO_FIVE)}=Jazz-Medium Swing=132=3"
    assert lib.import_playlist(f'<a href="irealb://{urllib.parse.quote(body)}">x</a>') == 1
    plan = lib.fit("Two Five", gap_bars=0)
    placed = {(p["bar"], p["number"]) for p in plan["placements"]}
    assert (3, 7) in placed, placed
    from .xmlout import validate_musicxml
    import tempfile

    def _check(xml: str):
        with tempfile.NamedTemporaryFile("w", suffix=".musicxml", delete=False) as t:
            t.write(xml)
        assert not validate_musicxml(Path(t.name), 4), validate_musicxml(Path(t.name), 4)

    _check(lib.fit_xml("Two Five", plan["placements"])[1])
    _check(lib.twelve_keys_xml(ids[0])[1])
    assert not list((lib.lib / "practice").glob("*")), "exports must not be stored"
    r = srs.review(lib.db, ids[0], 3, lib.settings)
    assert r["in_days"] >= 1
    assert lib.svg(ids[0]).lstrip().startswith("<")
    print(f"library: 2 licks imported, fitted at bars {sorted(b for b, _ in placed)}, review in {r['in_days']} days")


TWO_FIVE = "{*AT44Eb^7XyQ|C-7XyQ|F-7 Bb7XyQ|Eb^7XyQ|Gh7 C7XyQ|F-XyQ|Ah7 D7XyQ|G-XyQ}"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="l4mh_selftest_"))
    chorus = test_ireal(tmp)
    test_roundtrip(tmp, chorus)
    test_library(tmp)
    print(json.dumps({"selftest": "passed", "dir": str(tmp)}))


if __name__ == "__main__":
    main()
