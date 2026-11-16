"""Realistic playback: renders a practice cycle (count-in, lick, band) to audio with FluidSynth
and a SoundFont, so the browser just plays a finished recording with sample-accurate timing.

Needs the FluidSynth library (Mac: `brew install fluid-synth`) and the `pyfluidsynth` package.
The SoundFont is found automatically (MuseScore's, or library/soundfonts/), or set in Settings.
If either is missing, the browser falls back to its built-in synth.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import random
import threading
import wave
from collections import OrderedDict
from pathlib import Path

SR = 44100
TAIL = 1.2  # seconds of ring-out after the cycle

GM = {"trumpet": 56, "muted_trumpet": 59, "flugelhorn": 56, "piano": 0, "epiano": 4, "bass": 32, "drums": 0}
CH_LEAD, CH_PIANO, CH_BASS, CH_DRUMS = 0, 1, 2, 9
RIDE, HAT_FOOT, KICK, SIDE_STICK = 51, 44, 36, 37


def soundfont_candidates(root: Path) -> list[Path]:
    home = Path.home()
    pats = [
        str(root / "library" / "soundfonts" / "*.sf[23]"),
        "/Applications/MuseScore Studio 4.app/Contents/Resources/sound/*.sf[23]",
        "/Applications/MuseScore 4.app/Contents/Resources/sound/*.sf[23]",
        "/Applications/MuseScore 3.app/Contents/Resources/sound/*.sf[23]",
        str(home / "Documents" / "MuseScore4" / "SoundFonts" / "*.sf[23]"),
        str(home / "Documents" / "MuseScore3" / "Soundfonts" / "*.sf[23]"),
        str(home / "Library" / "Audio" / "Sounds" / "Banks" / "*.sf[23]"),
        "/usr/share/sounds/sf2/FluidR3_GM.sf2",
        "/usr/share/soundfonts/*.sf[23]",
    ]
    out = []
    for p in pats:
        for f in sorted(glob.glob(p)):
            fp = Path(f)
            if fp.is_file() and fp.stat().st_size > 2_000_000 and fp not in out:  # skip tiny single-instrument files
                out.append(fp)
    # MuseScore's General MIDI fonts first, then anything else
    out.sort(key=lambda p: (0 if "muse" in str(p).lower() or "ms basic" in p.name.lower() else
                            1 if "library/soundfonts" in str(p) else 2))
    return out


class Renderer:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.fs = None
        self.sfid = None
        self.sf_path: Path | None = None
        self.error: str | None = None

    # -------------------------------------------------------------- setup
    def status(self, settings: dict) -> dict:
        ok = self._ensure(settings)
        return {"available": ok, "soundfont": str(self.sf_path) if self.sf_path else None, "error": self.error,
                "candidates": [str(p) for p in soundfont_candidates(self.root)]}

    def _ensure(self, settings: dict) -> bool:
        want = settings.get("soundfont") or ""
        path = Path(want).expanduser() if want else None
        if path is None or not path.exists():
            cands = soundfont_candidates(self.root)
            path = cands[0] if cands else None
        if path is None:
            self.error = "No SoundFont found. Put a General MIDI .sf2/.sf3 in library/soundfonts or set one in Settings."
            return False
        if self.fs is not None and self.sf_path == path:
            return True
        try:
            import fluidsynth  # pyfluidsynth
        except Exception as e:  # library missing
            self.error = f"FluidSynth is not installed ({e}). On the Mac: brew install fluid-synth, then uv pip install pyfluidsynth"
            return False
        try:
            if self.fs is not None:
                self.fs.delete()
            fs = fluidsynth.Synth(samplerate=float(SR), gain=0.5)
            sfid = fs.sfload(str(path))
            if sfid < 0:
                raise RuntimeError(f"could not load {path}")
            fs.set_reverb(roomsize=0.45, damping=0.4, width=0.8, level=0.35)
            fs.setting("synth.chorus.active", 0)
            self.fs, self.sfid, self.sf_path, self.error = fs, sfid, path, None
            self.cache.clear()
            return True
        except Exception as e:
            self.fs, self.error = None, f"FluidSynth could not start: {e}"
            return False

    # -------------------------------------------------------------- render
    def render(self, req: dict, settings: dict) -> bytes | None:
        if not self._ensure(settings):
            return None
        key = hashlib.sha1(json.dumps([req, str(self.sf_path), settings.get("lead_sound"),
                                       settings.get("volumes")], sort_keys=True).encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            events, total = cycle_events(req, settings)
            audio = self._play(events, total, settings)
            self.cache[key] = audio
            while len(self.cache) > 80:
                self.cache.popitem(last=False)
            return audio

    def _play(self, events: list, total: float, settings: dict) -> bytes:
        import numpy as np

        fs, sf = self.fs, self.sfid
        lead = GM.get(settings.get("lead_sound", "trumpet"), 56)
        fs.program_select(CH_LEAD, sf, 0, lead)
        fs.program_select(CH_PIANO, sf, 0, GM["piano"])
        fs.program_select(CH_BASS, sf, 0, GM["bass"])
        fs.program_select(CH_DRUMS, sf, 128, 0)
        vols = {CH_LEAD: 110, CH_PIANO: 80, CH_BASS: 105, CH_DRUMS: 85}
        vols.update({int(k): v for k, v in (settings.get("volumes") or {}).items()})
        for ch, v in vols.items():
            fs.cc(ch, 7, int(max(0, min(127, v))))
            fs.cc(ch, 91, 30 if ch != CH_BASS else 10)  # reverb send
        events.sort(key=lambda e: (e[0], 0 if e[1] == "off" else 1))
        chunks = []
        pos = 0
        for t, kind, ch, key, vel in events:
            n = int(round(t * SR))
            if n > pos:
                chunks.append(fs.get_samples(n - pos))
                pos = n
            if kind == "on":
                fs.noteon(ch, key, vel)
            else:
                fs.noteoff(ch, key)
        end = int((total + TAIL) * SR)
        if end > pos:
            chunks.append(fs.get_samples(end - pos))
        for ch in (CH_LEAD, CH_PIANO, CH_BASS, CH_DRUMS):
            fs.cc(ch, 123, 0)  # all notes off for the next render
        fs.get_samples(SR // 2)
        data = np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(2, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data.tobytes())
        return buf.getvalue()


# ------------------------------------------------------------------ music
_ROOT = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse(sym: str):
    from .analysis import parse_chord

    p = parse_chord(sym)
    if p is None:
        return None
    pc, q, rest = p
    bass = pc
    if "/" in sym:
        b = sym.split("/")[-1]
        if b and b[0] in _ROOT:
            bass = (_ROOT[b[0]] + (-1 if b[1:2] == "b" else 1 if b[1:2] == "#" else 0)) % 12
    alt = any(x in rest for x in ("alt", "b9", "#9", "b13"))
    return {"pc": pc, "q": q, "bass": bass, "alt": alt}


THIRD = {"maj": 4, "min": 3, "dom": 4, "hdim": 3, "dim": 3, "aug": 4}
FIFTH = {"maj": 7, "min": 7, "dom": 7, "hdim": 6, "dim": 6, "aug": 8}
SEVENTH = {"maj": 11, "min": 10, "dom": 10, "hdim": 10, "dim": 9, "aug": 10}
SCALE = {"maj": [0, 2, 4, 5, 7, 9, 11], "min": [0, 2, 3, 5, 7, 9, 10], "dom": [0, 2, 4, 5, 7, 9, 10],
         "hdim": [0, 1, 3, 5, 6, 8, 10], "dim": [0, 2, 3, 5, 6, 8, 9, 11], "aug": [0, 2, 4, 6, 8, 10]}


def voicing(ch: dict, form: int) -> list[int]:
    """Rootless left-hand voicing (A or B form) around middle C."""
    q = ch["q"]
    if q == "dom":
        ivs = [4, 9, 10, 14] if not ch["alt"] else [4, 10, 13, 15]
        if form:
            ivs = [10, 14, 16, 21] if not ch["alt"] else [10, 13, 16, 20]
    elif q == "maj":
        ivs = [4, 7, 11, 14] if not form else [11, 14, 16, 19]
    elif q == "min":
        ivs = [3, 7, 10, 14] if not form else [10, 14, 15, 19]
    elif q == "hdim":
        ivs = [3, 6, 10, 12]
    elif q == "dim":
        ivs = [3, 6, 9, 12]
    else:
        ivs = [4, 8, 10]
    base = ch["pc"] + 48
    while base + ivs[0] < 52:
        base += 12
    while base + ivs[0] > 63:
        base -= 12
    return [base + i for i in ivs]


def warp(q: float, swing: float) -> float:
    b = int(q // 1)
    f = q - b
    return b + swing if abs(f - 0.5) < 1e-3 else q


def spans_of(chords: list, length: float) -> list[dict]:
    pts = sorted(chords, key=lambda c: c[0])
    out = []
    for i, (o, s) in enumerate(pts):
        end = pts[i + 1][0] if i + 1 < len(pts) else length
        ch = parse(s)
        if ch and end > o:
            out.append({"o": o, "end": min(end, length), "ch": ch})
    return out


def cycle_events(req: dict, settings: dict) -> tuple[list, float]:
    """Events (seconds, on/off, channel, key, velocity) for one practice cycle."""
    seq = req["seq"]
    tempo = float(req.get("tempo") or 120)
    swing = float(req.get("swing") or 0.62)
    spq = 60.0 / tempo
    bpb = int((seq.get("ts") or [4, 4])[0])
    lead_beats = int(req.get("leadIn") or 0) * bpb
    L = float(seq["length"])
    R = L if req.get("response") else 0.0
    backing = req.get("backing")
    rng = random.Random(int(req.get("seed") or 0))
    ev: list = []

    def note(ch, key, start_q, dur_q, vel, sw=True):
        a = (warp(start_q, swing) if sw else start_q) * spq
        b = (warp(start_q + dur_q, swing) if sw else start_q + dur_q) * spq
        ev.append((a, "on", ch, int(key), int(max(1, min(127, vel)))))
        ev.append((max(a + 0.03, b), "off", ch, int(key), 0))

    # count-in: side stick on every beat
    for b in range(lead_beats):
        note(CH_DRUMS, SIDE_STICK, b, 0.3, 100 if b % bpb == 0 else 75, sw=False)
    t0 = lead_beats
    if req.get("playLick", True):
        notes = sorted(seq["notes"])
        for i, (o, d, m) in enumerate(notes):
            nxt = notes[i + 1][0] if i + 1 < len(notes) else o + d
            dur = min(d, nxt - o) * 0.97 if nxt > o else d * 0.97
            accent = 8 if abs(o - round(o)) > 1e-3 else 0  # jazz: lean on the off-beats
            note(CH_LEAD, m, t0 + o, max(0.08, dur), 92 + accent + rng.randint(-4, 4))
        for o, m in seq.get("graces") or []:  # grace notes: a quick note just before the beat
            g = min(0.12, t0 + o)
            if g > 0.01:
                note(CH_LEAD, m, t0 + o - g, g, 80)
    if backing:
        sp = spans_of(seq.get("chords") or [], L)
        for rep in range(2 if R else 1):
            off = t0 + rep * L
            if backing == "piano":
                _piano_only(note, sp, off, bpb)
            else:
                _comp(note, sp, off, bpb, rng)
                _walk(note, sp, off, L, rng)
                _drums(note, off, L, bpb, rng)
    total = (lead_beats + L + R) * spq
    return ev, total


def _piano_only(note, spans, off, bpb):
    for s in spans:
        root = s["ch"]["bass"] + 36
        if root < 40:
            root += 12
        v = [root] + voicing(s["ch"], 0)
        p = s["o"]
        while p < s["end"] - 1e-6:
            d = min(bpb, s["end"] - p)
            for k in v:
                note(CH_PIANO, k, off + p, d * 0.98, 62 if k > root else 70, sw=False)
            p += bpb


def _comp(note, spans, off, bpb, rng):
    """Light swing comping: hits on beats and anticipations, changing between A/B voicings."""
    patterns = [[(0, 1.2)], [(0, 0.4), (1.5, 0.9)], [(-0.5, 1.0)], [(1.5, 0.5), (3.0, 0.8)], [(0.5, 0.4), (2.0, 1.5)]]
    form = 0
    for s in spans:
        length = s["end"] - s["o"]
        p = 0.0
        while p < length - 1e-6:
            pat = rng.choice(patterns) if length - p >= 2 else [(0, min(1.0, length - p))]
            for at, d in pat:
                start = s["o"] + p + at
                if start < s["o"] - 0.6 or start >= s["end"]:
                    continue
                for k in voicing(s["ch"], form):
                    note(CH_PIANO, k, off + start, min(d, s["end"] - start + 0.5), 58 + rng.randint(-6, 6))
            p += min(4, length - p) if length - p >= 4 else 2
        form ^= 1


def _walk(note, spans, off, L, rng):
    """Walking bass: root on the change, chord/scale tones, chromatic approach into the next root."""
    prev = None
    for i, s in enumerate(spans):
        ch = s["ch"]
        nxt = spans[i + 1]["ch"] if i + 1 < len(spans) else spans[0]["ch"]
        beats = [b for b in range(int(round(s["o"])), int(round(s["end"])))]
        for k, b in enumerate(beats):
            last = k == len(beats) - 1 and len(beats) > 1
            if k == 0:
                pc = ch["bass"]
            elif last:
                tgt = nxt["bass"]
                pc = tgt + (1 if rng.random() < 0.5 else -1)
            else:
                choice = [THIRD[ch["q"]], FIFTH[ch["q"]], SCALE[ch["q"]][1], SCALE[ch["q"]][5 % len(SCALE[ch["q"]])]]
                pc = ch["pc"] + rng.choice(choice[:2] if k == 1 else choice)
            # nearest octave to the previous note, kept in E1..D3
            cands = [x for x in range(28, 51) if x % 12 == pc % 12]
            m = min(cands, key=lambda x: abs(x - (prev if prev is not None else 38)))
            prev = m
            note(CH_BASS, m, off + b, 0.92, 96 if k == 0 else 84, sw=False)


def _drums(note, off, L, bpb, rng):
    for q in range(int(round(L))):
        beat = q % bpb
        note(CH_DRUMS, RIDE, off + q, 0.5, 72 if beat in (1, 3) else 64, sw=False)
        if beat % 2 == 1:
            note(CH_DRUMS, RIDE, off + q + 0.5, 0.3, 50)  # swung skip note
            note(CH_DRUMS, HAT_FOOT, off + q, 0.3, 70, sw=False)
        note(CH_DRUMS, KICK, off + q, 0.3, 22, sw=False)  # feathered bass drum
