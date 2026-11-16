"""Auto-tagging of licks: harmonic function from chord symbols, melodic devices from the notes.

Everything works on concert pitch. Chords are plain text symbols ("Fm7", "Bb7b9", "Ebmaj7").
"""
from __future__ import annotations

import re

from .licks import NAME_TO_PC, key_name

# ----------------------------------------------------------------------------------- chords
_ROOT = re.compile(r"^([A-G])([b#]?)(.*?)(?:/([A-G][b#]?))?$")


def parse_chord(sym: str):
    """'Bbm7b5' -> (10, 'hdim', 'm7b5'). Returns None for N.C. or unparseable symbols."""
    if not sym or sym.startswith("N.C"):
        return None
    m = _ROOT.match(sym.strip())
    if not m:
        return None
    pc = NAME_TO_PC.get(m.group(1) + m.group(2), NAME_TO_PC.get(m.group(1)))
    rest = m.group(3) or ""
    r = rest.replace(" ", "")
    if re.match(r"^(m7b5|m7-5|ø|half-?dim|min7b5|mi7b5)", r):
        q = "hdim"
    elif re.match(r"^(dim|o|°)", r):
        q = "dim"
    elif re.match(r"^(mmaj|mM|minmaj|m\^|-\^)", r):
        q = "min"
    elif re.match(r"^(maj|M(?!i)|\^|Δ)", r):
        q = "maj"
    elif re.match(r"^(m|min|mi|-)(?!aj)", r):
        q = "min"
    elif re.match(r"^(7|9|11|13|alt|sus|b9|#9|\+7|aug7|7alt)", r):
        q = "dom"
    elif re.match(r"^(6|69|add|\+|aug|$|5|2)", r):
        q = "maj"
    else:
        q = "maj"
    if q == "maj" and re.match(r"^(\+|aug)$", r):
        q = "aug"
    return pc, q, rest


QUALITY_TONES = {  # chord tones (semitones above root) used for "is a chord tone"
    "maj": {0, 4, 7, 11, 9},
    "min": {0, 3, 7, 10, 9},
    "dom": {0, 4, 7, 10},
    "hdim": {0, 3, 6, 10},
    "dim": {0, 3, 6, 9},
    "aug": {0, 4, 8},
}
SCALES = {  # parent scale for "in the scale" checks
    "maj": {0, 2, 4, 5, 7, 9, 11},
    "min": {0, 2, 3, 5, 7, 9, 10},
    "dom": {0, 2, 4, 5, 7, 9, 10},
    "hdim": {0, 1, 3, 5, 6, 8, 10},
    "dim": {0, 2, 3, 5, 6, 8, 9, 11},
    "aug": {0, 2, 4, 6, 8, 10},
}
DEGREE = {0: "root", 1: "b9", 2: "9", 3: "#9", 4: "3rd", 5: "11", 6: "#11", 7: "5th", 8: "b13",
          9: "13", 10: "b7", 11: "maj7"}


def degree_name(rel: int, q: str) -> str:
    rel %= 12
    if q in ("min", "hdim", "dim") and rel == 3:
        return "b3"
    if q in ("hdim", "dim") and rel == 6:
        return "b5"
    if q in ("maj", "min") and rel == 9:
        return "6th"
    if q == "dim" and rel == 9:
        return "bb7"
    return DEGREE[rel]


def chord_spans(chords: list, length: float) -> list[dict]:
    """[[off, sym], ...] -> merged spans with durations; unparseable chords are skipped."""
    spans = []
    pts = sorted(chords, key=lambda c: c[0])
    for i, (off, sym) in enumerate(pts):
        end = pts[i + 1][0] if i + 1 < len(pts) else length
        pc = parse_chord(sym)
        if pc is None:
            continue
        if spans and spans[-1]["pc"] == pc[0] and spans[-1]["q"] == pc[1]:
            spans[-1]["end"] = end
            continue
        spans.append({"start": off, "end": end, "pc": pc[0], "q": pc[1], "sym": sym})
    return spans


# function name, chord pattern [(interval from first root, quality set)], tonic offset from first root, mode
_PATTERNS = [
    ("Turnaround", [(0, {"maj"}), (9, {"dom", "min"}), (2, {"min"}), (7, {"dom"})], 0, "major"),
    ("Turnaround", [(0, {"min"}), (5, {"dom"}), (10, {"min"}), (3, {"dom"})], 8, "major"),  # iii VI ii V
    ("Major II-V-I", [(0, {"min"}), (5, {"dom"}), (10, {"maj"})], 10, "major"),
    ("Minor II-V-I", [(0, {"hdim"}), (5, {"dom"}), (10, {"min"})], 10, "minor"),
    ("Minor II-V-I", [(0, {"min"}), (5, {"dom"}), (10, {"min"})], 10, "minor"),
    ("II-subV-I", [(0, {"min"}), (11, {"dom"}), (10, {"maj", "min"})], 10, "major"),
    ("Backdoor II-V", [(0, {"min"}), (5, {"dom"}), (7, {"maj"})], 7, "major"),
    ("Minor II-V", [(0, {"hdim"}), (5, {"dom"})], 10, "minor"),
    ("Major II-V", [(0, {"min"}), (5, {"dom"})], 10, "major"),
    ("V-I", [(0, {"dom"}), (5, {"maj"})], 5, "major"),
    ("V-i", [(0, {"dom"}), (5, {"min"})], 5, "minor"),
    ("Dominant cycle", [(0, {"dom"}), (5, {"dom"})], 5, "dominant"),
]
FUNCTION_ORDER = [p[0] for p in _PATTERNS] + ["Two major chords", "Blues", "Dominant", "Major", "Minor", "Half-diminished",
                                              "Diminished", "Other"]
FUNCTIONS = list(dict.fromkeys(FUNCTION_ORDER))


def harmonic_functions(spans: list[dict], bar_ql: float = 4.0) -> list[dict]:
    """All pattern matches found in the chord spans, most specific first.

    "Two major chords": the same major chord (Cmaj7 then Cmaj7) over two or more bars."""
    found = []
    for sp in spans:
        if sp["q"] == "maj" and sp["end"] - sp["start"] >= 2 * bar_ql - 1e-6:
            found.append({"function": "Two major chords", "tonic_pc": sp["pc"], "mode": "major",
                          "start": sp["start"], "end": sp["end"]})
    for name, pat, tonic_off, mode in _PATTERNS:
        for i in range(len(spans) - len(pat) + 1):
            r0 = spans[i]["pc"]
            if all(((spans[i + k]["pc"] - r0) % 12 == iv and spans[i + k]["q"] in qs)
                   for k, (iv, qs) in enumerate(pat)):
                found.append({"function": name, "tonic_pc": (r0 + tonic_off) % 12, "mode": mode,
                              "start": spans[i]["start"], "end": spans[i + len(pat) - 1]["end"]})
    return found


# ----------------------------------------------------------------------------------- devices
def _chord_at(spans, t):
    cur = None
    for s in spans:
        if s["start"] <= t + 1e-6:
            cur = s
    return cur


def melodic_devices(notes: list, spans: list[dict], beat: float = 1.0) -> dict:
    """Detect melodic devices. Returns {tag: count}."""
    tags: dict[str, int] = {}

    def add(t, k=1):
        tags[t] = tags.get(t, 0) + k

    ns = [n for n in notes if n[1] > 0]
    mids = [n[2] for n in ns]
    on_beat = [abs(n[0] / beat - round(n[0] / beat)) < 1e-3 for n in ns]
    ch = [_chord_at(spans, n[0]) for n in ns]

    def is_ct(i):
        c = ch[i]
        return c is not None and (mids[i] - c["pc"]) % 12 in QUALITY_TONES[c["q"]]

    # enclosure: two notes on either side (1-2 semitones) then the target chord tone
    for i in range(len(ns) - 2):
        a, b, t = mids[i] - mids[i + 2], mids[i + 1] - mids[i + 2], mids[i + 2]
        if {1, 2} & {abs(a)} and {1, 2} & {abs(b)} and a * b < 0 and is_ct(i + 2):
            add("Enclosure")
    # chromatic: approach into chord tone on beat; runs of 3+ semitone steps
    run = 1
    for i in range(1, len(ns)):
        step = mids[i] - mids[i - 1]
        if abs(step) == 1 and (i < 2 or mids[i - 1] - mids[i - 2] == step):
            run += 1
        else:
            if run >= 4:
                add("Chromatic run")
            run = 2 if abs(step) == 1 else 1
        if abs(step) == 1 and on_beat[i] and is_ct(i) and ch[i] is not None and \
                (mids[i - 1] - ch[i]["pc"]) % 12 not in SCALES[ch[i]["q"]]:
            add("Chromatic approach")
    if run >= 4:
        add("Chromatic run")
    # bebop scale: 6+ stepwise notes one direction including a passing tone outside the scale
    i = 0
    while i < len(ns) - 1:
        j = i
        d = 0
        while j + 1 < len(ns):
            st = mids[j + 1] - mids[j]
            if abs(st) in (1, 2) and (d == 0 or (st > 0) == (d > 0)):
                d = st
                j += 1
            else:
                break
        if j - i + 1 >= 6:
            outside = any(ch[k] is not None and (mids[k] - ch[k]["pc"]) % 12 not in SCALES[ch[k]["q"]]
                          for k in range(i, j + 1))
            add("Bebop scale" if outside else "Scale run")
        i = max(j, i + 1)
    # arpeggio: 4+ consecutive chord tones moving by 3rds/4ths in one direction
    i = 0
    while i < len(ns) - 3:
        j = i
        while j + 1 < len(ns) and 3 <= abs(mids[j + 1] - mids[j]) <= 5 and \
                (j == i or (mids[j + 1] - mids[j] > 0) == (mids[j] - mids[j - 1] > 0)) and is_ct(j + 1) and is_ct(j):
            j += 1
        if j - i + 1 >= 4:
            top = max((mids[k] - ch[k]["pc"]) % 12 for k in range(i, j + 1) if ch[k])
            add("Arpeggio")
            if any(ch[k] and (mids[k] - ch[k]["pc"]) % 12 in (2, 9, 5, 6) for k in range(i, j + 1)):
                add("Upper-structure arpeggio")
            i = j
        i += 1
    # digital patterns 1-2-3-5 / 1-2-b3-5 / 5-3-2-1 starting on a beat
    for i in range(len(ns) - 3):
        if not on_beat[i] or ch[i] is None:
            continue
        rel = [(mids[i + k] - mids[i]) for k in range(4)]
        root_rel = (mids[i] - ch[i]["pc"]) % 12
        if rel in ([0, 2, 4, 7], [0, 2, 3, 7]) and root_rel == 0:
            add("1-2-3-5 pattern")
        elif rel in ([0, -3, -5, -7], [0, -4, -5, -7]) and root_rel == 7:
            add("5-3-2-1 pattern")
    # guide tones: 7th resolving down by step to the 3rd of the next chord
    for i in range(1, len(ns)):
        a, b = ch[i - 1], ch[i]
        if a is None or b is None or a is b:
            continue
        if (mids[i - 1] - a["pc"]) % 12 in (10, 11) and (mids[i] - b["pc"]) % 12 in (3, 4) and \
                mids[i - 1] - mids[i] in (1, 2):
            add("Guide-tone resolution")
    # pentatonic: 5+ notes from one pentatonic scale with no semitones
    for i in range(len(ns) - 4):
        seg = mids[i:i + 5]
        if any(abs(seg[k + 1] - seg[k]) in (0, 1) for k in range(4)):
            continue
        pcs = {m % 12 for m in seg}
        if len(pcs) >= 4 and any(pcs <= {(r + x) % 12 for x in (0, 2, 4, 7, 9)} for r in range(12)):
            add("Pentatonic")
            break
    # blue note: b3 over a dominant or major chord
    for i in range(len(ns)):
        c = ch[i]
        if c and c["q"] in ("dom", "maj") and (mids[i] - c["pc"]) % 12 == 3:
            add("Blue note")
    # altered tensions over dominants
    for i in range(len(ns)):
        c = ch[i]
        if c and c["q"] == "dom" and (mids[i] - c["pc"]) % 12 in (1, 3, 8):
            add("Altered tensions")
            break
    # leaps
    leaps = sum(1 for i in range(1, len(ns)) if abs(mids[i] - mids[i - 1]) >= 7)
    if leaps >= 2:
        add("Wide leaps", leaps)
    return tags


def rhythm_tags(data: dict, beat: float = 1.0) -> list[str]:
    notes = data["notes"]
    out = []
    if data.get("triplet_notes", 0) >= 3:
        out.append("Triplets")
    durs = [n[1] for n in notes]
    if sum(1 for d in durs if abs(d - 0.25) < 1e-3) >= 4:
        out.append("16ths")
    if notes and sum(1 for d in durs if abs(d - 0.5) < 1e-3) >= 0.6 * len(notes):
        out.append("Eighth-note line")
    if notes:
        first = notes[0][0] / beat
        if abs(first - round(first)) > 1e-3:
            out.append("Starts off the beat")
        elif first >= 1:
            out.append("Starts after beat 1")
    # anticipation: a note starting just before a chord change and tied/held across it
    return out


# ----------------------------------------------------------------------------------- comments
_NORMAL = {
    "majoriiv": "Major II-V", "iiv": "Major II-V", "majoriivi": "Major II-V-I", "iivi": "Major II-V-I",
    "minoriiv": "Minor II-V", "minoriivi": "Minor II-V-I", "blues": "Blues", "turnaround": "Turnaround",
    "dominant": "Dominant", "vi": "V-I", "twomajorchords": "Two major chords", "twomaj": "Two major chords", "enclosure": "Enclosure", "bebop": "Bebop scale",
}


def normalize_tag(t: str) -> str:
    k = re.sub(r"[^a-z]", "", t.lower())
    return _NORMAL.get(k, t.strip())


def parse_comment(text: str) -> dict:
    """'Lick 43, Blues, Major II V, taken from Chet Baker's 1956 Tenderly' -> fields."""
    out = {"number": None, "artist": "", "tune": "", "year": None, "tags": [], "comment": text.strip()}
    if not text:
        return out
    m = re.search(r"\blick\s*#?\s*(\d+)", text, re.I)
    if m:
        out["number"] = int(m.group(1))
    src = re.search(r"(?:taken\s+)?\bfrom\s+(.+?)['’]s\s+(?:(\d{4})\s+)?(.+?)\s*(?:\((\d{4})\))?\s*$", text, re.I)
    if src:
        out["artist"] = src.group(1).strip()
        out["tune"] = src.group(3).strip().strip(".\"'")
        y = src.group(2) or src.group(4)
        out["year"] = int(y) if y else None
    else:
        y = re.search(r"\b(19[2-9]\d|20[0-4]\d)\b", text)
        if y:
            out["year"] = int(y.group(1))
    for piece in re.split(r"[,;]", text):
        p = piece.strip()
        if not p or re.match(r"^lick\s*#?\s*\d+$", p, re.I) or re.search(r"\bfrom\b", p, re.I):
            continue
        if len(p) <= 30:
            out["tags"].append(normalize_tag(p))
    return out


# ----------------------------------------------------------------------------------- main
def analyse(data: dict, context: str = "") -> dict:
    """Return tonic, mode, primary function and auto tags for a lick's concert data."""
    beat = 4.0 / data["ts"][1]
    spans = chord_spans(data["chords"], data["length"])
    funcs = harmonic_functions(spans, data["ts"][0] * beat)
    func = None
    tonic_pc, mode = None, "major"
    if funcs:
        funcs.sort(key=lambda f: (FUNCTION_ORDER.index(f["function"]), f["start"]))
        func = funcs[0]["function"]
        tonic_pc, mode = funcs[0]["tonic_pc"], funcs[0]["mode"]
        if mode == "dominant":
            mode = "major"
    tags = list(dict.fromkeys(f["function"] for f in funcs))
    qualities = {s["q"] for s in spans}
    is_blues = "blues" in context.lower()
    if is_blues:
        tags.append("Blues")
    if not funcs and spans:
        s0 = spans[0]
        if is_blues and s0["q"] == "dom":
            func = "Blues"
        else:
            func = {"dom": "Dominant", "maj": "Major", "min": "Minor", "hdim": "Half-diminished",
                    "dim": "Diminished"}.get(s0["q"], "Other")
        tonic_pc = s0["pc"]
        mode = "minor" if s0["q"] in ("min", "hdim") else "major"
        if len(qualities) == 1 and len({s["pc"] for s in spans}) == 1:
            tags.append(func)
            tags.append("One chord")
        else:
            tags.append(func)
    if is_blues and func in ("Dominant", "Dominant cycle", "V-I"):
        func = "Blues"
        if spans and spans[0]["q"] == "dom":
            tonic_pc = spans[0]["pc"]
    if any("7alt" in s["sym"] or re.search(r"[b#](9|5|13)", s["sym"]) for s in spans if s["q"] == "dom"):
        tags.append("Altered dominant")
    if tonic_pc is None:  # no chords: guess the key from the notes
        tonic_pc, mode = _guess_key(data["notes"])
        func = "Other"
    devices = melodic_devices(data["notes"], spans, beat)
    tags += list(devices)
    tags += rhythm_tags(data, beat)
    bars = data["bars"]
    tags.append(f"{bars} bar" + ("s" if bars != 1 else ""))
    mids = [n[2] for n in data["notes"]]
    if mids:
        wr_hi = max(mids) + 2
        if wr_hi >= 81:
            tags.append("High register")
        if min(mids) + 2 <= 60:
            tags.append("Low register")
        last = data["notes"][-1]
        c = _chord_at(spans, last[0])
        if c:
            tags.append(f"Ends on {degree_name(last[2] - c['pc'], c['q'])}")
    return {"function": func or "Other", "tonic_pc": tonic_pc, "mode": mode,
            "tonic_name": key_name(tonic_pc, mode), "tags": list(dict.fromkeys(tags)),
            "devices": devices, "functions": funcs,
            "low": min(mids) if mids else None, "high": max(mids) if mids else None}


def _guess_key(notes) -> tuple[int, str]:
    if not notes:
        return 0, "major"
    try:
        from music21 import note as m21note, stream

        s = stream.Stream()
        for o, d, m in notes:
            s.insert(o, m21note.Note(m, quarterLength=d))
        k = s.analyze("key")
        return k.tonic.pitchClass, ("minor" if k.mode == "minor" else "major")
    except Exception:
        return notes[0][2] % 12, "major"


# ----------------------------------------------------------------------------------- tunes
def bar_chord_offsets(n: int, beats_per_bar: int = 4) -> list[float]:
    """Beat offsets of n chords in one bar (iReal convention)."""
    if n <= 1:
        return [0]
    if n == 2:
        return [0, beats_per_bar / 2]
    if n == 3 and beats_per_bar == 4:
        return [0, 2, 3]
    return [k * beats_per_bar / n for k in range(n)]


def chart_timeline(bars: list[list[str]], beats_per_bar: int = 4) -> list[dict]:
    """iReal bars -> spans with absolute beat positions. Empty bar = previous chord continues."""
    events = []
    for i, chords in enumerate(bars):
        cs = [c for c in chords if c]
        if not cs:
            continue
        for o, c in zip(bar_chord_offsets(len(cs), beats_per_bar), cs):
            events.append([i * beats_per_bar + o, c])
    return chord_spans(events, len(bars) * beats_per_bar)


def tune_sections(bars: list[list[str]], beats_per_bar: int = 4) -> list[dict]:
    """Harmonic analysis of a tune chart: list of function spans with bar numbers (1-based)."""
    spans = chart_timeline(bars, beats_per_bar)
    funcs = harmonic_functions(spans, beats_per_bar)
    out = []
    taken: list[tuple[float, float]] = []
    funcs.sort(key=lambda f: (f["start"], FUNCTION_ORDER.index(f["function"])))
    for f in funcs:
        if any(not (f["end"] <= a or f["start"] >= b) for a, b in taken):
            continue
        taken.append((f["start"], f["end"]))
        out.append({**f, "bar": int(f["start"] // beats_per_bar) + 1,
                    "bar_end": int((f["end"] - 1e-6) // beats_per_bar) + 1,
                    "key": key_name(f["tonic_pc"], f["mode"] if f["mode"] != "dominant" else "major")})
    return out
