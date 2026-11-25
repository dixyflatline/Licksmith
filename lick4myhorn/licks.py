"""Licks: import from MusicXML, store as one MusicXML file per lick, transpose to 12 keys.

Internally a lick is handled in concert pitch. Files on disk are written for B-flat trumpet
(with a <transpose> element), so they open correctly in MuseScore, Dorico, Finale or Sibelius.

Import modes
  auto   - "Lick 12 ..." text marks a new lick; otherwise rehearsal marks; otherwise double
           barlines; otherwise cut along known progressions (ii-V-I, minor ii-V-i, turnaround...) by harmony.
  sheet  - same as auto, but never treats the whole file as one lick.
  single - the whole file (or the bar range) is one lick.
  range  - bars a-b of a solo, e.g. "17-20" or "17-20, 33-36" (one lick per range).
  phrases- a solo cut into short licks by harmony (see harmonic_units). Auto does this too when
           the file has no "Lick N" texts and no double barlines between licks.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from music21 import (bar as m21bar, clef, converter, expressions, harmony, instrument, interval,
                     key, layout, meter, metadata, note, pitch, stream, tempo, chord as m21chord)

from .chords import transpose_symbol

PC_NAMES_MAJOR = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
PC_NAMES_MINOR = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]
NAME_TO_PC = {n: i for i, n in enumerate(PC_NAMES_MAJOR)}
NAME_TO_PC.update({n: i for i, n in enumerate(PC_NAMES_MINOR)})
NAME_TO_PC.update({"F#": 6, "Gb": 6, "Cb": 11, "B#": 0, "E#": 5, "Fb": 4, "A#": 10, "D#": 3, "Db": 1})

WRITTEN_SHIFT = 2  # B-flat trumpet sounds a major 2nd below written

_LICK_RE = re.compile(r"\blick\s*#?\s*(\d+)", re.I)
_SKIP_TEXT = re.compile(r"^(swing|straight|medium|up|ballad|latin|bossa|even 8ths|fine|d\.?c\.?|d\.?s\.?)\b", re.I)


# --------------------------------------------------------------------------------------- utils
def key_name(pc: int, mode: str = "major") -> str:
    return (PC_NAMES_MINOR if mode == "minor" else PC_NAMES_MAJOR)[pc % 12]


_KIND = {
    "major": "", "minor": "m", "augmented": "+", "diminished": "dim", "dominant-seventh": "7",
    "dominant": "7", "major-seventh": "maj7", "minor-seventh": "m7", "diminished-seventh": "dim7",
    "augmented-seventh": "+7", "half-diminished-seventh": "m7b5", "half-diminished": "m7b5",
    "minor-major-seventh": "mmaj7", "major-minor": "mmaj7", "major-sixth": "6", "minor-sixth": "m6",
    "dominant-ninth": "9", "major-ninth": "maj9", "minor-ninth": "m9", "dominant-11th": "11",
    "major-11th": "maj11", "minor-11th": "m11", "dominant-13th": "13", "major-13th": "maj13",
    "minor-13th": "m13", "suspended-second": "sus2", "suspended-fourth": "sus4",
    "suspended-fourth-seventh": "7sus4", "power": "5", "augmented-ninth": "+9", "Neapolitan": "",
    "Italian": "", "French": "", "German": "", "pedal": "", "Tristan": "",
}


def m21_to_plain(h) -> str:
    """music21 ChordSymbol -> plain text symbol ("Ebm7", "Bb7b9", "Cm7b5", "C7/E")."""
    if isinstance(h, harmony.NoChord):
        return "N.C."
    try:
        root = h.root().name.replace("-", "b")
        suf = _KIND.get(h.chordKind)
    except Exception:
        suf = None
    if suf is not None:
        alts = []
        for md in h.getChordStepModifications():
            if md.modType == "subtract":
                continue
            sem = md.interval.semitones if md.interval is not None else 0
            acc = "b" if sem < 0 else "#" if sem > 0 else ""
            if suf == "m7" and md.degree == 5 and sem == -1:
                suf = "m7b5"
                continue
            if suf in ("", "6") and md.degree == 7 and sem == -1:
                suf = "7"
                continue
            if not acc and md.modType == "add" and suf == "7" and md.degree in (9, 11, 13):
                suf = str(md.degree)
                continue
            alts.append(f"{acc}{md.degree}" if acc else f"add{md.degree}")
        bass = ""
        try:
            b = h.bass()
            if b is not None and b.name != h.root().name:
                bass = "/" + b.name.replace("-", "b")
        except Exception:
            pass
        return root + suf + "".join(alts) + bass
    fig = h.figure or ""
    m = re.match(r"^[A-G][-#]*", fig)
    rest = fig[m.end():] if m else fig
    bass = ""
    if "/" in rest:
        rest, b = rest.split("/", 1)
        bass = "/" + b.replace("-", "b")
    try:
        root = h.root().name.replace("-", "b")
    except Exception:
        root = m.group(0).replace("-", "b") if m else "C"
    return root + rest + bass


def _plain_to_m21(sym: str):
    from .chords import to_music21

    return to_music21(sym)


@dataclass
class Fragment:
    measures: list  # concert-pitch music21 Measures (copies)
    first_bar: int
    last_bar: int
    texts: list[str] = field(default_factory=list)
    carry_chord: str | None = None  # harmony sounding at the start (from earlier bars)
    slurs: list = field(default_factory=list)  # [[copied notes under one slur], ...]


# --------------------------------------------------------------------------------------- loading
def load_concert(path: Path, pitch_in_file: str = "auto") -> stream.Score:
    """Parse MusicXML and return a concert-pitch score (first part only).

    pitch_in_file: "auto" (use the file's <transpose>; if none, assume written B-flat when the part
    looks like a trumpet), "written" (always B-flat written), "concert".
    """
    s = converter.parse(str(path))
    part = s.parts[0] if s.parts else s
    inst = part.getInstrument(returnDefault=False)
    has_transpose = inst is not None and inst.transposition is not None and inst.transposition.semitones != 0
    sc = stream.Score()
    sc.metadata = s.metadata or metadata.Metadata()
    if pitch_in_file == "concert":
        sc.insert(0, copy.deepcopy(part))
        return sc
    if has_transpose and pitch_in_file == "auto":
        c = s.toSoundingPitch()
        sc.insert(0, c.parts[0] if c.parts else c)
        return sc
    name = (inst.instrumentName if inst else "") or (part.partName or "")
    if pitch_in_file == "written" or re.search(r"tr(u)?mp|b-?flat|bb|cornet|flug", name, re.I) or not name:
        p = copy.deepcopy(part)
        p.transpose(-WRITTEN_SHIFT, inPlace=True)
        _transpose_harmony(p, interval.Interval(-WRITTEN_SHIFT))
        sc.insert(0, p)
        return sc
    sc.insert(0, copy.deepcopy(part))
    return sc


def _transpose_harmony(container, intv) -> None:
    """Replace every ChordSymbol by a correctly re-spelled transposed one."""
    for h in list(container.recurse().getElementsByClass(harmony.ChordSymbol)):
        sym = m21_to_plain(h)
        new = _plain_to_m21(transpose_symbol(sym, intv) if sym != "N.C." else sym)
        site = h.activeSite
        off = h.offset
        site.remove(h)
        site.insert(off, new)


def _texts(m: stream.Measure) -> list[str]:
    out = []
    for el in m.recurse().getElementsByClass([expressions.TextExpression, expressions.RehearsalMark]):
        t = (el.content or "").strip()
        if t:
            out.append(t)
    return out


def split_fragments(score: stream.Score, mode: str = "auto", ranges: str | None = None) -> list[Fragment]:
    part = score.parts[0] if score.parts else score
    measures = list(part.getElementsByClass(stream.Measure))
    if not measures:
        return []
    all_measures = measures
    if ranges and mode != "range":  # "only read these bars": every other mode works inside them
        spans_ = []
        for chunk in re.split(r"[,;]\s*", ranges.strip()):
            a_, _, b_ = chunk.partition("-")
            if a_.strip().isdigit():
                spans_.append((int(a_), int(b_ or a_)))
        measures = [m for m in all_measures if any(a_ <= m.number <= b_ for a_, b_ in spans_)] or all_measures
    numbers = [m.number for m in measures]

    def chord_before(idx: int) -> str | None:
        last = None
        for m in all_measures[:all_measures.index(measures[idx])]:
            for h in m.getElementsByClass(harmony.ChordSymbol):
                last = m21_to_plain(h)
        return last

    def make(i0: int, i1: int) -> Fragment:
        ms = [copy.deepcopy(m) for m in measures[i0:i1 + 1]]
        texts = []
        copies: dict[int, object] = {}
        for m, c in zip(measures[i0:i1 + 1], ms):
            texts += _texts(m)
            for a, b in zip(m.recurse().notesAndRests, c.recurse().notesAndRests):
                copies[id(a)] = b
        from music21 import spanner

        slurs = []  # slurs sit beside the measures, so they are carried over by hand
        for sl in part.recurse().getElementsByClass(spanner.Slur):
            els = sl.getSpannedElements()
            if len(els) >= 2 and all(id(e) in copies for e in els):
                slurs.append([copies[id(e)] for e in els])
        fr = Fragment(ms, numbers[i0], numbers[i1], texts, chord_before(i0))
        fr.slurs = slurs
        return fr

    groups: list[tuple[int, int]] = []
    if mode == "range" and ranges:
        for chunk in re.split(r"[,;]\s*", ranges.strip()):
            if not chunk:
                continue
            a, _, b = chunk.partition("-")
            a, b = int(a), int(b or a)
            idx = [i for i, n in enumerate(numbers) if a <= n <= b]
            if idx:
                groups.append((idx[0], idx[-1]))
    elif mode == "single":
        groups = [(0, len(measures) - 1)]
    else:
        lick_starts = [i for i, m in enumerate(measures) if any(_LICK_RE.search(t) for t in _texts(m))]
        marks = [i for i, m in enumerate(measures) if list(m.getElementsByClass(expressions.RehearsalMark))]
        ends: set[int] = set()
        for i, m in enumerate(measures):
            rb = m.rightBarline
            if rb is not None and rb.type in ("double", "final", "light-light", "light-heavy", "heavy"):
                ends.add(i)
        ends.discard(len(measures) - 1)

        def cut(starts: list[int]) -> list[tuple[int, int]]:
            starts = sorted(set([0] + starts))
            bounds = starts + [len(measures)]
            out = []
            for a, b in zip(bounds, bounds[1:]):
                end = b - 1
                inner = sorted(e for e in ends if a <= e < end)
                out.append((a, inner[0] if inner else end))
            return out

        def by_barlines() -> list[tuple[int, int]]:
            out, a = [], 0
            for e in sorted(ends):
                out.append((a, e))
                a = e + 1
            if a < len(measures):
                out.append((a, len(measures) - 1))
            return out

        if mode == "phrases":
            # a solo: short licks by harmony; rehearsal marks and double barlines are hard boundaries
            # (rehearsal marks do not cut: some books put one on every bar, and a ii-V-I must stay whole)
            starts = sorted({e + 1 for e in ends})
            groups = [u for g in cut(starts) for u in harmonic_units(measures, *g)]
        elif lick_starts:
            # "Lick 43 ..." texts mark the licks; a stretch much longer than a lick (the material before
            # the first label, or what follows the last one) is cut by harmony instead
            groups = [u for g in cut(lick_starts) for u in (harmonic_units(measures, *g) if g[1] - g[0] + 1 > 8 else [g])]
        elif ends and mode in ("auto", "sheet"):
            # a lick sheet with double barlines; only very long ones get split by harmony
            groups = [u for g in by_barlines()
                      for u in (harmonic_units(measures, *g) if g[1] - g[0] + 1 > 4 else [g])]
        elif mode == "auto" and len(measures) <= 8 and not marks and \
                not any(m.getElementsByClass(harmony.ChordSymbol) for m in measures):
            groups = [(0, len(measures) - 1)]  # a short file with no chords or markings is one lick
        elif mode in ("auto", "sheet"):
            # no lick markings: treat the file as a solo and cut it into short harmonic phrases
            groups = [u for g in cut(sorted({e + 1 for e in ends})) for u in harmonic_units(measures, *g)]
    frags = []
    for a, b in groups:
        # trim empty bars at both ends
        while a <= b and not list(measures[a].recurse().getElementsByClass(note.Note)):
            a += 1
        while b >= a and not list(measures[b].recurse().getElementsByClass(note.Note)):
            b -= 1
        if a <= b:
            frags.append(make(a, b))
    return frags


def _bar_chords(measures: list, carry: str | None) -> list[list[tuple[int, str]]]:
    """Per bar: [(pc, quality)], carrying the last chord into bars without symbols."""
    from .analysis import parse_chord

    out = []
    last = parse_chord(carry) if carry else None
    for m in measures:
        cs = [parse_chord(m21_to_plain(h)) for h in m.getElementsByClass(harmony.ChordSymbol)]
        cs = [c[:2] for c in cs if c]
        if cs:
            last = cs[-1]
            out.append(cs)
        else:
            out.append([last] if last else [])
    return out


def _n_notes(m) -> int:
    return len(list(m.recurse().getElementsByClass(note.Note)))


def harmonic_units(measures: list, a: int, b: int) -> list[tuple[int, int]]:
    """Cut bars a..b into licks along the harmony.

    Known progressions stay whole, using the same definitions as the auto tags (analysis._PATTERNS):
    a turnaround (I vi ii V), a major ii-V-I, a minor ii-V-i (iim7b5 V7 im), ii-subV-I, backdoor ii-V,
    the two-chord ii-V, V-I, V-i, and the same major chord over two or more bars ("two major chords",
    cut two bars at a time). The longest progression wins. A chord held for several bars gives a
    progression only its last bar (as the first chord) or its first bar (as the last chord); the
    other bars of the held chord stand alone. Bars outside any progression: a chord held for two
    bars makes a two-bar lick, any other bar stands alone, and a unit with fewer than three notes
    joins the previous one (a short pickup joins the next)."""
    from .analysis import _PATTERNS

    carry = None
    for m in measures[:a]:
        for h in m.getElementsByClass(harmony.ChordSymbol):
            carry = m21_to_plain(h)
    bc = _bar_chords(measures[a:b + 1], carry)
    n = b - a + 1
    # chord spans in bars (the same chord on consecutive bars is one span)
    spans: list[dict] = []
    for k, cb in enumerate(bc):
        for pc, q in cb:
            if spans and (spans[-1]["pc"], spans[-1]["q"]) == (pc, q):
                spans[-1]["b1"] = k
            else:
                spans.append({"pc": pc, "q": q, "b0": k, "b1": k})
    matched: dict[int, int] = {}  # first bar of a progression -> last bar
    taken: set[int] = set()
    pats = [p for p in _PATTERNS if p[0] != "Dominant cycle"]
    for name, pat, _off, _mode in sorted(pats, key=lambda p: -len(p[1])):
        L = len(pat)
        for i in range(len(spans) - L + 1):
            r0 = spans[i]["pc"]
            if not all((((spans[i + j]["pc"] - r0) % 12 == iv) and spans[i + j]["q"] in qs)
                       for j, (iv, qs) in enumerate(pat)):
                continue
            # a chord held for several bars: the progression uses only the last bar of its first chord
            # and the first bar of its last chord; the other bars of a held chord stand alone
            lo, hi = spans[i]["b1"], spans[i + L - 1]["b0"]
            if any(x in taken for x in range(lo, hi + 1)):
                continue
            matched[lo] = hi
            taken.update(range(lo, hi + 1))
    for sp in spans:  # the same major chord over two or more bars (Cmaj7, Cmaj7)
        lo, hi = sp["b0"], sp["b1"]
        if sp["q"] == "maj" and hi > lo and not any(x in taken for x in range(lo, hi + 1)):
            x = lo  # two bars at a time; an odd bar left over stays with the last pair
            while x <= hi:
                y = hi if hi - x == 2 else min(x + 1, hi)
                matched[x] = y
                taken.update(range(x, y + 1))
                x = y + 1
    units: list[list[int]] = []
    kinds: list[bool] = []  # True = a known progression
    k = 0
    while k < n:
        if k in matched:
            units.append(list(range(k, matched[k] + 1))); kinds.append(True)
            k = matched[k] + 1
            continue
        unit = [k]
        cb = bc[k]
        if len(cb) == 1 and k + 1 < n and k + 1 not in taken and len(bc[k + 1]) == 1 and cb[0] == bc[k + 1][0] and \
                not list(measures[a + k + 1].getElementsByClass(expressions.RehearsalMark)):
            unit = [k, k + 1]
        units.append(unit); kinds.append(False)
        k = unit[-1] + 1
    merged: list[list[int]] = []
    mk: list[bool] = []
    for u, known in zip(units, kinds):
        notes = sum(_n_notes(measures[a + i]) for i in u)
        if merged and notes < 3:
            merged[-1] += u
        else:
            merged.append(list(u)); mk.append(known)
    if len(merged) > 1 and sum(_n_notes(measures[a + i]) for i in merged[0]) < 3:
        merged[1] = merged[0] + merged[1]  # a short pickup bar belongs to the lick that follows
        merged.pop(0)
    return [(a + u[0], a + u[-1]) for u in merged]


# --------------------------------------------------------------------------------------- data
def _f(x) -> float:
    return round(float(x), 6)


def fragment_data(frag: Fragment) -> dict:
    """Concert-pitch note/chord lists, offsets in quarter notes from the start of the first bar."""
    ts = None
    notes: list[list] = []
    chords: list[list] = []
    graces: list[list] = []  # [offset, concert midi] of grace notes: played just before the beat they lead into
    tuplets = 0
    cursor = 0.0
    for m in frag.measures:
        t = m.timeSignature or (m.getContextByClass(meter.TimeSignature))
        if t is not None and ts is None:
            ts = [t.numerator, t.denominator]
        bar_len = m.barDuration.quarterLength if m.barDuration else 4.0
        for h in m.recurse().getElementsByClass(harmony.ChordSymbol):
            chords.append([_f(cursor + float(h.offset)), m21_to_plain(h)])
        for n in m.recurse().getElementsByClass([note.Note, m21chord.Chord]):
            if isinstance(n, note.Note) and n.duration.isGrace:
                graces.append([_f(cursor + float(n.offset)), n.pitch.midi])
                continue
            if isinstance(n, harmony.ChordSymbol) or n.duration.isGrace or n.quarterLength == 0:
                continue
            midi = n.pitch.midi if isinstance(n, note.Note) else max(p.midi for p in n.pitches)
            off = cursor + float(n.offset)
            if n.duration.tuplets:
                tuplets += 1
            tie = n.tie.type if n.tie is not None else None
            if tie in ("stop", "continue") and notes and notes[-1][2] == midi and \
                    abs(notes[-1][0] + notes[-1][1] - off) < 1e-3:
                notes[-1][1] = _f(notes[-1][1] + float(n.quarterLength))
            else:
                notes.append([_f(off), _f(n.quarterLength), midi])
        cursor += float(bar_len)
    if not chords or chords[0][0] > 0:
        if frag.carry_chord:
            chords.insert(0, [0.0, frag.carry_chord])
    ts = ts or [4, 4]
    return {"ts": ts, "bars": len(frag.measures), "length": _f(cursor), "notes": notes,
            "chords": chords, "triplet_notes": tuplets, "graces": graces}


# --------------------------------------------------------------------------------------- scores
_STRIP = (expressions.TextExpression, expressions.RehearsalMark, layout.SystemLayout, layout.PageLayout,
          layout.StaffLayout, tempo.MetronomeMark, tempo.TempoText, key.KeySignature, clef.Clef,
          meter.TimeSignature, instrument.Instrument)


def lick_score(frag_or_measures, carry_chord: str | None, title: str = "", label: str = "") -> stream.Score:
    """Clean concert-pitch single-part score holding just the lick (renumbered bars, no key sig)."""
    src = frag_or_measures.measures if isinstance(frag_or_measures, Fragment) else frag_or_measures
    ts = None
    for m in src:
        if m.timeSignature is not None:
            ts = m.timeSignature
            break
    if ts is None:
        ts = src[0].getContextByClass(meter.TimeSignature) or meter.TimeSignature("4/4")
    part = stream.Part()
    part.insert(0, instrument.Trumpet())
    pairs: dict[int, object] = {}  # id(original note) -> its copy, to carry slurs over
    for i, m0 in enumerate(src):
        m = copy.deepcopy(m0)
        for a, b in zip(m0.recurse().notesAndRests, m.recurse().notesAndRests):
            pairs[id(a)] = b
        m.number = i + 1
        for el in list(m.recurse().getElementsByClass(_STRIP)):
            el.activeSite.remove(el)
        m.leftBarline = None
        m.rightBarline = None
        if i == 0:
            m.insert(0, clef.TrebleClef())
            m.insert(0, meter.TimeSignature(ts.ratioString))
            if carry_chord and not any(h.offset == 0 for h in m.getElementsByClass(harmony.ChordSymbol)):
                m.insert(0, _plain_to_m21(carry_chord))
            if label:
                te = expressions.TextExpression(label)
                te.placement = "above"
                m.insert(0, te)
        part.append(m)
    part.getElementsByClass(stream.Measure)[-1].rightBarline = m21bar.Barline("final")
    from music21 import spanner

    if isinstance(frag_or_measures, Fragment):
        groups = frag_or_measures.slurs
    else:
        site = src[0].activeSite if src else None
        groups = [sl.getSpannedElements() for sl in site.recurse().getElementsByClass(spanner.Slur)] if site else []
    for els in groups:  # slurs live beside the measures, not inside them; keep those inside the lick
        if len(els) >= 2 and all(id(e) in pairs for e in els):
            part.insert(0, spanner.Slur([pairs[id(e)] for e in els]))
    _fix_edge_ties(part)
    sc = stream.Score()
    sc.metadata = metadata.Metadata()
    sc.metadata.title = title
    sc.insert(0, part)
    return sc


def _fix_edge_ties(part) -> None:
    """A lick cut from a solo must not start or end in the middle of a tie."""
    from music21 import tie as m21tie

    ns = [n for n in part.recurse().getElementsByClass([note.Note, m21chord.Chord])
          if not isinstance(n, harmony.ChordSymbol)]
    if not ns:
        return
    first, last = ns[0], ns[-1]
    if first.tie is not None and first.tie.type in ("stop", "continue"):
        first.tie = None if first.tie.type == "stop" else m21tie.Tie("start")
    if last.tie is not None and last.tie.type in ("start", "continue"):
        last.tie = None if last.tie.type == "start" else m21tie.Tie("stop")


def _reset_accidentals(s) -> None:
    for n in s.recurse().notes:
        for p in n.pitches:
            if p.accidental is not None:
                p.accidental.displayStatus = None
                p.accidental.displayType = "normal"
    for m in s.recurse().getElementsByClass(stream.Measure):
        try:
            m.makeAccidentals(inPlace=True, cautionaryNotImmediateRepeat=False)
        except Exception:
            pass


def transpose_score(sc: stream.Score, intv: interval.Interval, octaves: int = 0) -> stream.Score:
    out = copy.deepcopy(sc)
    for p in out.parts:
        for n in p.recurse().getElementsByClass([note.Note, m21chord.Chord]):
            if isinstance(n, harmony.ChordSymbol):
                continue
            n.transpose(intv, inPlace=True)
            for _ in range(abs(octaves)):
                n.transpose(interval.Interval("P8" if octaves > 0 else "-P8"), inPlace=True)
        _transpose_harmony(p, intv)
    _reset_accidentals(out)
    return out


def for_display(sc: stream.Score, written: bool = True) -> stream.Score:
    """Prepare a concert-pitch score for output.

    music21's exporter always writes notes and chord symbols at written pitch for a transposing
    instrument (adding <transpose>), so the score itself stays in concert pitch."""
    out = copy.deepcopy(sc)
    for p in out.parts:
        for inst in list(p.recurse().getElementsByClass(instrument.Instrument)):
            inst.activeSite.remove(inst)
        if written:
            p.insert(0, instrument.Trumpet())
        else:
            i = instrument.Instrument()
            i.instrumentName = "Trumpet (concert pitch)"
            i.partName = "Trumpet (concert)"
            p.insert(0, i)
        p.atSoundingPitch = True
    out.atSoundingPitch = True
    return out


to_written = for_display


def score_xml(sc: stream.Score) -> str:
    """MusicXML text; eighths and sixteenths are beamed per beat, as in a jazz book."""
    from music21.musicxml.m21ToXml import GeneralObjectExporter
    from .xmlout import rebeam

    try:
        ts = next(iter(sc.recurse().getElementsByClass(meter.TimeSignature)), None)
        beat = float(ts.beatDuration.quarterLength) if ts is not None else 1.0
        rebeam(sc, beat if beat >= 1.0 else 1.0)
    except Exception:  # beaming is cosmetic; never block drawing or saving
        pass
    return GeneralObjectExporter(sc).parse().decode("utf-8")


def write_xml(sc: stream.Score, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(score_xml(sc), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------- keys
ORDERS = {
    "mine": "My order (original, P4 up, P5 up, M2 down, then cycle of 4ths)",
    "common": "Most common keys first (the lick's own key, then keys by how many standards use them)",
    "fourths": "Cycle of 4ths",
    "fifths": "Cycle of 5ths",
    "diatonic": "Diatonic (scale degrees of the original key, then cycle of 4ths)",
    "chromatic": "Chromatic up",
    "chromatic_down": "Chromatic down",
    "random": "Random (new each day)",
}


# How many of ~1000 Real Book tunes sit in each key (a major key and its relative minor counted together), by concert major key:
# F/Dm 231, Eb/Cm 197, C/Am 161, Bb/Gm 158, G/Em 96, Ab/Fm 90, Db/Bbm 32, D/Bm 13, Gb/Ebm 11, E/C#m 6, B/G#m 3, A/F#m 2
COMMON_MAJOR_PCS = (5, 3, 0, 10, 7, 8, 1, 2, 6, 4, 11, 9)


def key_order(original_pc: int, order: str = "mine", my_steps=(0, 5, 7, -2), seed: int | None = None, mode: str = "major") -> list[int]:
    o = original_pc % 12
    if order == "common":
        shift = 3 if mode == "minor" else 0   # a minor lick moves between the minor keys that share a signature with those major keys
        seq = [o] + [(m - shift) % 12 for m in COMMON_MAJOR_PCS if (m - shift) % 12 != o]
    elif order == "fifths":
        seq = [(o + 7 * i) % 12 for i in range(12)]
    elif order == "chromatic":
        seq = [(o + i) % 12 for i in range(12)]
    elif order == "chromatic_down":
        seq = [(o - i) % 12 for i in range(12)]
    elif order == "diatonic":
        seq = [(o + d) % 12 for d in (0, 2, 4, 5, 7, 9, 11)]
    elif order == "random":
        import random

        seq = [o] + random.Random(seed).sample([(o + i) % 12 for i in range(1, 12)], 11)
    elif order == "mine":
        seq = []
        for st in my_steps:
            pc = (o + int(st)) % 12
            if pc not in seq:
                seq.append(pc)
    else:
        seq = [(o + 5 * i) % 12 for i in range(12)]
    for i in range(12):  # complete with cycle of 4ths from the original
        pc = (o + 5 * i) % 12
        if pc not in seq:
            seq.append(pc)
    return seq[:12]


def interval_between(src_name: str, dst_name: str) -> interval.Interval:
    """Spelled interval from one tonic name to another, chosen within -6..+5 semitones."""
    p1 = pitch.Pitch(src_name.replace("b", "-") + "4")
    best = None
    for octv in (3, 4, 5):
        p2 = pitch.Pitch(dst_name.replace("b", "-") + str(octv))
        semis = p2.midi - p1.midi
        if -6 <= semis <= 5:
            best = interval.Interval(noteStart=note.Note(p1), noteEnd=note.Note(p2))
            break
    return best or interval.Interval("P1")


def fit_octave(midis: list[int], semis: int, lo_written: int, hi_written: int, center: float | None = None) -> tuple[int, bool]:
    """Pick an octave shift (in octaves) so the transposed lick fits the written range.

    Returns (octaves, fits). Prefers staying closest to the original register."""
    if not midis:
        return 0, True
    center = center if center is not None else sum(midis) / len(midis)
    best = None
    for octv in (-2, -1, 0, 1, 2):
        w = [m + semis + 12 * octv + WRITTEN_SHIFT for m in midis]
        out = sum(1 for x in w if x < lo_written or x > hi_written)
        dist = abs(sum(w) / len(w) - WRITTEN_SHIFT - center)
        cand = (out, dist, octv)
        if best is None or cand < best:
            best = cand
    return best[2], best[0] == 0


_SIG_MAJOR = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7, "F": 1, "Bb": 2, "Eb": 3,
              "Ab": 4, "Db": 5, "Gb": 6, "Cb": 7}
_SIG_MINOR = {"A": 0, "E": 1, "B": 2, "F#": 3, "C#": 4, "G#": 5, "D#": 6, "A#": 7, "D": 1, "G": 2, "C": 3,
              "F": 4, "Bb": 5, "Eb": 6, "Ab": 7}


def display_key_name(pc: int, mode: str = "major") -> str:
    """Spelling of a tonic with the fewest accidentals in the key signature (flats on a tie)."""
    table = _SIG_MINOR if mode == "minor" else _SIG_MAJOR
    cands = [n for n in table if NAME_TO_PC.get(n) == pc % 12]
    cands.sort(key=lambda n: (table[n], "#" in n))
    return cands[0] if cands else key_name(pc, mode)


def concert_name_for(pc_concert: int, mode: str, written: bool) -> tuple[str, str]:
    """(concert tonic name, display tonic name), spelled so the DISPLAYED key is the simple one."""
    if not written:
        n = display_key_name(pc_concert, mode)
        return n, n
    w = display_key_name(pc_concert + WRITTEN_SHIFT, mode)
    c = pitch.Pitch(w.replace("b", "-") + "4").transpose("-M2").name.replace("-", "b")
    return c, w


def transposition_plan(lick: dict, target_pc: int, settings: dict) -> dict:
    """How to move a lick (concert tonic lick['tonic_pc']) to target_pc (concert)."""
    mode = lick.get("mode", "major")
    written = settings.get("display", "written") == "written"
    src = lick.get("tonic_name") or key_name(lick["tonic_pc"], mode)
    dst, disp = concert_name_for(target_pc, mode, written)
    intv = interval_between(src, dst)
    midis = [n[2] for n in lick["data"]["notes"]]
    octv, fits = fit_octave(midis, intv.semitones, settings["range_low"], settings["range_high"])
    return {"interval": intv, "octaves": octv, "fits": fits, "semis": intv.semitones + 12 * octv,
            "concert_key": dst, "display_key": disp, "target_pc": target_pc % 12,
            "label": disp + ("m" if mode == "minor" else "")}


def transposed_data(lick: dict, plan: dict) -> dict:
    d = lick["data"]
    return {
        "ts": d["ts"], "bars": d["bars"], "length": d["length"],
        "notes": [[o, du, m + plan["semis"]] for o, du, m in d["notes"]],
        "graces": [[o, m + plan["semis"]] for o, m in d.get("graces", [])],
        "chords": [[o, transpose_symbol(s, plan["interval"]) if s != "N.C." else s] for o, s in d["chords"]],
    }


def load_lick_score(path: Path) -> stream.Score:
    """Per-lick files are written B-flat; return concert pitch."""
    return load_concert(path, "auto")


def pretty_chord(sym: str) -> str:
    """'Bbm7b5' -> 'B♭m7♭5' for display."""
    if sym == "N.C.":
        return sym
    m = re.match(r"^([A-G])([b#]?)(.*)$", sym)
    if not m:
        return sym
    acc = {"b": "♭", "#": "♯", "": ""}[m.group(2)]
    rest = re.sub(r"b(?=\d)", "♭", m.group(3))
    rest = re.sub(r"#(?=\d)", "♯", rest)
    rest = re.sub(r"/([A-G])b", "/\\1♭", rest)
    rest = re.sub(r"/([A-G])#", "/\\1♯", rest)
    return m.group(1) + acc + rest


def render_svg(sc: stream.Score, written: bool = True, width: int = 1600, scale: int = 45,
               options: dict | None = None) -> str:
    """SVG of a concert-pitch score (shown written for B-flat unless written=False).

    Chord symbols are drawn as plain text so they display with any font."""
    import verovio

    out = for_display(sc, written)
    for inst in out.recurse().getElementsByClass(instrument.Instrument):
        inst.partName = ""
        inst.partAbbreviation = ""
    for p in out.parts:
        p.partName = ""
        p.partAbbreviation = ""
    for h in list(out.recurse().getElementsByClass(harmony.ChordSymbol)):
        sym = m21_to_plain(h)
        if written and sym != "N.C.":
            sym = transpose_symbol(sym, "M2")
        te = expressions.TextExpression(pretty_chord(sym))
        te.placement = "above"
        te.style.fontWeight = "bold"
        site, off = h.activeSite, h.offset
        site.remove(h)
        site.insert(off, te)
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": width, "pageHeight": 60000, "adjustPageHeight": True, "scale": scale, "footer": "none",
                   "header": "none", "breaks": "auto", "pageMarginTop": 30, "pageMarginBottom": 20,
                   "pageMarginLeft": 20, "pageMarginRight": 20, "svgViewBox": True})
    if options:
        tk.setOptions(options)
    tk.loadData(score_xml(out))
    return "\n".join(tk.renderToSVG(i) for i in range(1, tk.getPageCount() + 1))
