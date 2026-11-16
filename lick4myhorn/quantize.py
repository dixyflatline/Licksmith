"""Turn performed notes (seconds) into written rhythm (beats), the way a transcriber would.

For each beat we try a few rhythm grids and pick the one that fits the onsets in that beat
best, with a small penalty for busier grids. In swing feel, an off-beat at about 2/3 of the
beat is written as a plain eighth note (swung eighths), which is how the books notate it.

`simplicity` scales the penalty: higher = lean harder toward simple, book-like rhythms.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction as F

import numpy as np

from .grid import time_to_beat


@dataclass
class QNote:
    start: F        # beats from bar 1, beat 1 (global)
    end: F
    pitch: int      # concert MIDI pitch
    velocity: int = 80


# name -> (performed positions, written positions, penalty)
_OPTIONS_SWING = {
    "swing8": ([0, 2 / 3, 1], [F(0), F(1, 2), F(1)], 0.0),
    "straight8": ([0, 1 / 2, 1], [F(0), F(1, 2), F(1)], 0.004),
    "triplet": ([0, 1 / 3, 2 / 3, 1], [F(0), F(1, 3), F(2, 3), F(1)], 0.006),
    "swung16": ([0, 1 / 6, 1 / 2, 2 / 3, 1], [F(0), F(1, 4), F(1, 2), F(3, 4), F(1)], 0.008),
    "sixteenth": ([0, 1 / 4, 1 / 2, 3 / 4, 1], [F(0), F(1, 4), F(1, 2), F(3, 4), F(1)], 0.008),
}
_OPTIONS_STRAIGHT = {
    "straight8": ([0, 1 / 2, 1], [F(0), F(1, 2), F(1)], 0.0),
    "triplet": ([0, 1 / 3, 2 / 3, 1], [F(0), F(1, 3), F(2, 3), F(1)], 0.006),
    "sixteenth": ([0, 1 / 4, 1 / 2, 3 / 4, 1], [F(0), F(1, 4), F(1, 2), F(3, 4), F(1)], 0.004),
}


_FAMILY = {"triplet": "tri", "swung16": "six", "sixteenth": "six", "swing8": "eight", "straight8": "eight"}


# Extra cost (beat^2, per note, x simplicity) for writing a 3-note beat as 16ths instead of a triplet.
SIXTEENTH_PENALTY_3 = 0.012


def _best_option(fracs: list[float], options: dict, simplicity: float, only: set[str] | None = None,
                 sync_guard: bool = True):
    """Returns (name, written positions, cost) of the best-fitting grid for one beat."""
    best = None
    n = len(fracs)
    for name, (perf, written, pen) in options.items():
        if only is not None and _FAMILY[name] not in only:
            continue
        if _FAMILY[name] == "six":
            if n <= 2 and sync_guard:
                continue  # one or two notes in a beat: swung eighths or triplets, never 16th syncopation
            if n == 3:
                pen = pen + SIXTEENTH_PENALTY_3  # three notes: prefer a triplet unless 16ths fit clearly better
        perf_a = np.asarray(perf)
        idx = [int(np.argmin(np.abs(perf_a - f))) for f in fracs]
        err = sum((perf_a[i] - f) ** 2 for i, f in zip(idx, fracs))
        collisions = len(idx) - len(set(idx))
        cost = err + pen * simplicity * max(len(fracs), 1) + collisions * 0.2
        if best is None or cost < best[0]:
            best = (cost, name, [written[i] for i in idx])
    return best[1], best[2], best[0]


def _choose_grids(by_beat_fracs: dict[int, list[float]], swing_of_beat, bpb: int, simplicity: float,
                  consistency: float, sync_guard: bool = True) -> dict[int, str]:
    """Pick a grid per beat, then make busy beats in the same bar agree (all 16ths or all triplets)
    unless the minority beat fits clearly worse that way. `consistency` = how much extra error
    (in beat^2 per note) we accept to keep a bar uniform; 0 turns it off."""
    choice = {}
    for beat, fracs in by_beat_fracs.items():
        opts = _OPTIONS_SWING if swing_of_beat(beat) else _OPTIONS_STRAIGHT
        choice[beat] = _best_option(fracs, opts, simplicity, sync_guard=sync_guard)
    if consistency <= 0:
        return {b: c[0] for b, c in choice.items()}
    bars: dict[int, list[int]] = {}
    for beat in choice:
        bars.setdefault(beat // bpb, []).append(beat)
    for beats in bars.values():
        busy = [b for b in beats if len(by_beat_fracs[b]) >= 3]
        fams = [_FAMILY[choice[b][0]] for b in busy]
        n_tri, n_six = fams.count("tri"), fams.count("six")
        if not (n_tri and n_six):
            continue
        major = "six" if n_six >= n_tri else "tri"
        for b in busy:
            if _FAMILY[choice[b][0]] == major:
                continue
            opts = _OPTIONS_SWING if swing_of_beat(b) else _OPTIONS_STRAIGHT
            alt = _best_option(by_beat_fracs[b], opts, simplicity, only={major, "eight"}, sync_guard=sync_guard)
            if alt[2] - choice[b][2] <= consistency * len(by_beat_fracs[b]):
                choice[b] = alt
    return {b: c[0] for b, c in choice.items()}


def _snap(frac: float, option: str, swing: bool) -> F:
    """Performed position inside a beat -> written position on that beat's chosen grid."""
    perf, written, _ = (_OPTIONS_SWING if swing else _OPTIONS_STRAIGHT)[option]
    i = int(np.argmin(np.abs(np.asarray(perf) - frac)))
    return written[i]


def quantize_notes(notes: list[dict], grid: np.ndarray, form, simplicity: float = 1.0,
                   min_rest: F = F(1, 2), consistency: float = 0.006, sync_guard: bool = True) -> list[QNote]:
    """notes: [{"onset": s, "offset": s, "pitch": midi, "velocity": v}] (one instrument).
    Returns quantized notes inside bars marked transcribe=true, as a monophonic line.
    Gaps shorter than `min_rest` beats are closed (the earlier note is held)."""
    bpb = form.beats_per_bar
    wanted = {b.number for b in form.transcribed_bars()}
    feel_by_bar = {b.number: b.feel for b in form.bars}
    if not notes or not wanted:
        return []
    on_b = time_to_beat([n["onset"] for n in notes], grid)
    off_b = time_to_beat([n["offset"] for n in notes], grid)

    # group onsets by beat (an onset late in a beat may land on the next downbeat)
    by_beat: dict[int, list[int]] = {}
    for i, b in enumerate(on_b):
        bar = int(np.floor(b / bpb)) + 1
        if bar not in wanted and int(np.floor((b + 0.2) / bpb)) + 1 not in wanted:
            continue
        by_beat.setdefault(int(np.floor(b)), []).append(i)

    fracs_by_beat = {beat: [float(on_b[i] - beat) for i in idxs] for beat, idxs in by_beat.items()}
    swing_of = lambda bt: feel_by_bar.get(bt // bpb + 1, "swing") == "swing"  # noqa: E731
    grids = _choose_grids(fracs_by_beat, swing_of, bpb, simplicity, consistency, sync_guard)
    starts: dict[F, tuple[int, int]] = {}  # written start -> (note index, velocity)
    for beat, idxs in by_beat.items():
        written = [_snap(f, grids[beat], swing_of(beat)) for f in fracs_by_beat[beat]]
        for i, w in zip(idxs, written):
            s = F(beat) + w
            vel = int(notes[i].get("velocity", 80))
            if s in starts:
                # two notes landed on one spot (a fast run). Do not lose one: the earlier of the two steps back, or the later one
                # steps forward, to the free neighbouring slot of this beat's grid; only if both are taken is the quieter one dropped.
                j, vj = starts[s]
                step = F(1, 3) if _FAMILY[grids[beat]] == "tri" else F(1, 4)
                lo, hi = (j, i) if on_b[j] <= on_b[i] else (i, j)
                vlo, vhi = (vj, vel) if lo == j else (vel, vj)
                if s - step not in starts and s - step >= F(beat) and s - step >= 0:
                    starts[s - step] = (lo, vlo); starts[s] = (hi, vhi)
                    continue
                if s + step not in starts and s + step < F(beat) + 1:
                    starts[s + step] = (hi, vhi); starts[s] = (lo, vlo)
                    continue
                if vj >= vel:
                    continue  # keep the louder of two notes landing on the same spot
            starts[s] = (i, vel)

    ordered = sorted(starts.items())
    out: list[QNote] = []
    for k, (s, (i, vel)) in enumerate(ordered):
        bar = int(s // bpb) + 1
        if bar not in wanted:
            continue
        swing = feel_by_bar.get(bar, "swing") == "swing"
        eb = float(off_b[i])
        ebeat = int(np.floor(eb))
        default = "swing8" if swing_of(ebeat) else "straight8"
        e = F(ebeat) + _snap(eb - ebeat, grids.get(ebeat, default), swing_of(ebeat))
        nxt = ordered[k + 1][0] if k + 1 < len(ordered) else None
        if nxt is not None and (e > nxt or nxt - e < min_rest):
            e = nxt  # legato: no rests shorter than min_rest, as books write it
        if e <= s:
            step = F(1, 2) if swing else F(1, 4)
            e = min(s + step, nxt) if nxt is not None else s + step
        out.append(QNote(start=s, end=e, pitch=int(notes[i]["pitch"]), velocity=vel))
    return out
