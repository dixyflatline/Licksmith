"""Import iReal Pro playlists and look up tunes.

The iReal forum distributes playlists as an HTML page (or text) containing an
`irealb://...` link. `import_playlist` parses every tune once and caches the result as
plain JSON in library/charts/charts.json, so lookups are fast and the data stays portable.
"""
from __future__ import annotations

import contextlib
import difflib
import io
import json
import re
import urllib.parse
from pathlib import Path

from .chords import ireal_to_plain, split_ireal_measure


def _extract_links(text: str) -> list[str]:
    links = re.findall(r"irealb://[^\"'<>\s]+", text)
    if not links and text.strip().startswith("irealb://"):
        links = [text.strip()]
    return links


def parse_playlist_text(text: str) -> list[dict]:
    """Parse all tunes from text holding one or more irealb:// links."""
    from pyRealParser import Tune

    tunes: list[dict] = []
    for link in _extract_links(text):
        with contextlib.redirect_stdout(io.StringIO()):  # pyRealParser prints per tune
            parsed = Tune.parse_ireal_url(urllib.parse.unquote(link))
        for t in parsed:
            try:
                bars = flatten(t.chord_string)
            except Exception:  # fall back to pyRealParser's own flattening
                bars = [[ireal_to_plain(c) for c in split_ireal_measure(m)] for m in t.measures_as_strings]
            tunes.append(
                {
                    "title": t.title,
                    "composer": t.composer,
                    "style": t.style,
                    "key": t.key,
                    "time_signature": list(t.time_signature),
                    "bars": bars,
                    "raw": t.chord_string,
                }
            )
    return tunes


# ------------------------------------------------------------------------------------------
# Our own flattening of repeats, endings, segno/coda and bar repeats. pyRealParser does this
# with string substitutions that can merge the last bar of a first ending with the next bar.

_TOKEN = re.compile(
    r"(?P<comment><[^>]*>)|(?P<bar>[{}\[\]|])|(?P<section>\*\w)|(?P<time>T\d\d)|(?P<ending>N\d)"
    r"|(?P<alt>\([^)]*\))|(?P<chord>[sl]?[A-G][b#]?[^\s,|{}\[\]<(*]*)|(?P<repeat1>x)|(?P<repeat2>r)"
    r"|(?P<nc>n)|(?P<slash>p)|(?P<invisible>W(?:/[A-G][b#]?)?)|(?P<segno>S)|(?P<coda>Q)|(?P<other>\S)"
)


def _tokens(chord_string: str):
    for m in _TOKEN.finditer(chord_string):
        kind = m.lastgroup
        yield kind, m.group(kind)


def _bars(chord_string: str) -> list[dict]:
    """Chord string -> bars with their markers (before flattening)."""
    bars: list[dict] = []

    def new():
        return {"chords": [], "start_rep": False, "end_rep": False, "ending": None, "segno": False,
                "coda": False, "coda_after": False, "comments": [], "final": False}

    cur = new()
    pending_start = False

    def close():
        nonlocal cur, pending_start
        if cur["chords"] or cur["ending"] or cur["coda"] or cur["segno"] or cur["end_rep"] or cur["start_rep"] \
                or cur["comments"]:
            bars.append(cur)
        cur = new()
        if pending_start:
            cur["start_rep"] = True
            pending_start = False

    for kind, tok in _tokens(chord_string):
        if kind == "bar":
            if tok == "{":
                if cur["chords"]:
                    close()
                cur["start_rep"] = True
                continue
            if tok == "}":
                cur["end_rep"] = True
            if cur["chords"] or tok == "}":
                close()
            continue
        if kind == "chord":
            c = tok[1:] if tok[0] in "sl" else tok
            cur["chords"].append(c)
        elif kind in ("repeat1", "repeat2", "nc", "slash"):
            cur["chords"].append({"repeat1": "x", "repeat2": "r", "nc": "n", "slash": "p"}[kind])
        elif kind == "ending":
            cur["ending"] = int(tok[1:])
        elif kind == "segno":
            cur["segno"] = True
        elif kind == "coda":
            # a coda sign before any chord marks this bar; after chords it is the jump at the bar's end
            if cur["chords"]:
                cur["coda_after"] = True
            else:
                cur["coda"] = True
        elif kind == "comment":
            cur["comments"].append(tok[1:-1])
    if cur["chords"] or cur["end_rep"]:
        bars.append(cur)
    return bars


def _times(bar: dict) -> int:
    for c in bar["comments"]:
        m = re.search(r"(\d+)\s*x", c, re.I)
        if m:
            return max(2, int(m.group(1)))
    return 2


def _expand(bars: list[dict], lo: int, hi: int, repeats: bool = True) -> list[dict]:
    """Play bars[lo:hi] taking repeats and endings."""
    out: list[dict] = []
    i, rep_start, pass_, jumped = lo, lo, 1, False
    counts: dict[int, int] = {}
    guard = 0
    while i < hi and guard < 10000:
        guard += 1
        b = bars[i]
        if b["start_rep"] and not jumped:
            rep_start, pass_ = i, 1
        jumped = False
        if b["ending"] is not None and b["ending"] != pass_ and repeats:
            j = next((k for k in range(i + 1, hi) if bars[k]["ending"] == pass_), None)
            if j is None:  # no ending for this pass: take the last one
                later = [k for k in range(i + 1, hi) if bars[k]["ending"] is not None]
                j = later[-1] if later and bars[later[-1]]["ending"] > b["ending"] else None
            if j is not None:
                i = j
                continue
        out.append(b)
        if b["end_rep"] and repeats:
            n = counts.get(i, 0) + 1
            counts[i] = n
            if n < _times(b):
                pass_ += 1
                i, jumped = rep_start, True
                continue
        i += 1
    return out


def flatten(chord_string: str) -> list[list[str]]:
    """Cleaned iReal chord string -> list of bars of plain chord symbols, repeats expanded."""
    bars = _bars(chord_string)
    codas = [k for k, b in enumerate(bars) if b["coda"]]
    jumps = [k for k, b in enumerate(bars) if b["coda_after"]]
    if codas and (jumps or len(codas) >= 2):
        # D.C./D.S. al Coda: play to the coda section, go back to the segno (or the top) and
        # play to the jump sign, then play the coda
        coda_start = codas[-1]
        jump = jumps[0] if jumps else codas[0] - 1
        segno = next((k for k, b in enumerate(bars) if b["segno"]), 0)
        played = _expand(bars, 0, coda_start) + _expand(bars, segno, jump + 1, repeats=False) + \
            _expand(bars, coda_start, len(bars))
    else:
        fine = next((k for k, b in enumerate(bars) if any("fine" in c.lower() for c in b["comments"])), None)
        played = _expand(bars, 0, len(bars))
        if fine is not None and any(re.search(r"D\.?[CS]\.?.*fine", c, re.I) for b in bars for c in b["comments"]):
            segno = next((k for k, b in enumerate(bars) if b["segno"]), 0)
            played += _expand(bars, segno, fine + 1, repeats=False)
    out: list[list[str]] = []
    skip_empty = False
    for b in played:
        if skip_empty and not b["chords"]:
            skip_empty = False
            continue  # the empty bar that belongs to a two-bar repeat
        skip_empty = False
        if b["chords"][:1] == ["r"]:
            if len(out) >= 2:
                a, c = list(out[-2]), list(out[-1])
                out += [a, c]
            skip_empty = True
            continue
        cs = []
        for c in b["chords"]:
            if c == "x":
                cs = list(out[-1]) if out else []
                break
            if c == "p":
                prev = cs[-1] if cs else (out[-1][-1] if out and out[-1] else None)
                if prev:
                    cs.append(prev)
                continue
            cs.append("N.C." if c == "n" else ireal_to_plain(c))
        out.append(cs)
    return out


def import_playlist(src: Path, charts_dir: Path) -> Path:
    """Parse a playlist file and merge it into charts_dir/charts.json. Returns the JSON path."""
    tunes = parse_playlist_text(Path(src).read_text(encoding="utf-8", errors="ignore"))
    charts_dir.mkdir(parents=True, exist_ok=True)
    out = charts_dir / "charts.json"
    existing = json.loads(out.read_text()) if out.exists() else {}
    for t in tunes:
        existing[t["title"]] = t
    out.write_text(json.dumps(existing, indent=1, ensure_ascii=False))
    return out


def find_tune(title: str, charts_json: Path) -> dict:
    """Find a tune by title (case-insensitive, closest match)."""
    charts = json.loads(Path(charts_json).read_text())
    by_lower = {k.lower(): k for k in charts}
    key = by_lower.get(title.lower())
    if key is None:
        close = difflib.get_close_matches(title.lower(), list(by_lower), n=1, cutoff=0.6)
        if not close:
            raise KeyError(f"Tune '{title}' not found in {charts_json}")
        key = by_lower[close[0]]
    return charts[key]
