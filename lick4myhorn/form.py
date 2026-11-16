"""The form map of a recording (form.json): which bars are intro / head / solos / tag,
which chords each bar has, and which bars to transcribe.

Bar numbers are 1-based and global: bar 1 starts at grid.first_downbeat (seconds).
Bars before the first downbeat (pickups, rubato intros) are simply ignored.

Example form.json
-----------------
{
  "title": "Blue Train", "artist": "Lee Morgan",
  "source": {"url": "https://www.youtube.com/watch?v=...", "audio": "audio.wav"},
  "tune": "Blue Train",                    # iReal title (optional if "chorus" is given)
  "chorus": [["Eb7"], ["Ab7"], ...],        # concert chords, one list per bar (optional)
  "beats_per_bar": 4, "bpm": 132, "feel": "swing",
  "instrument": "trumpet",
  "ensemble": ["trumpet", "tenor_sax", "trombone", "acoustic_piano", "acoustic_bass", "drums"],
  "grid": {"first_downbeat": 3.21, "anchors": [[61, 112.40]]},
  "sections": [
    {"label": "head", "choruses": 2},
    {"label": "tenor solo", "choruses": 7},
    {"label": "trumpet solo", "choruses": 5, "transcribe": true},
    {"label": "tag", "bars": 4, "repeat_last": 4},
    {"label": "vamp", "bars": 8, "vamp": ["Eb7"]}
  ]
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Bar:
    number: int                 # global, 1-based
    section: str
    chorus: int | None          # 1-based chorus number inside the section, if any
    bar_in_chorus: int | None   # 1-based
    chords: list[str]           # concert pitch, plain symbols
    transcribe: bool
    feel: str


@dataclass
class Form:
    data: dict
    bars: list[Bar] = field(default_factory=list)

    @property
    def beats_per_bar(self) -> int:
        return int(self.data.get("beats_per_bar", 4))

    @property
    def bpm(self) -> float:
        return float(self.data.get("bpm", 120))

    @property
    def chorus_len(self) -> int:
        return len(self.data.get("chorus") or [])

    def transcribed_bars(self) -> list[Bar]:
        return [b for b in self.bars if b.transcribe]


def load_form(path: Path, charts_json: Path | None = None) -> Form:
    data = json.loads(Path(path).read_text())
    chorus = data.get("chorus")
    if not chorus and data.get("tune"):
        if charts_json is None or not Path(charts_json).exists():
            raise FileNotFoundError(
                "form.json names a tune but has no 'chorus' chords, and no iReal charts are imported yet. "
                "Run: l4mh charts import <playlist.html>"
            )
        from .ireal import find_tune

        tune = find_tune(data["tune"], charts_json)
        chorus = tune["bars"]
        data.setdefault("beats_per_bar", tune["time_signature"][0])
        data["chorus"] = chorus
    if not chorus:
        raise ValueError("form.json needs either 'chorus' (chords per bar) or 'tune' (an iReal title).")
    return Form(data=data, bars=expand_sections(data))


def _fill_chords(chords: list[list[str]]) -> list[list[str]]:
    """Empty bars inherit the previous bar's last chord."""
    out, last = [], ["N.C."]
    for c in chords:
        c = [x for x in c if x] or [last[-1]]
        out.append(c)
        last = c
    return out


def expand_sections(data: dict) -> list[Bar]:
    chorus = _fill_chords(data["chorus"])
    feel = data.get("feel", "swing")
    bars: list[Bar] = []
    n = 0
    for sec in data.get("sections", []):
        label = sec.get("label", "section")
        transcribe = bool(sec.get("transcribe", False))
        sec_feel = sec.get("feel", feel)
        if "choruses" in sec:
            for ch in range(int(sec["choruses"])):
                for i, chords in enumerate(chorus):
                    n += 1
                    bars.append(Bar(n, label, ch + 1, i + 1, chords, transcribe, sec_feel))
            continue
        count = int(sec.get("bars", 0))
        if sec.get("loop_chorus"):  # a recording of unknown length: the chart repeats until the bars run out
            for i in range(count):
                n += 1
                bars.append(Bar(n, label, i // len(chorus) + 1, i % len(chorus) + 1, chorus[i % len(chorus)], transcribe, sec_feel))
            continue
        if "chords" in sec:
            seq = _fill_chords(sec["chords"])
        elif "repeat_last" in sec:
            k = int(sec["repeat_last"])
            prev = [b.chords for b in bars[-k:]] or chorus[-k:]
            seq = prev
        elif "vamp" in sec:
            seq = [sec["vamp"]]
        else:
            seq = [["N.C."]]
        for i in range(count):
            n += 1
            bars.append(Bar(n, label, None, None, seq[i % len(seq)], transcribe, sec_feel))
    return bars
