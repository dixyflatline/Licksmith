"""Fit licks into a tune: find every place in an iReal chart where a lick's harmony fits, pick
licks (favorites and licks you are learning first), and write a practice MusicXML of the whole tune
with the chosen licks transposed and octave-fitted, and empty bars left for your own playing.

A lick fits at bar b when, after transposing it, every chord in the lick matches the tune's chord
over the same beats (same root, same chord family: major / minor / dominant / half-diminished /
diminished), up to the beat where the lick's last note ends.
"""
from __future__ import annotations

import re

import copy
import math

from music21 import expressions, harmony, layout, metadata, meter, note, stream, tempo

from . import licks as L
from .analysis import bar_chord_offsets, chart_timeline, chord_spans, parse_chord, tune_sections
from .chords import to_music21
from .srs import state_of


def _lick_profile(lick: dict) -> tuple[list[dict], float]:
    d = lick["data"]
    spans = chord_spans(d["chords"], d["length"])
    end = max((o + du for o, du, _ in d["notes"]), default=0.0)
    beat = 4.0 / d["ts"][1]
    end = math.ceil(end / beat - 1e-6) * beat
    return spans, end


def _chord_at(spans, t):
    cur = None
    for s in spans:
        if s["start"] <= t + 1e-6 < s["end"] + 1e-9 or (s["start"] <= t + 1e-6 and s is spans[-1]):
            cur = s
    return cur


def fits_at(lick_spans, lick_end, tune_spans, start_beat) -> int | None:
    """Semitone shift (0-11) if the lick fits at start_beat, else None."""
    if not lick_spans or lick_spans[0]["start"] > 1e-6:
        return None
    shift = None
    for i, ls in enumerate(lick_spans):
        a = start_beat + ls["start"]
        if ls["start"] >= lick_end - 1e-6:
            break
        b = start_beat + min(ls["end"], lick_end)
        ts = _chord_at(tune_spans, a)
        if ts is None or ts["q"] != ls["q"]:
            return None
        if abs(ts["start"] - a) > 1e-6 and i > 0:
            return None  # the tune's chord must change where the lick's chord changes
        if ts["end"] < b - 1e-6:
            return None  # the tune changes chord while the lick still holds this one
        s = (ts["pc"] - ls["pc"]) % 12
        if shift is None:
            shift = s
        elif s != shift:
            return None
    return shift


def _minor_map(lk: dict):
    """For a major ii-V(-I) lick, the spans and a function (offset, midi) -> midi that gives its minor ii-V(-i) version, or None
    when the lick is not that shape. ii becomes half-diminished (the 5th drops to b5), V gets b9 and b13, I becomes minor
    (the 3rd drops to b3 and a major 7th to b7)."""
    d = lk["data"]
    spans = chord_spans(d["chords"], d["length"])
    qs = [s["q"] for s in spans]
    if qs not in (["min", "dom"], ["min", "dom", "maj"]):
        return None
    if (spans[1]["pc"] - spans[0]["pc"]) % 12 != 5 or (len(spans) == 3 and (spans[2]["pc"] - spans[1]["pc"]) % 12 != 5):
        return None
    lower = {0: {7}, 1: {2, 9}, 2: {4, 11}}   # scale degrees (semitones above the chord root) that drop a half step

    def fn(off: float, midi: int) -> int:
        for i, s in enumerate(spans):
            if s["start"] - 1e-6 <= off < s["end"] - 1e-6 or (i == len(spans) - 1 and off >= s["start"] - 1e-6):
                return midi - 1 if (midi - s["pc"]) % 12 in lower[i] else midi
        return midi
    return spans, fn


def minor_variant(lk: dict) -> dict | None:
    """The minor-key version of a major ii-V(-I) lick (same rhythm, a few notes lowered), or None."""
    mm = _minor_map(lk)
    if mm is None:
        return None
    spans, fn = mm
    v = copy.deepcopy(lk)
    d = v["data"]
    d["notes"] = [[o, du, fn(o, m)] for o, du, m in d["notes"]]
    d["graces"] = [[o, fn(o, m)] for o, m in d.get("graces", [])]
    root = lambda sym: re.match(r"^[A-G][b#]?", sym).group(0)
    new = []
    for i, s in enumerate(spans):
        new.append([s["start"], root(s["sym"]) + ["m7b5", "7b9", "m6"][i]])
    d["chords"] = new
    v["variant"] = "minor"
    v["mode"] = "minor"
    v["tonic_name"] = L.key_name(v["tonic_pc"], "minor")
    v["function"] = "Minor II-V-I" if len(spans) == 3 else "Minor II-V"
    return v


def _alter_score_minor(base, lk: dict) -> None:
    """The same change as minor_variant, on the notated lick (music21 score, concert pitch)."""
    mm = _minor_map(lk)
    if mm is None:
        return
    _, fn = mm
    for n in base.recurse().getElementsByClass(note.Note):
        off = float(n.getOffsetInHierarchy(base))
        new = fn(off, n.pitch.midi)
        if new != n.pitch.midi:
            n.pitch.midi = new
            n.pitch.spelledWithFlats = True


def resolve(lick_lookup, p: dict) -> dict:
    """The lick of a placement: the original, or its minor version when the placement says so."""
    lk = lick_lookup(p["lick_id"])
    if p.get("variant") == "minor":
        return minor_variant(lk) or lk
    return lk


def candidates(tune: dict, lick_rows: list[dict], settings: dict, variants: bool = True) -> dict[int, list[dict]]:
    """{bar index (0-based): [candidate, ...]} sorted best first."""
    bpb = tune["ts"][0] if tune.get("ts") else 4
    tune_spans = chart_timeline(tune["bars"], bpb)
    n_bars = len(tune["bars"])
    out: dict[int, list[dict]] = {}
    if variants:
        lick_rows = lick_rows + [v for v in (minor_variant(r) for r in lick_rows) if v]
    for lk in lick_rows:
        if lk["data"]["ts"][0] != bpb:
            continue
        spans, end = _lick_profile(lk)
        if not spans or end <= 0:
            continue
        lick_bars = max(1, math.ceil(end / bpb - 1e-6))
        for b in range(n_bars - lick_bars + 1):
            shift = fits_at(spans, end, tune_spans, b * bpb)
            if shift is None:
                continue
            target = (lk["tonic_pc"] + shift) % 12
            plan = L.transposition_plan(lk, target, settings)
            out.setdefault(b, []).append({"lick_id": lk["id"], "number": lk["number"], "bar": b + 1,
                                          "bars": lick_bars, "target_pc": target, "key": plan["label"],
                                          "fits_range": plan["fits"], "score": _score(lk, plan, lick_bars),
                                          "function": lk["function"], "variant": lk.get("variant"),
                                          "label": lick_label(lk) + (" (minor version)" if lk.get("variant") else "")})
    for b in out:
        out[b].sort(key=lambda c: -c["score"])
    return out


def _score(lk: dict, plan: dict, bars: int) -> float:
    s = 1.0 + 0.25 * bars
    if lk.get("favorite"):
        s += 3
    st = state_of(lk)
    s += {"lapsed": 2, "learning": 1.5, "reviewing": 1.0, "new": 0.5, "known": 0.25}.get(st, 0)
    if not plan["fits"]:
        s -= 2
    return round(s, 2)


def default_name(lk: dict) -> str:
    """The descriptive name of a lick when you have not typed one: "Chet Baker Autumn Leaves"."""
    return " ".join(x.strip() for x in (lk.get("artist") or "", lk.get("tune") or "") if x and x.strip())


def lick_label(lk: dict) -> str:
    """What a lick is called everywhere: the name you gave it, else artist + tune. Number, bars, function and key are
    not part of the name; they are columns and tags. (An old automatic name like "Lick 43" counts as no name.)"""
    name = (lk.get("name") or "").strip()
    if name and not re.fullmatch(r"Lick\s*\d+", name, re.I):
        return name
    return default_name(lk) or f"Lick {lk.get('number', '')}".strip()


def plan_tune(tune: dict, lick_rows: list[dict], settings: dict, gap_bars: int = 1,
              only_ids: set[int] | None = None, seed: int | None = None, variants: bool = True) -> dict:
    """Walk through the tune placing a lick at each spot where one fits.

    The lick at each spot is drawn at random, weighted by its score (favorites and licks you are
    learning come up more often), preferring licks not used yet in this tune. seed=None gives a
    new draw every time. Returns placements + all candidates for swapping."""
    import random

    rng = random.Random(seed)
    rows = [r for r in lick_rows if only_ids is None or r["id"] in only_ids]
    cands = candidates(tune, rows, settings, variants)
    placements = []
    used: dict[int, int] = {}
    b = 0
    n = len(tune["bars"])
    while b < n:
        cs = cands.get(b)
        if not cs:
            b += 1
            continue
        fewest = min(used.get(c["lick_id"], 0) for c in cs)
        pool = [c for c in cs if used.get(c["lick_id"], 0) == fewest]
        best = rng.choices(pool, weights=[max(0.1, c["score"]) ** 2 for c in pool])[0]
        placements.append(dict(best))
        used[best["lick_id"]] = used.get(best["lick_id"], 0) + 1
        b += best["bars"] + gap_bars
    return {"placements": placements, "candidates": {str(k): v for k, v in cands.items()},
            "sections": tune_sections(tune["bars"], tune["ts"][0] if tune.get("ts") else 4)}


def practice_score(tune: dict, placements: list[dict], lick_lookup, settings: dict,
                   bpm: int | None = None) -> stream.Score:
    """Whole-tune concert-pitch score with the placed licks; other bars are whole-bar rests."""
    bpb = tune["ts"][0] if tune.get("ts") else 4
    den = tune["ts"][1] if tune.get("ts") else 4
    by_bar: dict[int, tuple[dict, int, list]] = {}
    for p in sorted(placements, key=lambda p: p["bar"]):
        lk = resolve(lick_lookup, p)
        plan = L.transposition_plan(lk, p["target_pc"], settings)
        base = L.load_lick_score(lk["_path"])
        if p.get("variant") == "minor":
            _alter_score_minor(base, lick_lookup(p["lick_id"]))
        t = L.transpose_score(base, plan["interval"], plan["octaves"])
        ms = list(t.parts[0].getElementsByClass(stream.Measure))
        for k in range(p["bars"]):
            if k < len(ms):
                by_bar[p["bar"] - 1 + k] = (lk, k, ms[k], p)
    part = stream.Part()
    last = None
    for i, chords in enumerate(tune["bars"]):
        m = stream.Measure(number=i + 1)
        if i == 0:
            m.insert(0, meter.TimeSignature(f"{bpb}/{den}"))
            if bpm:
                m.insert(0, tempo.MetronomeMark(number=bpm))
        if i and i % 4 == 0:
            m.insert(0, layout.SystemLayout(isNew=True))
        cs = [c for c in chords if c]
        for o, c in zip(bar_chord_offsets(len(cs), bpb), cs):
            if c != last or o == 0 and i in by_bar and by_bar[i][1] == 0:
                m.insert(o, to_music21(c))
            last = c
        if i in by_bar:
            lk, k, lm, p = by_bar[i]
            if k == 0:
                te = expressions.TextExpression(f"#{lk['number']}" + (f", {lk['artist']}" if lk.get("artist") else ""))
                te.placement = "below"
                te.style.fontSize = 8
                m.insert(0, te)
            for el in lm.recurse().getElementsByClass([note.Note, note.Rest, note.Unpitched]):
                if el.duration.isGrace:
                    continue
                m.insert(el.getOffsetInHierarchy(lm), copy.deepcopy(el))
        else:
            r = note.Rest(quarterLength=bpb * 4 / den)
            r.fullMeasure = True
            m.insert(0, r)
        part.append(m)
    sc = stream.Score()
    sc.metadata = metadata.Metadata()
    sc.metadata.title = tune["title"]
    sc.metadata.movementName = "Practice: licks fitted to the changes"
    sc.metadata.composer = tune.get("composer", "")
    sc.insert(0, part)
    return sc


def play_data(tune: dict, placements: list[dict], lick_lookup, settings: dict) -> dict:
    """Notes + chords (concert) for browser playback of the practice plan."""
    bpb = tune["ts"][0] if tune.get("ts") else 4
    notes, chords, graces = [], [], []
    for p in placements:
        lk = resolve(lick_lookup, p)
        plan = L.transposition_plan(lk, p["target_pc"], settings)
        td = L.transposed_data(lk, plan)
        base = (p["bar"] - 1) * bpb
        notes += [[o + base, d, m] for o, d, m in td["notes"]]
        graces += [[o + base, m] for o, m in td.get("graces", [])]
    for i, cs in enumerate(tune["bars"]):
        cs = [c for c in cs if c]
        for o, c in zip(bar_chord_offsets(len(cs), bpb), cs):
            chords.append([i * bpb + o, c])
    return {"ts": tune.get("ts", [4, 4]), "bars": len(tune["bars"]), "length": len(tune["bars"]) * bpb,
            "notes": sorted(notes), "graces": sorted(graces), "chords": chords}
