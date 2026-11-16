"""Screenshot -> lick.

A vision model reads a picture of sheet music and answers in ABC notation (a short text format).
This module holds everything around that: the settings the picture is read with (key signature,
time signature, which instrument the page is written for), the ABC parser that turns the answer
into a MusicXML score, the sanity checks that flag bars worth a second look, and the calls to
the vision providers (DeepSeek or Anthropic, plain HTTPS, no extra packages).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction as F
from pathlib import Path

from music21 import duration, interval, key as m21key, meter, note, pitch, stream

# ------------------------------------------------------------------------------- settings
KEY_NAMES = {0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
             -1: "F", -2: "Bb", -3: "Eb", -4: "Ab", -5: "Db", -6: "Gb", -7: "Cb"}
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"
# Interval from concert to written pitch for each instrument the page can be written for.
BASES = {"trumpet": "M2", "alto": "M6", "tenor": "M9", "concert": "P1"}
BASIS_LABEL = {"trumpet": "B♭ instrument (trumpet, clarinet, tenor sax)", "alto": "E♭ instrument (alto or bari sax)",
               "tenor": "B♭ tenor sax (octave up)", "concert": "Concert pitch"}
PROVIDERS = {
    "deepseek": {"url": "https://api.deepseek.com/chat/completions", "env": "DEEPSEEK_API_KEY",
                 "models": ["deepseek-v4-flash-vision-exp", "deepseek-flash"]},
    "anthropic": {"url": "https://api.anthropic.com/v1/messages", "env": "ANTHROPIC_API_KEY",
                  "models": ["claude-sonnet-5-5"]},
}


class CaptureError(Exception):
    pass


import collections
import time as _time

TRACES: collections.deque = collections.deque(maxlen=8)   # what was sent / received, newest last


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


# ------------------------------------------------------------------------------- secrets
def _secrets_path(lib_dir: Path) -> Path:
    return Path(lib_dir) / "secrets.json"


def get_key(lib_dir: Path, provider: str) -> str:
    env = os.environ.get(PROVIDERS[provider]["env"], "").strip()
    if env:
        return env
    try:
        return json.loads(_secrets_path(lib_dir).read_text()).get(provider, "").strip()
    except (OSError, ValueError):
        return ""


def set_key(lib_dir: Path, provider: str, value: str) -> None:
    p = _secrets_path(lib_dir)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    if value.strip():
        data[provider] = value.strip()
    else:
        data.pop(provider, None)
    p.write_text(json.dumps(data))
    try:
        p.chmod(0o600)
    except OSError:
        pass


# ------------------------------------------------------------------------------- ABC parsing
_TOKEN = re.compile(r"""
    (?P<chord>"[^"]*")
  | (?P<field>\[[A-Za-z]:[^\]]*\])
  | (?P<tuplet>\(\d(?::\d?)?(?::\d?)?)
  | (?P<slur_open>\()
  | (?P<slur_close>\))
  | (?P<shortdeco>[.~HLMPTuv])
  | (?P<deco>![^!]*!|\+[^+]*\+)
  | (?P<grace>\{[^}]*\})
  | (?P<ending>\[\d|\|\d)
  | (?P<bar>[:|\[]*\|[\]:|]*|::)
  | (?P<group>\[(?:[\^_=]*[A-Ga-g][',]*\d*/*\d*\s*)+\])(?P<glen>\d*/*\d*)
  | (?P<note>(?P<acc>[\^_=]*)(?P<l>[A-Ga-g])(?P<oct>[',]*)(?P<len>\d*/*\d*)(?P<tie>-?))
  | (?P<rest>[zx](?P<rlen>\d*/*\d*))
  | (?P<mrest>Z(?P<mn>\d*))
  | (?P<broken>[<>]+)
  | (?P<tie2>-)
""", re.X)
# decorations: ABC names (!name!) and one-letter shortcuts -> our names
DECO = {"accent": "accent", ">": "accent", "emphasis": "accent", "L": "accent",
        "staccato": "staccato", ".": "staccato", "staccatissimo": "staccatissimo", "wedge": "staccatissimo",
        "tenuto": "tenuto", "marcato": "marcato", "^": "marcato",
        "fermata": "fermata", "H": "fermata", "trill": "trill", "tr": "trill", "T": "trill",
        "mordent": "mordent", "lowermordent": "mordent", "M": "mordent",
        "uppermordent": "invmordent", "pralltriller": "invmordent", "P": "invmordent",
        "turn": "turn", "breath": "breath", "fall": "fall", "doit": "doit", "scoop": "scoop", "plop": "plop"}
DECO_ABC = {"accent": "!accent!", "staccato": "!staccato!", "staccatissimo": "!staccatissimo!",
            "tenuto": "!tenuto!", "marcato": "!marcato!", "fermata": "!fermata!", "trill": "!trill!",
            "mordent": "!lowermordent!", "invmordent": "!uppermordent!", "turn": "!turn!", "breath": "!breath!",
            "fall": "!fall!", "doit": "!doit!", "scoop": "!scoop!", "plop": "!plop!"}
_NOTE_IN_GROUP = re.compile(r"([\^_=]*)([A-Ga-g])([',]*)(\d*/*\d*)")
_TUPLET_Q = {2: 3, 3: 2, 4: 3, 5: 2, 6: 2, 7: 2, 8: 3, 9: 2}


def _length(txt: str) -> F:
    if not txt:
        return F(1)
    if "/" not in txt:
        return F(int(txt))
    num, _, rest = txt.partition("/")
    n = int(num) if num else 1
    slashes = 1 + rest.count("/")
    d = rest.replace("/", "")
    if d:
        return F(n, int(d))
    return F(n, 2 ** slashes)


def key_accidentals(sharps: int) -> dict[str, str]:
    if sharps >= 0:
        return {l: "#" for l in SHARP_ORDER[:sharps]}
    return {l: "-" for l in FLAT_ORDER[:-sharps]}


def _strip_header(abc: str) -> tuple[str, dict]:
    """Remove ABC header lines and comments; return the body and the L:/M: fields found."""
    fields = {}
    body = []
    for line in abc.replace("\r", "").split("\n"):
        line = re.sub(r"(?<!\\)%.*$", "", line)
        m = re.match(r"^\s*([A-Za-z]):\s*(.*)$", line)
        if m and not line.lstrip().startswith(('"', "[")):
            if m.group(1) in "LMK":
                fields[m.group(1)] = m.group(2).strip()
            continue
        body.append(line.rstrip("\\"))
    return " ".join(body), fields


def clean_abc(text: str) -> str:
    """Pull the ABC out of a model answer (JSON, code fences, chatter)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    return t


def parse_abc(abc: str, sharps: int = 0, meter_str: str = "4/4") -> dict:
    """ABC -> {'measures': [[event, ...], ...], 'issues': [{'bar', 'msg'}], 'meter', 'sharps'}.

    The key and meter come from the capture form and win over anything written in the ABC itself.
    Every event is {'type': 'note'|'rest', 'name': 'B-4', 'ql': Fraction, 'tie': bool, 'chord': str|None}."""
    body, fields = _strip_header(abc)
    unit = F(1, 8)
    if "L" in fields:
        m = re.match(r"(\d+)\s*/\s*(\d+)", fields["L"])
        if m:
            unit = F(int(m.group(1)), int(m.group(2)))
    num, den = (int(x) for x in meter_str.split("/"))
    bar_ql = F(num * 4, den)
    base_acc = key_accidentals(sharps)

    measures: list[list[dict]] = [[]]
    issues: list[dict] = []
    bar_acc: dict[tuple[str, int], str] = {}
    pending_chord = None
    pending_tuplet: list = []  # [remaining, ratio]
    last_event = None
    broken = None  # factor for the next event
    tie_open = False
    pending_decos: list[str] = []
    pending_grace: list[dict] = []
    pending_slurs = 0

    def bar_no() -> int:
        return len(measures)

    def new_bar():
        nonlocal bar_acc
        if measures[-1]:
            measures.append([])
        bar_acc = {}

    def apply_length(ql: F) -> F:
        nonlocal broken
        if pending_tuplet:
            ql = ql * pending_tuplet[1]
            pending_tuplet[0] -= 1
            if pending_tuplet[0] <= 0:
                pending_tuplet.clear()
        if broken is not None:
            ql = ql * broken
            broken = None
        return ql

    def pitch_name(acc_txt: str, letter: str, octs: str) -> str:
        up = letter.upper()
        octave = 4 if letter.isupper() else 5
        octave += octs.count("'") - octs.count(",")
        written = base_acc.get(up, "")
        explicit = None
        if acc_txt:
            n = len(acc_txt)
            explicit = {"^": "#" * n, "_": "-" * n, "=": ""}[acc_txt[0]]
            if acc_txt[0] == "=" and not written and (up, octave) not in bar_acc:
                issues.append({"bar": bar_no(), "msg": f"natural sign on {up} that the key signature does not alter"})
            elif explicit == written and acc_txt[0] != "=" and (up, octave) not in bar_acc:
                issues.append({"bar": bar_no(), "msg": f"{acc_txt}{up} repeats the key signature"})
            bar_acc[(up, octave)] = explicit
        acc = bar_acc.get((up, octave), written)
        return f"{up}{acc}{octave}"

    def add_note(acc_txt: str, letter: str, octs: str, ln: str, tie: bool):
        nonlocal pending_chord, last_event, tie_open, broken, pending_slurs
        name = pitch_name(acc_txt, letter, octs)
        ql = apply_length(_length(ln) * unit * 4)
        ev = {"type": "note", "name": name, "ql": ql, "tie": tie, "chord": pending_chord}
        if pending_decos:
            ev["deco"] = list(dict.fromkeys(pending_decos)); pending_decos.clear()
        if pending_grace:
            ev["grace"] = list(pending_grace); pending_grace.clear()
        if pending_slurs:
            ev["slur_open"] = pending_slurs; pending_slurs = 0
        pending_chord = None
        measures[-1].append(ev)
        last_event = ev

    pos = 0
    for m in _TOKEN.finditer(body):
        kind = m.lastgroup
        # lastgroup is the last *named* group that matched; resolve the real alternative
        for k in ("chord", "field", "tuplet", "slur_open", "slur_close", "shortdeco", "deco", "grace", "ending", "bar", "group", "note", "rest", "mrest", "broken", "tie2"):
            if m.group(k) is not None:
                kind = k
                break
        txt = m.group(0)
        if kind == "chord":
            c = txt.strip('"').strip()
            if c and c[0] not in "^_<>@":  # ABC annotations start with those; ours are chord symbols
                pending_chord = c
        elif kind == "field":
            pass  # inline K:/M: fields are ignored; the capture form decides
        elif kind == "tuplet":
            parts = txt[1:].split(":")
            n = int(parts[0])
            q = int(parts[1]) if len(parts) > 1 and parts[1] else _TUPLET_Q.get(n, 2)
            pending_tuplet[:] = [int(parts[2]) if len(parts) > 2 and parts[2] else n, F(q, n)]
        elif kind in ("deco", "shortdeco"):
            nm = DECO.get(txt.strip("!+") if kind == "deco" else txt)
            if nm:
                pending_decos.append(nm)
        elif kind == "slur_open":
            pending_slurs += 1
        elif kind == "slur_close":
            if last_event is not None and last_event["type"] == "note":
                last_event["slur_close"] = last_event.get("slur_close", 0) + 1
        elif kind == "grace":
            inner = txt[1:-1]
            slash = inner.startswith("/")
            for gm in _NOTE_IN_GROUP.finditer(inner.lstrip("/")):
                pending_grace.append({"name": pitch_name(gm.group(1), gm.group(2), gm.group(3)), "slash": slash})
        elif kind == "bar":
            new_bar()
        elif kind == "note":
            last = txt[-1] == "-"
            add_note(m.group("acc"), m.group("l"), m.group("oct"), m.group("len"), last)
        elif kind == "group":
            first = _NOTE_IN_GROUP.search(txt)
            if first:
                add_note(first.group(1), first.group(2), first.group(3), m.group("glen") or first.group(4), False)
        elif kind == "rest":
            ql = apply_length(_length(m.group("rlen")) * unit * 4)
            ev = {"type": "rest", "ql": ql, "tie": False, "chord": pending_chord}
            pending_chord = None
            measures[-1].append(ev)
            last_event = ev
        elif kind == "mrest":
            n = int(m.group("mn") or 1)
            for i in range(n):
                measures[-1].append({"type": "rest", "ql": bar_ql, "tie": False, "chord": pending_chord, "whole": True})
                pending_chord = None
                if i < n - 1:
                    new_bar()
        elif kind == "broken":
            if last_event is not None:
                f = F(3, 2) if txt[0] == ">" else F(1, 2)
                f2 = F(1, 2) if txt[0] == ">" else F(3, 2)
                last_event["ql"] = last_event["ql"] * f
                broken = f2
        elif kind == "tie2":
            if last_event is not None and last_event["type"] == "note":
                last_event["tie"] = True

    if not measures[-1]:
        measures.pop()
    first = sum((e["ql"] for e in measures[0]), F(0)) if measures else F(0)
    if len(measures) > 1 and 0 < first < bar_ql:
        measures[0].insert(0, {"type": "rest", "ql": bar_ql - first, "tie": False, "chord": None})
        issues.append({"bar": 1, "msg": "starts with a pickup, so a rest was put in front to keep the beats in the right place"})
    # bar lengths
    for i, ms in enumerate(measures, 1):
        total = sum((e["ql"] for e in ms), F(0))
        short_ok = (i == 1 or i == len(measures)) and total < bar_ql
        if total != bar_ql and not short_ok:
            issues.append({"bar": i, "msg": f"adds up to {float(total / (F(4, den))):g}/{num} beats' worth "
                                            f"({_beats(total, den)} of {num}/{den})"})
        elif total > bar_ql:
            issues.append({"bar": i, "msg": f"too long: {_beats(total, den)} of {num}/{den}"})
    if not measures:
        raise CaptureError("No notes found in the ABC text.")
    return {"measures": measures, "issues": issues, "meter": meter_str, "sharps": sharps}


def _beats(total: F, den: int) -> str:
    units = total / F(4, den)
    return f"{float(units):g} beats" if den == 4 else f"{float(units):g}/{den}"


# ------------------------------------------------------------------------------- ABC -> score
def _decorate(el, names: list[str]) -> None:
    """Put the ornaments named in the ABC (accent, staccato, trill ...) on a music21 note."""
    from music21 import articulations as A, expressions as X

    art = {"accent": A.Accent, "staccato": A.Staccato, "staccatissimo": A.Staccatissimo, "tenuto": A.Tenuto,
           "marcato": A.StrongAccent, "breath": A.BreathMark, "fall": A.Falloff, "doit": A.Doit,
           "scoop": A.Scoop, "plop": A.Plop}
    exp = {"fermata": X.Fermata, "trill": X.Trill, "mordent": X.Mordent, "invmordent": X.InvertedMordent,
           "turn": X.Turn}
    for nm in names:
        if nm in art:
            el.articulations.append(art[nm]())
        elif nm in exp:
            el.expressions.append(exp[nm]())


def ornaments_of(el) -> list[str]:
    """Our ornament names for a music21 note (the reverse of _decorate)."""
    from music21 import articulations as A, expressions as X

    pairs = [(A.Staccatissimo, "staccatissimo"), (A.Staccato, "staccato"), (A.StrongAccent, "marcato"),
             (A.Accent, "accent"), (A.Tenuto, "tenuto"), (A.BreathMark, "breath"), (A.Falloff, "fall"),
             (A.Doit, "doit"), (A.Scoop, "scoop"), (A.Plop, "plop"), (X.Fermata, "fermata"), (X.Trill, "trill"),
             (X.InvertedMordent, "invmordent"), (X.Mordent, "mordent"), (X.Turn, "turn")]
    out = []
    for obj in list(el.articulations) + list(el.expressions):
        for cls, nm in pairs:  # most specific first (a StrongAccent is not an Accent here)
            if type(obj) is cls:
                out.append(nm)
                break
    return list(dict.fromkeys(out))


def build_score(parsed: dict, basis: str = "trumpet", chords: str = "same") -> stream.Score:
    """Concert-pitch score from the parsed ABC. `basis` is what the page is written for; chords are
    transposed with the notes ('same') or already in concert pitch ('concert')."""
    from .licks import transpose_symbol, _plain_to_m21

    part = stream.Part()
    part.insert(0, meter.TimeSignature(parsed["meter"]))
    wr = interval.Interval(BASES.get(basis, "P1"))
    to_concert = wr.reverse()
    chord_list: list[tuple[F, str]] = []
    offset = F(0)
    for i, ms in enumerate(parsed["measures"], 1):
        m = stream.Measure(number=i)
        local = F(0)
        for ev in ms:
            dur = duration.Duration(quarterLength=ev["ql"])
            if ev["type"] == "rest":
                el = note.Rest(duration=dur)
            else:
                el = note.Note(ev["name"], duration=dur)
                for g in ev.get("grace", []):  # {/g} slashed = acciaccatura, {g} = appoggiatura
                    m.insert(float(local), note.Note(g["name"]).getGrace(appoggiatura=not g["slash"]))
                _decorate(el, ev.get("deco", []))
            if ev.get("chord"):
                chord_list.append((offset + local, ev["chord"]))
            m.insert(float(local), el)
            ev["_el"] = el
            local += ev["ql"]
        part.append(m)
        offset += sum((e["ql"] for e in ms), F(0)) if ms else F(0)
    # ties: a note flagged tied joins the very next event when that is a note of the same pitch
    from music21 import tie as m21tie
    flat = [ev for ms in parsed["measures"] for ev in ms]
    prev_tied = False
    for j, ev in enumerate(flat):
        if ev["type"] != "note":
            prev_tied = False
            continue
        nxt = flat[j + 1] if j + 1 < len(flat) else None
        tied = bool(ev["tie"] and nxt and nxt["type"] == "note" and nxt["name"] == ev["name"])
        if tied:
            ev["_el"].tie = m21tie.Tie("continue" if prev_tied else "start")
        elif prev_tied:
            ev["_el"].tie = m21tie.Tie("stop")
        prev_tied = tied
    from music21 import spanner

    open_slurs: list = []  # a slur may be opened more than once on a note and closed on a later one
    for ev in flat:
        if ev["type"] != "note":
            continue
        open_slurs += [ev["_el"]] * ev.get("slur_open", 0)
        for _ in range(ev.get("slur_close", 0)):
            if open_slurs:
                a = open_slurs.pop()
                if a is not ev["_el"]:
                    part.insert(0, spanner.Slur(a, ev["_el"]))
    if basis != "concert":
        part.transpose(to_concert, inPlace=True)
    for off, sym in chord_list:
        sym = normalise_symbol(sym)
        if chords == "same" and basis != "concert":
            try:
                sym = transpose_symbol(sym, to_concert)
            except Exception:
                pass
        # place the symbol in whichever measure holds that offset
        for m in part.getElementsByClass(stream.Measure):
            mo = m.getOffsetBySite(part)
            if mo <= float(off) < mo + max(m.duration.quarterLength, 0.001):
                m.insert(float(off) - mo, _plain_to_m21(sym))
                break
    sc = stream.Score()
    sc.insert(0, part)
    return sc


def normalise_symbol(s: str) -> str:
    """Chord symbol as people print them -> the plain form the rest of the app understands."""
    s = s.strip().replace("♭", "b").replace("♯", "#").replace("Δ", "maj7").replace("∆", "maj7")
    s = s.replace("ø", "m7b5").replace("°", "dim").replace("^", "maj")
    s = re.sub(r"^([A-G][b#]?)-", r"\1m", s)
    s = re.sub(r"^([A-G][b#]?)mi(?!n)", r"\1m", s)
    s = s.replace("(", "").replace(")", "")
    return s


def write_capture(parsed: dict, out_dir: Path, basis: str, chords: str, stem: str) -> Path:
    from . import licks as L

    sc = build_score(parsed, basis, chords)
    # same writer as the lick files: B-flat trumpet with a <transpose> element, so "Auto" reads it right
    return L.write_xml(L.for_display(sc, True), Path(out_dir) / f"{stem}.musicxml")


# ------------------------------------------------------------------------------- the picture
def split_bars(png: bytes, max_bars: int = 24) -> list[bytes]:
    """Cut a crop of one or more staves into one picture per bar, using the barlines.

    Returns [] when the staff cannot be found (then the whole picture is sent)."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return []
    im = Image.open(io.BytesIO(png)).convert("L")
    a = np.asarray(im) < 140
    H, W = a.shape
    rowfrac = a.mean(axis=1)
    line_rows = np.where(rowfrac > 0.45)[0]
    if len(line_rows) < 5:
        return []
    # group staff-line rows into lines, then lines into staves of five
    lines, cur = [], [line_rows[0]]
    for r in line_rows[1:]:
        if r - cur[-1] <= 2:
            cur.append(r)
        else:
            lines.append(int(np.mean(cur)))
            cur = [r]
    lines.append(int(np.mean(cur)))
    staves, i = [], 0
    while i + 4 < len(lines):
        grp = lines[i:i + 5]
        gaps = np.diff(grp)
        if gaps.max() <= 1.6 * max(gaps.min(), 1) + 2:
            staves.append((grp[0], grp[-1]))
            i += 5
        else:
            i += 1
    if not staves:
        return []
    bands = []
    for k, (top, bot) in enumerate(staves):
        lo = 0 if k == 0 else (staves[k - 1][1] + top) // 2
        hi = H if k == len(staves) - 1 else (bot + staves[k + 1][0]) // 2
        bands.append((lo, hi, top, bot))
    out = []
    for lo, hi, top, bot in bands:
        seg = a[top:bot + 1, :]
        colfrac = seg.mean(axis=0)
        cols = np.where(colfrac > 0.93)[0]
        xs, cur = [], []
        for c in cols:
            if cur and c - cur[-1] > 2:
                xs.append(int(np.mean(cur)))
                cur = []
            cur.append(int(c))
        if cur:
            xs.append(int(np.mean(cur)))
        spacing = (bot - top) / 4
        xs = [x for j, x in enumerate(xs) if j == 0 or x - xs[j - 1] > 3 * spacing]  # drop double barlines

        def sticks_out(x: int) -> bool:
            """A stem runs well past the staff; a barline stops at the outer staff lines."""
            col = a[:, max(0, x - 1):x + 2].any(axis=1)
            up = 0
            while top - 1 - up >= 0 and col[top - 1 - up]:
                up += 1
            down = 0
            while bot + 1 + down < H and col[bot + 1 + down]:
                down += 1
            return up > 0.7 * spacing or down > 0.7 * spacing

        line_set = set()
        for ln in lines:
            line_set.update(range(ln - 2, ln + 3))
        r0, r1 = max(0, int(top - 0.5 * spacing)), min(H, int(bot + 0.5 * spacing) + 1)
        free_rows = [r for r in range(r0, r1) if r not in line_set]

        def has_neighbours(x: int) -> bool:
            """Stems come with noteheads and beams touching them; a barline has clear space beside it."""
            worst = 0.0
            for lo, hi in ((x - 6, x - 3), (x + 4, x + 7)):
                lo, hi = max(0, lo), min(W, hi)
                if hi > lo and free_rows:
                    worst = max(worst, a[free_rows, lo:hi].any(axis=1).mean())
            return worst > 0.1

        xs = [x for x in xs if not sticks_out(x) and not has_neighbours(x)]
        edges = [0] + [x for x in xs if 8 < x < W - 8] + [W]
        edges = sorted(set(edges))
        for x0, x1 in zip(edges, edges[1:]):
            if x1 - x0 < 4 * spacing:
                continue
            seg = a[lo:hi, x0 + int(spacing):max(x0 + int(spacing) + 1, x1 - int(spacing))]
            keep = [r for r in range(seg.shape[0]) if (r + lo) not in line_set]
            if not keep or seg[keep].mean() < 0.004:
                continue  # nothing but staff lines (the space after the final barline)
            tile = im.crop((max(0, x0 - 2), lo, min(W, x1 + 3), hi))
            scale = max(1.0, 420 / tile.height)
            if scale > 1:
                tile = tile.resize((int(tile.width * scale), int(tile.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            tile.save(buf, "PNG")
            out.append(buf.getvalue())
    return out if 0 < len(out) <= max_bars else []


def prepare_image(png: bytes, max_side: int = 1800) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        return png
    im = Image.open(io.BytesIO(png))
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------------------- prompt
def build_prompt(sharps: int, meter_str: str, basis: str, one_bar: bool) -> str:
    keyname = KEY_NAMES.get(sharps, "C")
    if sharps == 0:
        keytxt = "no sharps or flats (C major / A minor)"
    elif sharps > 0:
        keytxt = f"{sharps} sharp{'s' if sharps > 1 else ''} ({', '.join(SHARP_ORDER[:sharps])})"
    else:
        keytxt = f"{-sharps} flat{'s' if sharps < -1 else ''} ({', '.join(FLAT_ORDER[:-sharps])})"
    scope = ("The picture shows ONE bar (the music between two barlines, with the chord symbol above it)."
             if one_bar else "The picture shows one or more bars of one melodic line, with chord symbols above the staff.")
    return f"""You are reading a picture of printed or handwritten-font jazz sheet music (a single melodic line with chord symbols) and writing it down in ABC notation. {scope}

Fixed facts from the person (do not guess these from the picture; they may be cropped out):
- Key signature: {keytxt}. In ABC this is K:{keyname}. Because of it, write NO accidental for a note the key signature already alters (with F major, a B-flat is just B and B-natural is =B). Write an accidental only where the picture prints one, and remember a printed accidental holds for the rest of that bar.
- Time signature: {meter_str}.
- Write the notes at the pitch printed on the page (treble clef, middle C = C). Do not transpose.

Rules:
- Use L:1/8. Put a | between bars; end the last bar with |]. Every bar must add up to a full {meter_str} bar except a pickup first bar or a short last bar.
- Octaves: C D E F G A B is the octave from middle C up; c d e f g a b the next octave; add ' to go higher and , to go lower.
- Note lengths are multiples of the 1/8 unit: C = eighth, C2 = quarter, C3 = dotted quarter, C4 = half, C8 = whole, C/2 = sixteenth. A dotted eighth followed by a sixteenth is C3/2 D/2.
- Eighth-note triplets: (3CDE. Ties: C4-|C2 (hyphen right after the first note). Rests: z (z4 = half rest). Slurs, articulations, dynamics, grace notes: leave out.
- Chord symbols go in double quotes right before the note they sit above, written in plain ASCII exactly as printed: "Bb7" "F#m7b5" "Cmaj7" "Dm7" "G7alt". Use b and # for flat and sharp. If a chord symbol is printed, include it; never invent one.
- Never invent or "fix" notes. If you cannot read a note, give your best guess and list that bar number in unsure_bars.

Answer with JSON only, no other text:
{{"abc": "<the ABC body, bars separated by |>", "unsure_bars": [<1-based bar numbers you are not sure of>], "notes": "<one short sentence about anything odd, or empty>"}}"""


# ------------------------------------------------------------------------------- providers
def _post(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:400]
        raise CaptureError(f"{e.code} from {url.split('/')[2]}: {detail}") from None
    except urllib.error.URLError as e:
        raise CaptureError(f"Could not reach {url.split('/')[2]}: {e.reason}") from None


def ask_vision(provider: str, api_key: str, prompt: str, png: bytes, model: str = "", step: dict | None = None) -> str:
    step = step if step is not None else {}
    step.update({"image": _data_uri(png), "image_bytes": len(png), "prompt": prompt, "calls": []})
    if not api_key:
        raise CaptureError(f"No API key for {provider}. Add it in Settings › Screenshot reader, or set {PROVIDERS[provider]['env']}.")
    b64 = base64.b64encode(png).decode()
    cfg = PROVIDERS[provider]
    models = [model] if model else cfg["models"]
    last = None

    def call(url, headers, body, label):
        rec = {"model": body["model"], "variant": label, "request_keys": sorted(body.keys())}
        step["calls"].append(rec)
        try:
            r = _post(url, headers, body)
        except CaptureError as e:
            rec["error"] = str(e)
            raise
        rec["response"] = json.dumps(r, ensure_ascii=False)[:4000]
        return r

    for mdl in models:
        try:
            if provider == "anthropic":
                r = call(cfg["url"], {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {
                    "model": mdl, "max_tokens": 2000, "temperature": 0,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text", "text": prompt}]}]}, "anthropic")
                text = "".join(b.get("text", "") for b in r.get("content", []))
            else:
                body = {"model": mdl, "max_tokens": 4000, "temperature": 0,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
                try:  # answer straight away: thinking mode can use up the whole budget and leave no answer
                    r = call(cfg["url"], {"Authorization": f"Bearer {api_key}"}, {**body, "thinking": {"type": "disabled"}}, "thinking off")
                except CaptureError as e:
                    if "thinking" not in str(e).lower():
                        raise
                    r = call(cfg["url"], {"Authorization": f"Bearer {api_key}"}, body, "plain")
                ch = r["choices"][0]
                text = ch["message"].get("content") or ""
                if not text.strip():
                    raise CaptureError(f"{provider} sent an empty answer (finish_reason={ch.get('finish_reason')}; "
                                       f"it may have spent its budget thinking). Model: {mdl}")
            step["answer"] = text
            return text
        except CaptureError as e:
            last = e
            step["error"] = str(e)
            if not re.search(r"\b(400|404)\b", str(e)) or mdl == models[-1]:
                raise
    raise last or CaptureError("No answer")


def parse_answer(text: str) -> dict:
    t = clean_abc(text)
    data = None
    try:
        data = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except ValueError:
                data = None
    if isinstance(data, dict) and "abc" in data:
        return {"raw": text, "abc": str(data["abc"]).strip(), "unsure": [int(x) for x in data.get("unsure_bars", []) if str(x).isdigit()],
                "notes": str(data.get("notes", ""))}
    return {"raw": text, "abc": t, "unsure": [], "notes": "The reader did not answer in the expected format; check the ABC."}


def read_image(png: bytes, provider: str, api_key: str, sharps: int, meter_str: str, basis: str,
               tiles: str = "bars", model: str = "", label: str = "") -> dict:
    """Read a crop of sheet music. tiles='bars' sends each bar as its own picture (better for models
    that shrink pictures), 'whole' sends the crop as one."""
    png = prepare_image(png)
    pieces = split_bars(png) if tiles == "bars" else []
    trace = {"time": _time.strftime("%H:%M:%S"), "label": label, "provider": provider, "model": model or "(default)",
             "key": sharps, "meter": meter_str, "basis": basis, "tiles_setting": tiles, "image": _data_uri(png),
             "bars_found": len(pieces), "steps": []}
    TRACES.append(trace)
    if len(pieces) > 1:
        prompt = build_prompt(sharps, meter_str, basis, one_bar=True)
        steps = [{"bar": i} for i in range(1, len(pieces) + 1)]
        trace["steps"] = steps

        def one(i):
            return parse_answer(ask_vision(provider, api_key, prompt, pieces[i], model, steps[i]))

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(one, i) for i in range(len(pieces))]
            answers = []
            for i, f in enumerate(futs):
                try:
                    answers.append(f.result())
                    steps[i]["parsed"] = {k: answers[-1][k] for k in ("abc", "unsure", "notes")}
                except CaptureError:
                    raise
        bars, unsure, notes = [], [], []
        raw = [a["raw"][:300] for a in answers]
        for i, a in enumerate(answers, 1):
            body, _ = _strip_header(a["abc"])
            body = body.replace("]", "")
            segs = [x.strip() for x in body.split("|") if x.strip()]
            extra = len(segs) > 1   # a one-bar picture should give one bar; more means the reader is guessing
            body = segs[0] if segs else ""
            bars.append(body)
            if extra:
                notes.append(f"bar {i}: the reader returned {len(segs)} bars for a one-bar picture; kept the first")
            if a["unsure"] or not body or extra:
                unsure.append(i)
            if a["notes"]:
                notes.append(f"bar {i}: {a['notes']}")
        out = {"abc": " | ".join(bars) + " |]", "unsure": unsure, "notes": "; ".join(notes), "tiles": len(pieces), "raw": raw}
        trace["result"] = out["abc"]
        return out
    prompt = build_prompt(sharps, meter_str, basis, one_bar=False)
    step = {"bar": "whole picture"}
    trace["steps"] = [step]
    a = parse_answer(ask_vision(provider, api_key, prompt, png, model, step))
    step["parsed"] = {k: a[k] for k in ("abc", "unsure", "notes")}
    a["tiles"] = 1
    a["raw"] = [a["raw"][:600]]
    trace["result"] = a["abc"]
    return a


# ------------------------------------------------------------------------------- scoring (accuracy test)
def compare_notes(truth: list[tuple[float, float, int]], got: list[tuple[float, float, int]]) -> dict:
    """truth/got: (offset, quarterLength, concert midi). A note is right when pitch, start and length agree."""
    ts = {(round(o * 12), round(d * 12), p) for o, d, p in truth}
    gs = {(round(o * 12), round(d * 12), p) for o, d, p in got}
    ok = len(ts & gs)
    return {"truth": len(ts), "got": len(gs), "right": ok, "wrong": len(ts) - ok,
            "extra": len(gs - ts)}


# ------------------------------------------------------------------------------- score -> ABC
_PC_SHARPS = [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5]  # major tonic pitch class -> key signature


def sharps_for(tonic_pc: int, mode: str = "major") -> int:
    """Key signature (sharps; flats negative) for a tonic; a minor tonic uses its relative major."""
    return _PC_SHARPS[(tonic_pc + (3 if mode == "minor" else 0)) % 12]


def _abc_len(units: F) -> str:
    if units == 1:
        return ""
    if units.denominator == 1:
        return str(units.numerator)
    if units.numerator == 1:
        return f"/{units.denominator}" if units.denominator != 2 else "/"
    return f"{units.numerator}/{units.denominator}"


def score_to_abc(sc: stream.Score, sharps: int = 0) -> str:
    """ABC (L:1/8) for a one-part score in the pitch it should be read in. Accidentals are written
    only where the key signature and earlier notes in the bar do not already give them, so
    parse_abc() gives back the same notes. Ornaments (!accent!, !trill! ...), slurs and grace notes
    are written too."""
    from music21 import harmony, spanner

    from .licks import m21_to_plain

    part = sc.parts[0] if getattr(sc, "parts", None) and len(sc.parts) else sc
    base = key_accidentals(sharps)
    slur_start: dict[int, int] = {}
    slur_end: dict[int, int] = {}
    for sl in part.recurse().getElementsByClass(spanner.Slur):
        els = sl.getSpannedElements()
        if len(els) >= 2:
            slur_start[id(els[0])] = slur_start.get(id(els[0]), 0) + 1
            slur_end[id(els[-1])] = slur_end.get(id(els[-1]), 0) + 1
    bar_acc: dict = {}

    def pitch_text(p) -> str:
        letter = p.step
        octave = p.octave if p.octave is not None else 4
        alter = int(round(p.alter))
        want = ("#" * alter) if alter > 0 else ("-" * -alter)
        have = bar_acc.get((letter, octave), base.get(letter, ""))
        acc = ""
        if want != have:
            acc = "=" if not want else (("^" if alter > 0 else "_") * abs(alter))
            bar_acc[(letter, octave)] = want
        name = letter.lower() + "'" * (octave - 5) if octave >= 5 else letter + "," * (4 - octave)
        return acc + name

    out_bars = []
    for m in part.getElementsByClass(stream.Measure):
        chords = sorted(((float(h.offset), h) for h in m.getElementsByClass(harmony.Harmony)), key=lambda t: t[0])
        bar_acc.clear()
        toks: list[str] = []
        ci = 0
        tuplet_left = 0
        graces: list[str] = []
        grace_slash = False
        els = list(m.notesAndRests)
        for ei, el in enumerate(els):
            if isinstance(el, harmony.Harmony):
                continue
            if el.duration.isGrace and isinstance(el, note.Note):
                graces.append(pitch_text(el.pitch))
                grace_slash = grace_slash or bool(getattr(el.duration, "slash", False))
                continue
            off = float(el.offset)
            pre = ""
            while ci < len(chords) and chords[ci][0] <= off + 1e-6:
                pre = '"' + m21_to_plain(chords[ci][1]) + '"'
                ci += 1
            nominal = el.duration.quarterLength
            tup = el.duration.tuplets[0] if el.duration.tuplets else None
            if tup is not None:
                nominal = duration.Duration(type=el.duration.type, dots=el.duration.dots).quarterLength
            units = F(nominal).limit_denominator(64) / F(1, 2)
            tp = ""
            if tup is not None and tuplet_left == 0:
                n_, q_ = tup.numberNotesActual, tup.numberNotesNormal
                r = n_
                if (n_, q_) == (3, 2):  # a triplet group fills one beat (or half a beat for 16th triplets): count its events
                    target = F(1) if F(el.duration.quarterLength).limit_denominator(64) >= F(1, 3) else F(1, 2)
                    acc, cnt = F(0), 0
                    for e2 in els[ei:]:
                        if isinstance(e2, harmony.Harmony) or not e2.duration.tuplets:
                            break
                        acc += F(e2.duration.quarterLength).limit_denominator(64)
                        cnt += 1
                        if acc >= target:
                            break
                    if acc == target:
                        r = cnt
                tp = f"({n_}" if (n_, q_) == (3, 2) and r == 3 else f"({n_}:{q_}:{r}"
                tuplet_left = r
            if tuplet_left:
                tuplet_left -= 1
            if isinstance(el, note.Rest):
                toks.append(tp + pre + "z" + _abc_len(units))
                continue
            p = el.pitches[-1] if hasattr(el, "pitches") and len(el.pitches) > 1 else el.pitch
            gtxt = ""
            if graces:
                gtxt = "{" + ("/" if grace_slash else "") + "".join(graces) + "}"
                graces, grace_slash = [], False
            decos = "".join(DECO_ABC[d] for d in ornaments_of(el))
            slur_o = "(" * slur_start.get(id(el), 0)
            slur_c = ")" * slur_end.get(id(el), 0)
            tie = "-" if el.tie is not None and el.tie.type in ("start", "continue") else ""
            toks.append(tp + pre + decos + gtxt + slur_o + pitch_text(p) + _abc_len(units) + tie + slur_c)
        out_bars.append(" ".join(toks))
    return " | ".join(out_bars) + " |]"
