"""SQLite store: licks, practice cards, review log, tunes, practice plans, settings.

The database lives in library/lick4myhorn.sqlite next to the MusicXML files. Every lick is also a
MusicXML file in library/licks/, so nothing is locked inside the database.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS licks(
  id INTEGER PRIMARY KEY,
  number INTEGER UNIQUE,
  name TEXT,
  function TEXT,
  tonic_pc INTEGER,
  tonic_name TEXT,
  mode TEXT,
  artist TEXT DEFAULT '',
  tune TEXT DEFAULT '',
  year INTEGER,
  comment TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  source TEXT DEFAULT '',
  source_bars TEXT DEFAULT '',
  file TEXT,
  tags TEXT DEFAULT '[]',
  auto_tags TEXT DEFAULT '[]',
  data TEXT,
  bars INTEGER,
  low INTEGER,
  high INTEGER,
  favorite INTEGER DEFAULT 0,
  archived INTEGER DEFAULT 0,
  created TEXT,
  updated TEXT
);
CREATE TABLE IF NOT EXISTS cards(
  lick_id INTEGER PRIMARY KEY REFERENCES licks(id) ON DELETE CASCADE,
  fsrs TEXT,
  due TEXT,
  reps INTEGER DEFAULT 0,
  lapses INTEGER DEFAULT 0,
  stability REAL,
  last_review TEXT,
  last_rating INTEGER,
  introduced TEXT
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY,
  lick_id INTEGER REFERENCES licks(id) ON DELETE CASCADE,
  ts TEXT,
  rating INTEGER,
  keys_done TEXT DEFAULT '[]',
  trouble_keys TEXT DEFAULT '[]',
  tempo INTEGER,
  mode TEXT DEFAULT 'drill',
  note TEXT DEFAULT '',
  due_after TEXT,
  stability REAL
);
CREATE TABLE IF NOT EXISTS key_tempo(
  lick_id INTEGER REFERENCES licks(id) ON DELETE CASCADE,
  pc INTEGER,
  tempo INTEGER,
  best INTEGER,
  ts TEXT,
  PRIMARY KEY(lick_id, pc)
);
CREATE TABLE IF NOT EXISTS tunes(
  title TEXT PRIMARY KEY,
  composer TEXT,
  style TEXT,
  key TEXT,
  ts TEXT,
  bars TEXT,
  playlist TEXT DEFAULT '',
  learning INTEGER DEFAULT 0,
  added TEXT
);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY,
  tune TEXT,
  created TEXT,
  transpose INTEGER DEFAULT 0,
  placements TEXT,
  file TEXT
);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS routine_notes(
  day TEXT,
  item TEXT,
  note TEXT,
  PRIMARY KEY(day, item)
);
CREATE TABLE IF NOT EXISTS routine_checks(
  day TEXT,
  item TEXT,
  PRIMARY KEY(day, item)
);
CREATE INDEX IF NOT EXISTS reviews_lick ON reviews(lick_id);
CREATE INDEX IF NOT EXISTS reviews_ts ON reviews(ts);
"""

def _items(*texts):
    return [{"id": "", "text": t} for t in texts]


DEFAULT_ROUTINE = [
    {"id": "warmup", "title": "Warm up", "items": _items(
        "Long tones", "Lip slurs / flexibility", "Mouthpiece buzzing", "Easy scales at a slow tempo")},
    {"id": "technical", "title": "Technical study", "items": _items(
        "Scales and arpeggios in all keys", "Interval / pattern exercises", "Tonguing or articulation study")},
    {"id": "tunes", "title": "Learn tunes", "items": _items(
        "Melody from memory", "Chord tones / guide tones over the changes", "Solo over the form with a backing track")},
    {"id": "licks", "title": "Learn licks", "items": _items(
        "Today's lick session (reviews + new)", "One lick in all 12 keys")},
    {"id": "cooldown", "title": "Cool down", "items": _items(
        "Soft low-register playing", "Long tones, pp", "Rest the chops")},
]

DEFAULTS = {
    "routine": DEFAULT_ROUTINE,     # practice checklist: sections of items, checked off once per day
    "display": "written",          # written (B-flat) | concert
    "range_low": 54,               # written F#3
    "range_high": 84,              # written C6
    "key_order": "mine",
    "my_steps": [0, 5, 7, -2],     # original, P4 up, P5 up, M2 down (then cycle of 4ths)
    "new_per_day": 2,
    "max_reviews": 20,
    "desired_retention": 0.9,
    "tempo": 120,
    "start_tempo": 60,             # lick practice: first tempo in a key you have not practiced yet
    "tempo_step": 10,              # lick practice: the faster/slower buttons
    "stay_in_key": True,           # lick practice: keep looping one key until you move on yourself
    "swing": 0.62,                 # position of the off-beat eighth (0.5 straight, 0.667 triplet)
    "lead_in": 1,                  # count-in bars
    "reps_per_key": 2,             # times through the lick before moving to the next key
    "call_response": True,         # leave the same number of bars empty to play it back
    "play_lick": True,
    "backing": True,
    "backing_style": "band",       # band (piano, bass, drums) | piano (chords only); previews always use piano
    "instrument": "trumpet",
    "sound_engine": "soundfont",   # soundfont (FluidSynth, realistic) | synth (built-in browser synth)
    "soundfont": "",               # path to a .sf2/.sf3; empty = find one automatically
    "lead_sound": "trumpet",       # trumpet | muted_trumpet
    "vision_provider": "deepseek",  # deepseek | anthropic (screenshot reader)
    "vision_model": "",            # empty = the provider's default model
    "vision_tiles": "bars",        # bars (one picture per bar) | whole
    "capture_key": 0,              # last key signature used for a capture (-7..7 sharps)
    "capture_meter": "4/4",
    "capture_basis": "trumpet",    # trumpet | alto | tenor | concert
    "capture_chords": "same",      # same (written like the notes) | concert
}

JSON_COLS = {"tags", "auto_tags", "data", "keys_done", "trouble_keys", "placements", "bars_json"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tunes)")}
        if "raw" not in cols:  # added after the first release
            self.conn.execute("ALTER TABLE tunes ADD COLUMN raw TEXT DEFAULT ''")
        self.conn.commit()

    # ------------------------------------------------------------------ helpers
    def q(self, sql: str, args=()) -> list[dict]:
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        r = self.conn.execute(sql, args).fetchone()
        return self._row(r) if r else None

    def x(self, sql: str, args=()) -> int:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for k in list(d):
            if k in JSON_COLS and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict:
        out = dict(DEFAULTS)
        for r in self.conn.execute("SELECT k, v FROM settings"):
            try:
                out[r["k"]] = json.loads(r["v"])
            except ValueError:
                out[r["k"]] = r["v"]
        return out

    def set_settings(self, values: dict) -> dict:
        for k, v in values.items():
            if k in DEFAULTS:
                self.conn.execute("INSERT OR REPLACE INTO settings(k, v) VALUES(?, ?)", (k, json.dumps(v)))
        self.conn.commit()
        return self.settings()

    # ------------------------------------------------------------------ routine checklist
    def routine(self) -> list[dict]:
        """The checklist template; every section and item gets a stable id so ticks survive renames."""
        out = []
        for si, sec in enumerate(self.settings().get("routine") or []):
            sid = str(sec.get("id") or f"s{si}")
            items = [{"id": str(it.get("id") or f"{sid}-{ii}"), "text": str(it.get("text", "")).strip()}
                     for ii, it in enumerate(sec.get("items", [])) if str(it.get("text", "")).strip()]
            out.append({"id": sid, "title": str(sec.get("title", "")).strip() or "Section", "items": items})
        return out

    def routine_checked(self, day: str) -> list[str]:
        return [r["item"] for r in self.q("SELECT item FROM routine_checks WHERE day=?", (day,))]

    def routine_set(self, day: str, item: str, done: bool) -> None:
        if done:
            self.conn.execute("INSERT OR IGNORE INTO routine_checks(day, item) VALUES(?,?)", (day, item))
        else:
            self.conn.execute("DELETE FROM routine_checks WHERE day=? AND item=?", (day, item))
        self.conn.commit()

    def routine_notes(self, day: str) -> dict:
        return {r["item"]: r["note"] for r in self.q("SELECT item, note FROM routine_notes WHERE day=?", (day,))}

    def routine_note_set(self, day: str, item: str, note: str) -> None:
        note = note.strip()
        if note:
            self.conn.execute("INSERT OR REPLACE INTO routine_notes(day, item, note) VALUES(?,?,?)", (day, item, note))
        else:
            self.conn.execute("DELETE FROM routine_notes WHERE day=? AND item=?", (day, item))
        self.conn.commit()

    def routine_log(self, since: str) -> list[dict]:
        """Every tick and note since a day, newest first, with the section and item text they belong to."""
        names = {it["id"]: (sec["title"], it["text"]) for sec in self.routine() for it in sec["items"]}
        rows: dict = {}
        for r in self.q("SELECT day, item FROM routine_checks WHERE day>=?", (since,)):
            rows[(r["day"], r["item"])] = {"day": r["day"], "item": r["item"], "done": True, "note": ""}
        for r in self.q("SELECT day, item, note FROM routine_notes WHERE day>=?", (since,)):
            rows.setdefault((r["day"], r["item"]), {"day": r["day"], "item": r["item"], "done": False})["note"] = r["note"]
        out = []
        for row in rows.values():
            if row["item"] in names:
                row["section"], row["text"] = names[row["item"]]
                out.append(row)
        return sorted(out, key=lambda r: r["day"], reverse=True)

    def routine_counts(self, since: str) -> dict:
        return {r["day"]: r["n"] for r in
                self.q("SELECT day, COUNT(*) n FROM routine_checks WHERE day>=? GROUP BY day", (since,))}

    # ------------------------------------------------------------------ licks
    def next_number(self) -> int:
        r = self.conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM licks").fetchone()
        return r[0]

    def lick(self, lick_id: int) -> dict | None:
        return self.one("SELECT * FROM licks WHERE id=?", (lick_id,))

    def lick_by_number(self, number: int) -> dict | None:
        return self.one("SELECT * FROM licks WHERE number=?", (number,))

    def save_lick(self, rec: dict) -> int:
        """Insert, or update the lick with the same number (practice history is kept)."""
        rec = dict(rec)
        for k in ("tags", "auto_tags", "data"):
            if k in rec and not isinstance(rec[k], str):
                rec[k] = json.dumps(rec[k])
        rec["updated"] = now_iso()
        existing = self.lick_by_number(rec["number"]) if rec.get("number") else None
        if existing:
            cols = [k for k in rec if k not in ("id", "created", "favorite")]
            self.conn.execute(f"UPDATE licks SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                              [rec[c] for c in cols] + [existing["id"]])
            self.conn.commit()
            return existing["id"]
        rec.setdefault("created", rec["updated"])
        cols = [k for k in rec if k != "id"]
        cur = self.conn.execute(f"INSERT INTO licks({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                                [rec[c] for c in cols])
        self.conn.commit()
        return cur.lastrowid

    def update_lick(self, lick_id: int, fields: dict) -> None:
        allowed = {"name", "function", "artist", "tune", "year", "comment", "notes", "tags", "favorite",
                   "archived", "tonic_pc", "tonic_name", "mode", "number"}
        f = {k: (json.dumps(v) if k == "tags" else v) for k, v in fields.items() if k in allowed}
        if not f:
            return
        f["updated"] = now_iso()
        self.conn.execute(f"UPDATE licks SET {', '.join(k + '=?' for k in f)} WHERE id=?",
                          list(f.values()) + [lick_id])
        self.conn.commit()

    def delete_lick(self, lick_id: int) -> None:
        self.conn.execute("DELETE FROM licks WHERE id=?", (lick_id,))
        self.conn.commit()

    def licks(self, include_archived: bool = False) -> list[dict]:
        sql = """SELECT l.*, c.due, c.reps, c.lapses, c.stability, c.last_review, c.last_rating, c.introduced
                 FROM licks l LEFT JOIN cards c ON c.lick_id = l.id"""
        if not include_archived:
            sql += " WHERE l.archived = 0"
        return self.q(sql + " ORDER BY l.number")

    # ------------------------------------------------------------------ tunes
    def save_tune(self, t: dict, playlist: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO tunes(title, composer, style, key, ts, bars, playlist, learning, added, raw) "
            "VALUES(?,?,?,?,?,?,?,COALESCE((SELECT learning FROM tunes WHERE title=?),0),?,?)",
            (t["title"], t.get("composer", ""), t.get("style", ""), t.get("key", ""),
             json.dumps(t.get("time_signature", [4, 4])), json.dumps(t["bars"]), playlist, t["title"], now_iso(),
             t.get("raw", "")))
        self.conn.commit()

    def tunes(self) -> list[dict]:
        out = self.q("SELECT title, composer, style, key, ts, playlist, learning FROM tunes ORDER BY title")
        for t in out:
            t["ts"] = json.loads(t["ts"]) if t.get("ts") else [4, 4]
        return out

    def tune(self, title: str) -> dict | None:
        t = self.one("SELECT * FROM tunes WHERE title=?", (title,))
        if t:
            t["ts"] = json.loads(t["ts"]) if t.get("ts") else [4, 4]
            t["bars"] = json.loads(t["bars"])
        return t
