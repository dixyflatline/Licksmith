"""Lick export: everything a lick is, in plain files other software can read.

A lick can be shown (MusicXML + note list), heard as MIDI, heard as the original recording (mp3 clip) and followed on the
original YouTube video. The JSON ties these together: every note and chord has a time in the clip (`t_audio`) and in the
video (`t_video` = `t_audio` + `origin.clip_start`), so a player can highlight notes while either one plays.

No dependencies beyond the standard library, so the format can be produced and checked anywhere.
"""
from __future__ import annotations

import json
import re
import struct
from urllib.parse import parse_qs, urlparse

FORMAT = "licksmith-lick"
VERSION = 1
WRITTEN_SEMIS = 2          # the B-flat trumpet reads a major second above concert pitch
PAD = 0.1                  # seconds of recording kept before and after the bars when a lick is cut

_COMMENT = re.compile(r"\((?P<work>.+)\), (?P<a>\d+(?:\.\d+)?)-(?P<b>\d+(?:\.\d+)?) s of the recording")


# ------------------------------------------------------------------------------------------ YouTube
def video_id(url: str | None) -> str | None:
    """The 11-character id of a youtube.com / music.youtube.com / youtu.be link, else None."""
    if not url:
        return None
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("music.")
    vid = None
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [None])[0]
        else:
            m = re.match(r"^/(?:embed|shorts|live|v)/([^/?]+)", u.path)
            vid = m.group(1) if m else None
    return vid if vid and re.fullmatch(r"[\w-]{11}", vid) else None


def origin_from_comment(comment: str | None, source_info: dict | None) -> dict | None:
    """Licks sent before the origin was stored carry it only as text ("Solo bars 5-6 (work), 12.3-16.0 s of the recording").
    The times there are rounded to 0.1 s and leave out the padding, so what comes back is marked approximate."""
    m = _COMMENT.search(comment or "")
    if not m:
        return None
    a, b = float(m["a"]), float(m["b"])
    o = {"work": m["work"], "clip_start": round(max(0.0, a - PAD), 3), "clip_end": round(b + PAD, 3), "approximate": True}
    return _origin_links(o, source_info)


def _origin_links(o: dict, source_info: dict | None) -> dict:
    o = dict(o)
    src = source_info or {}
    url = o.get("url") or src.get("url")
    o["url"] = url
    o["video_title"] = o.get("video_title") or src.get("title")
    vid = video_id(url)
    o["type"] = "youtube" if vid else ("file" if o.get("work") else None)
    o["video_id"] = vid
    if vid:
        t = int(o.get("clip_start") or 0)
        o["watch_url"] = f"https://www.youtube.com/watch?v={vid}&t={t}s"
        o["embed_url"] = f"https://www.youtube.com/embed/{vid}?start={t}&end={int(round(o.get('clip_end') or 0))}"
    return o


def make_origin(work: str, source_info: dict | None, clip_start: float, clip_end: float) -> dict:
    """What gets stored next to the clip when a lick is sent from the Transcribe page."""
    return _origin_links({"work": work, "clip_start": round(clip_start, 3), "clip_end": round(clip_end, 3)}, source_info)


# ------------------------------------------------------------------------------------------ timing
def bar_marks(marks: list[float] | None, bars: int, duration: float | None) -> list[float]:
    """Seconds into the clip at each bar line (bars + 1 values). Clips without stored marks share the audio evenly
    between a 0.1 s lead-in and lead-out, the same assumption practice mode makes."""
    if marks and len(marks) == bars + 1:
        return [float(x) for x in marks]
    if marks and len(marks) >= 2:
        a, b = marks[0], marks[-1]
    else:
        a, b = PAD, max(0.6, (duration or 0.0) - PAD)
    return [a + (b - a) * i / bars for i in range(bars + 1)]


def beat_to_seconds(beat: float, marks: list[float], bar_len: float) -> float:
    """Quarter-note position in the lick -> seconds in the clip, following the stored bar lines (so a bar played a bit
    faster or slower than its neighbours still lines up)."""
    n = len(marks) - 1
    k = min(n - 1, max(0, int(beat // bar_len + 1e-9)))
    frac = (beat - k * bar_len) / bar_len
    return marks[k] + frac * (marks[k + 1] - marks[k])


# ------------------------------------------------------------------------------------------ MIDI
def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def midi_bytes(data: dict, bpm: float, title: str = "", semis: int = 0, ppq: int = 480) -> bytes:
    """A format-0 standard MIDI file of the lick: lead notes (trumpet, channel 1) and chord symbols as markers.
    `data` is the lick's stored data (offsets in quarter notes, concert pitch); `semis` moves it (e.g. +2 for written)."""
    ev: list[tuple[int, int, bytes]] = []  # (tick, order, message): order puts note-offs before note-ons at the same tick

    def text(tick, kind, s):
        b = s.encode("utf-8")
        ev.append((tick, 0, b"\xff" + bytes([kind]) + _vlq(len(b)) + b))

    ts = data.get("ts") or [4, 4]
    ev.append((0, 0, b"\xff\x51\x03" + struct.pack(">I", int(round(60_000_000 / max(20.0, bpm))))[1:]))
    ev.append((0, 0, b"\xff\x58\x04" + bytes([ts[0], {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(ts[1], 2), 24, 8])))
    ev.append((0, 0, b"\xc0" + bytes([56])))  # GM 57 = trumpet
    if title:
        text(0, 0x03, title)
    for off, sym in data.get("chords", []):
        text(int(round(off * ppq)), 0x06, str(sym))
    for off, dur, midi in data.get("notes", []):
        p = max(0, min(127, int(midi) + semis))
        on, offt = int(round(off * ppq)), int(round((off + dur) * ppq))
        ev.append((on, 2, bytes([0x90, p, 96])))
        ev.append((max(on + 1, offt - 4), 1, bytes([0x80, p, 0])))   # a hair short so repeated notes re-articulate
    for off, midi in data.get("graces", []):
        p = max(0, min(127, int(midi) + semis))
        on = max(0, int(round(off * ppq)) - ppq // 8)
        ev.append((on, 2, bytes([0x90, p, 80])))
        ev.append((on + ppq // 8 - 4, 1, bytes([0x80, p, 0])))
    end = int(round(float(data.get("length", 0)) * ppq))
    ev.append((max(end, max((t for t, _, _ in ev), default=0)), 3, b"\xff\x2f\x00"))
    ev.sort(key=lambda e: (e[0], e[1]))
    body, last = b"", 0
    for tick, _, msg in ev:
        body += _vlq(tick - last) + msg
        last = tick
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + b"MTrk" + struct.pack(">I", len(body)) + body


# ------------------------------------------------------------------------------------------ the lick record
def lick_record(lk: dict, *, audio: dict | None, origin: dict | None, tempo: float, files: dict) -> dict:
    """The JSON of one lick. `lk` is a library lick (with its `data`); `audio` is {"duration", "bar_marks"} when a
    recording exists; `origin` comes from make_origin / origin_from_comment; `files` names the sibling files."""
    d = lk["data"]
    ts = d.get("ts") or [4, 4]
    bars = int(d.get("bars") or lk.get("bars") or 1)
    bar_len = ts[0] * 4 / ts[1]
    marks = bar_marks((audio or {}).get("bar_marks"), bars, (audio or {}).get("duration")) if audio else None
    t0 = (origin or {}).get("clip_start") if origin else None

    def times(beat):
        if marks is None:
            return {}
        ta = beat_to_seconds(beat, marks, bar_len)
        out = {"t_audio": round(ta, 3)}
        if t0 is not None:
            out["t_video"] = round(ta + t0, 3)
        return out

    if marks:
        tempo = round(bar_len * bars * 60.0 / max(0.1, marks[-1] - marks[0]), 1)  # the speed the recording was played at
    rec = {
        "format": FORMAT, "version": VERSION,
        "number": lk["number"], "label": lk.get("label"), "name": lk.get("name"), "function": lk.get("function"),
        "artist": lk.get("artist") or "", "tune": lk.get("tune") or "", "year": lk.get("year"),
        "tags": lk.get("tags") or [], "auto_tags": lk.get("auto_tags") or [], "comment": lk.get("comment") or "",
        "source_bars": lk.get("source_bars"),
        "key": {"tonic": lk.get("tonic_name"), "tonic_pc": lk.get("tonic_pc"), "mode": lk.get("mode"), "pitch": "concert"},
        "meter": ts, "bars": bars, "length_beats": d.get("length"), "tempo_bpm": tempo,
        "transposition": {"written_semitones_above_concert": WRITTEN_SEMIS, "note": "every midi value below is concert pitch"},
        "notes": [dict({"bar": int(off // bar_len) + 1, "start_beats": off, "dur_beats": du, "midi": m,
                        "midi_written": m + WRITTEN_SEMIS}, **times(off)) for off, du, m in d.get("notes", [])],
        "grace_notes": [dict({"start_beats": off, "midi": m}, **times(off)) for off, m in d.get("graces", [])],
        "chords": [dict({"start_beats": off, "symbol": s}, **times(off)) for off, s in d.get("chords", [])],
        "files": files,
        "audio": None, "origin": None,
    }
    if marks:
        rec["audio"] = {"file": files.get("audio"), "duration": round((audio or {}).get("duration") or 0, 3),
                        "bar_marks": [round(x, 3) for x in marks],
                        "marks_are_measured": bool((audio or {}).get("bar_marks")),
                        "note": "bar_marks[i] = seconds into the clip where bar i+1 starts; the last value is the end of the last bar"}
    if origin and origin.get("type"):
        o = dict(origin)
        o["time_map"] = ("t_video = t_audio + clip_start" if o.get("clip_start") is not None else None)
        if marks and o.get("clip_start") is not None:
            o["bar_marks_video"] = [round(x + o["clip_start"], 3) for x in marks]
        rec["origin"] = o
    return rec


README = """Licksmith lick export
=====================

index.json            every lick in this bundle (number, label, folder)
licks/L0043/lick.json everything about one lick; see below
licks/L0043/lick.musicxml  the notation (B-flat part, opens in MuseScore, Finale, Dorico, ...)
licks/L0043/lick.mid  a MIDI file at the speed of the recording (concert pitch)
licks/L0043/audio.mp3 the clip of the original recording, when the lick has one

lick.json
---------
notes / chords / grace_notes  start_beats and dur_beats are quarter notes from the first beat of the lick;
                              midi is concert pitch (midi_written adds 2 for the B-flat trumpet).
audio.bar_marks               seconds into audio.mp3 where each bar starts (last value = end of the last bar).
t_audio                       seconds into audio.mp3 for that note/chord, following the bar marks.
origin.url / watch_url / embed_url
                              the original recording (YouTube) and a link that starts at the lick.
t_video                       seconds into the YouTube video: t_video = t_audio + origin.clip_start.
origin.approximate            true for licks saved before Licksmith stored the origin: the start can be off by ~0.1 s.

To follow along: play audio.mp3 (or seekTo(origin.clip_start) in the YouTube player) and light up the note whose
t_audio / t_video is the last one not after the player's current time.
"""
