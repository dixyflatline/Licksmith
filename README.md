# Licksmith

A personal jazz lick library and practice app for B♭ trumpet. It follows Spec v2 in the
"Jazz Transcriber — Reverse-Engineered Product Spec" doc.

**Names.** A lick is named after where it comes from, e.g. "Chet Baker Autumn Leaves" (artist + tune), or any name you type in the lick's Name box. The number, bars, function and key are columns, and everything else is a tag, all editable.

**Lick library and practice (main app).** You write licks in MuseScore, export MusicXML and
import them. The app tags each lick, drills it in 12 keys inside your trumpet range with a
backing band, schedules reviews on the forgetting curve (FSRS), and fits licks into iReal tunes.

**Transcription (parked).** The audio-to-notes pipeline below still works, but the models are
not accurate enough yet (see the Parked section of the spec).

All outputs are open formats: MusicXML (open it in MuseScore to edit), MIDI, JSON, CSV and SQLite.

**Naming note.** The project used to be called Lick4MyHorn. The command is now `licksmith` (`l4mh` still works as an alias). The Python package folder is still `lick4myhorn/` and the database file is still `library/lick4myhorn.sqlite`, so existing installs and libraries keep working.

## One-time setup (Apple Silicon Mac)

```bash
cd ~/Documents/Dev/Licksmith    # or wherever you keep the project folder
bash setup_mac.sh          # ffmpeg, uv, Python 3.12 venv, MuScriptor, self-test
source .venv/bin/activate  # in every new terminal
```

After an update, refresh the packages once: `uv pip install -e ".[audio]"`.

## Lick library and practice app

```bash
source .venv/bin/activate
licksmith serve                 # opens http://127.0.0.1:8765 in your browser; Ctrl-C to stop
licksmith serve --lan           # same, and every computer on your local network can use it too
```

**Using it from other computers.** `--lan` listens on all network interfaces and prints the addresses to type into
another computer's browser, for example `http://192.168.1.20:8765` or `http://my-mac.local:8765`. The first time, macOS
asks whether Python may accept incoming connections: choose Allow. Everyone shares one library (same licks, practice
history and routine). There is no login, so only do this on a network you trust.

**Writing lick sheets.** One MusicXML file can hold many licks. Start each lick with a staff
text such as `Lick 43, Blues, Major II V, taken from Chet Baker's 1956 Tenderly` (number, your
tags, and the source are read from it), or just separate licks with double barlines. Put chord
symbols on every lick. Write for B♭ trumpet as usual; concert-pitch files work too. Importing a
lick with an existing number updates that lick and keeps its practice history, so you can fix a
lick in MuseScore and import the sheet again. To take licks from a solo, choose "Bars from a
solo" and type bar ranges like `17-20, 33-36`.

**Pages.**

- *Routine* (landing page): your daily practice checklist. The checklist lives only on the Routine page, with no shortcut buttons; the Licks and Play tabs are one click away in the nav.
- *Licks* (was Today): reviews that are due, then new licks (2 a day by default, favorites first).
- *Practice*: the lick in each key, in your key order. Play runs a count-in, the lick, then
  the same bars with only the band so you play it back; it moves to the next key after N
  times. Space = play/stop, T = mark the key as hard, 1–4 = rate Again / Hard / Good / Easy.
  The rating sets the next review date.
- *Practice sound*: a lick saved from Transcribe has a recording, and the Practice page plays it by default; the Sound menu switches to the MIDI band (licks without a recording always use MIDI). The note being heard lights up in red in both modes. The recording is shifted to each key and sped up or slowed to the tempo slider on the fly (ffmpeg, cached in `library/licks/audio/cache`), with the band still playing the backing.
- *Library*: click a column heading (#, Name, Bars, Function, Key, Tags, State, Next) to sort by it, click again to reverse; search and filter by function, tag or state; open a lick to see any key, edit
  tags, or download the 12-key sheet.
- *Import*: lick sheets and solos (MusicXML), and iReal playlists (Share › HTML, or paste the
  `irealb://` link).
- *Play* (formerly Tunes): the chart with its ii–V–I's and turnarounds marked. "Fit my licks" places licks
  where their chords match beat for beat, transposed and in range. Click any bar to choose
  another lick, play it with the band, and save a practice MusicXML with labels like
  "Chet Baker Tenderly (F)".
- *Transcribe → Tap bar lines*: play the recording and press Enter on beat 1 of each bar to set the bar lines (Backspace undoes a tap, Space pauses/resumes, Esc finishes; later lines move along).
- *Transcribe → Saved transcriptions on this computer*: lists every folder in `library/works` with its size, last change, whether you edited bars, and a warning when two folders come from the same YouTube video. Each has Open and Delete (with a confirmation). The bar editor also has a Delete button next to the song menu.
- *Learn* (learn by ear): the Transcribe tab's bar cards for a song you transcribed (select any run of adjacent bars by clicking the card headers, chorus and bar number, waveform / square-wave play buttons, score with chords, note editing with all its keys, same colours, Space = start/stop), with a loop controller on top: the ticked bars play as the recording or as MIDI, a draggable tempo slider (shown in BPM; the recording slows without changing pitch, and the clicks and your turn follow the same steady tempo), then an optional silent "your turn" with a soft click. Clear selection button, or click the empty space beside the cards, unticks every bar. No transcription model, chord import, tempo/key/bars, transcribe-again or waveform.
- *Progress*: licks known / learning / lapsed, practice per day, upcoming reviews and the keys you
  marked hard.
- *Settings*: B♭ or concert display, written range (default F♯3–C6), key order (default
  "my order": original, up a 4th, up a 5th, down a whole step, then the cycle of 4ths), new licks
  per day, target recall, tempo, swing and count-in.

**Sound.** Playback uses real instrument samples: the app renders each pass (count-in, lick,
piano, bass, drums) with FluidSynth and a General MIDI SoundFont, then the browser plays it.
It needs `brew install fluid-synth` (setup_mac.sh does this) and finds a SoundFont by itself:
MuseScore's, or any `.sf2`/`.sf3` you put in `library/soundfonts/`. Settings › Sound shows which
file is in use and lets you pick another. Without FluidSynth it falls back to a simple synth.

Without the browser: `licksmith licks import sheet.musicxml` and `licksmith licks list`.

**Where things live** (all in `library/`): `lick4myhorn.sqlite` (licks, tags, practice log and
schedule), `licks/L0043.musicxml` (one file per lick), `sources/` (copies of what you imported),
`practice/` (12-key sheets and tune practice sheets), `charts/charts.json` (iReal charts).
Back up the `library` folder and you have everything.

MuScriptor needs a free Hugging Face account. Accept the licence on the model page once
(medium, plus large if you want it), then log in when the script asks. The weights are for
non-commercial use only, which is fine for personal practice.

## Transcription (parked): Blue Train benchmark

```bash
# 1. Download the recording (pick the original album track, about 10:43 long)
licksmith fetch blue-train_lee-morgan "https://www.youtube.com/watch?v=..."

# 2. Map the form: open library/works/blue-train_lee-morgan/form.json and set
#    grid.first_downbeat = time in seconds of bar 1 of the head; check the chorus counts.
licksmith gridcheck blue-train_lee-morgan
#    Listen to grid_check.wav. There is a click on every beat, a higher click on each downbeat,
#    and a bell where each chorus starts. bars.csv lists every bar's time. Where clicks drift,
#    add anchors: "anchors": [[bar, seconds], ...], then run gridcheck again.

# 3. Transcribe (the whole track, once; the model runs on the Mac GPU)
licksmith transcribe blue-train_lee-morgan --model medium

# 4. Build the B-flat score of the trumpet solo
licksmith build blue-train_lee-morgan            # -> engines/muscriptor-medium/solo.musicxml

# 5. Compare with the book
licksmith bench blue-train_lee-morgan --book benchmarks/blue-train_book.musicxml
```

### Making the book reference (once per solo)

`benchmarks/blue-train_book_pages47-48.pdf` holds the two book pages. Turn them into
`benchmarks/blue-train_book.musicxml`:

1. Run the PDF through music OCR: Audiveris (free, open source, has a macOS build), or
   MuseScore's online PDF import.
2. Open the result in MuseScore and fix it against the printed page. The book's
   handwriting-style font trips OCR, so check every bar.
3. Keep it exactly as printed: B♭ written pitch, bar 1 = the first bar of the solo, 61 bars.
   Export as MusicXML.

`bench` writes a per-chorus table (match, pitch, octave, rhythm, missing, extra, **fixes per
chorus**) and a CSV listing every disagreement by bar and beat. Your bar: 1–2 fixes per chorus
is good, more than 10 rejects the engine. The book is a benchmark, not absolute truth.

Useful knobs:

- `--simplicity 2` on `build` leans harder toward simple, book-like rhythms.
- `--model large` on `transcribe` is slower but more accurate.
- `--concert` on `build` writes concert pitch instead of B♭.
- `licksmith transcribe <work> --engine midi --midi other.mid --name other-tool` imports a MIDI file
  from any other tool, so its output can be benchmarked the same way.

## Transcribe (a link or a recording in, bar-aligned licks out)

`licksmith serve`, then the **Transcribe** tab (it was called Align).

- **New transcription**: paste a YouTube or YouTube Music link, or choose an audio file. Pick the lead instrument (trumpet, alto or
  tenor sax, trombone, flute), the Muscriptor model (large is the most accurate and the slowest; the licence of each model has to be
  accepted once on Hugging Face), and tick who else plays on the recording. Telling Muscriptor the real band matters: notes of an
  instrument that is not listed get filed under another one (a flute listed as "organ", a trumpet line landing on the bass). A bass
  is always included, because its notes set the bar lines. It runs in the background (the list shows progress), whole recording,
  and the result is a work under `library/works/<title>_<artist>/` with `audio.wav`, `engines/muscriptor-<model>/` and a `form.json`.
- **Progress**: each job lists its steps (download or convert, find tempo and bars, start Muscriptor, transcribe every chunk, build the notes),
  says where it is in the current one (for example "Chunk 37 of 91"), and shows the percentage for the whole job with the time
  running and the time left. The chunk count is real (`lick4myhorn/mus_runner.py` lets Muscriptor's own progress events through);
  the first chunk is slow while the model warms up. Don't stop `licksmith serve` while a job runs: it stops the job.
- **Tempo, key & bars**: the first tempo, downbeat, key and number of bars are only a guess from the audio. Fix them in this panel
  (Apply puts the bar lines on an even grid again), then drag the bar lines onto the bass downbeats.
- **Transcribe again** runs another model or band for the same recording; each run is kept and chosen in the Transcription menu.
- **Chords from iReal Pro** (manual): a new recording starts without chords. Open this panel, look up the tune among your imported charts,
  set the key to play in and where the form starts (put the cursor in that bar and press "Set Bar 1 to Cursor"; this is how you skip an intro, and that bar then starts the left-hand column), and check the preview. Nothing is
  written until you press *Use these chords*. The chart then repeats for the length of the recording. The panel also compares the bass
  root of every bar with the chart's chord root in all 12 keys and every starting bar, and shows the match and the bars that differ. It
  fills in the key and start bar only when one reading clearly wins; otherwise it says so and leaves them for you. Needs bar lines that
  sit on the bass downbeats and a decent bass transcription.
- The older `licksmith fetch` / `licksmith transcribe` commands still work and their works show up here too.

- **Timeline**: waveform, horn notes (gold), bass notes (blue) and draggable bar lines. Drag each line onto the bass
  downbeat (Shift-drag moves every later line too; "Snap lines to bass" does a first pass). To move *all* the lines together, use ◀ ▶
  (step: 10 ms to 1 beat; Alt ← → does the same, add Shift for five steps), or drag one line onto beat 1 and press
  "Set beat one": every later line moves by the same amount and the earlier ones stay. Each move re-quantizes the
  notes to your bar times, which fixes the drift of a constant-tempo grid. Saved in `works/<w>/align.json`.
- **Playback**: Play runs the real recording with a cursor on the timeline and the current bar/note highlighted.
  Click the timeline to seek, Space = play/stop, the ▶ on a bar plays from there. Clicking a note plays just that note (synth).
- **Editing**: drag a note up/down (Shift = semitones), select it and use ↑ ↓, or edit the bar's ABC text. Edited bars are
  **locked** (shaded on the timeline) and keep your version when bar lines move; "auto" unlocks a bar.
- **Make a lick**: click a bar number (one bar is enough; Shift-click the last bar to take 2 or 3 in a row), add tags and press *Send to library*. The
  lick (your edited ABC, with its chords) lands in the library together with that piece of the recording
  (`library/licks/audio/L0043.mp3`), which plays in the lick's window as "Original recording".
- **Export MusicXML** writes `align_solo.musicxml` (B♭ trumpet). Use Import > "Bars from a solo" to turn bars into licks.

## iReal Pro charts

```bash
licksmith charts import ~/Downloads/jazz1400.html   # the forum playlist page (or a text file with irealb:// links)
licksmith charts show "Blue Train"
```

When a work's `form.json` has `"tune"` and no `"chorus"` list, it takes the chords from the
imported charts.

## Layout

```
lick4myhorn/        code: licks, analysis, db, srs, fit, library, server, static/ (web app);
                    cli, fetch, engines, grid, quantize, xmlout, bench, ireal, form (transcription)
library/            lick4myhorn.sqlite, licks/, sources/, practice/
library/charts/     charts.json (imported iReal playlists)
library/works/<w>/  form.json, audio.wav, bars.csv, grid_check.wav, engines/<engine>/{notes.json, solo.musicxml, solo.mid}, bench/
benchmarks/         published solos entered as MusicXML (B♭, bar 1 = first solo bar)
```

Self-test without audio: `python -m lick4myhorn.selftest`.
