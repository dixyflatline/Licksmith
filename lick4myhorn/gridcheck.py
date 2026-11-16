"""Human-in-the-loop check of the beat grid and form map.

Writes grid_check.wav (the recording with a click on every beat, a higher click on each
downbeat and a bell on the first bar of every chorus/section) and bars.csv (bar -> time).
Listen; where the clicks drift off the band, note the real time of that bar line and add
it to form.json as an anchor: "grid": {"anchors": [[bar, seconds], ...]}.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


def _tone(freq: float, dur: float, sr: int, amp: float) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 40)


def write_bars_csv(form, grid: np.ndarray, out: Path) -> Path:
    bpb = form.beats_per_bar
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bar", "time_s", "mm:ss", "section", "chorus", "bar_in_chorus", "chords", "transcribe"])
        for b in form.bars:
            t = float(grid[(b.number - 1) * bpb])
            w.writerow([b.number, round(t, 3), f"{int(t // 60)}:{t % 60:05.2f}", b.section, b.chorus or "",
                        b.bar_in_chorus or "", " ".join(b.chords), "yes" if b.transcribe else ""])
    return out


def write_click_check(audio: Path, form, grid: np.ndarray, out: Path) -> Path:
    import soundfile as sf

    y, sr = sf.read(str(audio), always_2d=True)
    y = y.mean(axis=1) * 0.6
    bpb = form.beats_per_bar
    starts = {b.number for b in form.bars if b.bar_in_chorus in (1, None) and
              (b.number == 1 or form.bars[b.number - 2].section != b.section or b.bar_in_chorus == 1)}
    for i, t in enumerate(grid):
        pos = int(t * sr)
        if pos < 0 or pos >= len(y):
            continue
        bar = i // bpb + 1
        if i % bpb == 0 and bar in starts:
            click = _tone(1760, 0.25, sr, 0.5)
        elif i % bpb == 0:
            click = _tone(1320, 0.06, sr, 0.35)
        else:
            click = _tone(880, 0.04, sr, 0.2)
        end = min(len(y), pos + len(click))
        y[pos:end] += click[: end - pos]
    sf.write(str(out), np.clip(y, -1, 1), sr)
    return out
