"""Write quantized notes + chart chords as MusicXML (B-flat trumpet part by default).

Notes are stored in concert pitch inside the program and written at written pitch here,
with the part's <transpose> element set, so MuseScore / Dorico / Finale / Sibelius can all
toggle between written and concert pitch.
"""
from __future__ import annotations

from fractions import Fraction as F
from pathlib import Path

from music21 import expressions, instrument, key, layout, metadata, meter, note, pitch, stream, tempo
from music21 import bar as m21bar
from music21 import beam
from music21 import tie as m21tie

from .chords import to_music21, transpose_symbol, written_interval_for

_SEMITONES = {"P1": 0, "M2": 2, "M6": 9, "M9": 14}


def _spell(midi: int, prefer_flats: bool) -> pitch.Pitch:
    p = pitch.Pitch(midi)
    if p.accidental is not None and p.accidental.name == "sharp" and prefer_flats:
        p = p.getEnharmonic()
    if p.accidental is not None and p.accidental.name == "flat" and not prefer_flats:
        p = p.getEnharmonic()
    return p


# Note values allowed inside one beat, longest first (quarter-note units).
_IN_BEAT = [F(3, 4), F(2, 3), F(1, 2), F(1, 3), F(1, 4), F(1, 6), F(1, 8), F(1, 12)]


def _pieces(start: F, length: F, bpb: int, kind: str = "note") -> list[tuple[F, F]]:
    """Split a span into plain, notatable values (no odd tuplets), the way jazz books write them.

    Notes: a value that fits exactly is written as one note, including a dotted quarter on
    the beat, a half note on any beat, and a syncopated quarter on the "and" of a beat,
    instead of tying two shorter notes. Rests: only whole, half (beats 1 and 3), quarter and
    shorter values that never cross a beat.
    Otherwise the span is cut at beat boundaries into standard values and tied.
    """
    out: list[tuple[F, F]] = []
    cur, end = F(start), F(start) + F(length)
    while cur < end:
        rem = end - cur
        frac = cur - int(cur)
        if kind == "note":
            exact = None
            if frac == 0:
                if rem == 4 and cur == 0:
                    exact = rem
                elif rem == 3 and cur in (0, 1):
                    exact = rem
                elif rem in (2, F(3, 2)) and cur + rem <= bpb:
                    exact = rem
            elif frac == F(1, 2) and rem == 1 and cur + 1 <= bpb:
                exact = rem  # syncopated quarter on the "and"
            if exact is not None:
                out.append((cur, F(exact)))
                cur += exact
                continue
        if frac == 0:
            beats = int(rem)
            sizes = (4, 3, 2, 1) if kind == "note" else (4, 2, 1)
            take = 0
            for size in sizes:
                ok_pos = (size == 1 or (size == 2 and (kind == "note" or cur % 2 == 0))
                          or (size >= 3 and cur == 0) or (size == 3 and cur == 1 and kind == "note"))
                if size <= beats and ok_pos and cur + size <= bpb:
                    take = size
                    break
            if take:
                out.append((cur, F(take)))
                cur += take
                continue
        nb = F(int(cur) + 1)
        seg = min(end, nb) - cur
        for v in _IN_BEAT:
            if v <= seg and ((cur + v) * 12).denominator == 1:
                out.append((cur, v))
                cur += v
                break
        else:  # off the 1/12 grid (should not happen): absorb the remainder
            out.append((cur, seg))
            cur += seg
    return out


def _bracket_tuplets(m: stream.Measure) -> None:
    """One triplet bracket per contiguous run of triplet values inside a beat."""
    by_beat: dict[int, list] = {}
    for el in sorted(m.notesAndRests, key=lambda e: e.offset):
        by_beat.setdefault(int(F(el.offset)), []).append(el)
    for els in by_beat.values():
        runs, run = [], []
        for el in els:
            if el.duration.tuplets:
                run.append(el)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        for run in runs:
            beamed = len(run) >= 2 and all(isinstance(e, note.Note) and e.duration.type in _LEVELS for e in run)
            for el in run:
                for t in el.duration.tuplets:
                    t.type = None
                    t.bracket = not beamed  # beamed triplets show just the "3", as in the books
            if len(run) == 1:
                run[0].duration.tuplets[0].type = "startStop"
            else:
                run[0].duration.tuplets[0].type = "start"
                run[-1].duration.tuplets[0].type = "stop"
    m.streamStatus.tuplets = True


def _piece_tie(tie: str | None, k: int, n: int) -> str | None:
    """Tie type for piece k of n of one note whose overall tie state is `tie`."""
    if n == 1:
        return tie
    first = "start" if tie in (None, "start") else "continue"
    last = "stop" if tie in (None, "stop") else "continue"
    return first if k == 0 else last if k == n - 1 else "continue"


_CHORD_PCS: dict[str, dict[int, str]] = {}


def _chord_names(sym: str) -> dict[int, str]:
    """pitch class -> spelled name for the tones of a chord symbol (cached)."""
    if sym not in _CHORD_PCS:
        cs = to_music21(sym)
        names = {}
        for p in getattr(cs, "pitches", []) or []:
            names.setdefault(p.pitchClass, p.name)
        # the minor third over a dominant chord is a blue note: spell it as a flat third
        if names and "7" in sym and "m" not in sym.replace("maj", ""):
            root = cs.root()
            if root is not None:
                b3 = root.transpose("m3")
                names.setdefault(b3.pitchClass, b3.name)
        _CHORD_PCS[sym] = names
    return _CHORD_PCS[sym]


def _as_name(midi: int, want: str | None, sharp: bool) -> pitch.Pitch:
    p = pitch.Pitch(midi)
    if want is not None:
        for cand in (p, p.getEnharmonic(), p.getLowerEnharmonic(), p.getHigherEnharmonic()):
            if cand.name == want:
                return cand
    if p.accidental is None or p.accidental.name == "natural":
        return p
    if sharp and p.accidental.name == "flat":
        return p.getEnharmonic()
    if not sharp and p.accidental.name == "sharp":
        return p.getEnharmonic()
    return p


def spell_line(qnotes, form, shift: int, chord_at) -> dict[int, pitch.Pitch]:
    """Spell each note the way a jazz transcriber would: chord tones as the chord spells
    them (F# over D7, Ab blue third over F7), other black keys by direction of the line
    (sharps going up, flats going down), flats when there is no direction."""
    seq = sorted(qnotes, key=lambda q: q.start)
    out = {}
    for k, q in enumerate(seq):
        midi = q.pitch + shift
        if midi % 12 in (0, 2, 4, 5, 7, 9, 11):
            out[id(q)] = pitch.Pitch(midi)
            continue
        names = _chord_names(chord_at(q.start))
        want = names.get(midi % 12)
        nxt = seq[k + 1] if k + 1 < len(seq) and seq[k + 1].start - q.end <= 1 else None
        sharp = nxt is not None and (nxt.pitch + shift) > midi and (nxt.pitch + shift) - midi <= 2
        out[id(q)] = _as_name(midi, want, sharp)
    return out


def _chord_offsets(n: int, bpb: int) -> list[F]:
    if n <= 1:
        return [F(0)]
    if n == 2 and bpb % 2 == 0:
        return [F(0), F(bpb, 2)]
    return [F(i * bpb, n) for i in range(n)]


def _prefers_flats(sym: str, key_name: str | None) -> bool:
    if "#" in sym[:2]:
        return False
    if key_name and "#" in key_name:
        return False
    return True


def build_score(
    qnotes,
    form,
    bar_numbers: list[int] | None = None,
    instrument_name: str = "trumpet",
    concert: bool = False,
    renumber: bool = False,
    title: str | None = None,
) -> stream.Score:
    bpb = form.beats_per_bar
    bars = [b for b in form.bars if bar_numbers is None or b.number in set(bar_numbers)]
    intv = "P1" if concert else written_interval_for(instrument_name)
    shift = _SEMITONES.get(intv, 0)

    score = stream.Score()
    score.metadata = metadata.Metadata()
    score.metadata.title = title or form.data.get("title", "Transcription")
    score.metadata.composer = form.data.get("artist", "")

    part = stream.Part()
    inst = instrument.Trumpet() if instrument_name == "trumpet" else instrument.Instrument()
    part.insert(0, inst)
    part.atSoundingPitch = concert

    bar_by_num = {b.number: b for b in form.bars}

    def chord_at(start) -> str:
        b = bar_by_num.get(int(F(start) // bpb) + 1)
        if b is None:
            return "N.C."
        offs = _chord_offsets(len(b.chords), bpb)
        pos = F(start) - (b.number - 1) * bpb
        sym = b.chords[0]
        for off, c in zip(offs, b.chords):
            if pos >= off:
                sym = c
        return transpose_symbol(sym, intv) if sym != "N.C." else sym

    spelled = spell_line(qnotes, form, shift, chord_at)

    notes_by_bar: dict[int, list] = {}
    for q in qnotes:
        notes_by_bar.setdefault(int(q.start // bpb) + 1, []).append(q)

    prev_label = None
    prev_m = None
    last_chord = None
    carry = None  # (pitch, remaining beats) for a note tied over the bar line
    for i, bar in enumerate(bars):
        m = stream.Measure(number=(i + 1) if renumber else bar.number)
        if i == 0:
            m.insert(0, meter.TimeSignature(f"{bpb}/4"))
            k = form.data.get("key")
            if k and form.data.get("key_signature", "auto") != "none":
                written_key = transpose_symbol(k.replace("-", "").replace("m", ""), intv)
                try:
                    m.insert(0, key.Key(written_key.replace("b", "-")))
                except Exception:
                    pass
            m.insert(0, tempo.MetronomeMark(number=round(form.bpm)))
            if bar.feel == "swing":
                m.insert(0, expressions.TextExpression("Swing"))
        new_section = bar.section != prev_label
        new_chorus = bar.chorus is not None and bar.bar_in_chorus == 1
        if new_section:
            m.insert(0, expressions.RehearsalMark(bar.section.capitalize()))
            prev_label = bar.section
        elif new_chorus:
            m.insert(0, expressions.RehearsalMark(f"Ch. {bar.chorus}"))
        if (new_section or new_chorus) and prev_m is not None:
            prev_m.rightBarline = m21bar.Barline("double")

        written_chords = [transpose_symbol(c, intv) if c != "N.C." else c for c in bar.chords]
        for off, sym in zip(_chord_offsets(len(written_chords), bpb), written_chords):
            if sym != last_chord or (off == 0 and (new_section or new_chorus)):
                m.insert(float(off), to_music21(sym))  # chord symbols only where the harmony changes
            last_chord = sym
        flats = _prefers_flats(written_chords[0], form.data.get("key"))

        bar_start = F((bar.number - 1) * bpb)
        bar_end = bar_start + bpb
        events = []  # (start_in_bar, length, Pitch or None, tie)
        if carry is not None:
            p, remaining = carry
            length = min(remaining, F(bpb))
            events.append((F(0), length, p, "stop" if remaining <= bpb else "continue"))
            carry = (p, remaining - length) if remaining > bpb else None
        for q in sorted(notes_by_bar.get(bar.number, []), key=lambda x: x.start):
            s = F(q.start) - bar_start
            if events and s < events[-1][0] + events[-1][1]:
                prev = events[-1]
                events[-1] = (prev[0], s - prev[0], prev[2], prev[3])
                if events[-1][1] <= 0:
                    events.pop()
            e = min(F(q.end), bar_end) - bar_start
            p = spelled.get(id(q)) or _spell(q.pitch + shift, flats)
            tie = "start" if F(q.end) > bar_end else None
            if tie:
                carry = (p, F(q.end) - bar_end)
            events.append((s, max(e - s, F(1, 12)), p, tie))

        cursor = F(0)
        for s, length, p, tie in events:
            if s > cursor:
                for off, ln in _pieces(cursor, s - cursor, bpb, kind="rest"):
                    m.insert(off, note.Rest(quarterLength=ln))
            parts = _pieces(s, length, bpb)
            for k, (off, ln) in enumerate(parts):
                n = note.Note(p, quarterLength=ln)
                t = _piece_tie(tie, k, len(parts))
                if t:
                    n.tie = m21tie.Tie(t)
                m.insert(off, n)
            cursor = s + length
        if cursor < bpb:
            for off, ln in _pieces(cursor, F(bpb) - cursor, bpb, kind="rest"):
                m.insert(off, note.Rest(quarterLength=ln))
        _bracket_tuplets(m)
        if i % 4 == 0 and i > 0:
            m.insert(0, layout.SystemLayout(isNew=True))
        part.append(m)
        prev_m = m

    score.insert(0, part)
    rebeam(score)
    return score


_LEVELS = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4}


def _beam_group(notes: list) -> None:
    """Beam one beat's run of notes: level 1 across the whole run; deeper levels unbroken
    across adjacent notes that have them, with partial hooks for lone short notes."""
    levels = [_LEVELS.get(n.duration.type, 0) for n in notes]
    for n in notes:
        n.beams = beam.Beams()
    if len(notes) < 2:
        return
    for lvl in range(1, max(levels) + 1):
        for i, n in enumerate(notes):
            if levels[i] < lvl:
                continue
            left = i > 0 and levels[i - 1] >= lvl
            right = i < len(notes) - 1 and levels[i + 1] >= lvl
            if left and right:
                kind, direction = "continue", None
            elif right:
                kind, direction = "start", None
            elif left:
                kind, direction = "stop", None
            else:
                kind, direction = "partial", ("right" if i == 0 else "left")
            b = beam.Beam(type=kind, number=lvl)
            if direction:
                b.direction = direction
            n.beams.beamsList.append(b)


def rebeam(score: stream.Score, beat: float = 1.0) -> None:
    """Jazz-book beaming: notes shorter than a quarter are beamed per beat; rests break beams."""
    for m in score.recurse().getElementsByClass(stream.Measure):
        run: list = []
        run_beat = None

        def flush():
            nonlocal run
            _beam_group(run)
            run = []

        for el in m.notesAndRests:
            if el.duration.isGrace:
                continue
            off = float(el.offset)
            end = off + float(el.quarterLength)
            bt = int(off // beat)
            short = isinstance(el, note.Note) and el.duration.type in _LEVELS
            fits = end <= (bt + 1) * beat + 1e-6
            if not short or not fits or bt != run_beat:
                flush()
                run_beat = bt
            if short and fits:
                run.append(el)
            elif isinstance(el, note.Note):
                el.beams = beam.Beams()
        flush()
        m.streamStatus.beams = True  # stop music21 from re-beaming on export


_TYPE_LEN = {"whole": F(4), "half": F(2), "quarter": F(1), "eighth": F(1, 2), "16th": F(1, 4),
             "32nd": F(1, 8), "64th": F(1, 16), "128th": F(1, 32)}


def validate_musicxml(path: Path, beats_per_bar: int = 4) -> list[str]:
    """Check the file the way MuseScore does: every note's written type (+dots, +tuplet ratio)
    must equal its duration, each measure must add up to a full bar, and tuplets must not nest."""
    import re

    x = Path(path).read_text()
    div = int(re.search(r"<divisions>(\d+)", x).group(1))
    problems = []
    for m in re.finditer(r'<measure[^>]*number="(\d+)"[^>]*>(.*?)</measure>', x, re.S):
        num, body = m.group(1), m.group(2)
        total, depth = F(0), 0
        for n in re.findall(r"<note\b[^>]*>.*?</note>", body, re.S):
            if "<chord/>" in n or "<grace" in n:
                continue
            d = F(int(re.search(r"<duration>(\d+)", n).group(1)), div)
            t = re.search(r"<type>(\w+)", n)
            if not t and re.search(r'<rest[^>]*measure="yes"', n):
                total += d  # whole-bar rest: valid without a type
                continue
            if not t:
                problems.append(f"bar {num}: note without a type")
                continue
            v = _TYPE_LEN[t.group(1)] * (2 - F(1, 2 ** len(re.findall(r"<dot\b[^>]*/>", n))))
            tm = re.search(r"<actual-notes>(\d+)</actual-notes>\s*<normal-notes>(\d+)", n)
            if tm:
                v = v * int(tm.group(2)) / int(tm.group(1))
            if v != d:
                problems.append(f"bar {num}: {t.group(1)} written but lasts {d} beats")
            total += v
            for kind in re.findall(r'<tuplet[^>]*type="(\w+)"', n):
                depth += 1 if kind == "start" else -1
                if depth > 1:
                    problems.append(f"bar {num}: nested tuplets")
        if total != beats_per_bar:
            problems.append(f"bar {num}: adds up to {total} beats, not {beats_per_bar}")
        if depth != 0:
            problems.append(f"bar {num}: unclosed tuplet")
    return problems


def write_musicxml(score: stream.Score, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(out_path))
    return out_path


def write_midi(score: stream.Score, out_path: Path) -> Path:
    out_path = Path(out_path)
    score.write("midi", fp=str(out_path))
    return out_path
