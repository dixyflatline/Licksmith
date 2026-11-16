"""Align: line the bars of a transcription up with the real recording, then fix them bar by bar.

The workflow (see README, "Align"):
  1. Muscriptor's MIDI keeps both the horn and the bass tracks (engines.extract_bass).
  2. The page shows the waveform, the bass notes, the horn notes and draggable bar lines. You line
     each bar line up with the bass downbeat. Every move re-quantizes the notes to *your* bar times,
     which is what cures the drift a constant-tempo grid causes.
  3. Each bar is one piece of ABC text (written pitch for the trumpet). Edit it (or drag a note up or
     down) and the bar is *locked*: moving bar lines later never overwrites it.

State lives in library/works/<work>/align.json:
  {"engine": "muscriptor-medium", "bar_times": [s, ...],          # N+1 times, N = bars to transcribe
   "bars": {"3": {"abc": "...", "locked": true}}}                  # only the bars you edited
Bars are numbered 1..N *inside the transcribed section* (bar 1 = first bar of the solo).
"""
from __future__ import annotations

import json
import re
from fractions import Fraction as F
from pathlib import Path

import numpy as np

from . import capture as C
from .engines import RANGES, drop_chords, in_range, load_notes
from .form import load_form

LETTERS = "CDEFGAB"
_WRITTEN_SEMIS = {"trumpet": 2, "tenor_sax": 14, "alto_sax": 9, "trombone": 0}  # concert -> written, in semitones
PEAKS_PER_SEC = 100
BASS_OVERLAP = 0.05  # seconds two bass-track notes may overlap before the upper one counts as melody
BASS_TOP = 55  # MIDI pitch: bass-track notes at or above this (G3) are really the melody


def _strip_bar(abc: str) -> str:
    return re.sub(r"[\s|\]:]+$", "", abc.strip()).strip()


class AlignWork:
    def __init__(self, work_dir: Path, charts_json: Path | None = None):
        self.dir = Path(work_dir)
        self.name = self.dir.name
        self.form = load_form(self.dir / "form.json", charts_json)
        self.bpb = self.form.beats_per_bar
        self.instrument = self.form.data.get("instrument", "trumpet")
        # a recording made on the Transcribe page has no chord chart until you take one from iReal Pro
        self.free = bool(self.form.data.get("free_form")) and not self.form.data.get("chorus_source")
        self.audio = self.dir / (self.form.data.get("source", {}).get("audio") or "audio.wav")
        self.bars = self.form.transcribed_bars()
        if not self.bars:
            raise ValueError("form.json has no section with transcribe: true")
        self.n = len(self.bars)
        self.first_global = self.bars[0].number
        self.state_path = self.dir / "align.json"
        self.state = self._load_state()
        self._notes_cache: dict[str, tuple[list, list]] = {}
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._auto_cache: tuple | None = None

    # ------------------------------------------------------------------ engines and notes
    def engines(self) -> list[str]:
        base = self.dir / "engines"
        return sorted(p.name for p in base.glob("*") if (p / "notes.json").exists() and (p / "raw.mid").exists()) \
            if base.exists() else []

    def sharps(self) -> int:
        a = self.state.get("sharps")
        if a is not None:
            return int(a)
        if str(self.form.data.get("key_signature", "")).lower() == "none":
            return 0
        key = self.form.data.get("key")
        if key:
            from music21 import pitch

            m = re.match(r"([A-Ga-g][b#]?)\s*(m|min|minor)?", str(key))
            if not m:
                return 0
            pc = (pitch.Pitch(m.group(1).upper().replace("b", "-")).pitchClass + _WRITTEN_SEMIS.get(self.instrument, 0)) % 12
            return C.sharps_for(pc, "minor" if m.group(2) else "major")
        return 0

    def _load_state(self) -> dict:
        try:
            st = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            st = {}
        if not st.get("engine"):
            eng = self.engines()
            pref = [e for e in eng if e == "muscriptor-medium"] or eng
            st["engine"] = pref[0] if pref else None
        st.setdefault("bars", {})
        if len(st.get("bar_times") or []) != self.n + 1:
            st["bar_times"] = self._initial_times()
        return st

    def _snap(self) -> dict:
        return json.loads(json.dumps({"bar_times": self.state["bar_times"], "bars": self.state["bars"]}))

    def _push(self) -> None:
        """Remember the bar lines and your bar edits before a change (⌘Z)."""
        self._undo.append(self._snap())
        del self._undo[:-200]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snap())
        self.state.update(self._undo.pop())
        self._auto_cache = None
        self.save()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snap())
        self.state.update(self._redo.pop())
        self._auto_cache = None
        self.save()
        return True

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def _initial_times(self) -> list[float]:
        """Start from the constant-tempo (or anchored) grid in form.json; you then drag it onto the music."""
        from .grid import build_grid

        grid = build_grid(self.form, None)
        times = [float(grid[(self.first_global - 1 + i) * self.bpb]) for i in range(self.n + 1)]
        return [round(t, 4) for t in times]

    def notes(self) -> tuple[list[dict], list[dict]]:
        """(horn notes, bass notes) of the chosen engine, in seconds."""
        eng = self.state.get("engine")
        if not eng:
            return [], []
        if eng not in self._notes_cache:
            from .engines import extract_bass

            ed = self.dir / "engines" / eng
            horn_all = load_notes(ed)
            bass_path = ed / "bass.json"
            if not bass_path.exists() and (ed / "raw.mid").exists():
                try:  # a transcription of only part of the recording starts at its window start
                    ej = json.loads((ed / "engine.json").read_text()).get("window")
                    offset = max(0.0, float(ej[0])) if ej else 0.0
                except (OSError, ValueError, TypeError, IndexError):
                    offset = 0.0
                bass_path.write_text(json.dumps(extract_bass(ed / "raw.mid", offset)))
            bass = json.loads(bass_path.read_text()) if bass_path.exists() else []
            # The model sometimes files the melody under the bass in a stretch of the tune. A bass line stays low, so notes
            # above BASS_TOP in the bass track are taken as horn notes (the horn filters below then tidy them up).
            # A bass plays one note at a time: where two bass-track notes sound together, the upper one is the melody.
            up = [n for n in bass if n["pitch"] >= BASS_TOP or any(
                m is not n and m["pitch"] < n["pitch"] and min(m["offset"], n["offset"]) - max(m["onset"], n["onset"]) > BASS_OVERLAP
                for m in bass if abs(m["onset"] - n["onset"]) < 4)]
            ids = {id(n) for n in up}
            horn_all = sorted(horn_all + [dict(n) for n in up], key=lambda n: n["onset"])
            bass = [n for n in bass if id(n) not in ids]
            horn = in_range(horn_all, self.instrument)
            horn, _ = drop_chords(horn)
            self._notes_cache[eng] = (horn, bass)
        return self._notes_cache[eng]

    def set_engine(self, eng: str) -> None:
        if eng not in self.engines():
            raise ValueError(f"No such engine: {eng}")
        self.state["engine"] = eng
        self._auto_cache = None
        self.save()

    # ------------------------------------------------------------------ waveform
    def peaks(self) -> dict:
        cache = self.dir / "align_peaks.json"
        if cache.exists() and cache.stat().st_mtime >= self.audio.stat().st_mtime:
            return json.loads(cache.read_text())
        import soundfile as sf

        y, sr = sf.read(str(self.audio), always_2d=True)
        y = y.mean(axis=1)
        hop = max(1, int(sr / PEAKS_PER_SEC))
        n = len(y) // hop
        blocks = y[: n * hop].reshape(n, hop)
        mx, mn = blocks.max(axis=1), blocks.min(axis=1)
        top = float(max(np.abs(mx).max(), np.abs(mn).max())) or 1.0
        out = {"per_sec": sr / hop, "duration": len(y) / sr,
               "max": [round(float(v / top), 3) for v in mx], "min": [round(float(v / top), 3) for v in mn]}
        cache.write_text(json.dumps(out))
        return out

    # ------------------------------------------------------------------ bar times
    def set_times(self, times: list[float]) -> None:
        if len(times) != self.n + 1:
            raise ValueError(f"Need {self.n + 1} bar-line times, got {len(times)}")
        t = [float(x) for x in times]
        if any(b - a < 0.3 for a, b in zip(t, t[1:])):
            raise ValueError("Bar lines must stay in order and at least 0.3 s apart")
        self._push()
        self.state["bar_times"] = [round(x, 4) for x in t]
        self._auto_cache = None
        self.save()

    def _grid(self) -> np.ndarray:
        """Beat times for every bar of the form, from your bar lines (extended with the nearest bar length)."""
        times = self.state["bar_times"]
        bpb = self.bpb
        nb = len(self.form.bars)
        bar_t = []
        first_len, last_len = times[1] - times[0], times[-1] - times[-2]
        for g in range(1, nb + 2):  # global bar g starts at bar_t[g-1]
            k = g - self.first_global  # index into times
            if k < 0:
                bar_t.append(times[0] + k * first_len)
            elif k <= self.n:
                bar_t.append(times[k])
            else:
                bar_t.append(times[-1] + (k - self.n) * last_len)
        grid = []
        for i in range(nb):
            a, b = bar_t[i], bar_t[i + 1]
            grid += [a + (b - a) * j / bpb for j in range(bpb)]
        grid.append(bar_t[nb])
        return np.asarray(grid, dtype=float)

    # ------------------------------------------------------------------ auto transcription
    def auto_abc(self) -> list[str]:
        """ABC of every bar, quantized to the current bar lines (ignores your locked edits)."""
        if self._auto_cache is not None:
            return self._auto_cache
        from .quantize import quantize_notes
        from .xmlout import build_score

        horn, _ = self.notes()
        grid = self._grid()
        q = quantize_notes(horn, grid, self.form, simplicity=1.0)
        numbers = [b.number for b in self.bars]
        score = build_score(q, self.form, numbers, self.instrument, concert=False, renumber=True)
        abc = C.score_to_abc(score, self.sharps())
        segs = [s.strip() for s in re.split(r"\s\|\]?\s?|\s\|\]$", abc.strip())]
        segs = [s for i, s in enumerate(segs) if s or i < self.n]
        if len(segs) != self.n:  # fall back to one call per measure (slower but exact)
            from music21 import stream

            segs = []
            part = score.parts[0]
            for m in part.getElementsByClass(stream.Measure):
                sc = stream.Score()
                p = stream.Part()
                p.append(m)
                sc.insert(0, p)
                segs.append(_strip_bar(C.score_to_abc(sc, self.sharps())))
        segs = [_strip_bar(s) for s in segs][: self.n]
        if self.free:  # no chart: no "N.C." over the bars
            segs = [re.sub(r'"N\.C\."', "", s) for s in segs]
        segs += ["z4"] * (self.n - len(segs))
        self._auto_cache = segs
        return segs

    def bars_view(self) -> list[dict]:
        auto = self.auto_abc()
        out = []
        for i, b in enumerate(self.bars):
            st = self.state["bars"].get(str(i + 1))
            locked = bool(st and st.get("locked"))
            abc = st["abc"] if locked else auto[i]
            out.append({"n": i + 1, "global": b.number, "chorus": b.chorus, "bar_in_chorus": b.bar_in_chorus,
                        "t0": self.state["bar_times"][i], "t1": self.state["bar_times"][i + 1],
                        "locked": locked, "abc": abc, "chords": [] if self.free else _written_chords(b.chords, self.instrument),
                        **self.bar_info(abc)})
        return out

    # ------------------------------------------------------------------ one bar
    def meter(self) -> str:
        return f"{self.bpb}/4"

    def bar_info(self, abc: str) -> dict:
        """Where each note of a bar starts (in beats) and its concert MIDI pitch, for playback and the cursor."""
        try:
            parsed = C.parse_abc(abc + " |]", sharps=self.sharps(), meter_str=self.meter())
        except Exception as e:  # noqa: BLE001
            return {"notes": [], "issues": [{"bar": 1, "msg": str(e)}]}
        notes, pos = [], F(0)
        semis = _WRITTEN_SEMIS.get(self.instrument, 0)
        for ev in (parsed["measures"][0] if parsed["measures"] else []):
            if ev["type"] == "note":
                from music21 import pitch

                notes.append({"beat": float(pos), "len": float(ev["ql"]), "midi": pitch.Pitch(ev["name"]).midi - semis,
                              "grace": len(ev.get("grace") or []),
                              "graces": [pitch.Pitch(g["name"]).midi - semis for g in ev.get("grace") or []]})
            else:  # rests are selectable too: midi None
                notes.append({"beat": float(pos), "len": float(ev["ql"]), "midi": None, "rest": True})
            pos += ev["ql"]
        return {"notes": notes, "issues": parsed["issues"], "beats_used": float(pos)}

    def set_bar_abc(self, k: int, abc: str) -> dict:
        self._check(k)
        abc = _strip_bar(abc)
        info = self.bar_info(abc)
        if any("msg" in i and i.get("bar") == 1 and "Traceback" in i["msg"] for i in info["issues"]):
            raise ValueError(info["issues"][0]["msg"])
        self._push()
        self.state["bars"][str(k)] = {"abc": abc, "locked": True}
        self.save()
        return self.bars_view()[k - 1]

    def unlock(self, k: int) -> dict:
        self._check(k)
        self._push()
        self.state["bars"].pop(str(k), None)
        self.save()
        return self.bars_view()[k - 1]

    def shift_note(self, k: int, index: int, steps: int, chromatic: bool = False) -> dict:
        """Move the index-th note of bar k by `steps`. Default: scale steps, spelled with the key signature
        (a dragged blue note becomes the plain letter). chromatic=True: semitones, spelled with flats
        (sharps in a sharp key), so a Bb can be dragged down to A and back."""
        self._check(k)
        view = self.bars_view()[k - 1]
        parsed = C.parse_abc(view["abc"] + " |]", sharps=self.sharps(), meter_str=self.meter())
        evs = parsed["measures"][0]
        if not 0 <= index < len(evs) or evs[index]["type"] != "note":
            raise ValueError("Select a note, not a rest")
        ev = evs[index]
        from music21 import pitch

        p = pitch.Pitch(ev["name"])
        if chromatic:
            q = pitch.Pitch(p.midi + steps)
            if q.accidental is not None and q.accidental.alter > 0 and self.sharps() <= 0:
                q = q.getEnharmonic()
            ev["name"] = q.nameWithOctave
        else:
            pos = p.octave * 7 + LETTERS.index(p.step) + steps
            letter, octave = LETTERS[pos % 7], pos // 7
            alter = {"#": 1, "-": -1, "": 0}[C.key_accidentals(self.sharps()).get(letter, "")[:1]]
            ev["name"] = letter + ("#" * alter if alter > 0 else "-" * -alter) + str(octave)
        sc = C.build_score(parsed, "concert", "concert")
        return self.set_bar_abc(k, C.score_to_abc(sc, self.sharps()))

    # ------------------------------------------------------------------ keyboard edits (MuseScore-style)
    def edit_note(self, k: int, index: int, op: str, value=None) -> dict:
        """One keyboard edit on the index-th note of bar k (rests are not counted). Ops:
        semitone/step (value = +-n), octave (+-1), letter ("A".."G": nearest octave), length (quarter-lengths,
        e.g. "1/2"), dot, triplet (the whole beat of the note, toggle), rest (turn into a rest)."""
        self._check(k)
        view = self.bars_view()[k - 1]
        parsed = C.parse_abc(view["abc"] + " |]", sharps=self.sharps(), meter_str=self.meter())
        events = parsed["measures"][0] if parsed["measures"] else []
        if not 0 <= index < len(events):
            raise ValueError("No such note")
        events = apply_edit(events, index, op, value, self.sharps(), self.bpb)
        parsed["measures"][0] = events
        sc = C.build_score(parsed, "concert", "concert")
        out = self.set_bar_abc(k, C.score_to_abc(sc, self.sharps()))
        return out

    def _check(self, k: int) -> None:
        if not 1 <= k <= self.n:
            raise ValueError(f"Bar {k} is outside 1..{self.n}")

    # ------------------------------------------------------------------ helpers for the page
    def snap_to_bass(self, reach: float = 0.25) -> list[float]:
        """Slide each bar line (up to `reach` s) to where the bass notes around it fit best.

        A walking bass plays on every beat, so the nearest bass note is often the wrong beat and a fixed window
        does nothing when the grid has drifted. Instead, for each line we try shifts and score how well the
        beats around it (the downbeat counts double) land on bass onsets; two passes, as lines affect neighbours."""
        _, bass = self.notes()
        onsets = np.array(sorted(n["onset"] for n in bass))
        times = list(self.state["bar_times"])
        if not len(onsets):
            return times
        cap = 0.12

        def cost(pos: float) -> float:
            return min(float(np.min(np.abs(onsets - pos))), cap)

        for _ in range(2):
            for i in range(len(times)):
                prev_len = (times[i] - times[i - 1]) if i else (times[1] - times[0])
                next_len = (times[i + 1] - times[i]) if i + 1 < len(times) else prev_len
                beats = [(0.0, 2.0)] + [(k * next_len / self.bpb, 1.0) for k in range(1, self.bpb)] \
                    + [(-k * prev_len / self.bpb, 1.0) for k in range(1, self.bpb)]
                best, bd = 0.0, None
                for d in np.arange(-reach, reach + 1e-9, 0.01):
                    c = sum(w * cost(times[i] + d + off) for off, w in beats) + 0.05 * abs(d)
                    if bd is None or c < bd - 1e-9:
                        best, bd = float(d), c
                times[i] += best
            for i in range(1, len(times)):  # keep strictly increasing
                times[i] = max(times[i], times[i - 1] + 0.31)
        return times

    def summary(self) -> dict:
        horn, bass = self.notes()
        return {"work": self.name, "title": self.form.data.get("title", self.name),
                "artist": self.form.data.get("artist", ""), "tune": self.form.data.get("tune") or self.form.data.get("title", ""), "bpb": self.bpb, "n": self.n,
                "instrument": self.instrument, "sharps": self.sharps(), "meter": self.meter(),
                "free_form": self.free, "bars_editable": bool(self.form.data.get("free_form")), "chart_offset": self.form.data.get("chart_offset") if self.form.data.get("chorus_source") else None, "chart_len": len(self.form.data.get("chorus") or []), "ensemble": self.form.data.get("ensemble", []), "bpm": self.form.bpm, "key": self.form.data.get("key", ""),
                "first_downbeat": float((self.form.data.get("grid") or {}).get("first_downbeat", 0.0)),
                "engine": self.state.get("engine"), "engines": self.engines(),
                "times": self.state["bar_times"], "bars": self.bars_view(),
                "horn": [[round(n["onset"], 3), round(n["offset"], 3), n["pitch"]] for n in horn],
                "bass": [[round(n["onset"], 3), round(n["offset"], 3), n["pitch"]] for n in bass],
                "written_shift": _WRITTEN_SEMIS.get(self.instrument, 0)}

    def send_to_library(self, lib, a: int, b: int, tags: list[str] | None = None, pad: float = 0.1,
                        artist: str | None = None, tune: str | None = None) -> dict:
        """Join bars a..b (1-3 adjacent bars) into one lick in the library, with that piece of the recording."""
        if not (1 <= a <= b <= self.n):
            raise ValueError("Pick bars inside the solo")
        if not 1 <= b - a + 1 <= 3:
            raise ValueError("A lick is 1 to 3 adjacent bars")
        view = self.bars_view()[a - 1:b]
        abcs = [v["abc"] or "z4" for v in view]
        if '"' not in abcs[0] and view[0]["chords"]:
            abcs[0] = f'"{view[0]["chords"][0]}"' + abcs[0]
        t0, t1 = view[0]["t0"] - pad, view[-1]["t1"] + pad
        fd = self.form.data
        album = str(fd.get("album", ""))
        year = next(iter(re.findall(r"(19\d\d|20\d\d)", album)), None)
        g0, g1 = view[0]["global"], view[-1]["global"]
        meta = {"artist": fd.get("artist", "") if artist is None else artist.strip(),
                "tune": (fd.get("tune") or fd.get("title", "")) if tune is None else tune.strip(),
                "year": int(year) if year else None, "tags": tags or [], "source": None,
                "source_bars": f"{g0}-{g1}" if g1 != g0 else str(g0),
                "comment": f"Solo bar{'s' if b != a else ''} {a if a == b else f'{a}-{b}'} ({self.name}), {t0 + pad:.1f}-{t1 - pad:.1f} s of the recording"}
        audio = (self.audio, t0, t1) if self.audio.exists() else None
        bars_at = [v["t0"] - t0 for v in view] + [view[-1]["t1"] - t0]
        return lib.add_lick_from_abc(" | ".join(abcs) + " |]", self.sharps(), self.meter(), meta, audio, bars_at)

    # ------------------------------------------------------------------ chords from an iReal chart
    def _bass_by_bar(self) -> list[dict]:
        """Per bar: the bass pitch classes heard on beat 1 (weight 1) and beat 3 (weight .5), from your bar lines."""
        _, bass = self.notes()
        T = self.state["bar_times"]
        out = []
        for i in range(self.n):
            beat = (T[i + 1] - T[i]) / self.bpb
            pcs: dict[int, float] = {}
            for n in bass:
                x = (n["onset"] - T[i]) / beat  # position in beats from the bar line
                if -0.25 <= x < self.bpb - 0.25:
                    w = 2.0 if abs(x) <= 0.5 or abs(x - 2) <= 0.5 else 1.0  # downbeat and beat 3 count double
                    pcs[n["pitch"] % 12] = pcs.get(n["pitch"] % 12, 0) + w
            out.append(pcs)
        return out

    def _score(self, chorus: list[list[str]], rotation: int, bass: list[dict]) -> tuple[float, list[dict]]:
        """Share of bars whose bass root is the root of the chart's chord (either chord of a two-chord bar; the bass note of a
        slash chord counts too). A bar also counts when the chart's root is a large part of what the bass plays in it."""
        total = got = 0
        per = []
        loose = bool(self.form.data.get("free_form"))  # no chorus structure: the chart simply runs from bar 1
        for i, b in enumerate(self.bars):
            pos = b.bar_in_chorus - 1 if b.bar_in_chorus is not None else (i if loose else None)
            if pos is None or not bass[i]:
                continue
            chords = chorus[(pos + rotation) % len(chorus)]
            roots = {pc for c in chords for pc in _chord_root_pcs(c)}
            w = bass[i]
            top = max(w, key=w.get)
            ok = top in roots or sum(w.get(r, 0) for r in roots) >= 0.4 * sum(w.values())
            total += 1
            got += ok
            per.append({"n": i + 1, "chord": chords[0], "bass": top, "ok": ok})
        return (got / total if total else 0.0), per

    def check_chords(self) -> dict:
        """How well the chords in form.json agree with the bass notes, and which bars disagree."""
        chorus = self.form.data["chorus"]
        score, per = self._score(chorus, 0, self._bass_by_bar())
        names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
        return {"score": round(score, 3), "bad": [{"bar": p["n"], "chord": p["chord"], "bass": names[p["bass"]]}
                                                  for p in per if not p["ok"]]}

    def detect_chart(self, title: str, charts_json: Path) -> dict:
        """Try the chart in all 12 keys and every starting bar; rank by how often the bass plays its chord tones."""
        chart = _chart(title, charts_json)
        bars = chart["bars"]
        L_ = len(bars)
        bass = self._bass_by_bar()
        if not any(bass):
            raise ValueError("No bass notes to compare with (this transcription has no bass track)")
        flats = _prefers_flats_key(self.form.data.get("key"))
        base_pc = _key_pc(chart.get("key"))
        target_pc = _key_pc(self.form.data.get("key"))
        res = []
        for t in range(12):
            tb = [[_transpose_plain(c, t, flats) for c in bar] for bar in bars]
            for r in range(L_):
                sc, _ = self._score(tb, r, bass)
                res.append((sc, t, r))
        exp0 = (target_pc - base_pc) % 12 if base_pc is not None and target_pc is not None else None
        res.sort(key=lambda x: (x[0], x[1] == exp0, x[2] == 0), reverse=True)  # ties: the expected key, then chart bar 1
        out = []
        for sc, t, r in res[:5]:
            tb = [[_transpose_plain(c, t, flats) for c in bars[(i + r) % L_]] for i in range(L_)]
            out.append({"score": round(sc, 3), "transpose": t, "rotation": r, "chorus": tb,
                        "key": _name_pc((base_pc + t) % 12, flats) if base_pc is not None else None,
                        "expected": base_pc is not None and target_pc is not None and t == (target_pc - base_pc) % 12})
        exp = (target_pc - base_pc) % 12 if base_pc is not None and target_pc is not None else 0
        tb = [[_transpose_plain(c, exp, flats) for c in bar] for bar in bars]
        gap = (out[0]["score"] - out[1]["score"]) if len(out) > 1 else 0
        return {"title": chart["title"], "chart_key": chart.get("key"), "bars": L_, "candidates": out,
                "suggested": {"transpose": exp, "rotation": 0, "chorus": tb,
                              "score": round(self._score(tb, 0, bass)[0], 3)},
                "confident": out[0]["score"] >= 0.45 and gap > 0.04,  # the bass only decides when one reading clearly stands out
                "counted": sum(1 for x in bass if x),
                "chorus_len": len(self.form.data["chorus"])}

    def preview_chart(self, title: str, charts_json: Path, transpose: int | None = None, rotation: int = 0) -> dict:
        """The chart's chords in the chosen key, starting at the chosen chart bar, without changing anything."""
        chart = _chart(title, charts_json)
        bars = chart["bars"]
        L_ = len(bars)
        base_pc = _key_pc(chart.get("key"))
        target_pc = _key_pc(self.form.data.get("key"))
        if transpose is None:
            transpose = (target_pc - base_pc) % 12 if base_pc is not None and target_pc is not None else 0
        transpose = int(transpose) % 12
        rotation = int(rotation) % L_
        flats = _prefers_flats_key(self.form.data.get("key"))
        chorus = [[_transpose_plain(c, transpose, flats) for c in bars[(i + rotation) % L_]] for i in range(L_)]
        bass = self._bass_by_bar()
        score, per = self._score(chorus, 0, bass) if any(bass) else (None, [])
        return {"bad": [p["n"] for p in per if not p["ok"]], "counted": len(per), "title": chart["title"], "chart_key": chart.get("key"), "chart_pc": base_pc, "bars": L_,
                "transpose": transpose, "rotation": rotation, "chorus": chorus,
                "score": None if score is None else round(score, 3), "n_bars": self.n}

    def apply_chart(self, title: str, charts_json: Path, transpose: int, rotation: int) -> None:
        """Write the chart's chords (transposed, starting at the right bar) into form.json as the chorus."""
        chart = _chart(title, charts_json)
        bars = chart["bars"]
        L_ = len(bars)
        flats = _prefers_flats_key(self.form.data.get("key"))
        chorus = [[_transpose_plain(c, int(transpose), flats) for c in bars[(i + int(rotation)) % L_]] for i in range(L_)]
        path = self.dir / "form.json"
        data = json.loads(path.read_text())
        data.setdefault("chorus_before_ireal", data.get("chorus"))
        data["chorus"] = chorus
        if data.get("free_form"):  # let the chart repeat over however many bars the recording has
            for sec in data.get("sections", []):
                if sec.get("transcribe"):
                    sec.pop("chords", None)
                    sec["loop_chorus"] = True
        data["chart_offset"] = int(rotation) % L_  # the chorus above starts at this chart bar (0 = the chart's own bar 1)
        data["chorus_source"] = f"iReal Pro: {chart['title']} (shifted {int(transpose)} semitones, starting at chart bar {int(rotation) + 1})"
        path.write_text(json.dumps(data, indent=2))
        old_chords = [b.chords for b in self.bars]
        self.form = load_form(path, None)
        self.free = bool(self.form.data.get("free_form")) and not self.form.data.get("chorus_source")
        self.bars = self.form.transcribed_bars()
        if len(self.bars) != self.n:
            self.n = len(self.bars)
            self.state["bar_times"] = self._initial_times()
        # locked bars carry their chord symbols inside the ABC: bring them along
        for k, st in self.state["bars"].items():
            i = int(k) - 1
            if i < len(self.bars):
                st["abc"] = _swap_chords(st["abc"], _written_chords(self.bars[i].chords, self.instrument))
        self._auto_cache = None
        self._undo.clear()
        self._redo.clear()
        self.save()
        del old_chords

    # ------------------------------------------------------------------ export
    def score(self):
        """Concert-pitch score of all bars (your edits win), for MusicXML export."""
        abc = " | ".join(b["abc"] or "z4" for b in self.bars_view()) + " |]"
        parsed = C.parse_abc(abc, sharps=self.sharps(), meter_str=self.meter())
        return C.build_score(parsed, self.instrument if self.instrument in C.BASES else "trumpet", "same")

    def export_musicxml(self) -> Path:
        from . import licks as L

        out = self.dir / "align_solo.musicxml"
        sc = self.score()
        sc.insert(0, __import__("music21").metadata.Metadata(title=self.form.data.get("title", self.name),
                                                             composer=self.form.data.get("artist", "")))
        return L.write_xml(L.for_display(sc, True), out)


_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_ROOT_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _name_pc(pc: int, flats: bool) -> str:
    return (_NAMES_FLAT if flats else _NAMES_SHARP)[pc % 12]


def _root_pc(name: str) -> int:
    return (_ROOT_PC[name[0].upper()] + (1 if name[1:2] == "#" else -1 if name[1:2] == "b" else 0)) % 12


def _key_pc(key) -> int | None:
    m = re.match(r"([A-Ga-g][b#]?)", str(key or ""))
    return _root_pc(m.group(1).upper() if len(m.group(1)) == 1 else m.group(1)[0].upper() + m.group(1)[1:]) if m else None


def _prefers_flats_key(key) -> bool:
    """Flat spellings unless the recording's key is a sharp key (G, D, A, E, B, F#, or their minors)."""
    m = re.match(r"([A-G][b#]?)\s*(m|min|minor|-)?", str(key or ""))
    if not m:
        return True
    pc = _root_pc(m.group(1))
    sharp_major = {7, 2, 9, 4, 11, 6}
    return ((pc + (3 if m.group(2) else 0)) % 12) not in sharp_major


def _transpose_plain(sym: str, semis: int, flats: bool) -> str:
    m = re.match(r"^([A-G][b#]?)(.*?)(?:/([A-G][b#]?))?$", sym)
    if not m or sym == "N.C.":
        return sym
    out = _name_pc(_root_pc(m.group(1)) + semis, flats) + m.group(2)
    if m.group(3):
        out += "/" + _name_pc(_root_pc(m.group(3)) + semis, flats)
    return out


_ROOT_CACHE: dict[str, tuple[int, ...]] = {}


def _chord_root_pcs(sym: str) -> tuple[int, ...]:
    """Pitch classes the bass may play for a chord symbol: the slash bass note if there is one, and the root."""
    if sym not in _ROOT_CACHE:
        m = re.match(r"^([A-G][b#]?)(.*?)(?:/([A-G][b#]?))?$", sym)
        _ROOT_CACHE[sym] = tuple(_root_pc(x) for x in ((m.group(3), m.group(1)) if m and m.group(3) else (m.group(1),) if m else ()))
    return _ROOT_CACHE[sym]


def _chord_tones(sym: str) -> list[int]:
    """Pitch classes of a plain chord symbol: root first, then third, fifth, seventh."""
    m = re.match(r"^([A-G][b#]?)(.*?)(?:/([A-G][b#]?))?$", sym)
    if not m:
        return [-1]
    r, q = _root_pc(m.group(1)), m.group(2)
    minor = q.startswith("m") and not q.startswith("maj")
    dim = "dim" in q or "m7b5" in q or "b5" in q
    tones = [r, (r + (3 if minor or dim else 4)) % 12, (r + (6 if dim else 7)) % 12]
    if "maj7" in q or "maj9" in q:
        tones.append((r + 11) % 12)
    elif "7" in q or "9" in q or "13" in q:
        tones.append((r + 10) % 12)
    elif "6" in q:
        tones.append((r + 9) % 12)
    return tones


def _chart(title: str, charts_json: Path) -> dict:
    charts = json.loads(Path(charts_json).read_text())
    key = next((k for k in charts if k.lower() == title.strip().lower()), None)
    if key is None:
        raise ValueError(f"'{title}' is not in the imported charts")
    return charts[key]


def search_charts(q: str, charts_json: Path, limit: int = 25) -> list[str]:
    import difflib

    try:
        titles = list(json.loads(Path(charts_json).read_text()))
    except (OSError, ValueError):
        return []
    ql = q.strip().lower()
    hits = [t for t in titles if ql and ql in t.lower()]
    if len(hits) < limit and ql:
        hits += [t for t in difflib.get_close_matches(q, titles, n=limit, cutoff=0.5) if t not in hits]
    return hits[:limit]


def _swap_chords(abc: str, chords: list[str]) -> str:
    """Replace the quoted chord symbols of a bar's ABC with the bar's new chords, in order."""
    found = re.findall(r'"[^"]*"', abc)
    if not chords:
        return abc
    if not found:
        return f'"{chords[0]}"' + abc
    it = iter(chords)
    return re.sub(r'"[^"]*"', lambda m: f'"{next(it, m.group(0).strip(chr(34)))}"', abc)


def apply_edit(events: list, i: int, op: str, value, sharps: int, bpb: int, following: dict | None = None) -> list:
    """One MuseScore-style edit on events[i] (notes and rests of one bar, as parse_abc gives them); returns the
    bar's new events. Ops: semitone/step (value = +-n), octave (+-1), letter ("A".."G": nearest octave),
    length (quarter-lengths, e.g. "1/2"), dot, triplet (the note's whole beat, toggle), tie (to the next note, toggle), rest.
    `following`: the first event of the next bar, when there is one (a tie may cross the bar line)."""
    before = [e["ql"] for e in events]  # lengths before this edit (edits below change them in place)
    ev = events[i]
    from music21 import pitch

    if ev["type"] == "rest" and op in ("semitone", "step", "octave", "letter"):
        if op != "letter":
            raise ValueError("Type a note name (A-G) to turn this rest into a note")
        near = [e for e in events[:i][::-1] if e["type"] == "note"] + [e for e in events[i + 1:] if e["type"] == "note"]
        ev = events[i] = {"type": "note", "name": near[0]["name"] if near else "B4", "ql": ev["ql"], "tie": False,
                          "chord": ev.get("chord")}
    if op in ("semitone", "step", "octave", "letter"):
        p = pitch.Pitch(ev["name"])
        if op == "semitone":
            q = pitch.Pitch(p.midi + int(value))
            if q.accidental is not None and q.accidental.alter > 0 and sharps <= 0:
                q = q.getEnharmonic()
            ev["name"] = q.nameWithOctave
        elif op == "octave":
            ev["name"] = f"{p.name}{p.octave + int(value)}"
        else:
            if op == "step":
                dpos = p.octave * 7 + LETTERS.index(p.step) + int(value)
            else:
                t = LETTERS.index(str(value).upper())
                cur = p.octave * 7 + LETTERS.index(p.step)
                dpos = min((o * 7 + t for o in range(p.octave - 1, p.octave + 2)), key=lambda x: abs(x - cur))
            letter, octave = LETTERS[dpos % 7], dpos // 7
            alter = {"#": 1, "-": -1, "": 0}[C.key_accidentals(sharps).get(letter, "")[:1]]
            ev["name"] = letter + ("#" * alter if alter > 0 else "-" * -alter) + str(octave)
    elif op == "rest":
        if ev["type"] == "rest":
            return events
        events[i] = {"type": "rest", "ql": ev["ql"], "tie": False, "chord": ev.get("chord")}
    elif op in ("length", "dot"):
        ql = ev["ql"]
        if op == "length":
            new = F(str(value))
        else:
            undotted = ql * 2 / 3
            pow2 = lambda n: n > 0 and n & (n - 1) == 0  # noqa: E731
            is_dotted = pow2(undotted.numerator) and pow2(undotted.denominator)
            new = undotted if is_dotted else ql * 3 / 2
        if new <= 0:
            raise ValueError("Bad length")
        ev["ql"] = new
        events = _replace(events, i, i + 1, [ev], bpb, before)
    elif op == "triplet":
        events = _beat_triplet(events, i, bpb)
    elif op == "tie":
        # T ties the note to the next one (again = remove the tie). A tie joins two notes of the same pitch.
        if ev["type"] != "note":
            raise ValueError("Select a note to tie")
        if ev.get("tie"):
            ev["tie"] = False
        else:
            nxt = events[i + 1] if i + 1 < len(events) else following
            if not nxt or nxt["type"] != "note":
                raise ValueError("Nothing to tie to: a tie needs a note right after this one")
            if nxt["name"] != ev["name"]:
                raise ValueError("A tie joins two notes of the same pitch: make the next note the same pitch first")
            ev["tie"] = True
    elif op == "grace":
        # "/" adds a slashed grace note before the note, a scale step below it; pressing it again removes the grace note(s)
        if ev["type"] != "note":
            raise ValueError("Select a note to put a grace note before")
        if ev.get("grace"):
            ev["grace"] = []
        else:
            p = pitch.Pitch(ev["name"])
            dpos = p.octave * 7 + LETTERS.index(p.step) - 1
            letter, octave = LETTERS[dpos % 7], dpos // 7
            alter = {"#": 1, "-": -1, "": 0}[C.key_accidentals(sharps).get(letter, "")[:1]]
            slash = not (isinstance(value, dict) and value.get("slash") is False)
            ev["grace"] = [{"name": letter + ("#" * alter if alter > 0 else "-" * -alter) + str(octave), "slash": slash}]
    else:
        raise ValueError(f"Unknown edit: {op}")
    return events


def _beat_triplet(events: list, i: int, bar_len: int) -> list:
    """Cmd+3: turn the beat holding events[i] into a triplet structure, whatever it holds; again = back to straight.

    The notes of the beat keep their order and pitches (rests are rebuilt):
      1 note -> note + 2 rests (triplet eighths)       2 notes -> long-short swing triplet (2:1)
      3 notes -> three triplet eighths                  4-6 notes -> six triplet sixteenths (spare slots = rests)
    Back to straight: 1 note -> quarter, 2 -> two eighths, 3 -> eighth + two sixteenths, 4+ -> four sixteenths."""
    starts, pos = [], F(0)
    for e in events:
        starts.append(pos)
        pos += e["ql"]
    beat = int(starts[i])
    lo = next((k for k, st in enumerate(starts) if st == beat), None)
    hi = next((k for k, st in enumerate(starts) if st == beat + 1), len(events) if pos == beat + 1 else None)
    if lo is None or hi is None:
        raise ValueError("A note crosses the beat line here; shorten it first, then press ⌘3")
    inbeat = events[lo:hi]
    notes = [dict(e) for e in inbeat if e["type"] == "note"]
    if not notes:
        raise ValueError("Select a note to make its beat a triplet")
    lead_chord = inbeat[0].get("chord")
    for e in notes:
        e["chord"] = None
    notes[0]["chord"] = lead_chord
    rest = lambda ql: {"type": "rest", "ql": ql, "tie": False, "chord": None}  # noqa: E731
    is_trip = any(e["ql"].denominator % 3 == 0 for e in inbeat)
    k = len(notes)
    if k > 6 or (is_trip and k > 4):
        notes = notes[:6 if not is_trip else 4]
        k = len(notes)
    if not is_trip:
        if k == 1:
            new = [dict(notes[0], ql=F(1, 3)), rest(F(1, 3)), rest(F(1, 3))]
        elif k == 2:
            new = [dict(notes[0], ql=F(2, 3)), dict(notes[1], ql=F(1, 3))]
        elif k == 3:
            new = [dict(n, ql=F(1, 3)) for n in notes]
        else:
            new = [dict(n, ql=F(1, 6)) for n in notes] + [rest(F(1, 6)) for _ in range(6 - k)]
    else:
        if k == 1:
            new = [dict(notes[0], ql=F(1))]
        elif k == 2:
            new = [dict(notes[0], ql=F(1, 2)), dict(notes[1], ql=F(1, 2))]
        elif k == 3:
            new = [dict(notes[0], ql=F(1, 2)), dict(notes[1], ql=F(1, 4)), dict(notes[2], ql=F(1, 4))]
        else:
            new = [dict(n, ql=F(1, 4)) for n in notes[:4]]
    for e in new:
        e.pop("_el", None)
    return events[:lo] + new + events[hi:]


def _replace(events: list, i: int, j: int, new: list, bar_len: int, before: list) -> list:
    """Swap events[i:j] for `new`, keeping the bar the same length: spare time becomes a rest right after
    the new notes, missing time is taken from the events that follow (like MuseScore overwriting)."""
    old = sum(before[i:j], F(0))
    gain = sum((e["ql"] for e in new), F(0)) - old
    out = events[:i] + new
    rest = [dict(e) for e in events[j:]]
    if gain < 0:
        out.append({"type": "rest", "ql": -gain, "tie": False, "chord": None})
    else:
        need = gain
        while rest and need > 0:
            if rest[0]["ql"] <= need:
                need -= rest.pop(0)["ql"]
            else:
                rest[0]["ql"] -= need
                need = 0
    out += rest
    if sum((e["ql"] for e in out), F(0)) > bar_len + 1e-9:
        raise ValueError("Not enough room left in this bar")
    return out


def _written_chords(chords: list[str], instrument: str) -> list[str]:
    from .chords import transpose_symbol

    iv = {"trumpet": "M2", "tenor_sax": "M9", "alto_sax": "M6"}.get(instrument)
    out = []
    for c in chords:
        try:
            out.append(transpose_symbol(c, iv) if iv and c != "N.C." else c)
        except Exception:  # noqa: BLE001
            out.append(c)
    return out


def update_grid(work_dir: Path, bpm: float | None = None, first_downbeat: float | None = None, bars: int | None = None,
                beats_per_bar: int | None = None, key: str | None = None) -> None:
    """Change the tempo / first downbeat / number of bars / beats per bar / concert key of a work and put its bar lines back on an
    even grid (your locked bars are cleared: they belong to the old bar lines). Only works made on the Transcribe page can change
    their number of bars."""
    work_dir = Path(work_dir)
    path = work_dir / "form.json"
    data = json.loads(path.read_text())
    if bpm is not None:
        if not 30 <= float(bpm) <= 400:
            raise ValueError("Tempo must be between 30 and 400 BPM")
        data["bpm"] = round(float(bpm), 2)
    if first_downbeat is not None:
        if float(first_downbeat) < 0:
            raise ValueError("The first downbeat can't be before the start of the recording")
        data.setdefault("grid", {})["first_downbeat"] = round(float(first_downbeat), 3)
        data["grid"]["anchors"] = []
    if beats_per_bar is not None:
        if int(beats_per_bar) not in (2, 3, 4, 5, 6, 7):
            raise ValueError("Beats per bar: 2 to 7")
        data["beats_per_bar"] = int(beats_per_bar)
    if key is not None:
        k = key.strip()
        if k and not re.fullmatch(r"[A-Ga-g][b#]?(m|min|minor)?", k):
            raise ValueError('Key like "F", "Bb" or "Gm" (concert pitch)')
        if k:
            data["key"] = k[0].upper() + k[1:]
        else:
            data.pop("key", None)
    if bars is not None:
        if not data.get("free_form"):
            raise ValueError("The number of bars follows the chord chart for this recording")
        if not 4 <= int(bars) <= 800:
            raise ValueError("Bars: 4 to 800")
        for sec in data.get("sections", []):
            if sec.get("transcribe"):
                sec["bars"] = int(bars)
    path.write_text(json.dumps(data, indent=2))
    st_path = work_dir / "align.json"
    try:
        st = json.loads(st_path.read_text())
    except (OSError, ValueError):
        st = {}
    st.pop("bar_times", None)
    st.pop("sharps", None)
    st["bars"] = {}
    st_path.write_text(json.dumps(st, indent=1))


def list_works(library: Path) -> list[dict]:
    out = []
    base = Path(library) / "works"
    for d in sorted(base.glob("*")) if base.exists() else []:
        if not (d / "form.json").exists():
            continue
        has_audio = any((d / n).exists() for n in ("audio.wav",))
        try:
            fm = json.loads((d / "form.json").read_text())
        except (OSError, ValueError):
            fm = {}
        out.append({"work": d.name, "title": fm.get("title", d.name), "artist": fm.get("artist", ""),
                    "free_form": bool(fm.get("free_form")), "has_audio": has_audio, "engines": sorted(
            p.name for p in (d / "engines").glob("*") if (p / "raw.mid").exists()) if (d / "engines").exists() else [],
                    "aligned": (d / "align.json").exists()})
    return out
