"""Compare a transcription against a published one (the book) and count the fixes needed.

Both files are MusicXML at written pitch, with bar 1 = first bar of the solo.
Per bar, notes are matched in passes:
  match   - same pitch, same start (within tolerance)
  octave  - same start, same pitch class, different octave
  pitch   - same start, different pitch
  rhythm  - same pitch, start off by more than the tolerance but within half a beat
  missing - in the book only
  extra   - in the transcription only
"fixes" = octave + pitch + rhythm + missing + extra: each is one edit in MuseScore.
The book is a benchmark, not absolute truth; the detail CSV lists every disagreement
with bar and beat so it can be checked by ear.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from music21 import converter, note


@dataclass
class N:
    bar: int
    beat: float  # quarter notes from bar start
    midi: int

    def label(self) -> str:
        from music21 import pitch

        return pitch.Pitch(self.midi).nameWithOctave


def load_notes(path: Path, first_bar: int = 1) -> list[N]:
    s = converter.parse(str(path))
    part = s.parts[0]
    out: list[N] = []
    for i, m in enumerate(part.getElementsByClass("Measure")):
        bar = i + 1  # count measures, ignoring printed numbers / pickups
        if bar < first_bar:
            continue
        for el in m.recurse().notes:
            if not isinstance(el, note.Note) or el.duration.isGrace:
                continue
            if el.tie is not None and el.tie.type in ("stop", "continue"):
                continue
            out.append(N(bar - first_bar + 1, float(el.getOffsetInHierarchy(m)), el.pitch.midi))
    return out


KINDS = ["match", "octave", "pitch", "rhythm", "missing", "extra"]


def compare(book: list[N], trans: list[N], tol: float = 0.17, rhythm_tol: float = 0.5):
    rows = []
    bars = sorted({n.bar for n in book} | {n.bar for n in trans})
    for bar in bars:
        b = [n for n in book if n.bar == bar]
        t = [n for n in trans if n.bar == bar]
        used_b, used_t = set(), set()

        def take(pred, kind):
            for i, bn in enumerate(b):
                if i in used_b:
                    continue
                best = None
                for j, tn in enumerate(t):
                    if j in used_t or not pred(bn, tn):
                        continue
                    d = abs(bn.beat - tn.beat)
                    if best is None or d < best[0]:
                        best = (d, j)
                if best is not None:
                    used_b.add(i)
                    used_t.add(best[1])
                    rows.append((kind, bn, t[best[1]]))

        take(lambda x, y: x.midi == y.midi and abs(x.beat - y.beat) <= tol, "match")
        take(lambda x, y: x.midi % 12 == y.midi % 12 and abs(x.beat - y.beat) <= tol, "octave")
        take(lambda x, y: abs(x.beat - y.beat) <= tol and abs(x.midi - y.midi) <= 4, "pitch")
        take(lambda x, y: x.midi == y.midi and abs(x.beat - y.beat) <= rhythm_tol, "rhythm")
        for i, bn in enumerate(b):
            if i not in used_b:
                rows.append(("missing", bn, None))
        for j, tn in enumerate(t):
            if j not in used_t:
                rows.append(("extra", None, tn))
    return rows


def summarize(rows, chorus_len: int):
    per: dict[int, dict[str, int]] = {}
    for kind, bn, tn in rows:
        bar = (bn or tn).bar
        ch = (bar - 1) // chorus_len + 1 if chorus_len else 1
        d = per.setdefault(ch, {k: 0 for k in KINDS})
        d[kind] += 1
    for d in per.values():
        d["book_notes"] = d["match"] + d["octave"] + d["pitch"] + d["rhythm"] + d["missing"]
        d["pitch_fixes"] = d["octave"] + d["pitch"]
        d["fixes"] = d["octave"] + d["pitch"] + d["rhythm"] + d["missing"] + d["extra"]
        d["agreement"] = round(100.0 * d["match"] / d["book_notes"], 1) if d["book_notes"] else 0.0
    return dict(sorted(per.items()))


def verdict(fixes_per_chorus: float) -> str:
    if fixes_per_chorus <= 2:
        return "GO (<=2 fixes per chorus)"
    if fixes_per_chorus <= 10:
        return "USABLE WITH EDITING (3-10 fixes per chorus)"
    return "REJECT (>10 fixes per chorus)"


def run_bench(book_xml: Path, trans_xml: Path, chorus_len: int, out_dir: Path, engine: str = "engine",
              book_first_bar: int = 1, tol: float = 0.17) -> Path:
    book = load_notes(book_xml, book_first_bar)
    trans = load_notes(trans_xml)
    trans_bars = len(converter.parse(str(trans_xml)).parts[0].getElementsByClass("Measure"))
    n_bars = min(max((n.bar for n in book), default=0), trans_bars)
    book = [n for n in book if n.bar <= n_bars]
    trans = [n for n in trans if n.bar <= n_bars]
    rows = compare(book, trans, tol=tol)
    per = summarize(rows, chorus_len)

    out_dir.mkdir(parents=True, exist_ok=True)
    detail = out_dir / f"bench_{engine}_detail.csv"
    with detail.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "bar", "beat", "book", "transcription"])
        for kind, bn, tn in sorted(rows, key=lambda r: ((r[1] or r[2]).bar, (r[1] or r[2]).beat)):
            if kind == "match":
                continue
            ref = bn or tn
            w.writerow([kind, ref.bar, round(ref.beat + 1, 2), bn.label() if bn else "", tn.label() if tn else ""])

    totals = {k: sum(d[k] for d in per.values()) for k in KINDS + ["book_notes", "pitch_fixes", "fixes"]}
    n_ch = max(len(per), 1)
    fpc = totals["fixes"] / n_ch
    lines = [
        f"# Benchmark: {engine}",
        "",
        f"Book: `{Path(book_xml).name}` · Transcription: `{Path(trans_xml).name}` · "
        f"{n_bars} bars, {n_ch} choruses of {chorus_len}",
        "",
        f"**Fixes per chorus: {fpc:.1f} → {verdict(fpc)}**  ",
        f"Agreement with the book: {100.0 * totals['match'] / max(totals['book_notes'], 1):.1f}% of book notes",
        "",
        "| Chorus | Book notes | Match | Agreement % | Pitch | Octave | Rhythm | Missing | Extra | Fixes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ch, d in per.items():
        lines.append(
            f"| {ch} | {d['book_notes']} | {d['match']} | {d['agreement']} | {d['pitch']} | {d['octave']} | "
            f"{d['rhythm']} | {d['missing']} | {d['extra']} | {d['fixes']} |"
        )
    lines += [
        "",
        f"Every disagreement, with bar and beat: `{detail.name}`. The book simplifies some rhythms, "
        "so check rhythm and missing rows by ear before blaming the engine.",
    ]
    report = out_dir / f"bench_{engine}.md"
    report.write_text("\n".join(lines) + "\n")
    return report
