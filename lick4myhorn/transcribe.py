"""New transcriptions from the Transcribe page: a YouTube / YouTube Music link or an audio file in,
a work (library/works/<name>/) out that the Align editor can open.

Steps of one job (run one after another in a background thread, since Muscriptor is heavy):
  1. get the audio        yt-dlp download, or convert the uploaded file       -> audio.wav
  2. a first guess        tempo, first downbeat, number of bars              -> form.json (free form: no chord chart)
  3. Muscriptor           lead instrument + the rest of the band, whole file  -> engines/<name>/{raw.mid, notes.json, bass.json}
  4. a key guess          from the transcribed notes                         -> form.json "key"

The guesses are only a start: the Transcribe page lets you change tempo, downbeat, key and bar count, and drag every bar line.
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path

# lead instrument (what you want to transcribe) -> the name Muscriptor knows it by
LEADS = {"trumpet": "trumpet", "alto_sax": "soprano_and_alto_sax", "tenor_sax": "tenor_sax",
         "trombone": "trombone", "flute": "flutes"}
LEAD_LABELS = {"trumpet": "Trumpet", "alto_sax": "Alto sax", "tenor_sax": "Tenor sax", "trombone": "Trombone", "flute": "Flute"}
# everything else Muscriptor can be told is in the recording (see `muscriptor list-instruments`)
BAND = ["acoustic_piano", "electric_piano", "organ", "acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar",
        "acoustic_bass", "electric_bass", "drums", "violin", "viola", "cello", "contrabass", "string_ensemble", "voice",
        "trumpet", "trombone", "tuba", "french_horn", "brass_section", "soprano_and_alto_sax", "tenor_sax", "baritone_sax",
        "clarinet", "flutes", "chromatic_percussion"]
BASSES = ("acoustic_bass", "electric_bass", "contrabass")
STEP_LABELS = {"download": "Download the audio", "convert": "Convert the audio file", "prepare": "Find the tempo and bar lines",
               "model": "Start Muscriptor", "transcribe": "Transcribe every chunk", "notes": "Build the notes"}
STEP_WEIGHT = {"download": 0.08, "convert": 0.04, "prepare": 0.04, "model": 0.04, "transcribe": 0.78, "notes": 0.02}
DEFAULT_BAND = ["acoustic_piano", "acoustic_bass", "drums"]
MODELS = ["small", "medium", "large"]


def slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def work_name(title: str, artist: str) -> str:
    t, a = slug(title), slug(artist)
    return (f"{t}_{a}" if a else t) or "recording"


def unique_dir(base: Path, name: str) -> Path:
    d = base / name
    k = 2
    while d.exists():
        d = base / f"{name}-{k}"
        k += 1
    return d


def clean_title(raw: str) -> str:
    """'Chet Baker - Autumn Leaves (Official Video) [HD]' -> 'Chet Baker - Autumn Leaves'."""
    t = re.sub(r"\s*[\(\[][^)\]]*(official|video|audio|lyrics?|hd|remaster|transcription|solo)[^)\]]*[\)\]]", "", raw or "", flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -|")


def split_artist_title(video_title: str, uploader: str | None) -> tuple[str, str]:
    """('Artist - Title' videos are common; otherwise the uploader is the best guess for the artist, minus ' - Topic'.)"""
    t = clean_title(video_title)
    if " - " in t:
        a, b = t.split(" - ", 1)
        return b.strip(), a.strip()
    up = re.sub(r"\s*-\s*Topic$", "", uploader or "").strip()
    return t, up


def estimate_grid(audio: Path) -> dict:
    """First guess of the bar grid: tempo, the time of the first beat, and how many bars the recording holds."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(np.atleast_1d(tempo)[0]) or 120.0
    while bpm < 95:  # trackers often find half time on swing; jazz tempos mostly sit between 95 and 260
        bpm *= 2
    while bpm > 260:
        bpm /= 2
    beats = np.asarray(beats, dtype=float)
    first = float(beats[0]) if len(beats) else 0.0
    dur = len(y) / sr
    bars = max(8, int((dur - first) / (4 * 60.0 / bpm)))
    return {"bpm": round(bpm, 1), "first_downbeat": round(first, 2), "bars": min(bars, 600), "duration": round(dur, 1)}


_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MINOR_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]


def estimate_key(notes: list[dict]) -> str | None:
    """Concert key ('F', 'Gm') from the duration-weighted pitch classes of the notes (Krumhansl profiles)."""
    import numpy as np

    if len(notes) < 20:
        return None
    h = np.zeros(12)
    for n in notes:
        h[n["pitch"] % 12] += max(0.05, n["offset"] - n["onset"])
    best, best_score = None, -2.0
    for pc in range(12):
        for prof, names, suffix in ((_MAJOR, _MAJOR_NAMES, ""), (_MINOR, _MINOR_NAMES, "m")):
            sc = float(np.corrcoef(h, np.roll(prof, pc))[0, 1])
            if sc > best_score:
                best, best_score = names[pc] + suffix, sc
    return best


class Transcriber:
    """Runs transcription jobs one at a time. Jobs live in memory (this server run); the works they make are on disk."""

    def __init__(self, library: Path):
        self.works = Path(library) / "works"
        self.jobs: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def submit(self, kind: str, **opts) -> dict:
        lead = opts.get("instrument") or "trumpet"
        if lead not in LEADS:
            raise ValueError(f"Unknown instrument {lead}")
        model = opts.get("model") or "large"
        if model not in MODELS:
            raise ValueError(f"Unknown model {model}")
        first = {"url": "download", "file": "convert"}.get(kind)
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "state": "queued", "message": "Waiting for its turn", "progress": 0.0,
               "steps": ([first, "prepare"] if first else []) + ["model", "transcribe", "notes"], "step": None, "detail": "", "step_frac": 0.0,
               "work": opts.get("work"), "title": opts.get("title") or "", "created": time.time(), "error": None,
               "label": opts.get("label") or opts.get("title") or opts.get("url") or "recording"}
        job["opts"] = {**opts, "instrument": lead, "model": model, "band": self._band(opts.get("band"), lead)}
        with self._lock:
            self.jobs[job["id"]] = job
            self.q.put(job["id"])
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
        return self.public(job)

    def public(self, job: dict) -> dict:
        out = {k: v for k, v in job.items() if k not in ("opts", "t_gen")}
        if job["state"] == "transcribing":
            out.update(self._live(job))
        cur = out.get("step")
        order = job["steps"]
        out["steps"] = [{"id": s, "label": STEP_LABELS[s],
                         "state": "done" if job["state"] == "done" or (cur in order and order.index(s) < order.index(cur))
                         else "active" if s == cur and job["state"] not in ("error", "queued") else "todo"} for s in order]
        out["percent"] = 100 if job["state"] == "done" else self._percent(job, cur, out.get("step_frac", 0.0)) if cur else 0
        if job.get("t_run"):
            out["elapsed_s"] = round((job.get("t_end") or time.time()) - job["t_run"])
        return out

    @staticmethod
    def _percent(job: dict, step: str | None, frac: float) -> int:
        order = job["steps"]
        if step not in order:
            return 0
        total = sum(STEP_WEIGHT[s] for s in order)
        done = sum(STEP_WEIGHT[s] for s in order[: order.index(step)])
        return min(99, round(100 * (done + STEP_WEIGHT[step] * max(0.0, min(1.0, frac))) / total))

    def _step(self, job: dict, step: str, detail: str = "", frac: float = 0.0, state: str | None = None) -> None:
        job.update(step=step, detail=detail, step_frac=frac, message=detail or STEP_LABELS[step])
        if state:
            job["state"] = state

    def _speed(self, model: str) -> float:
        """Seconds of work per second of audio for this model on this Mac, from earlier runs' logs (or a rough default)."""
        ratios = []
        for log in self.works.glob("*/engines/*/muscriptor.log"):
            try:
                if json.loads((log.parent / "engine.json").read_text()).get("model") != model:
                    continue
                m = re.search(r"transcribe total: ([\d.]+)s \(([\d.]+)s audio\)", log.read_text(errors="ignore"))
                if m and float(m.group(2)) > 20:
                    ratios.append(float(m.group(1)) / float(m.group(2)))
            except (OSError, ValueError):
                continue
        return sum(ratios) / len(ratios) if ratios else {"small": 0.5, "medium": 1.0, "large": 1.7}.get(model, 1.5)

    def list(self) -> list[dict]:
        return [self.public(j) for j in sorted(self.jobs.values(), key=lambda j: -j["created"])]

    @staticmethod
    def _band(band, lead: str) -> list[str]:
        """Instruments to tell Muscriptor about: the lead, the chosen band, and always a bass (its notes set the bar lines)."""
        names = [LEADS[lead]] + [b for b in (band if band is not None else DEFAULT_BAND) if b in BAND and b != LEADS[lead]]
        if not any(b in BASSES for b in names):
            names.append("acoustic_bass")
        return list(dict.fromkeys(names))

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            try:
                jid = self.q.get(timeout=60)
            except queue.Empty:
                return
            job = self.jobs[jid]
            try:
                self._run(job)
            except Exception as e:  # noqa: BLE001
                job.update(state="error", error=self._explain(e, job), message=self._explain(e, job))

    @staticmethod
    def _explain(e: Exception, job: dict) -> str:
        msg = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        log = job.get("log")
        if log and Path(log).exists():
            tail = [ln for ln in Path(log).read_text(errors="ignore").splitlines() if re.search(r"error|denied|license|401|403|gated", ln, re.I)]
            if tail:
                msg = tail[-1].strip()[:300]
        if "gated" in msg.lower() or "401" in msg or "403" in msg:
            msg += " (accept the model licence on its Hugging Face page and log in: see README)"
        return msg[:400]

    def _run(self, job: dict) -> None:
        o = job["opts"]
        job["t_run"] = time.time()
        if job["kind"] == "rerun":
            d = self.works / o["work"]
            if not (d / "audio.wav").exists():
                raise RuntimeError("This work has no audio.wav")
        else:
            d = self._get_audio(job)
            self._prepare(job, d)
        self._transcribe(job, d)
        job.update(state="done", message="Ready: open it below", detail="", progress=1.0, work=d.name, t_end=time.time())

    def _get_audio(self, job: dict) -> Path:
        from .fetch import fetch, to_wav

        o = job["opts"]
        self.works.mkdir(parents=True, exist_ok=True)
        tmp = unique_dir(self.works, "_new-" + job["id"])
        tmp.mkdir(parents=True)
        if job["kind"] == "url":
            self._step(job, "download", "Contacting YouTube…", 0.0, "downloading")

            def on_dl(frac, total):
                if total == -1:
                    self._step(job, "download", "Downloaded. Converting to WAV…", 0.97)
                else:
                    mb = f" of {total / 1e6:.1f} MB" if total else ""
                    self._step(job, "download", f"Downloading the audio: {round(frac * 100)}%{mb}", 0.95 * frac)

            fetch(o["url"], tmp, on_dl)
            src = json.loads((tmp / "source.json").read_text())
            vt, va = split_artist_title(src.get("title") or "", src.get("uploader"))
            title, artist = (o.get("title") or vt or "Recording").strip(), (o.get("artist") or va or "").strip()
            source = {"url": o["url"], "audio": "audio.wav", "video_title": src.get("title")}
        else:
            self._step(job, "convert", "Converting the audio file to WAV…", 0.2, "preparing")
            up = Path(o["file"])
            shutil.move(str(up), tmp / up.name)
            to_wav(tmp / up.name, tmp / "audio.wav")
            stem = clean_title(Path(o.get("filename") or up.name).stem.replace("_", " "))
            vt, va = split_artist_title(stem, "")
            title, artist = (o.get("title") or vt or "Recording").strip(), (o.get("artist") or va or "").strip()
            source = {"audio": "audio.wav", "file": o.get("filename") or up.name}
        d = unique_dir(self.works, work_name(title, artist))
        tmp.rename(d)
        job.update(work=d.name, title=title)
        job["opts"].update(title=title, artist=artist, source=source)
        return d

    def _prepare(self, job: dict, d: Path) -> None:
        o = job["opts"]
        self._step(job, "prepare", "Listening for the tempo and where the bars fall (about 10 seconds)…", 0.3, "preparing")
        g = estimate_grid(d / "audio.wav")
        form = {
            "title": o["title"], "artist": o.get("artist", ""), "tune": o["title"], "free_form": True,
            "beats_per_bar": 4, "bpm": g["bpm"], "feel": "swing", "instrument": o["instrument"],
            "ensemble": o["band"], "source": o["source"],
            "grid": {"first_downbeat": g["first_downbeat"], "anchors": [],
                     "_help": "A first guess from the audio: fix it on the Transcribe page (Tempo & bars)."},
            "chorus": [["N.C."]],
            "sections": [{"label": "transcription", "bars": g["bars"], "chords": [["N.C."]], "transcribe": True}],
        }
        (d / "form.json").write_text(json.dumps(form, indent=2))

    def _engine_name(self, d: Path, model: str) -> str:
        base = f"muscriptor-{model}"
        name, k = base, 2
        while (d / "engines" / name).exists():
            name = f"{base}-{k}"
            k += 1
        return name

    def _transcribe(self, job: dict, d: Path) -> None:
        from . import engines

        o = job["opts"]
        name = self._engine_name(d, o["model"])
        out = d / "engines" / name
        out.mkdir(parents=True, exist_ok=True)
        log = out / "muscriptor.log"
        job.update(log=str(log), engine=name, model=o["model"], t_gen=None)
        self._step(job, "model", f"Starting Muscriptor {o['model']}…", 0.0, "transcribing")
        engines.run_muscriptor(d / "audio.wav", out, o["band"], o["instrument"], o["model"], 2.0, None, log=log)
        if job["kind"] != "rerun":
            try:
                notes = json.loads((out / "notes.json").read_text())
                key = estimate_key([n for n in notes if 52 <= n["pitch"] <= 91])
                f = d / "form.json"
                data = json.loads(f.read_text())
                if key and not data.get("key"):
                    data["key"] = key
                    f.write_text(json.dumps(data, indent=2))
            except Exception:  # noqa: BLE001 - a missing key guess is not worth failing the job
                pass

    def _live(self, job: dict) -> dict:
        """Where the Muscriptor run is, read from its log: starting up, transcribing chunk c of N, or writing the result."""
        log = job.get("log")
        text = Path(log).read_text(errors="ignore") if log and Path(log).exists() else ""
        m = re.search(r"audio: ([\d.]+)s → (\d+) chunk", text)
        if not m:
            reading = "Transcribing " in text
            return {"step": "model", "step_frac": 0.5 if reading else 0.1, "detail":
                    "Reading the audio…" if reading else "Loading the Muscriptor model (the first start can take a minute)…"}
        audio_s, total = float(m.group(1)), int(m.group(2))
        if "Saved MIDI" in text:
            return {"step": "notes", "step_frac": 0.5, "detail": "Splitting the MIDI into trumpet/sax, bass and the rest, and saving the notes…"}
        if "generate total" in text:
            return {"step": "notes", "step_frac": 0.1, "detail": "Finished reading every chunk. Writing the MIDI file…"}
        now = time.time()
        if not job.get("t_gen"):
            job["t_gen"] = now
        el = now - job["t_gen"]
        prog = re.findall(r"\[progress\] (\d+)/(\d+)", text)
        reports = "[progress] ready" in text  # started through mus_runner: it will say when each chunk is done
        if prog or reports:
            c = int(prog[-1][0]) if prog else 0
            frac = c / max(1, total)
            marks = job.setdefault("marks", {})
            if "first" not in marks or c != marks["last"][1]:
                marks.setdefault("first", (now, c))
                marks["last"] = (now, c)
            (t0, c0), (t1, _) = marks["first"], marks["last"]
            # time left = (seconds per chunk, seen since the first one finished) x chunks to go, minus what this chunk has already taken
            eta = max(0.0, (t1 - t0) / (c - c0) * (total - c) - (now - t1)) if c - c0 >= 2 else None
            est = False
        else:  # an older run that cannot report its position: guess from how fast earlier runs were
            guess = audio_s * self._speed(job.get("model") or "large")
            frac = min(0.97, el / guess) if guess else 0.0
            c = int(frac * total)
            eta = max(0.0, guess - el) if el < guess else None
            est = True
        fmt = lambda x: f"{int(x // 60)}:{int(x % 60):02d}"
        detail = (f"{'About chunk' if est else 'Chunk'} {c} of {total} · the recording is cut into 5-second chunks, {fmt(audio_s * c / total)} of "
                  f"{fmt(audio_s)} done" + (" · estimated from earlier runs, this run can't report its position" if est else
                                            " · the first chunk is the slowest while the model warms up" if c == 0 else ""))
        return {"step": "transcribe", "step_frac": frac, "detail": detail, "eta_s": None if eta is None else round(eta),
                "chunk": c, "chunks": total, "estimated": est, "message": detail}
