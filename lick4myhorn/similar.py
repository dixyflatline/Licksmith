"""Find licks that are the same line in another key (or nearly).

A lick is reduced to its melodic shape: the interval from each note to the next and the time
between their starts. Shapes ignore key and octave, so a transposed copy has the same shape.

  same    - identical shape (a pure transposition, maybe in another octave)
  inside  - one lick's whole shape appears inside the other (e.g. a 1-bar ii-V inside a 2-bar line)
  similar - shapes mostly agree (a few notes or rhythms changed)
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .licks import PC_NAMES_MAJOR

_INTERVAL_NAMES = {0: "same key", 1: "a half step up", 2: "a whole step up", 3: "a minor 3rd up",
                   4: "a major 3rd up", 5: "a 4th up", 6: "a tritone away", 7: "a 5th up",
                   8: "a minor 6th up", 9: "a major 6th up", 10: "a whole step down", 11: "a half step down"}


def shape(data: dict) -> list[tuple[int, int]]:
    notes = sorted(data["notes"])
    out = []
    for a, b in zip(notes, notes[1:]):
        out.append((b[2] - a[2], round((b[0] - a[0]) * 12)))  # interval, onset gap in 1/12 beats
    return out


def _phase(data: dict) -> int:
    n = data["notes"]
    return round((min(x[0] for x in n) % 1) * 12) if n else 0


def compare(a: dict, b: dict) -> dict | None:
    """Relation of lick b to lick a (dicts with 'data'), or None."""
    sa, sb = shape(a["data"]), shape(b["data"])
    if len(sa) < 3 or len(sb) < 3:
        return None
    na, nb = sorted(a["data"]["notes"]), sorted(b["data"]["notes"])
    if sa == sb:
        kind, ratio, offset = "same", 1.0, 0
    else:
        short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
        m = SequenceMatcher(None, long_, short, autojunk=False).find_longest_match(0, len(long_), 0, len(short))
        if m.size == len(short) and len(short) >= 4:
            kind, ratio = "inside", len(short) / len(long_)
            # align the first note of the shorter lick with its place in the longer one
            offset = m.a if short is sb else -m.a
        else:
            ratio = SequenceMatcher(None, sa, sb, autojunk=False).ratio()
            if ratio < 0.8:
                return None
            kind, offset = "similar", 0
    ia = offset if offset > 0 else 0
    ib = -offset if offset < 0 else 0
    semis = (nb[ib][2] - na[ia][2]) if kind != "similar" else (b["tonic_pc"] - a["tonic_pc"])
    return {"kind": kind, "ratio": round(ratio, 2), "semis": semis % 12,
            "octaves": (nb[ib][2] - na[ia][2] - semis % 12) // 12 if kind != "similar" else 0,
            "rhythm_shift": kind == "same" and _phase(a["data"]) != _phase(b["data"])}


def describe(rel: dict, other_label: str) -> str:
    where = _INTERVAL_NAMES[rel["semis"]]
    if rel["kind"] == "same":
        s = f"Same lick as {other_label}" + ("" if rel["semis"] == 0 else f", {where}")
        if rel["octaves"] and rel["semis"] == 0:
            s += ", another octave"
        if rel["rhythm_shift"]:
            s += " (starts on a different part of the beat)"
        return s
    if rel["kind"] == "inside":
        return f"Overlaps {other_label} — one is contained in the other" + ("" if rel["semis"] == 0 else f" ({where})")
    return f"Similar to {other_label} ({round(rel['ratio'] * 100)}% the same shape)"


def find_related(target: dict, others: list[dict], limit: int = 5) -> list[dict]:
    out = []
    for o in others:
        if o.get("id") is not None and o.get("id") == target.get("id"):
            continue
        r = compare(o, target)
        if r:
            out.append({**r, "lick_id": o.get("id"), "number": o.get("number"), "label": o.get("label") or f"Lick {o.get('number')}"})
    order = {"same": 0, "inside": 1, "similar": 2}
    out.sort(key=lambda r: (order[r["kind"]], -r["ratio"]))
    return out[:limit]


def key_name(pc: int) -> str:
    return PC_NAMES_MAJOR[pc % 12]
