"""Beat grid: when does every beat of every bar happen in the recording?

Beat 0 is bar 1, beat 1 (form.json grid.first_downbeat). The grid comes from beat
tracking when librosa and the audio are available, otherwise from a constant tempo.
Human anchors ([bar, seconds] pairs) then pull the grid onto bar lines you have checked
by ear, by piecewise-linear warping between anchors.
"""
from __future__ import annotations

import numpy as np


def constant_grid(bpm: float, first_downbeat: float, n_beats: int) -> np.ndarray:
    return first_downbeat + np.arange(n_beats + 1) * (60.0 / bpm)


def tracked_beats(audio_path: str, bpm: float) -> np.ndarray:
    """Beat times (s) from librosa, corrected to the expected tempo (fixes half/double time)."""
    import librosa

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    _, beats = librosa.beat.beat_track(y=y, sr=sr, start_bpm=bpm, tightness=400, units="time")
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 8:
        return beats
    expected = 60.0 / bpm
    ibi = float(np.median(np.diff(beats)))
    ratio = ibi / expected
    if 1.7 < ratio < 2.3:      # tracker found half-time: add midpoints
        mids = (beats[:-1] + beats[1:]) / 2
        beats = np.sort(np.concatenate([beats, mids]))
    elif 0.4 < ratio < 0.6:    # tracker found double-time: keep every other beat
        beats = beats[::2]
    return beats


def grid_from_beats(beats: np.ndarray, first_downbeat: float, n_beats: int, bpm: float,
                    window: float = 0.2) -> np.ndarray:
    """Follow the music beat by beat from the human-given first downbeat.

    Each next beat is predicted from the recent tempo; a tracked beat within `window`
    (fraction of a beat) of the prediction is accepted, otherwise the prediction is kept.
    So gaps or wrong stretches in the tracker never derail the grid.
    """
    beats = np.sort(np.asarray(beats, dtype=float))
    ibi = 60.0 / bpm
    seq = [first_downbeat]
    recent: list[float] = []
    for _ in range(n_beats):
        step = float(np.median(recent[-8:])) if len(recent) >= 4 else ibi
        pred = seq[-1] + step
        if len(beats):
            j = int(np.argmin(np.abs(beats - pred)))
            if abs(beats[j] - pred) <= window * step:
                pred = float(beats[j])
        d = pred - seq[-1]
        if 0.8 * ibi <= d <= 1.25 * ibi:  # keep the tempo estimate sane
            recent.append(d)
        seq.append(pred)
    return np.asarray(seq, dtype=float)


def apply_anchors(grid: np.ndarray, anchors: list[list[float]], beats_per_bar: int) -> np.ndarray:
    """Warp the grid so bar `b` (1-based) starts exactly at each anchor time."""
    if not anchors:
        return grid
    pts = sorted((int(b), float(t)) for b, t in anchors)
    idx = [(b - 1) * beats_per_bar for b, _ in pts if 0 <= (b - 1) * beats_per_bar < len(grid)]
    targets = [t for b, t in pts if 0 <= (b - 1) * beats_per_bar < len(grid)]
    if not idx:
        return grid
    src = np.concatenate([[grid[0]], grid[idx]])
    dst = np.concatenate([[grid[0]], targets])
    order = np.argsort(src)
    src, dst = src[order], dst[order]
    warped = np.interp(grid, src, dst)
    # beyond the last anchor keep the original spacing, shifted
    tail = grid > src[-1]
    warped[tail] = grid[tail] + (dst[-1] - src[-1])
    return warped


def build_grid(form, audio_path: str | None = None) -> np.ndarray:
    g = form.data.get("grid", {})
    first = float(g.get("first_downbeat", 0.0))
    n_beats = len(form.bars) * form.beats_per_bar
    grid = None
    if audio_path and g.get("track_beats", True):
        try:
            grid = grid_from_beats(tracked_beats(audio_path, form.bpm), first, n_beats, form.bpm)
        except ImportError:
            grid = None
    if grid is None:
        grid = constant_grid(form.bpm, first, n_beats)
    return apply_anchors(grid, g.get("anchors", []), form.beats_per_bar)


def time_to_beat(t: np.ndarray | float, grid: np.ndarray) -> np.ndarray:
    """Seconds -> fractional beat index on the grid (linear extrapolation at the ends)."""
    t = np.asarray(t, dtype=float)
    idx = np.arange(len(grid), dtype=float)
    out = np.interp(t, grid, idx)
    first_step = grid[1] - grid[0]
    last_step = grid[-1] - grid[-2]
    out = np.where(t < grid[0], (t - grid[0]) / first_step, out)
    out = np.where(t > grid[-1], idx[-1] + (t - grid[-1]) / last_step, out)
    return out
