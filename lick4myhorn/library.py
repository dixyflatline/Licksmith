"""Library service: ties the lick engine, database, scheduler and tune fitting together.

Folder layout (all under <project>/library):
  lick4myhorn.sqlite      practice database
  licks/L0043.musicxml    one file per lick, written for B-flat trumpet
  sources/                copies of the lick sheets / solos you imported
  charts/charts.json      iReal charts (shared with the transcription tools)
  practice/               generated practice sheets (12 keys, tune fits)
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import analysis as A
from . import fit as F
from . import licks as L
from . import lickexport as X
from . import similar
from . import srs
from .db import DB


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:60] or "untitled"


_PLACEHOLDERS = {"composer / arranger", "composer", "music21", "untitled score", "untitled", "title", "subtitle",
                 "lyricist", "arranger"}


def _real(text) -> str:
    """Metadata text unless it is a notation program's placeholder."""
    t = (text or "").strip()
    return "" if t.lower() in _PLACEHOLDERS or t.lower().endswith((".musicxml", ".xml", ".mxl")) else t


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lib = self.root / "library"
        for d in ("licks", "sources", "charts", "practice"):
            (self.lib / d).mkdir(parents=True, exist_ok=True)
        self.db = DB(self.lib / "lick4myhorn.sqlite")
        self._previews: dict[str, dict] = {}
        self._svg_cache: dict[tuple, str] = {}

    # ------------------------------------------------------------------ settings
    @property
    def settings(self) -> dict:
        return self.db.settings()

    # ------------------------------------------------------------------ licks
    def lick(self, lick_id: int) -> dict:
        lk = self.db.lick(lick_id)
        if lk is None:
            raise KeyError(lick_id)
        lk["_path"] = self.lib / lk["file"]
        card = self.db.one("SELECT * FROM cards WHERE lick_id=?", (lick_id,)) or {}
        for k in ("due", "reps", "lapses", "stability", "last_review", "last_rating", "introduced"):
            lk[k] = card.get(k)
        lk["state"] = srs.state_of(lk)
        return lk

    def list_licks(self, include_archived: bool = False) -> list[dict]:
        out = []
        for r in self.db.licks(include_archived):
            r.pop("data", None)
            r["state"] = srs.state_of(r)
            r["label"] = F.lick_label(r)
            r["all_tags"] = list(dict.fromkeys((r.get("tags") or []) + (r.get("auto_tags") or [])))
            out.append(r)
        return out

    # ------------------------------------------------------------------ import
    def preview_import(self, src: Path, filename: str, mode: str = "auto", ranges: str | None = None,
                       pitch_in_file: str = "auto", defaults: dict | None = None) -> dict:
        defaults = defaults or {}
        raw = Path(src).read_bytes()  # the file is kept in library/sources only when the licks are saved
        sc = L.load_concert(Path(src), pitch_in_file)
        md = sc.metadata
        frags = L.split_fragments(sc, mode, ranges)
        token = uuid.uuid4().hex
        items = []
        next_no = self.db.next_number()
        taken = set()
        for i, fr in enumerate(frags):
            data = L.fragment_data(fr)
            text = " ".join(fr.texts)
            info_text = " ".join(t for t in fr.texts
                                 if not L._SKIP_TEXT.match(t) and re.search(r"lick|from|,", t, re.I))
            parsed = A.parse_comment(info_text)
            artist = parsed["artist"] or defaults.get("artist") or _real(md.composer if md else "")
            tune = parsed["tune"] or defaults.get("tune") or (_real(md.title) if md and mode in ("range", "phrases") else "")
            year = parsed["year"] or (int(defaults["year"]) if defaults.get("year") else None)
            user_tags = list(dict.fromkeys(parsed["tags"] + [A.normalize_tag(t) for t in defaults.get("tags", []) if t]))
            context = " ".join([text, tune, " ".join(user_tags), defaults.get("context", "")])
            an = A.analyse(data, context)
            if any(t in A.FUNCTIONS for t in user_tags):  # your own label wins over the auto one
                fn = next(t for t in user_tags if t in A.FUNCTIONS)
                if fn != an["function"]:
                    an["function"] = fn
            number = parsed["number"]
            if number is not None and number in taken:  # "Blues Lick 1" can appear twice in one book
                number = None
            if number is None:
                while next_no in taken or self.db.lick_by_number(next_no):
                    next_no += 1
                number = next_no
                next_no += 1
            taken.add(number)
            exists = self.db.lick_by_number(number)
            score = L.lick_score(fr, fr.carry_chord)
            items.append({
                "index": i, "number": number, "replaces": bool(exists),
                "bars_in_source": f"{fr.first_bar}-{fr.last_bar}", "bars": data["bars"],
                "function": an["function"], "tonic_name": an["tonic_name"], "mode": an["mode"],
                "tonic_pc": an["tonic_pc"], "auto_tags": an["tags"], "tags": user_tags,
                "artist": artist, "tune": tune, "year": year, "comment": parsed["comment"],
                "chords": data["chords"],
                "play": {k: data[k] for k in ("ts", "bars", "length", "notes", "graces", "chords")}, "svg": L.render_svg(score, self.settings["display"] == "written", width=1100),
            })
            self._previews.setdefault(token, {"raw": raw, "name": filename, "source": None, "items": {}})
            self._previews[token]["items"][i] = {"frag": fr, "data": data, "analysis": an}
        self._mark_related(items, token)
        return {"token": token, "source": filename, "items": items,
                "functions": A.FUNCTIONS}

    def _library_shapes(self) -> list[dict]:
        from .fit import lick_label

        out = []
        for r in self.db.q("SELECT id, number, function, artist, tune, year, tonic_pc, bars, data FROM licks WHERE archived=0"):
            r["label"] = lick_label(r)
            out.append(r)
        return out

    def _mark_related(self, items: list[dict], token: str) -> None:
        """Flag licks already in the library (or earlier in this file) in another key."""
        lib = self._library_shapes()
        cache = self._previews[token]["items"] if token in self._previews else {}
        seen: list[dict] = []
        for it in items:
            me = {"data": cache[it["index"]]["data"], "tonic_pc": it["tonic_pc"], "number": it["number"]}
            others = [o for o in lib if o["number"] != it["number"]] + seen
            rel = similar.find_related(me, others)
            it["related"] = [{**r, "text": similar.describe(r, r["label"])} for r in rel]
            it["duplicate"] = any(r["kind"] == "same" for r in rel)
            seen.append({**me, "id": None, "label": f"Lick {it['number']} (this file)"})

    def save_import(self, token: str, items: list[dict]) -> list[int]:
        prev = self._previews.get(token)
        if prev is None:
            raise KeyError("Import preview expired; upload the file again.")
        ids = []
        if any(it.get("keep", True) for it in items) and prev["source"] is None:
            name = prev["name"]
            keep = self.lib / "sources" / f"{datetime.now():%Y%m%d-%H%M%S}_{_slug(Path(name).stem)}{Path(name).suffix or '.musicxml'}"
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(prev["raw"])
            prev["source"] = str(keep.relative_to(self.lib))
        for it in items:
            if not it.get("keep", True):
                continue
            cached = prev["items"][int(it["index"])]
            fr, data, an = cached["frag"], cached["data"], cached["analysis"]
            number = int(it["number"])
            func = it.get("function") or an["function"]
            tonic_pc, mode = an["tonic_pc"], an["mode"]
            if it.get("tonic_name"):
                tonic_pc = L.NAME_TO_PC.get(it["tonic_name"], tonic_pc)
                mode = it.get("mode") or mode
            rec_label = F.lick_label({"number": number, "bars": data["bars"], "function": func, "artist": it.get("artist"),
                                      "tune": it.get("tune"), "year": it.get("year")})
            score = L.lick_score(fr, fr.carry_chord, title=f"Lick {number}", label=rec_label)
            fname = f"licks/L{number:04d}.musicxml"
            L.write_xml(L.for_display(score, True), self.lib / fname)
            rec = {
                "number": number, "name": it.get("name") or F.default_name({"artist": it.get("artist"), "tune": it.get("tune")}), "function": func,
                "tonic_pc": tonic_pc, "tonic_name": L.key_name(tonic_pc, mode), "mode": mode,
                "artist": it.get("artist") or "", "tune": it.get("tune") or "",
                "year": int(it["year"]) if it.get("year") else None, "comment": it.get("comment") or "",
                "source": prev["source"], "source_bars": f"{fr.first_bar}-{fr.last_bar}", "file": fname,
                "tags": [t for t in it.get("tags", []) if t], "auto_tags": an["tags"], "data": data,
                "bars": data["bars"], "low": an["low"], "high": an["high"],
            }
            ids.append(self.db.save_lick(rec))
        self._svg_cache.clear()
        return ids

    # ---- a lick made from the Align tab: bars you transcribed + the matching piece of the recording
    def lick_audio_path(self, number: int) -> Path:
        return self.lib / "licks" / "audio" / f"L{number:04d}.mp3"

    def add_lick_from_abc(self, abc: str, sharps: int, meter: str, meta: dict, audio: tuple[Path, float, float] | None,
                          audio_bars: list[float] | None = None, origin: dict | None = None) -> dict:
        """abc = the bars as B-flat trumpet reads them. audio = (recording, start s, end s) to cut next to the lick."""
        import subprocess

        parsed, fr = self._abc_fragment(abc, sharps, meter)
        data = L.fragment_data(fr)
        user_tags = [A.normalize_tag(t) for t in meta.get("tags", []) if t]
        an = A.analyse(data, " ".join([meta.get("tune") or "", " ".join(user_tags)]))
        func = next((t for t in user_tags if t in A.FUNCTIONS), an["function"])
        number = self.db.next_number()
        label = F.lick_label({"number": number, "bars": data["bars"], "function": func, "artist": meta.get("artist"),
                              "tune": meta.get("tune"), "year": meta.get("year")})
        score = L.lick_score(fr, None, title=f"Lick {number}", label=label)
        fname = f"licks/L{number:04d}.musicxml"
        L.write_xml(L.for_display(score, True), self.lib / fname)
        rec = {"number": number, "name": F.default_name(meta), "function": func,
               "tonic_pc": an["tonic_pc"], "tonic_name": L.key_name(an["tonic_pc"], an["mode"]), "mode": an["mode"],
               "artist": meta.get("artist") or "", "tune": meta.get("tune") or "", "year": meta.get("year"),
               "comment": meta.get("comment") or "", "source": meta.get("source"), "source_bars": meta.get("source_bars"),
               "file": fname, "tags": user_tags, "auto_tags": an["tags"], "data": data, "bars": data["bars"],
               "low": an["low"], "high": an["high"]}
        lick_id = self.db.save_lick(rec)
        has_audio = False
        if audio:
            src, t0, t1 = audio
            out = self.lick_audio_path(number)
            out.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t0):.3f}", "-to", f"{t1:.3f}",
                                "-i", str(src), "-ac", "1", "-codec:a", "libmp3lame", "-q:a", "4", str(out)])
            has_audio = r.returncode == 0 and out.exists()
            side = {}
            if has_audio and audio_bars:  # seconds into the clip where each bar starts, then where the last one ends
                side["bars"] = [round(x, 3) for x in audio_bars]
            if has_audio and origin:      # where in the original recording (e.g. the YouTube video) the clip begins
                side["origin"] = origin
            if side:
                out.with_suffix(".json").write_text(json.dumps(side))
        self._svg_cache.clear()
        return {"id": lick_id, "number": number, "label": label, "audio": has_audio, "issues": parsed["issues"]}

    def lick_sidecar(self, number: int) -> dict:
        try:
            return json.loads(self.lick_audio_path(number).with_suffix(".json").read_text())
        except (OSError, ValueError):
            return {}

    def work_source(self, work: str | None) -> dict:
        """source.json of a transcription work (the URL the audio came from), or {}."""
        if not work or "/" in work or work.startswith("."):
            return {}
        try:
            return json.loads((self.lib / "works" / work / "source.json").read_text())
        except (OSError, ValueError):
            return {}

    def lick_origin(self, lick: dict) -> dict | None:
        """Where the lick's recording comes from: stored with the clip, else worked out from the lick's comment."""
        o = self.lick_sidecar(lick["number"]).get("origin")
        if o:
            return X._origin_links(o, self.work_source(o.get("work")))
        m = X._COMMENT.search(lick.get("comment") or "")
        return X.origin_from_comment(lick.get("comment"), self.work_source(m["work"]) if m else None)

    # ---- export: one lick as files other software can read
    def export_lick(self, lick_id: int) -> tuple[dict, dict]:
        """(lick.json content, {file name in the bundle: bytes or a Path})."""
        import subprocess

        lk = self.lick(lick_id)
        from .fit import lick_label

        lk["label"] = lick_label(lk)
        n = lk["number"]
        files: dict = {"musicxml": "lick.musicxml", "midi": "lick.mid"}
        blobs: dict = {"lick.musicxml": Path(lk["_path"])}
        audio = None
        ap = self.lick_audio_path(n)
        if ap.exists():
            dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(ap)],
                                 capture_output=True, text=True).stdout.strip()
            audio = {"duration": float(dur) if dur else 0.0, "bar_marks": self.audio_bars(n)}
            files["audio"] = "audio.mp3"
            blobs["audio.mp3"] = ap
        rec = X.lick_record(lk, audio=audio, origin=self.lick_origin(lk), tempo=float(self.settings.get("tempo", 120)),
                            files=files)
        blobs["lick.mid"] = X.midi_bytes(lk["data"], rec["tempo_bpm"], lk["label"])
        blobs["lick.json"] = json.dumps(rec, indent=1, ensure_ascii=False).encode("utf-8")
        return rec, blobs

    def export_zip(self, ids: list[int], dest: Path) -> int:
        """A zip of the licks (index.json, README.txt, licks/L0043/...). Returns how many were written."""
        import zipfile

        index = []
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for i in ids:
                rec, blobs = self.export_lick(i)
                folder = f"licks/L{rec['number']:04d}"
                for name, b in blobs.items():
                    if isinstance(b, Path):
                        z.write(b, f"{folder}/{name}")
                    else:
                        z.writestr(f"{folder}/{name}", b)
                index.append({"number": rec["number"], "label": rec["label"], "folder": folder,
                              "audio": bool(rec["audio"]), "youtube": (rec["origin"] or {}).get("watch_url")})
            z.writestr("index.json", json.dumps({"format": "licksmith-export", "version": X.VERSION, "licks": index},
                                                indent=1, ensure_ascii=False))
            z.writestr("README.txt", X.README)
        return len(index)

    def audio_bars(self, number: int) -> list[float] | None:
        try:
            return json.loads(self.lick_audio_path(number).with_suffix(".json").read_text())["bars"]
        except (OSError, ValueError, KeyError):
            return None

    def clip_variant(self, lick: dict, semis: int = 0, bpm: float | None = None) -> tuple[Path, float, list[float]]:
        """The lick's recording shifted by `semis` semitones and played at `bpm` (quarter notes per minute), tempo and pitch
        changed independently. Returns (file, seconds from the start of the file to the first bar line). Made on demand and kept
        in library/licks/audio/cache."""
        import subprocess

        number = lick["number"]
        src = self.lick_audio_path(number)
        marks = self.audio_bars(number)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                                   capture_output=True, text=True).stdout.strip() or 0)
        nbars = max(1, int(lick.get("bars") or 1))
        if not marks or len(marks) < 2:  # older clips: the bars share the clip evenly between the 0.1 s lead-in and lead-out
            start, end = 0.1, max(0.6, dur - 0.1)
            marks = [start + (end - start) * i / nbars for i in range(nbars + 1)]  # one mark per bar line, not just start and end
        else:
            nbars = len(marks) - 1
        bpb = int((lick["data"].get("ts") or [4, 4])[0])
        orig_bpm = bpb * nbars * 60.0 / max(0.1, marks[-1] - marks[0])
        r = max(0.25, min(3.0, (bpm / orig_bpm) if bpm else 1.0))  # playing speed relative to the recording (4x slower .. 3x faster)
        s = int(semis)
        scaled = [m / r for m in marks]
        if s == 0 and abs(r - 1) < 0.01:
            return src, marks[0], marks
        out = src.parent / "cache" / f"L{number:04d}_s{s}_r{r:.3f}.mp3"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            f = 2 ** (s / 12)
            t, parts = r / f, []  # asetrate moves pitch and speed together; atempo gives the speed back (each step 0.5..2)
            while t > 2.0:
                parts.append(2.0); t /= 2.0
            while t < 0.5:
                parts.append(0.5); t *= 2.0
            parts.append(t)
            flt = f"aresample=44100,asetrate={44100 * f:.2f},aresample=44100," + ",".join(f"atempo={x:.5f}" for x in parts)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", flt, "-ac", "1",
                            "-codec:a", "libmp3lame", "-q:a", "4", str(out)], check=True)
        return out, marks[0] / r, scaled

    def has_audio(self, number: int) -> bool:
        return self.lick_audio_path(number).exists()

    def related(self, lick_id: int) -> list[dict]:
        lk = self.lick(lick_id)
        rel = similar.find_related(lk, self._library_shapes(), limit=8)
        return [{**r, "text": similar.describe(r, r["label"])} for r in rel]

    def repeats(self) -> list[dict]:
        """Every pair of licks in the library that are the same line or nearly."""
        rows = self._library_shapes()
        out = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                r = similar.compare(a, b)
                if r:
                    out.append({**r, "a": {"id": a["id"], "label": a["label"]}, "b": {"id": b["id"], "label": b["label"]},
                                "text": similar.describe(r, a["label"])})
        order = {"same": 0, "inside": 1, "similar": 2}
        out.sort(key=lambda r: (order[r["kind"]], -r["ratio"]))
        return out

    # ---- a lick as ABC text, for editing
    def lick_abc(self, lick_id: int) -> dict:
        from . import capture as C

        lk = self.lick(lick_id)
        from music21 import interval

        written = L.transpose_score(L.load_lick_score(lk["_path"]), interval.Interval("M2"))  # as B-flat trumpet reads it
        sharps = C.sharps_for((lk["tonic_pc"] + 2) % 12, lk["mode"])
        ts = lk["data"].get("ts") or [4, 4]
        return {"abc": C.score_to_abc(written, sharps), "sharps": sharps, "meter": f"{ts[0]}/{ts[1]}",
                "keys": [{"sharps": k, "name": v} for k, v in sorted(C.KEY_NAMES.items())]}

    def _abc_fragment(self, abc: str, sharps: int, meter_str: str):
        from . import capture as C
        from music21 import stream as m21stream

        parsed = C.parse_abc(abc, sharps=sharps, meter_str=meter_str)
        sc = C.build_score(parsed, "trumpet", "same")
        from music21 import spanner

        ms = list(sc.parts[0].getElementsByClass(m21stream.Measure))
        fr = L.Fragment(measures=ms, first_bar=1, last_bar=len(ms), texts=[], carry_chord=None)
        fr.slurs = [sl.getSpannedElements() for sl in sc.parts[0].recurse().getElementsByClass(spanner.Slur)]
        return parsed, fr

    def lick_abc_preview(self, abc: str, sharps: int, meter_str: str, justify: bool = False) -> dict:
        """`justify`: stretch the bar over the whole page width (so every bar is drawn the same width, notes spread to fit)."""
        parsed, fr = self._abc_fragment(abc, sharps, meter_str)
        sc = L.lick_score(fr, None, title="")
        return {"svg": L.render_svg(sc, self.settings["display"] == "written", width=1100, scale=40,
                                    options={"minLastJustification": 0.0} if justify else None),
                "issues": parsed["issues"], "events": self._events_info(parsed)}

    @staticmethod
    def _events_info(parsed: dict) -> list[dict]:
        """Every note and rest of the lick in reading order (the order of the drawn symbols): concert MIDI, bar, start."""
        from music21 import pitch

        out = []
        for bi, ms in enumerate(parsed["measures"]):
            pos = 0.0
            for ev in ms:
                m = pitch.Pitch(ev["name"]).midi - 2 if ev["type"] == "note" else None  # written B-flat trumpet -> concert
                out.append({"bar": bi + 1, "beat": pos, "len": float(ev["ql"]), "midi": m, "grace": len(ev.get("grace") or []),
                            "graces": [pitch.Pitch(g["name"]).midi - 2 for g in ev.get("grace") or []]})
                pos += float(ev["ql"])
        return out

    def edit_abc(self, abc: str, sharps: int, meter_str: str, index: int, op: str, value=None) -> dict:
        """One keyboard edit (same keys as the Align tab) on the index-th note or rest of an ABC text; returns the new ABC
        plus its drawing. Nothing is saved: the page keeps the text until you press Save."""
        from fractions import Fraction

        from . import capture as C
        from .align import apply_edit

        parsed = C.parse_abc(abc.strip() + " |]" if not abc.rstrip().endswith(("|", "]")) else abc, sharps=sharps, meter_str=meter_str)
        num, den = (int(x) for x in meter_str.split("/"))
        n = index
        for bi, ms in enumerate(parsed["measures"]):
            if n < len(ms):
                nxt_bar = parsed["measures"][bi + 1] if bi + 1 < len(parsed["measures"]) else []
                parsed["measures"][bi] = apply_edit(ms, n, op, value, sharps, Fraction(num * 4, den),
                                                    following=nxt_bar[0] if nxt_bar else None)
                break
            n -= len(ms)
        else:
            raise ValueError("No such note")
        sc = C.build_score(parsed, "concert", "concert")
        new_abc = C.score_to_abc(sc, sharps)
        return {"abc": new_abc, **self.lick_abc_preview(new_abc, sharps, meter_str)}

    def save_lick_abc(self, lick_id: int, abc: str, sharps: int, meter_str: str) -> dict:
        """Rewrite the lick's notes from edited ABC. Number, tags, notes and practice history stay;
        the file, the stored notes and the automatic tags are redone."""
        lk = self.lick(lick_id)
        parsed, fr = self._abc_fragment(abc, sharps, meter_str)
        data = L.fragment_data(fr)
        an = A.analyse(data, " ".join([lk.get("tune") or "", " ".join(lk.get("tags") or [])]))
        label = F.lick_label({"number": lk["number"], "bars": data["bars"], "function": lk["function"],
                              "artist": lk.get("artist"), "tune": lk.get("tune"), "year": lk.get("year")})
        score = L.lick_score(fr, None, title=f"Lick {lk['number']}", label=label)
        L.write_xml(L.for_display(score, True), lk["_path"])
        self.db.conn.execute("UPDATE licks SET data=?, bars=?, low=?, high=?, auto_tags=?, updated=? WHERE id=?",
                             (json.dumps(data), data["bars"], an["low"], an["high"], json.dumps(an["tags"]),
                              datetime.now().isoformat(timespec="seconds"), lick_id))
        self.db.conn.commit()
        self._svg_cache.clear()
        return {"issues": parsed["issues"], "bars": data["bars"]}

    def rebeam_files(self) -> int:
        """Rewrite every saved lick file with eighths and sixteenths beamed per beat. The old
        files are copied to library/licks/before-beams first. Returns how many were rewritten."""
        keep = self.lib / "licks" / "before-beams"
        n = 0
        for r in self.db.licks():
            path = self.lib / r["file"]
            if not path.exists():
                continue
            keep.mkdir(parents=True, exist_ok=True)
            if not (keep / path.name).exists():
                shutil.copy2(path, keep / path.name)
            sc = L.load_lick_score(path)
            L.write_xml(L.for_display(sc, True), path)
            n += 1
        return n

    def reanalyse(self, lick_id: int) -> dict:
        lk = self.lick(lick_id)
        an = A.analyse(lk["data"], " ".join([lk.get("tune") or "", " ".join(lk.get("tags") or [])]))
        self.db.conn.execute("UPDATE licks SET auto_tags=? WHERE id=?", (json.dumps(an["tags"]), lick_id))
        self.db.conn.commit()
        return an

    # ------------------------------------------------------------------ keys / rendering
    def keys_for(self, lick_id: int, order: str | None = None) -> list[dict]:
        lk = self.lick(lick_id)
        st = self.settings
        order = order or st["key_order"]
        seed = int(datetime.now().strftime("%Y%m%d")) + lick_id
        out = []
        for pc in L.key_order(lk["tonic_pc"], order, st["my_steps"], seed, lk.get("mode", "major")):
            plan = L.transposition_plan(lk, pc, st)
            out.append({"pc": pc, "label": plan["label"], "concert": plan["concert_key"], "fits": plan["fits"],
                        "octaves": plan["octaves"]})
        return out

    def svg(self, lick_id: int, target_pc: int | None = None, display: str | None = None) -> str:
        lk = self.lick(lick_id)
        st = dict(self.settings)
        if display:
            st["display"] = display
        pc = lk["tonic_pc"] if target_pc is None else int(target_pc) % 12
        key = (lick_id, pc, st["display"], st["range_low"], st["range_high"], lk["updated"])
        if key not in self._svg_cache:
            plan = L.transposition_plan(lk, pc, st)
            base = L.load_lick_score(lk["_path"])
            t = L.transpose_score(base, plan["interval"], plan["octaves"])
            for te in list(t.recurse().getElementsByClass("TextExpression")):
                te.activeSite.remove(te)  # the label is shown above the music in the app
            self._svg_cache[key] = L.render_svg(t, st["display"] == "written", width=1100, scale=40)
        return self._svg_cache[key]

    def svg_events(self, lick_id: int) -> list[dict]:
        """Every symbol the drawn lick has, in drawing order (notes, rests, grace notes; ties drawn separately):
        start and end in quarter notes from the lick's first beat. Used to light up the note being played."""
        from music21 import chord as m21chord, harmony, note as m21note, stream

        sc = L.load_lick_score(self.lick(lick_id)["_path"])
        out, cursor = [], 0.0
        for m in sc.parts[0].getElementsByClass(stream.Measure):
            bar_len = float(m.barDuration.quarterLength) if m.barDuration else 4.0
            els = [e for e in m.notesAndRests if not isinstance(e, harmony.Harmony)]
            for e in sorted(els, key=lambda x: (float(x.offset), 0 if x.duration.isGrace else 1)):
                grace = bool(e.duration.isGrace)
                out.append({"s": round(cursor + float(e.offset), 4), "e": round(cursor + float(e.offset) + float(e.quarterLength), 4),
                            "g": grace, "r": isinstance(e, m21note.Rest)})
            cursor += bar_len
        return out

    def play(self, lick_id: int, target_pc: int | None = None) -> dict:
        lk = self.lick(lick_id)
        if "graces" not in lk["data"]:
            try:
                ms = list(L.load_lick_score(lk["_path"]).parts[0].getElementsByClass("Measure"))
                lk["data"]["graces"] = L.fragment_data(L.Fragment(measures=ms, first_bar=1, last_bar=len(ms), texts=[],
                                                                  carry_chord=None))["graces"]
            except Exception:  # noqa: BLE001  a missing or odd file: just no grace notes
                lk["data"]["graces"] = []
        pc = lk["tonic_pc"] if target_pc is None else int(target_pc) % 12
        plan = L.transposition_plan(lk, pc, self.settings)
        d = L.transposed_data(lk, plan)
        d["label"] = plan["label"]
        d["semis"] = plan["semis"]
        return d

    def twelve_keys_xml(self, lick_id: int, order: str | None = None) -> tuple[str, str]:
        """(file name, MusicXML text) of the 12-key sheet, built on request; nothing is stored."""
        from music21 import bar as m21bar, expressions, layout, stream

        lk = self.lick(lick_id)
        st = self.settings
        base = L.load_lick_score(lk["_path"])
        part = stream.Part()
        n = 0
        keys = self.keys_for(lick_id, order)
        for ki, k in enumerate(keys):
            plan = L.transposition_plan(lk, k["pc"], st)
            t = L.transpose_score(base, plan["interval"], plan["octaves"])
            ms = list(t.parts[0].getElementsByClass(stream.Measure))
            for j, m in enumerate(ms):
                n += 1
                m.number = n
                for te in list(m.getElementsByClass(expressions.TextExpression)):
                    m.remove(te)
                if ki and j == 0:
                    for ts in list(m.getElementsByClass("TimeSignature")) + list(m.getElementsByClass("Clef")):
                        m.remove(ts)
                if j == 0:
                    m.insert(0, expressions.RehearsalMark(k["label"]))
                    if ki:
                        m.insert(0, layout.SystemLayout(isNew=True))
                m.rightBarline = m21bar.Barline("double") if j == len(ms) - 1 else None
                part.append(m)
        part.getElementsByClass(stream.Measure)[-1].rightBarline = m21bar.Barline("final")
        sc = stream.Score()
        from music21 import metadata

        sc.metadata = metadata.Metadata()
        sc.metadata.title = f"{F.lick_label(lk)} — 12 keys"
        sc.insert(0, part)
        return f"L{lk['number']:04d}_12keys_{order or st['key_order']}.musicxml", L.score_xml(L.for_display(sc, st["display"] == "written"))

    # ------------------------------------------------------------------ tunes
    def import_playlist(self, text: str, playlist: str = "") -> int:
        from .ireal import parse_playlist_text

        tunes = parse_playlist_text(text)
        charts = self.lib / "charts" / "charts.json"
        existing = json.loads(charts.read_text()) if charts.exists() else {}
        for t in tunes:
            self.db.save_tune(t, playlist)
            existing[t["title"]] = t
        charts.write_text(json.dumps(existing, indent=1, ensure_ascii=False))
        return len(tunes)

    def tune_view(self, title: str) -> dict:
        t = self.db.tune(title)
        if t is None:
            raise KeyError(title)
        t["sections"] = A.tune_sections(t["bars"], t["ts"][0])
        return t

    def fit(self, title: str, gap_bars: int = 0, only_ids: list[int] | None = None,
            pool: list[str] | None = None) -> dict:
        """pool: which licks may be used - "favorites" and/or "today" (today's exercise: due, new and
        already reviewed today). None or empty means all licks."""
        t = self.db.tune(title)
        if t is None:
            raise KeyError(title)
        rows = [self.lick(r["id"]) for r in self.db.licks()]
        if pool:
            ok: set[int] = set()
            if "favorites" in pool:
                ok |= {r["id"] for r in rows if r["favorite"]}
            if "today" in pool:
                td = srs.today(self.db, self.settings)
                ok |= {r["id"] for r in td["due"]} | {r["id"] for r in td["new"]} | set(td["done_today"])
            rows = [r for r in rows if r["id"] in ok]
        res = F.plan_tune(t, rows, self.settings, gap_bars=gap_bars, only_ids=set(only_ids) if only_ids else None)
        res["tune"] = {k: t[k] for k in ("title", "composer", "style", "key", "ts", "bars")}
        return res

    def fit_xml(self, title: str, placements: list[dict], bpm: int | None = None) -> tuple[str, str]:
        """(file name, MusicXML text) of the fitted tune, built on request; the plan itself is kept in the database."""
        t = self.db.tune(title)
        sc = F.practice_score(t, placements, self.lick, self.settings, bpm)
        name = f"{_slug(title)}_licks_{datetime.now():%Y%m%d-%H%M}.musicxml"
        self.db.x("INSERT INTO plans(tune, created, placements, file) VALUES(?,?,?,?)",
                  (title, datetime.now().isoformat(timespec="seconds"), json.dumps(placements), ""))
        return name, L.score_xml(L.for_display(sc, self.settings["display"] == "written"))

    def fit_svg(self, title: str, placements: list[dict]) -> str:
        t = self.db.tune(title)
        sc = F.practice_score(t, placements, self.lick, self.settings)
        # four bars per line (like a lead sheet) and a wide minimum bar, so chord names don't collide
        return L.render_svg(sc, self.settings["display"] == "written", width=1500, scale=38,
                            options={"breaks": "encoded", "measureMinWidth": 30, "spacingNonLinear": 0.5,
                                     "spacingLinear": 0.2})

    def fit_snippets(self, title: str, placements: list[dict]) -> dict[int, str]:
        """{bar number: small SVG of the lick notes in that bar} for the tune chart."""
        import copy

        import verovio
        from music21 import clef, expressions, harmony, layout, meter, stream, tempo

        t = self.db.tune(title)
        sc = F.practice_score(t, placements, self.lick, self.settings)
        written = self.settings["display"] == "written"
        bars = {p["bar"] + k for p in placements for k in range(p["bars"])}
        bpb = t["ts"][0] if t.get("ts") else 4
        out = {}
        tk = verovio.toolkit()
        tk.setOptions({"pageWidth": 700, "adjustPageHeight": True, "adjustPageWidth": True, "scale": 40,
                       "footer": "none", "header": "none", "pageMarginTop": 0, "pageMarginBottom": 0,
                       "pageMarginLeft": 12, "pageMarginRight": 4, "svgViewBox": True, "breaks": "none"})
        for m0 in sc.parts[0].getElementsByClass(stream.Measure):
            if m0.number not in bars:
                continue
            m = copy.deepcopy(m0)
            m.number = 1
            for el in list(m.recurse().getElementsByClass((harmony.ChordSymbol, expressions.TextExpression,
                                                           layout.SystemLayout, tempo.MetronomeMark,
                                                           meter.TimeSignature, clef.Clef))):
                el.activeSite.remove(el)
            m.insert(0, clef.TrebleClef())
            m.insert(0, meter.TimeSignature(f"{bpb}/{t['ts'][1] if t.get('ts') else 4}"))
            part = stream.Part()
            part.append(m)
            one = stream.Score()
            one.insert(0, part)
            disp = L.for_display(one, written)
            for inst in disp.recurse().getElementsByClass("Instrument"):
                inst.partName = ""
                inst.partAbbreviation = ""
            xml = L.score_xml(disp).replace("<clef>", '<clef print-object="no">') \
                .replace("<time>", '<time print-object="no">')
            tk.loadData(xml)
            out[m0.number] = tk.renderToSVG(1)
        return out

    def fit_play(self, title: str, placements: list[dict]) -> dict:
        t = self.db.tune(title)
        return F.play_data(t, placements, self.lick, self.settings)
