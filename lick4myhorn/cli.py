"""Command line: licksmith <command> ...   (run from the project folder)

  licksmith new <work> --title T --artist A --tune "iReal title"   create library/works/<work>/form.json
  licksmith fetch <work> <url>                                      download audio -> audio.wav
  licksmith charts import <playlist.html>                           import iReal playlists
  licksmith charts show "<title>"                                   print a tune's chords
  licksmith transcribe <work> [--engine muscriptor --model medium | --engine midi --midi file.mid]
  licksmith gridcheck <work>                                        clicks over the recording + bars.csv
  licksmith build <work> [--engine NAME] [--simplicity 1.0]         -> solo.musicxml + solo.mid
  licksmith merge <work> --engines A,B [--name merged]               fill A's empty bars from B
  licksmith bench <work> --book book.musicxml [--engine NAME]       compare with the published solo

  licksmith serve [--port 8765] [--lan]                             lick library + practice web app (--lan: every computer on the network)
  licksmith licks import <file.musicxml> [--mode auto|sheet|single|range --bars 17-20]
  licksmith licks list
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path.cwd()
LIB = ROOT / "library"
CHARTS = LIB / "charts" / "charts.json"


def work_dir(name: str) -> Path:
    return LIB / "works" / name


def _form(name: str):
    from .form import load_form

    return load_form(work_dir(name) / "form.json", CHARTS)


def _audio(name: str) -> Path | None:
    p = work_dir(name) / "audio.wav"
    return p if p.exists() else None


def cmd_new(a):
    d = work_dir(a.work)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "form.json"
    if f.exists():
        print(f"{f} already exists; not overwriting.")
        return
    tmpl = {
        "title": a.title or a.work, "artist": a.artist or "", "tune": a.tune or a.title or a.work,
        "beats_per_bar": 4, "bpm": a.bpm or 120, "feel": "swing", "instrument": "trumpet",
        "ensemble": ["trumpet", "tenor_sax", "acoustic_piano", "acoustic_bass", "drums"],
        "grid": {"first_downbeat": 0.0, "anchors": []},
        "sections": [
            {"label": "head", "choruses": 1},
            {"label": "trumpet solo", "choruses": 1, "transcribe": True},
        ],
    }
    f.write_text(json.dumps(tmpl, indent=2))
    print(f"Created {f}. Edit first_downbeat, sections and ensemble, then run fetch / transcribe.")


def cmd_fetch(a):
    from .fetch import fetch

    wav = fetch(a.url, work_dir(a.work))
    print(f"Saved {wav}")


def cmd_charts(a):
    from .ireal import find_tune, import_playlist

    if a.action == "import":
        out = import_playlist(Path(a.arg), CHARTS.parent)
        n = len(json.loads(out.read_text()))
        print(f"{out}: {n} tunes")
    else:
        t = find_tune(a.arg, CHARTS)
        print(f"{t['title']} ({t['composer']}) key {t['key']} {t['time_signature'][0]}/{t['time_signature'][1]}")
        for i in range(0, len(t["bars"]), 4):
            print(" | ".join(f"{' '.join(b):<12}" for b in t["bars"][i:i + 4]))


def cmd_transcribe(a):
    from . import engines

    form = _form(a.work)
    inst = form.data.get("instrument", "trumpet")
    if a.engine == "muscriptor":
        audio = _audio(a.work)
        if audio is None:
            raise SystemExit("No audio.wav yet: run licksmith fetch first.")
        ensemble = a.ensemble.split(",") if a.ensemble else form.data.get("solo_ensemble") or form.data.get("ensemble", [])
        window = None
        if a.solo_only:
            grid = _grid(a.work, form)
            bars = [b.number for b in form.transcribed_bars()]
            bpb = form.beats_per_bar
            window = (float(grid[(bars[0] - 1) * bpb]) - 2.0, float(grid[min(bars[-1] * bpb, len(grid) - 1)]) + 2.0)
        bp = tuple(float(x) for x in a.bandpass.split("-")) if a.bandpass else None
        name = a.name or "muscriptor-" + a.model + (f"-cfg{a.cfg_coef:g}" if a.cfg_coef != 1 else "") + \
            ("-solo" if a.solo_only else "") + (f"-bp{int(bp[0])}-{int(bp[1])}" if bp else "") + \
            (f"-hpss{a.hpss:g}" if a.hpss else "") + (f"-{a.channel}" if a.channel != "mix" else "")
        out = work_dir(a.work) / "engines" / name
        p = engines.run_muscriptor(audio, out, ensemble, inst, a.model, a.cfg_coef, window,
                                   channel=a.channel, bandpass=bp, hpss=a.hpss)
    elif a.engine == "midi":
        if not a.midi:
            raise SystemExit("--midi file.mid is required with --engine midi")
        out = work_dir(a.work) / "engines" / (a.name or "midi")
        p = engines.import_midi(Path(a.midi), out, inst)
    else:
        raise SystemExit(f"Unknown engine {a.engine}")
    print(f"Notes: {p}")
    print(f"Next: licksmith build {a.work} --engine {out.name}")


def _grid(name: str, form):
    from .grid import build_grid

    audio = _audio(name)
    return build_grid(form, str(audio) if audio else None)


def cmd_gridcheck(a):
    from .gridcheck import write_bars_csv, write_click_check

    form = _form(a.work)
    grid = _grid(a.work, form)
    d = work_dir(a.work)
    print(f"Wrote {write_bars_csv(form, grid, d / 'bars.csv')}")
    audio = _audio(a.work)
    if audio:
        print(f"Wrote {write_click_check(audio, form, grid, d / 'grid_check.wav')}  <- listen to this")


def _engine_dir(work: str, engine: str | None) -> Path:
    base = work_dir(work) / "engines"
    if engine:
        return base / engine
    dirs = sorted(p for p in base.glob("*") if (p / "notes.json").exists())
    if len(dirs) != 1:
        raise SystemExit(f"Pick an engine with --engine (found: {[p.name for p in dirs]})")
    return dirs[0]


def cmd_build(a):
    from .engines import load_notes
    from .quantize import quantize_notes
    from .xmlout import build_score, write_midi, write_musicxml

    form = _form(a.work)
    ed = _engine_dir(a.work, a.engine)
    grid = _grid(a.work, form)
    from .engines import in_range

    inst = form.data.get("instrument", "trumpet")
    raw = load_notes(ed)
    notes = in_range(raw, inst) if not a.keep_out_of_range else raw
    if len(notes) < len(raw):
        print(f"Dropped {len(raw) - len(notes)} notes outside the {inst} range (other instruments).")
    if not a.keep_chords:
        from .engines import drop_chords

        notes, n_chord = drop_chords(notes)
        if n_chord:
            print(f"Dropped {n_chord} notes that started together with others (chords = comping, not the horn).")
    q = quantize_notes(notes, grid, form, simplicity=a.simplicity, consistency=a.consistency,
                       sync_guard=not a.allow_16th_syncopation)
    bars = [b.number for b in form.transcribed_bars()]
    score = build_score(q, form, bars, form.data.get("instrument", "trumpet"), concert=a.concert, renumber=True)
    xml = write_musicxml(score, ed / "solo.musicxml")
    from .xmlout import validate_musicxml
    problems = validate_musicxml(xml, form.beats_per_bar)
    print("MusicXML check: OK" if not problems else "MusicXML check found problems:\n  " + "\n  ".join(problems[:20]))
    write_midi(score, ed / "solo.mid")
    print(f"{len(q)} notes in {len(bars)} bars -> {xml}")
    bpb = form.beats_per_bar
    filled = {int(n.start // bpb) + 1 for n in q}
    empty = [i + 1 for i, b in enumerate(bars) if b not in filled]
    if empty:
        print(f"Empty bars (no notes transcribed), numbered as in the score: {empty}")


def cmd_merge(a):
    """Fill bars a primary engine left empty with notes from other engines (in order)."""
    import numpy as np

    from .engines import load_notes, save_notes
    from .grid import time_to_beat

    form = _form(a.work)
    grid = _grid(a.work, form)
    bpb = form.beats_per_bar
    names = a.engines.split(",")

    def by_bar(notes):
        beats = time_to_beat([n["onset"] for n in notes], grid) if notes else np.array([])
        out: dict[int, list] = {}
        for n, b in zip(notes, beats):
            out.setdefault(int(np.floor(b / bpb)) + 1, []).append(n)
        return out

    from .engines import in_range

    inst = form.data.get("instrument", "trumpet")
    from .engines import drop_chords

    runs = [by_bar(drop_chords(in_range(load_notes(work_dir(a.work) / "engines" / n), inst))[0]) for n in names]
    rest_bars = {int(x) for x in a.rest_bars.split(",")} if a.rest_bars else set()
    solo_bars = [b.number for b in form.transcribed_bars()]
    merged, source = [], {}
    for i, bar in enumerate(solo_bars):
        if i + 1 in rest_bars:  # numbered as in the score
            continue
        if a.strategy == "most":
            best = max(range(len(names)), key=lambda k: (len(runs[k].get(bar, [])), -k))
            order = [best]
        else:
            order = range(len(names))
        for k in order:
            if runs[k].get(bar):
                merged += runs[k][bar]
                source[i + 1] = names[k]
                break
    out = work_dir(a.work) / "engines" / a.name
    out.mkdir(parents=True, exist_ok=True)
    save_notes(sorted(merged, key=lambda n: n["onset"]), out)
    filled = sorted(k for k, v in source.items() if v != names[0])
    print(f"{len(merged)} notes -> {out}/notes.json")
    print(f"Bars (score numbering) taken from {', '.join(names[1:])}: {filled or 'none'}")
    print(f"Next: licksmith build {a.work} --engine {a.name}")


def cmd_bench(a):
    from .bench import run_bench

    form = _form(a.work)
    ed = _engine_dir(a.work, a.engine)
    report = run_bench(Path(a.book), ed / "solo.musicxml", form.chorus_len, work_dir(a.work) / "bench",
                       engine=ed.name, book_first_bar=a.book_first_bar)
    print(report.read_text())


def cmd_serve(a):
    from .server import serve

    serve(ROOT, host="0.0.0.0" if a.lan else a.host, port=a.port, open_browser=not a.no_browser)


def cmd_licks(a):
    from .library import Library

    lib = Library(ROOT)
    if a.action == "list":
        for l in lib.list_licks():
            print(f"{l['number']:>4}  {l['label']:<60} {l['state']:<9} {', '.join(l['all_tags'][:6])}")
        return
    if a.action == "rebeam":
        print(f"Rewrote {lib.rebeam_files()} lick files with beams (old copies in {lib.lib / 'licks' / 'before-beams'})")
        return
    src = Path(a.file)
    p = lib.preview_import(src, src.name, a.mode, a.bars, a.pitch,
                           {"artist": a.artist or "", "tune": a.tune or "", "year": a.year or "",
                            "tags": [t.strip() for t in (a.tags or "").split(",") if t.strip()]})
    ids = lib.save_import(p["token"], p["items"])
    for it in p["items"]:
        print(f"Lick {it['number']}: {it['function']} in {it['tonic_name']} (concert), "
              f"{it['bars']} bars, tags: {', '.join((it['tags'] + it['auto_tags'])[:8])}")
    print(f"Saved {len(ids)} licks to {lib.lib / 'licks'}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="licksmith", description="Licksmith")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new"); s.add_argument("work"); s.add_argument("--title"); s.add_argument("--artist")
    s.add_argument("--tune"); s.add_argument("--bpm", type=float); s.set_defaults(fn=cmd_new)

    s = sub.add_parser("fetch"); s.add_argument("work"); s.add_argument("url"); s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("charts"); s.add_argument("action", choices=["import", "show"]); s.add_argument("arg")
    s.set_defaults(fn=cmd_charts)

    s = sub.add_parser("transcribe"); s.add_argument("work")
    s.add_argument("--engine", default="muscriptor", choices=["muscriptor", "midi"])
    s.add_argument("--model", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--cfg-coef", type=float, default=1.0,
                   help="classifier-free guidance: >1 follows the instrument list harder (try 2)")
    s.add_argument("--solo-only", action="store_true", help="transcribe only the bars marked transcribe (+2 s)")
    s.add_argument("--ensemble", help="instruments playing, comma-separated (overrides form.json)")
    s.add_argument("--bandpass", help="LOW-HIGH in Hz, e.g. 220-6000 (trumpet range, cuts bass and cymbals)")
    s.add_argument("--hpss", type=float, default=0.0, help="remove drums/cymbals first (1 gentle, 2-3 strong)")
    s.add_argument("--channel", default="mix", choices=["mix", "left", "right", "side"],
                   help="stereo records: pick the side the horn is panned to")
    s.add_argument("--midi"); s.add_argument("--name"); s.set_defaults(fn=cmd_transcribe)

    s = sub.add_parser("gridcheck"); s.add_argument("work"); s.set_defaults(fn=cmd_gridcheck)

    s = sub.add_parser("build"); s.add_argument("work"); s.add_argument("--engine")
    s.add_argument("--simplicity", type=float, default=1.0)
    s.add_argument("--consistency", type=float, default=0.006,
                   help="keep busy beats in a bar all 16ths or all triplets (0 = off, higher = stricter)")
    s.add_argument("--allow-16th-syncopation", action="store_true",
                   help="let beats with only 1-2 notes use 16th positions (off by default: swung 8ths/triplets)")
    s.add_argument("--concert", action="store_true", help="write concert pitch instead of B-flat")
    s.add_argument("--keep-chords", action="store_true",
                   help="keep notes that start together (normally treated as comping and dropped)")
    s.add_argument("--keep-out-of-range", action="store_true",
                   help="keep notes outside the instrument's range (normally dropped as other instruments)")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("merge", help="fill empty bars of the first engine from the others")
    s.add_argument("work"); s.add_argument("--engines", required=True, help="primary,fallback1,...")
    s.add_argument("--name", default="merged")
    s.add_argument("--strategy", default="fill", choices=["fill", "most"],
                   help="fill: take the first engine, fill its empty bars; most: per bar, the engine with most notes")
    s.add_argument("--rest-bars", help="bars (score numbering) that are rests in the solo, kept empty, e.g. 4")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("bench"); s.add_argument("work"); s.add_argument("--book", required=True)
    s.add_argument("--engine"); s.add_argument("--book-first-bar", type=int, default=1)
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("serve", help="lick library and practice web app")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--lan", action="store_true", help="let every computer on the local network use the app (same as --host 0.0.0.0)")
    s.add_argument("--no-browser", action="store_true"); s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("licks", help="import / list licks without the web app")
    s.add_argument("action", choices=["import", "list", "rebeam"]); s.add_argument("file", nargs="?")
    s.add_argument("--mode", default="auto", choices=["auto", "sheet", "single", "range"])
    s.add_argument("--bars", help="for --mode range, e.g. 17-20,33-36")
    s.add_argument("--pitch", default="auto", choices=["auto", "written", "concert"])
    s.add_argument("--artist"); s.add_argument("--tune"); s.add_argument("--year"); s.add_argument("--tags")
    s.set_defaults(fn=cmd_licks)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
