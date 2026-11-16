"""Transcription engines. Each one writes works/<work>/engines/<engine>/notes.json:
[{"onset": s, "offset": s, "pitch": midi, "velocity": v}, ...] for the target instrument only,
with times in seconds of the full recording.

- muscriptor: Kyutai/Mirelo MuScriptor, conditioned on the instruments that are playing.
  Options: model size, classifier-free guidance, and transcribing only a time window.
- midi:       import any MIDI file you made elsewhere (another tool, or a hand transcription).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Playable concert-pitch MIDI range per instrument (generous at the top for high-note players).
# Notes outside it cannot be that instrument: they are other instruments the engine mislabelled.
RANGES = {"trumpet": (52, 91), "tenor_sax": (44, 80), "alto_sax": (49, 85), "trombone": (40, 77)}


def in_range(notes: list[dict], instrument: str) -> list[dict]:
    lo, hi = RANGES.get(instrument, (0, 127))
    return [n for n in notes if lo <= n["pitch"] <= hi]


def drop_chords(notes: list[dict], window: float = 0.04, max_leap: int = 7) -> tuple[list[dict], int]:
    """A horn plays one note at a time. Notes starting within `window` seconds of each other
    are a chord, i.e. comping the engine mislabelled. From each chord keep at most the one
    note that continues the melody (closest to the last kept note, within `max_leap`
    semitones); drop the rest. Returns (kept notes, number dropped)."""
    notes = sorted(notes, key=lambda n: n["onset"])
    kept: list[dict] = []
    dropped = 0
    i = 0
    while i < len(notes):
        j = i + 1
        while j < len(notes) and notes[j]["onset"] - notes[i]["onset"] < window:
            j += 1
        group = notes[i:j]
        if len(group) == 1:
            kept.append(group[0])
        else:
            prev = kept[-1]["pitch"] if kept else None
            best = min(group, key=lambda n: abs(n["pitch"] - prev)) if prev is not None else None
            if best is not None and abs(best["pitch"] - prev) <= max_leap:
                kept.append(best)
                dropped += len(group) - 1
            else:
                dropped += len(group)
        i = j
    return kept, dropped


# GM programs (0-based) that count as "the trumpet" when picking a MIDI track.
TARGET_PROGRAMS = {"trumpet": {56, 59}, "tenor_sax": {66}, "alto_sax": {65}, "trombone": {57}, "flute": {73}}


def _pick_track(midi_path: Path, instrument: str, offset: float = 0.0) -> list[dict]:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    programs = TARGET_PROGRAMS.get(instrument, set())
    cands = [i for i in pm.instruments if not i.is_drum and (i.program in programs or instrument in i.name.lower())]
    if not cands:
        names = ", ".join(f"{i.name or '?'} (program {i.program})" for i in pm.instruments)
        raise RuntimeError(f"No {instrument} track in {midi_path.name}. Tracks: {names}")
    notes = [n for inst in cands for n in inst.notes]
    notes.sort(key=lambda n: (n.start, -n.velocity))
    return [{"onset": n.start + offset, "offset": n.end + offset, "pitch": n.pitch, "velocity": n.velocity}
            for n in notes]


def preprocess(audio: Path, out: Path, window: tuple[float, float] | None = None, channel: str = "mix",
               bandpass: tuple[float, float] | None = None, hpss: float = 0.0) -> Path:
    """Make the audio the engine hears. All steps optional:
    window   - crop to (start, end) seconds
    channel  - mix | left | right | side (side = L-R, isolates hard-panned parts on stereo records)
    bandpass - (low_hz, high_hz): 4th-order high- and low-pass around the horn's range
    hpss     - remove percussion (drums, cymbals) by harmonic/percussive separation; value = margin
               (1 = gentle, 2-3 = stronger)
    """
    flt = []
    if channel == "left":
        flt.append("pan=mono|c0=c0")
    elif channel == "right":
        flt.append("pan=mono|c0=c1")
    elif channel == "side":
        flt.append("pan=mono|c0=0.5*c0-0.5*c1")
    if bandpass:
        lo, hi = bandpass
        flt += [f"highpass=f={lo}:poles=2", f"highpass=f={lo}:poles=2",
                f"lowpass=f={hi}:poles=2", f"lowpass=f={hi}:poles=2"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if window:
        cmd += ["-ss", f"{max(0.0, window[0]):.3f}", "-to", f"{window[1]:.3f}"]
    cmd += ["-i", str(audio)]
    if flt:
        cmd += ["-af", ",".join(flt)]
    cmd += ["-ar", "44100", str(out)]
    subprocess.run(cmd, check=True)
    if hpss > 0:
        import librosa
        import numpy as np
        import soundfile as sf

        y, sr = sf.read(str(out), always_2d=True)
        chans = []
        for c in range(y.shape[1]):
            D = librosa.stft(y[:, c].astype(np.float32), n_fft=4096, hop_length=1024)
            H, _ = librosa.decompose.hpss(D, margin=hpss)
            chans.append(librosa.istft(H, hop_length=1024, length=y.shape[0]))
        h = np.stack(chans, axis=1)
        peak = float(np.abs(h).max()) or 1.0
        sf.write(str(out), h / peak * 0.9, sr)
    return out


def run_muscriptor(audio: Path, out_dir: Path, ensemble: list[str], instrument: str,
                   model: str = "medium", cfg_coef: float = 1.0,
                   window: tuple[float, float] | None = None, extra_args: list[str] | None = None,
                   channel: str = "mix", bandpass: tuple[float, float] | None = None, hpss: float = 0.0,
                   log: Path | None = None) -> Path:
    exe = shutil.which("muscriptor")
    if exe is None:
        raise RuntimeError("muscriptor is not installed in this environment (run setup_mac.sh).")
    out_dir.mkdir(parents=True, exist_ok=True)
    src, offset = audio, 0.0
    if window is not None or channel != "mix" or bandpass or hpss > 0:
        offset = max(0.0, window[0]) if window else 0.0
        src = preprocess(audio, out_dir / "engine_input.wav", window, channel, bandpass, hpss)
        print(f"Engine input (listen to it): {src}")
    raw = out_dir / "raw.mid"
    cmd = [exe, "transcribe", str(src), "--output", str(raw), "--model", model,
           "--detect-tempo", "false", "--cfg-coef", str(cfg_coef)]
    if ensemble:
        cmd += ["--instruments", ",".join(ensemble)]
    cmd += extra_args or []
    print("Running:", " ".join(cmd))
    if log is not None:  # the web app reads this file to show progress
        import os

        cmd = [sys.executable, "-m", "lick4myhorn.mus_runner"] + cmd[1:]  # same command, plus a progress line per chunk

        with open(log, "w") as f:  # unbuffered, so the progress lines show up while it runs
            subprocess.run(cmd, check=True, stdout=f, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    else:
        subprocess.run(cmd, check=True)
    (out_dir / "engine.json").write_text(json.dumps(
        {"engine": "muscriptor", "model": model, "cfg_coef": cfg_coef, "ensemble": ensemble,
         "window": window, "channel": channel, "bandpass": bandpass, "hpss": hpss,
         "extra_args": extra_args or []}, indent=1))
    (out_dir / "bass.json").write_text(json.dumps(extract_bass(raw, offset)))
    return save_notes(_pick_track(raw, instrument, offset), out_dir)


def extract_bass(midi_path: Path, offset: float = 0.0) -> list[dict]:
    """The bass track of a Muscriptor MIDI (kept next to the horn: its downbeats anchor the bar lines)."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    notes = [n for i in pm.instruments if not i.is_drum and ("bass" in (i.name or "").lower() or 32 <= i.program <= 39)
             for n in i.notes]
    notes.sort(key=lambda n: n.start)
    return [{"onset": n.start + offset, "offset": n.end + offset, "pitch": n.pitch, "velocity": n.velocity}
            for n in notes]


def import_midi(midi: Path, out_dir: Path, instrument: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(midi, out_dir / "raw.mid")
    return save_notes(_pick_track(out_dir / "raw.mid", instrument), out_dir)


def save_notes(notes: list[dict], out_dir: Path) -> Path:
    p = out_dir / "notes.json"
    p.write_text(json.dumps(notes))
    return p


def load_notes(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "notes.json").read_text())
