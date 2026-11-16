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
from .analysis import bar_chord_offsets, chart_timeline, chord_spans, tune_sections
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


def candidates(tune: dict, lick_rows: list[dict], settings: dict) -> dict[int, list[dict]]:
    """{bar index (0-based): [candidate, ...]} sorted best first."""
    bpb = tune["ts"][0] if tune.get("ts") else 4
    tune_spans = chart_timeline(tune["bars"], bpb)
    n_bars = len(tune["bars"])
    out: dict[int, list[dict]] = {}
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
                                          "function": lk["function"], "label": lick_label(lk)})
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
              only_ids: set[int] | None = None, seed: int | None = None) -> dict:
    """Walk through the tune placing a lick at each spot where one fits.

    The lick at each spot is drawn at random, weighted by its score (favorites and licks you are
    learning come up more often), preferring licks not used yet in this tune. seed=None gives a
    new draw every time. Returns placements + all candidates for swapping."""
    import random

    rng = random.Random(seed)
    rows = [r for r in lick_rows if only_ids is None or r["id"] in only_ids]
    cands = candidates(tune, rows, settings)
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
        lk = lick_lookup(p["lick_id"])
        plan = L.transposition_plan(lk, p["target_pc"], settings)
        base = L.load_lick_score(lk["_path"])
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
        lk = lick_lookup(p["lick_id"])
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
