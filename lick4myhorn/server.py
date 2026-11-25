"""Local web app: `licksmith serve` then open http://127.0.0.1:8765

All endpoints are async on purpose: it is a single-user app, so running everything on one thread
keeps SQLite and music21 simple and safe.
"""
from __future__ import annotations

import json
import tempfile
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import analysis as A
from . import licks as L
from . import srs
from .db import DEFAULT_ROUTINE
from .library import Library

STATIC = Path(__file__).parent / "static"


def create_app(root: Path) -> FastAPI:
    lib = Library(root)
    from .render import Renderer

    renderer = Renderer(root)
    app = FastAPI(title="Licksmith")
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def _lick_summary(lk: dict) -> dict:
        from .fit import lick_label

        return {k: lk.get(k) for k in ("id", "number", "name", "function", "tonic_name", "tonic_pc", "mode", "artist", "tune",
                                       "year", "tags", "auto_tags", "favorite", "bars", "due", "reps", "state",
                                       "comment", "notes", "last_rating", "archived", "file", "source",
                                       "source_bars")} | {"label": lick_label(lk)}

    def _scope_info(lick_id: int) -> dict:
        """The keys in this lick's working set, and the keys you have had Good (with the date), as key names."""
        sc = srs.lick_scope(lib.db, lick_id, lib.settings)
        names = {k["pc"]: k["label"] for k in lib.keys_for(lick_id)}
        nm = lambda p: names.get(int(p), str(p))
        return {"scope": sc["scope"], "scope_labels": [nm(p) for p in sc["scope"]],
                "known": sc["known"], "known_labels": [{"key": nm(p), **v} for p, v in sc["known"].items()],
                "good_streak": sc["good_streak"], "shelved_until": sc["shelved_until"], "good_def": srs.good_def(lib.settings)}

    def _get(lick_id: int) -> dict:
        try:
            return lib.lick(lick_id)
        except KeyError:
            raise HTTPException(404, "No such lick")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(STATIC / "brand" / "favicon.ico", media_type="image/x-icon")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("style.css", "audio.js", "app.js", "align.js", "lickedit.js", "transcribe.js"):  # new file version = new URL
            f = STATIC / name
            if f.exists():
                html = html.replace(f"/static/{name}", f"/static/{name}?v={int(f.stat().st_mtime)}")
        return html

    _svg_cache: dict = {}

    # ---------------------------------------------------------------- transcribe (a link or an audio file -> a work)
    from . import transcribe as TR

    transcriber = TR.Transcriber(lib.lib)

    @app.get("/api/transcribe/options")
    async def transcribe_options():
        return {"leads": [{"id": k, "label": v} for k, v in TR.LEAD_LABELS.items()], "band": TR.BAND, "models": TR.MODELS,
                "default_band": TR.DEFAULT_BAND, "default_model": "large", "lead_names": TR.LEADS}

    def _band(text) -> list[str] | None:
        if text is None:
            return None
        return [x for x in str(text).split(",") if x]

    @app.post("/api/transcribe/url")
    async def transcribe_url(body: dict = Body(...)):
        url = str(body.get("url", "")).strip()
        if not re.match(r"https?://", url):
            raise HTTPException(422, "Paste a YouTube or YouTube Music link (it starts with https://)")
        return _guard(lambda: transcriber.submit("url", url=url, title=str(body.get("title", "")).strip(),
                                                 artist=str(body.get("artist", "")).strip(), instrument=body.get("instrument"),
                                                 model=body.get("model"), band=body.get("band"), label=url))

    @app.post("/api/transcribe/upload")
    async def transcribe_upload(file: UploadFile = File(...), title: str = Form(""), artist: str = Form(""),
                                instrument: str = Form("trumpet"), model: str = Form("large"), band: str | None = Form(None)):
        (lib.lib / "works").mkdir(parents=True, exist_ok=True)
        ext = Path(file.filename or "audio").suffix.lower()[:8] or ".audio"
        tmp = lib.lib / "works" / f"_upload-{uuid.uuid4().hex[:8]}{ext}"
        with open(tmp, "wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        return _guard(lambda: transcriber.submit("file", file=str(tmp), filename=file.filename, title=title.strip(),
                                                 artist=artist.strip(), instrument=instrument, model=model, band=_band(band),
                                                 label=file.filename or "audio file"))

    @app.post("/api/transcribe/{work}/run")
    async def transcribe_again(work: str, body: dict = Body(...)):
        w = _aw(work)
        return _guard(lambda: transcriber.submit("rerun", work=work, instrument=w.form.data.get("instrument", "trumpet"),
                                                 model=body.get("model"), band=body.get("band"),
                                                 title=w.form.data.get("title", work), label=w.form.data.get("title", work)))

    @app.get("/api/transcribe/jobs")
    async def transcribe_jobs():
        return transcriber.list()

    # ---------------------------------------------------------------- align (bar lines on the recording)
    from . import align as AL

    _align: dict[str, AL.AlignWork] = {}

    def _aw(work: str) -> AL.AlignWork:
        if work not in _align:
            d = lib.lib / "works" / work
            if "/" in work or ".." in work or not (d / "form.json").exists():
                raise HTTPException(404, "No such work")
            try:
                _align[work] = AL.AlignWork(d, lib.lib / "charts" / "charts.json")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, str(e))
        return _align[work]

    def _guard(fn, *a):
        try:
            return fn(*a)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001  bad ABC, bad bar number...
            raise HTTPException(422, str(e))

    @app.get("/api/align")
    async def align_works():
        return AL.list_works(lib.lib)

    def _work_dir(work: str) -> Path:
        base = (lib.lib / "works").resolve()
        d = (base / work).resolve()
        if not work or "/" in work or "\\" in work or d.parent != base or not d.is_dir():
            raise HTTPException(404, "No such transcription")
        return d

    def _du(d: Path) -> int:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    @app.get("/api/works/disk")
    async def works_on_disk():
        """Every transcription folder under library/works, usable or not, with what is in it (the Transcribe tab's manager)."""
        base = lib.lib / "works"
        out, seen = [], {}
        for d in sorted(base.glob("*")) if base.exists() else []:
            if not d.is_dir():
                continue
            try:
                fm = json.loads((d / "form.json").read_text()) if (d / "form.json").exists() else {}
                src = json.loads((d / "source.json").read_text()) if (d / "source.json").exists() else {}
            except (OSError, ValueError):
                fm, src = {}, {}
            m = re.search(r"(?:youtu\.be/|[?&]v=)([\w-]{6,})", src.get("url", ""))
            vid = m.group(1) if m else None
            engines = sorted(p.name for p in (d / "engines").glob("*") if (p / "raw.mid").exists()) if (d / "engines").exists() else []
            edits = [f.stat().st_mtime for f in (d / "align.json", d / "form.json") if f.exists()]
            out.append({"work": d.name, "title": fm.get("title", d.name), "artist": fm.get("artist", ""), "url": src.get("url", ""),
                        "video": vid, "has_audio": (d / "audio.wav").exists(), "engines": engines,
                        "has_form": (d / "form.json").exists(), "aligned": (d / "align.json").exists(),
                        "mb": round(_du(d) / 1e6, 1), "changed": max(edits) if edits else d.stat().st_mtime})
            if vid:
                seen.setdefault(vid, []).append(d.name)
        for w in out:
            w["same_video_as"] = [n for n in seen.get(w["video"], []) if n != w["work"]] if w["video"] else []
        return out

    @app.delete("/api/align/{work}")
    async def delete_work(work: str):
        d = _work_dir(work)
        if any(j.get("work") == work and j.get("state") not in ("done", "error") for j in transcriber.jobs.values()):
            raise HTTPException(409, "A transcription of this song is still running. Wait for it to finish.")
        shutil.rmtree(d)
        _align.pop(work, None)
        return {"deleted": work}

    @app.get("/api/align/{work}")
    async def align_get(work: str):
        return _guard(_aw(work).summary)

    @app.get("/api/align/{work}/peaks")
    async def align_peaks(work: str):
        return _guard(_aw(work).peaks)

    @app.get("/api/align/{work}/audio")
    async def align_audio(work: str):
        w = _aw(work)
        if not w.audio.exists():
            raise HTTPException(404, "This work has no audio file")
        return FileResponse(w.audio, media_type="audio/wav")

    @app.put("/api/align/{work}/engine")
    async def align_engine(work: str, body: dict = Body(...)):
        w = _aw(work)
        _guard(w.set_engine, body["engine"])
        return w.summary()

    @app.put("/api/align/{work}/times")
    async def align_times(work: str, body: dict = Body(...)):
        w = _aw(work)
        _guard(w.set_times, body["times"])
        return w.summary()

    @app.get("/api/align/{work}/status")
    async def align_status(work: str):
        w = _aw(work)
        return {"dirty": w.dirty(), "saved_at": w.state.get("saved_at"), "lines_cleared": bool(w.state.get("lines_cleared"))}

    @app.post("/api/align/{work}/save")
    async def align_save(work: str):
        w = _aw(work)
        w.save_version()
        return w.summary()

    @app.post("/api/align/{work}/revert")
    async def align_revert(work: str):
        w = _aw(work)
        w.revert_to_saved()
        return w.summary()

    @app.post("/api/align/{work}/clear-lines")
    async def align_clear_lines(work: str):
        w = _aw(work)
        w.clear_lines()
        return w.summary()

    @app.put("/api/align/{work}/tap-lines")
    async def align_tap_lines(work: str, body: dict = Body(...)):
        w = _aw(work)
        _guard(w.set_tap_lines, body["lines"], body.get("start", 0))
        return w.summary()

    @app.post("/api/align/{work}/snap")
    async def align_snap(work: str):
        w = _aw(work)
        _guard(w.set_times, w.snap_to_bass())
        return w.summary()

    @app.post("/api/align/{work}/bar/{k}/abc")
    async def align_bar_abc(work: str, k: int, body: dict = Body(...)):
        return _guard(_aw(work).set_bar_abc, k, body["abc"])

    @app.post("/api/align/{work}/bar/{k}/shift")
    async def align_bar_shift(work: str, k: int, body: dict = Body(...)):
        return _guard(_aw(work).shift_note, k, int(body["index"]), int(body["steps"]), bool(body.get("chromatic")))

    @app.post("/api/align/{work}/bar/{k}/edit")
    async def align_bar_edit(work: str, k: int, body: dict = Body(...)):
        return _guard(_aw(work).edit_note, k, int(body["index"]), body["op"], body.get("value"))

    @app.post("/api/align/{work}/lick")
    async def align_lick(work: str, body: dict = Body(...)):
        return _guard(_aw(work).send_to_library, lib, int(body["from"]), int(body["to"]), body.get("tags") or [], 0.1,
                      body.get("artist"), body.get("tune"))

    def _charts() -> Path:
        return lib.lib / "charts" / "charts.json"

    @app.get("/api/align/{work}/charts")
    async def align_charts(work: str, q: str = ""):
        w = _aw(work)
        tune = w.form.data.get("tune") or w.form.data.get("title", "")
        found = AL.search_charts(q or tune, _charts())
        exact = next((t for t in found if t.lower() == tune.lower()), None)
        return {"tune": tune, "exact": exact, "matches": found, "check": _guard(w.check_chords),
                "source": w.form.data.get("chorus_source")}

    @app.post("/api/align/{work}/chords/detect")
    async def align_detect(work: str, body: dict = Body(...)):
        return _guard(_aw(work).detect_chart, body["title"], _charts())

    @app.post("/api/align/{work}/chords/choruses")
    async def align_choruses(work: str, body: dict = Body(...)):
        return _guard(_aw(work).find_choruses, body["title"], _charts(), body["lines"])

    @app.post("/api/align/{work}/chords/apply-choruses")
    async def align_apply_choruses(work: str, body: dict = Body(...)):
        w = _aw(work)
        _guard(w.apply_choruses, body["title"], _charts(), int(body["transpose"]), body["lines"], int(body["start"]), int(body["count"]), int(body.get("tag", 0)))
        return w.summary()

    @app.post("/api/align/{work}/chords/preview")
    async def align_preview(work: str, body: dict = Body(...)):
        t = body.get("transpose")
        return _guard(_aw(work).preview_chart, body["title"], _charts(), None if t is None else int(t), int(body.get("rotation") or 0))

    @app.post("/api/align/{work}/chords/apply")
    async def align_apply(work: str, body: dict = Body(...)):
        w = _aw(work)
        _guard(w.apply_chart, body["title"], _charts(), int(body["transpose"]), int(body["rotation"]))
        return w.summary()

    @app.put("/api/align/{work}/grid")
    async def align_grid(work: str, body: dict = Body(...)):
        _aw(work)  # 404 for unknown works
        d = lib.lib / "works" / work
        _guard(lambda: AL.update_grid(d, body.get("bpm"), body.get("first_downbeat"), body.get("bars"),
                                      body.get("beats_per_bar"), body.get("key")))
        _align.pop(work, None)
        lib._svg_cache.clear()
        _svg_cache.clear()
        return _aw(work).summary()

    @app.post("/api/align/{work}/undo")
    async def align_undo(work: str):
        w = _aw(work)
        w.undo()
        return w.summary()

    @app.post("/api/align/{work}/redo")
    async def align_redo(work: str):
        w = _aw(work)
        w.redo()
        return w.summary()

    @app.post("/api/align/{work}/bar/{k}/unlock")
    async def align_bar_unlock(work: str, k: int):
        return _guard(_aw(work).unlock, k)

    @app.post("/api/align/{work}/svg")
    async def align_svg(work: str, body: dict = Body(...)):
        w = _aw(work)
        key = ("justified", body["abc"], tuple(body.get("chords") or []))
        if key not in _svg_cache:
            abc = body["abc"]
            if body.get("chords") and '"' not in abc:  # the bar's first chord, written pitch
                abc = f'"{body["chords"][0]}"{abc}'
            r = _guard(lib.lick_abc_preview, abc, w.sharps(), w.meter(), True)
            _svg_cache[key] = r["svg"]
        return Response(_svg_cache[key], media_type="image/svg+xml")

    @app.get("/api/align/{work}/musicxml")
    async def align_musicxml(work: str):
        w = _aw(work)
        path = _guard(w.export_musicxml)
        return FileResponse(path, media_type="application/xml", filename=f"{work}_solo.musicxml")

    # ---------------------------------------------------------------- meta / settings
    @app.get("/api/meta")
    async def meta():
        return {"functions": A.FUNCTIONS, "orders": L.ORDERS, "root": str(lib.root),
                "library": str(lib.lib), "default_routine": DEFAULT_ROUTINE}

    @app.get("/api/settings")
    async def get_settings():
        return lib.settings

    @app.put("/api/settings")
    async def put_settings(values: dict = Body(...)):
        lib._svg_cache.clear()
        return lib.db.set_settings(values)

    # ---------------------------------------------------------------- practice routine checklist
    def _day(d: str | None) -> str:
        d = d or datetime.now().strftime("%Y-%m-%d")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HTTPException(400, "day must be YYYY-MM-DD")
        return d

    @app.get("/api/routine")
    async def get_routine(day: str | None = None):
        day = _day(day)
        sections = lib.db.routine()
        valid = {it["id"] for s in sections for it in s["items"]}
        checked = [i for i in lib.db.routine_checked(day) if i in valid]
        since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
        return {"day": day, "sections": sections, "checked": checked,
                "notes": {k: v for k, v in lib.db.routine_notes(day).items() if k in valid},
                "total": len(valid), "history": lib.db.routine_counts(since)}

    @app.put("/api/routine")
    async def put_routine(body: dict = Body(...)):
        secs = body.get("sections")
        if not isinstance(secs, list):
            raise HTTPException(400, "sections must be a list")
        lib.db.set_settings({"routine": [
            {"id": str(s.get("id", "")), "title": str(s.get("title", "")),
             "items": [{"id": str(i.get("id", "")), "text": str(i.get("text", ""))} for i in s.get("items", [])]}
            for s in secs]})
        return await get_routine(body.get("day"))

    @app.post("/api/routine/note")
    async def routine_note(body: dict = Body(...)):
        lib.db.routine_note_set(_day(body.get("day")), str(body["item"]), str(body.get("note", "")))
        return {"ok": True}

    @app.get("/api/routine/log")
    async def routine_log(days: int = 60):
        since = (datetime.now() - timedelta(days=max(1, min(days, 3650)))).strftime("%Y-%m-%d")
        return lib.db.routine_log(since)

    @app.get("/api/routine/export.csv")
    async def routine_export(days: int = 3650):
        import csv
        import io
        since = (datetime.now() - timedelta(days=max(1, min(days, 3650)))).strftime("%Y-%m-%d")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["session", "date", "section", "exercise_id", "exercise", "done", "note"])
        rows = lib.db.routine_log("0000-01-01")   # session numbers count from your first practice day, whatever the export range
        days = sorted({r["day"] for r in rows})
        number = {d: i + 1 for i, d in enumerate(days)}
        for r in sorted((r for r in rows if r["day"] >= since), key=lambda r: (r["day"], r["section"] or "", r["text"])):
            w.writerow([number[r["day"]], r["day"], r["section"], r["item"], r["text"], "yes" if r["done"] else "no", r["note"]])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="licksmith-routine.csv"'})

    @app.post("/api/routine/check")
    async def routine_check(body: dict = Body(...)):
        lib.db.routine_set(_day(body.get("day")), str(body["item"]), bool(body.get("done")))
        return {"ok": True}

    # ---------------------------------------------------------------- today / review
    @app.get("/api/today")
    async def today():
        t = srs.today(lib.db, lib.settings)
        def S(r):
            i = _scope_info(r["id"])
            return _lick_summary(lib.lick(r["id"])) | {k: i[k] for k in ("scope_labels", "known_labels", "shelved_until")}
        return {"focus": [S(r) for r in t["focus"]], "focus_total": t["focus_total"],
                "backlog": [S(r) for r in t["backlog"]], "new_held": t["new_held"], "chosen": t["chosen"],
                "shelved": [S(r) for r in t["shelved"]],
                "due": [S(r) for r in t["due"]],
                "new": [S(r) for r in t["new"]],
                "done_today": t["done_today"], "new_remaining": t["new_remaining"],
                "introduced_today": t["introduced_today"]}

    @app.post("/api/licks/{lick_id}/review")
    async def review(lick_id: int, body: dict = Body(...)):
        _get(lick_id)
        t = srs.today(lib.db, lib.settings)
        planned = lick_id in {r["id"] for r in t["due"] + t["new"]} | set(t["focus_ids"])   # anything else is a lick you chose yourself
        r = srs.review(lib.db, lick_id, int(body["rating"]), lib.settings, body.get("keys_done", []),
                       body.get("trouble_keys", []), body.get("tempo"), body.get("mode", "drill"),
                       body.get("note", ""), planned)
        for pc in body.get("clear_hard", []):   # keys played well this session: no longer marked hard
            lib.db.x("DELETE FROM key_hard WHERE lick_id=? AND pc=?", (lick_id, int(pc) % 12))
        names = {k["pc"]: k["label"] for k in lib.keys_for(lick_id)}
        r["expanded_labels"] = [names.get(p, str(p)) for p in r["expanded"]]
        r["scope_labels"] = [names.get(p, str(p)) for p in r["scope"]]
        return r

    @app.post("/api/licks/{lick_id}/unshelve")
    async def unshelve(lick_id: int):
        _get(lick_id)
        lib.db.x("UPDATE cards SET shelved_until=NULL, due=? WHERE lick_id=?", (datetime.now(timezone.utc).isoformat(timespec="seconds"), lick_id))
        return {"ok": True}

    @app.post("/api/licks/{lick_id}/scope")
    async def set_scope(lick_id: int, body: dict = Body(...)):
        """Choose by hand which keys are in a lick's working set (pitch classes)."""
        _get(lick_id)
        pcs = sorted({int(p) % 12 for p in body.get("scope", [])})
        if not pcs:
            raise HTTPException(400, "A lick needs at least one key")
        lib.db.x("INSERT INTO cards(lick_id, scope) VALUES(?,?) ON CONFLICT(lick_id) DO UPDATE SET scope=excluded.scope, good_streak=0", (lick_id, json.dumps(pcs)))
        return _scope_info(lick_id)

    PRACTICE_CFG_KEYS = {"order": str, "reps": int, "hear": bool, "sound": str, "resp": bool, "back": bool,
                         "stay": bool, "swing": int, "start_pc": int}

    @app.post("/api/licks/{lick_id}/practice-cfg")
    async def practice_cfg(lick_id: int, body: dict = Body(...)):
        """Remember how this lick is practised (stay in key, times per key, backing, start key...). The next
        session of this lick starts from it, and a lick with no setup of its own starts from the latest one."""
        _get(lick_id)
        old = _practice_cfg(lick_id) or {}
        cfg = dict(old)
        for k, typ in PRACTICE_CFG_KEYS.items():
            if k in (body.get("cfg") or {}):
                v = body["cfg"][k]
                cfg[k] = None if v is None else typ(v)
        lib.db.x("INSERT INTO lick_practice(lick_id, cfg, ts) VALUES(?,?,?) "
                 "ON CONFLICT(lick_id) DO UPDATE SET cfg=excluded.cfg, ts=excluded.ts",
                 (lick_id, json.dumps(cfg), datetime.now().isoformat(timespec="seconds")))
        # the sound choice only exists for licks with a recording, and the start key belongs to one lick
        shared = {k: v for k, v in cfg.items() if k not in ("start_pc", "sound")}
        lib.db.set_settings({"practice_last": {**(lib.settings.get("practice_last") or {}), **shared}})
        return cfg

    @app.post("/api/licks/{lick_id}/hard")
    async def mark_hard(lick_id: int, body: dict = Body(...)):
        """Mark (hard: true) or unmark one key of a lick as hard."""
        _get(lick_id)
        pc = int(body["pc"]) % 12
        if body.get("hard", True):
            lib.db.x("INSERT OR REPLACE INTO key_hard(lick_id, pc, ts) VALUES(?,?,?)",
                     (lick_id, pc, datetime.now().isoformat(timespec="seconds")))
        else:
            lib.db.x("DELETE FROM key_hard WHERE lick_id=? AND pc=?", (lick_id, pc))
        return {"pc": pc, "hard": bool(body.get("hard", True))}

    @app.get("/api/hard")
    async def hard_keys():
        """Licks with keys marked hard (most marks first), and per key how many licks it is hard in."""
        rows = lib.db.q("SELECT lick_id, pc FROM key_hard ORDER BY lick_id, pc")
        by_lick: dict[int, list[int]] = {}
        for r in rows:
            by_lick.setdefault(r["lick_id"], []).append(r["pc"])
        licks = []
        for lid, pcs in by_lick.items():
            try:
                licks.append({**_lick_summary(lib.lick(lid)), "pcs": pcs})
            except Exception:  # lick deleted meanwhile
                continue
        licks.sort(key=lambda l: (-len(l["pcs"]), l["number"]))
        by_key: dict[str, list[int]] = {}
        for l in licks:
            for pc in l["pcs"]:
                by_key.setdefault(str(pc), []).append(l["number"])
        return {"licks": licks, "by_key": by_key}

    def _practice_cfg(lick_id: int):
        r = lib.db.one("SELECT cfg FROM lick_practice WHERE lick_id=?", (lick_id,))
        try:
            return json.loads(r["cfg"]) if r and r["cfg"] else None
        except ValueError:
            return None

    @app.post("/api/licks/{lick_id}/key-tempo")
    async def key_tempo(lick_id: int, body: dict = Body(...)):
        """Remember how fast you played this lick in one key (pc = key's pitch class)."""
        _get(lick_id)
        pc, tempo = int(body["pc"]) % 12, int(body["tempo"])
        old = lib.db.q("SELECT best FROM key_tempo WHERE lick_id=? AND pc=?", (lick_id, pc))
        best = max(tempo, old[0]["best"] or 0) if old else tempo
        lib.db.x("INSERT INTO key_tempo(lick_id, pc, tempo, best, ts) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(lick_id, pc) DO UPDATE SET tempo=excluded.tempo, best=excluded.best, ts=excluded.ts",
                 (lick_id, pc, tempo, best, datetime.now().isoformat(timespec="seconds")))
        return {"pc": pc, "tempo": tempo, "best": best}

    # ---------------------------------------------------------------- licks
    @app.get("/api/licks")
    async def list_licks(archived: bool = False):
        return lib.list_licks(archived)

    @app.get("/api/repeats")
    async def repeats():
        return lib.repeats()

    @app.get("/api/licks/{lick_id}")
    async def lick_detail(lick_id: int, order: str | None = None):
        lk = _get(lick_id)
        out = _lick_summary(lk)
        out.update(_scope_info(lick_id))
        out["practice_cfg"] = _practice_cfg(lick_id) or lib.settings.get("practice_last") or None
        out["hard_pcs"] = [r["pc"] for r in lib.db.q("SELECT pc FROM key_hard WHERE lick_id=?", (lick_id,))]
        out["keys"] = lib.keys_for(lick_id, order or (out["practice_cfg"] or {}).get("order"))
        out["intervals"] = srs.preview_intervals(lib.db, lick_id, lib.settings)
        out["history"] = lib.db.q("SELECT ts, rating, keys_done, trouble_keys, tempo, mode FROM reviews "
                                  "WHERE lick_id=? ORDER BY ts DESC LIMIT 30", (lick_id,))
        out["key_tempo"] = {str(r["pc"]): r["tempo"] for r in
                            lib.db.q("SELECT pc, tempo FROM key_tempo WHERE lick_id=?", (lick_id,))}
        out["chords"] = lk["data"]["chords"]
        out["audio"] = lib.has_audio(lk["number"])
        out["audio_bars"] = lib.audio_bars(lk["number"]) if out["audio"] else None
        out["related"] = lib.related(lick_id)
        out["tonic_pc"] = lk["tonic_pc"]
        return out

    @app.patch("/api/licks/{lick_id}")
    async def patch_lick(lick_id: int, body: dict = Body(...)):
        _get(lick_id)
        if "tonic_name" in body:
            body["tonic_pc"] = L.NAME_TO_PC.get(body["tonic_name"], _get(lick_id)["tonic_pc"])
        if "number" in body:
            other = lib.db.lick_by_number(int(body["number"]))
            if other and other["id"] != lick_id:
                raise HTTPException(409, f"Lick {body['number']} already exists")
        lib.db.update_lick(lick_id, body)
        lib._svg_cache.clear()
        return _lick_summary(lib.lick(lick_id))

    @app.get("/api/licks/{lick_id}/abc")
    async def lick_abc(lick_id: int):
        _get(lick_id)
        return lib.lick_abc(lick_id)

    @app.post("/api/licks/{lick_id}/abc/preview")
    async def lick_abc_preview(lick_id: int, body: dict = Body(...)):
        _get(lick_id)
        try:
            return lib.lick_abc_preview(body["abc"], int(body.get("sharps", 0)), body.get("meter", "4/4"))
        except Exception as e:  # CaptureError or a bad ABC text
            raise HTTPException(422, str(e))

    @app.post("/api/licks/{lick_id}/abc/edit")
    async def lick_abc_edit(lick_id: int, body: dict = Body(...)):
        _get(lick_id)
        try:
            return lib.edit_abc(body["abc"], int(body.get("sharps", 0)), body.get("meter", "4/4"),
                                int(body["index"]), body["op"], body.get("value"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e))

    @app.post("/api/licks/{lick_id}/abc")
    async def lick_abc_save(lick_id: int, body: dict = Body(...)):
        _get(lick_id)
        try:
            return lib.save_lick_abc(lick_id, body["abc"], int(body.get("sharps", 0)), body.get("meter", "4/4"))
        except Exception as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/licks/{lick_id}")
    async def delete_lick(lick_id: int):
        _get(lick_id)
        lib.db.delete_lick(lick_id)
        return {"ok": True}

    @app.post("/api/licks/{lick_id}/reanalyse")
    async def reanalyse(lick_id: int):
        _get(lick_id)
        an = lib.reanalyse(lick_id)
        return {"auto_tags": an["tags"], "function": an["function"]}

    @app.get("/api/licks/{lick_id}/svg")
    async def lick_svg(lick_id: int, pc: int | None = None, display: str | None = None):
        _get(lick_id)
        return Response(lib.svg(lick_id, pc, display), media_type="image/svg+xml")

    @app.get("/api/licks/{lick_id}/play")
    async def lick_play(lick_id: int, pc: int | None = None):
        _get(lick_id)
        return lib.play(lick_id, pc)

    @app.get("/api/licks/{lick_id}/audio")
    async def lick_audio(lick_id: int, semis: int = 0, bpm: float | None = None):
        lk = _get(lick_id)
        p = lib.lick_audio_path(lk["number"])
        if not p.exists():
            raise HTTPException(404, "No recording saved with this lick")
        if not semis and not bpm:
            return FileResponse(p, media_type="audio/mpeg")
        try:  # practice: the recording in another key / at the practice tempo
            f, offset, marks = lib.clip_variant(lk, semis, bpm)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Could not shift the recording: {e}")
        return FileResponse(f, media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-Clip-Offset": f"{offset:.4f}",
                                                                 "X-Clip-Marks": json.dumps([round(x, 4) for x in marks])})

    @app.get("/api/licks/{lick_id}/events")
    async def lick_events(lick_id: int):
        _get(lick_id)
        return lib.svg_events(lick_id)

    @app.get("/api/licks/{lick_id}/musicxml")
    async def lick_file(lick_id: int):
        lk = _get(lick_id)
        return FileResponse(lk["_path"], filename=Path(lk["file"]).name,
                            media_type="application/vnd.recordare.musicxml+xml")

    # ---------------------------------------------------------------- export (notation + MIDI + audio + YouTube sync)
    @app.get("/api/licks/{lick_id}/export")
    async def lick_export(lick_id: int):
        """One lick as JSON: notes with times in the clip and in the original video, links to the files, the YouTube origin."""
        _get(lick_id)
        return _guard(lambda: lib.export_lick(lick_id)[0])

    @app.get("/api/licks/{lick_id}/export.mid")
    async def lick_export_midi(lick_id: int):
        lk = _get(lick_id)
        rec, blobs = _guard(lambda: lib.export_lick(lick_id))
        return Response(blobs["lick.mid"], media_type="audio/midi",
                        headers={"Content-Disposition": f'attachment; filename="L{lk["number"]:04d}.mid"'})

    @app.get("/api/export/licks.zip")
    async def licks_export_zip(ids: str = ""):
        """A zip bundle (JSON, MusicXML, MIDI, mp3 per lick). ids = comma separated lick ids; empty = every lick."""
        from starlette.background import BackgroundTask

        want = [int(x) for x in ids.split(",") if x.strip().isdigit()] or [r["id"] for r in lib.list_licks()]
        if not want:
            raise HTTPException(404, "No licks to export")
        for i in want:
            _get(i)
        tmp = Path(tempfile.mkdtemp()) / "licksmith-licks.zip"
        _guard(lambda: lib.export_zip(want, tmp))
        return FileResponse(tmp, media_type="application/zip", filename="licksmith-licks.zip",
                            background=BackgroundTask(lambda: shutil.rmtree(tmp.parent, ignore_errors=True)))

    @app.get("/api/licks/{lick_id}/twelve")
    async def lick_twelve(lick_id: int, order: str | None = None):
        _get(lick_id)
        name, xml = lib.twelve_keys_xml(lick_id, order)
        return Response(xml, media_type="application/vnd.recordare.musicxml+xml",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------------------------------------------------------------- import
    @app.post("/api/import/preview")
    async def import_preview(file: UploadFile = File(...), mode: str = Form("auto"), ranges: str = Form(""),
                             pitch_in_file: str = Form("auto"), artist: str = Form(""), tune: str = Form(""),
                             year: str = Form(""), tags: str = Form("")):
        suffix = Path(file.filename or "upload.musicxml").suffix or ".musicxml"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
        try:
            return lib.preview_import(Path(tmp.name), file.filename or "upload.musicxml", mode, ranges or None,
                                      pitch_in_file, {"artist": artist, "tune": tune, "year": year,
                                                      "tags": [t.strip() for t in tags.split(",") if t.strip()]})
        except Exception as e:  # show music21 parse problems in the UI
            raise HTTPException(400, f"Could not read that file: {e}")
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @app.post("/api/import/save")
    async def import_save(body: dict = Body(...)):
        try:
            ids = lib.save_import(body["token"], body["items"])
        except KeyError as e:
            raise HTTPException(410, str(e))
        return {"ids": ids}

    @app.post("/api/playlist")
    async def playlist(file: UploadFile | None = File(None), text: str = Form(""), name: str = Form("")):
        content = text
        if file is not None:
            content = (await file.read()).decode("utf-8", errors="ignore")
            name = name or Path(file.filename or "").stem
        if "irealb://" not in content:
            raise HTTPException(400, "No irealb:// link found. Export the playlist from iReal Pro "
                                     "(Share > Playlist > HTML) or paste the link.")
        n = lib.import_playlist(content, name)
        return {"imported": n}


    # ---------------------------------------------------------------- tunes / fitting
    @app.get("/api/tunes")
    async def tunes():
        return lib.db.tunes()

    @app.get("/api/tune")
    async def tune(title: str):
        try:
            return lib.tune_view(title)
        except KeyError:
            raise HTTPException(404, "No such tune")

    @app.post("/api/tune/learning")
    async def tune_learning(body: dict = Body(...)):
        lib.db.x("UPDATE tunes SET learning=? WHERE title=?", (1 if body.get("learning") else 0, body["title"]))
        return {"ok": True}

    @app.post("/api/fit")
    async def fit(body: dict = Body(...)):
        try:
            return lib.fit(body["title"], int(body.get("gap_bars", 0)), body.get("only_ids"), body.get("pool"), bool(body.get("variants", True)))
        except KeyError:
            raise HTTPException(404, "No such tune")

    @app.post("/api/fit/svg")
    async def fit_svg(body: dict = Body(...)):
        return Response(lib.fit_svg(body["title"], body["placements"]), media_type="image/svg+xml")

    @app.post("/api/fit/snippets")
    async def fit_snippets(body: dict = Body(...)):
        return lib.fit_snippets(body["title"], body["placements"])

    @app.post("/api/fit/play")
    async def fit_play(body: dict = Body(...)):
        return lib.fit_play(body["title"], body["placements"])

    @app.post("/api/fit/file")
    async def fit_file(body: dict = Body(...)):
        name, xml = lib.fit_xml(body["title"], body["placements"], body.get("bpm"))
        return Response(xml, media_type="application/vnd.recordare.musicxml+xml",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------------------------------------------------------------- progress / misc
    @app.get("/api/sound")
    async def sound_status():
        st = lib.settings
        out = renderer.status(st)
        out["engine"] = st.get("sound_engine", "soundfont")
        return out

    @app.post("/api/render")
    async def render(body: dict = Body(...)):
        audio = renderer.render(body, lib.settings)
        if audio is None:
            raise HTTPException(503, renderer.error or "FluidSynth is not available")
        return Response(audio, media_type="audio/wav")

    @app.get("/api/stats")
    async def stats():
        out = srs.stats(lib.db, lib.settings)
        out["hard_by_key"] = (await hard_keys())["by_key"]
        return out

    # ---------------------------------------------------------------- restart
    @app.post("/api/restart")
    async def restart(body: dict = Body(default={})):
        """Start the server again in the same process, terminal and options (after an update, or when it misbehaves). Stops a
        running transcription, so that needs force=true."""
        import os
        import sys
        import threading

        running = [j for j in transcriber.jobs.values() if j.get("state") not in ("done", "error")]
        if running and not body.get("force"):
            raise HTTPException(409, f"A transcription is still running ({len(running)} job{'s' if len(running) > 1 else ''}). "
                                     "Restarting stops it.")
        argv = [sys.executable, *getattr(sys, "orig_argv", [sys.executable, *sys.argv])[1:]]

        def again():
            os.environ["LICKSMITH_RESTARTED"] = "1"   # the browser is already open: don't open another tab
            os.execv(sys.executable, argv)

        threading.Timer(0.5, again).start()  # after this reply has gone out
        return {"ok": True, "pid": os.getpid()}

    @app.exception_handler(Exception)
    async def errors(_req, exc):
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app


def lan_addresses() -> list[str]:
    """This computer's addresses on the local network (nothing is sent: a UDP connect only picks the outgoing interface)."""
    import socket

    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            out.append(sk.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """host "0.0.0.0" (the --lan flag) lets every computer on the local network use the app. There are no accounts, so
    anyone who can reach the port can change the library: only do that on a network you trust."""
    import os
    import socket
    import threading
    import webbrowser

    import uvicorn

    app = create_app(root)
    lan = host in ("0.0.0.0", "::")
    url = f"http://{'127.0.0.1' if lan else host}:{port}"
    if open_browser and not os.environ.get("LICKSMITH_RESTARTED"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"Licksmith running at {url}  (Ctrl-C to stop)")
    if lan:
        print("Open to the local network. Other computers can use:")
        for ip in lan_addresses():
            print(f"  http://{ip}:{port}")
        name = socket.gethostname().split(".")[0]
        if name:
            print(f"  http://{name}.local:{port}   (Macs and most phones)")
        print("There is no login: anyone on this network can change your library.")
    uvicorn.run(app, host=host, port=port, log_level="warning")
