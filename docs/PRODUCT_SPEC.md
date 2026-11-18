# Licksmith: Product Specification

Version 1.0, written from the working implementation (November 2026).
Purpose of this document: give another engineer or AI coding agent everything needed to rebuild the application from scratch, with the same behaviour, data formats and screens. Where a number, default, name or rule matters it is stated exactly. Where the original implementation made a judgement call, the reason is given so a rebuild can keep the intent.

---

## 1. Product summary

Licksmith is a **personal, local, single-user web app** for a B♭ trumpet player who is learning jazz vocabulary ("licks"). It runs on the player's own Mac, is started from a terminal (`licksmith serve`), and is used in a browser at `http://127.0.0.1:8765`. There are no accounts, no cloud, no telemetry. Everything lives in one folder (`library/`) in open formats (MusicXML, MIDI, JSON, CSV, SQLite) so the player can back it up by copying the folder.

It does five jobs:

1. **Library**: store short phrases (1–4 bars, "licks") with chords, tags and an optional recording of the original performance.
2. **Practice**: drill each lick in all 12 keys, within the player's playable range, over a synthesized rhythm section, at a tempo the player controls, with a count-in and a "your turn" echo section.
3. **Scheduling**: schedule reviews with the FSRS forgetting-curve model (as Anki does), so the right licks come up each day.
4. **Tunes**: import iReal Pro chord charts (about 1,400 in the author's collection) and **fit** licks into a tune wherever the chords match, producing a practice MusicXML.
5. **Transcribe**: turn a YouTube link or audio file of a solo into bar-aligned notation (audio → notes with an AI model, then a human-in-the-loop editor to line the bar lines up and fix notes), and send selected bars into the library as licks, together with the matching piece of the recording.

The design priority is **minimising human effort and keeping the human in control**: guesses are made automatically (key, tempo, chart position, names), but anything uncertain is shown, never silently applied, and every guess can be overridden.

### 1.1 The user

- One person (the "player"). A trumpet player who reads written B♭ pitch. Everything the player sees defaults to **written B♭ pitch** (a lick "in C" concert is displayed a major second higher). A global **Concert pitch** toggle switches the display.
- Technically able to run a terminal command and install Homebrew packages; not a developer.
- Uses MuseScore 4 to write and edit notation, so every notation output must open cleanly in MuseScore.

### 1.2 Non-goals

- No multi-user, auth, sync, mobile app or hosting.
- No audio-to-notes model training; an external model (Muscriptor) is used as a black box.
- No commercial use of third-party model weights (licence is non-commercial); recordings used for transcription are for the player's own practice and are never uploaded anywhere by the app.
- No polyphonic/piano transcription; the transcribed line is monophonic (one lead instrument).

---

## 2. Platform, stack and run model

| Item | Requirement |
|---|---|
| OS | macOS on Apple Silicon (the transcription model uses the Mac GPU). The lick library and practice parts also run on Linux. |
| Python | 3.10–3.12 (3.12 used) |
| Backend | FastAPI + Uvicorn, one process, serves the JSON API and the static front end |
| Front end | Plain HTML/CSS/vanilla JavaScript. **No framework, no build step.** Files served from `/static/`. |
| Database | SQLite, one file, `library/lick4myhorn.sqlite` |
| Notation | `music21` (MusicXML read/write, transposition, chord symbols), `verovio` (render SVG of notation in the browser), ABC text as the editable lick format |
| Spaced repetition | `fsrs` Python package (Scheduler, Card, Rating) |
| Chart import | `pyRealParser` plus custom flattening of iReal chord strings |
| Sound | `pyfluidsynth` + FluidSynth (Homebrew `fluid-synth`) with a General MIDI SoundFont (MuseScore's, or any `.sf2/.sf3` in `library/soundfonts/`); built-in Web Audio synth as fallback |
| Audio tools | `ffmpeg` (convert, pitch shift, time stretch), `yt-dlp` (download), `librosa` + `soundfile` (beat tracking), `pretty_midi`, `numpy` |
| Transcription model | `muscriptor` ≥ 0.4.0 (Kyutai × Mirelo; code MIT, weights CC BY-NC 4.0; weights require a free Hugging Face login and accepting the licence per model size) |
| Vision (optional) | DeepSeek or Anthropic vision API to read a screenshot of sheet music into ABC (Section 10) |
| CLI | `licksmith` console script (`lick4myhorn.cli:main`; `l4mh` kept as an alias) |

**Run model.** `licksmith serve` starts Uvicorn on `127.0.0.1:8765`, opens the browser, and stays in the foreground until Ctrl-C. `licksmith serve --lan` (= `--host 0.0.0.0`) listens on every interface so other computers on the local network can use the app; it prints the LAN addresses and a no-login warning. The project root is `<project>/`, the data root `<project>/library/`. Long jobs (transcription) run in a background thread inside the server process; **stopping the server stops the job** and the UI warns about this.

**Setup script** (`setup_mac.sh`): installs `ffmpeg`, `uv`, `fluid-synth` via Homebrew; creates a Python 3.12 venv with `uv`; installs the package with the `audio` extras; checks for MuseScore 4; walks the user through the Hugging Face login; runs the offline self-test.

**Design rule for process safety:** all subprocesses (ffmpeg, yt-dlp, muscriptor) are launched with the venv's `bin/` on `PATH`; the server must be started from an activated venv so `muscriptor` and `yt-dlp` are found.

---

## 3. Repository and folder layout

```
lick4myhorn/               Python package
  server.py                FastAPI app, all HTTP routes
  library.py               service layer tying engine, DB, SRS, fit together
  db.py                    SQLite schema, settings defaults
  licks.py                 import/parse MusicXML, store, transpose to 12 keys, render SVG
  analysis.py              auto-tagging (harmonic function, melodic devices)
  srs.py                   FSRS scheduling
  fit.py                   fit licks into a tune
  similar.py               "same line in another key" detection
  render.py                FluidSynth practice-cycle renderer + comping/bass/drums generators
  chords.py                chord-symbol conversion/transposition
  ireal.py                 iReal Pro playlist import and flattening
  capture.py               screenshot → ABC → lick (vision models)
  align.py                 the Transcribe/Align editor engine (AlignWork)
  transcribe.py            background transcription jobs
  engines.py               Muscriptor / MIDI-import engines
  mus_runner.py            wrapper that surfaces Muscriptor progress
  fetch.py                 yt-dlp download
  grid.py, gridcheck.py    beat grid, click-track check
  form.py                  form.json model
  quantize.py              seconds → written rhythm
  xmlout.py                quantized notes → MusicXML
  bench.py, omrfix.py      benchmark vs a published solo; OMR cleanup
  selftest.py, cli.py
  static/  index.html, style.css, audio.js, app.js, align.js, transcribe.js, lickedit.js
library/                   user data (NOT in version control, see §17)
  lick4myhorn.sqlite
  licks/L0043.musicxml     one B♭ MusicXML file per lick
  licks/audio/L0043.mp3    optional recording of the lick
  licks/audio/cache/       pitch/tempo-shifted clips (generated)
  sources/                 copies of imported files
  practice/                12-key sheets, tune practice sheets
  charts/charts.json       imported iReal charts
  soundfonts/              optional .sf2/.sf3
  works/<title>_<artist>/  one folder per transcribed recording (§9)
benchmarks/                published solos entered as MusicXML for benchmarking
```

All paths are resolved relative to the project root so the whole folder can move.

---

## 4. Core concepts and data model

### 4.1 Pitch conventions

- **Internal storage and all analysis are in concert pitch.**
- **Files on disk** (`licks/L0043.musicxml`) are written for B♭ trumpet, with a MusicXML `<transpose>` element (chromatic −2, diatonic −1) so MuseScore/Dorico/Finale/Sibelius can toggle written/concert.
- The UI shows **written B♭** by default (concert tonic + a major 2nd for display). The `display` setting (`written`|`concert`) and the header toggle control this. Chord symbols are transposed for display the same way.
- Chord symbols are stored as **plain text in concert pitch**: `Eb7`, `Fm7`, `Bbmaj7`, `Cm7b5`, `Ddim7`, `G7b9`, `C7/E`. Display form uses ♭/♯ glyphs (`pretty`).

### 4.2 Lick

A lick is 1–4 bars (the library accepts any short fragment; the transcribe page sends 1–3 bars). Fields (SQLite `licks` table):

| Column | Meaning |
|---|---|
| `id` | internal integer key |
| `number` | unique lick number shown as `#43`; importing the same number **updates** that lick and keeps practice history |
| `name` | optional display name typed by the user |
| `function` | primary harmonic function, auto-detected (e.g. "Major II V", "Blues", "Minor II V", "Turnaround", "Dominant", "One chord") and editable |
| `tonic_pc`, `tonic_name`, `mode` | the lick's home key in concert pitch (`major`/`minor`) |
| `artist`, `tune`, `year` | source musician, song, year |
| `comment`, `notes` | source note, free notes |
| `source`, `source_bars` | where it came from (file, bar range like `17-20`, or `5` for one bar) |
| `file` | the lick's MusicXML file name |
| `tags` | JSON list, user tags (editable) |
| `auto_tags` | JSON list, detected devices (e.g. "Enclosure", "Triplets", "Pentatonic", "Blue note", "Altered tensions", "Chromatic approach", "One chord", "Arpeggio", "1-2-3-5 pattern") |
| `data` | JSON: concert-pitch notes `[offset_quarters, duration, midi]`, chords `[offset, symbol]`, graces, time signature, length, key info |
| `bars`, `low`, `high` | length in bars, lowest/highest concert MIDI |
| `favorite`, `archived` | flags |
| `created`, `updated` | ISO timestamps |

**Lick label rule** (used everywhere a lick is named): the name the user typed; else `"<artist> <tune>"` (e.g. "Chet Baker Autumn Leaves"); else a generic fallback. Number, bars, function and key are shown as separate columns, not packed into the name. Everything else is a tag.

### 4.3 Practice scheduling tables

- `cards(lick_id PK, fsrs JSON, due, reps, lapses, stability, last_review, last_rating, introduced)`: one FSRS card per lick.
- `reviews(id, lick_id, ts, rating, keys_done JSON, trouble_keys JSON, tempo, mode, note, due_after, stability)`: log of every rating.
- `key_tempo(lick_id, pc, tempo, best, ts)`: remembered tempo per lick per key (the tempo the player last used and best used).
- `tunes(title PK, composer, style, key, ts, bars JSON, playlist, learning, added)`: imported iReal tunes; `learning` flags tunes the player is working on.
- `plans(id, tune, created, transpose, placements JSON, file)`: saved tune-fit plans.
- `settings(k, v JSON)`: key/value settings (§14).

### 4.4 Work (transcribed recording)

A **work** is a folder `library/works/<slug>/` holding one recording and everything derived from it (§9). Slug = `<title>_<artist>` lower-case, hyphenated; duplicates get `-2`, `-3` (e.g. `september-in-the-rain_roy-hargrove-2`).

---

## 5. Application shell and navigation

Single page. Header: brand "Lick**4**MyHorn" (the 4 in the accent colour), back/forward arrow buttons (own history stack), tabs, spacer, **Concert pitch** checkbox.

Tabs, in order: **Routine, Learn, Licks, Play, Progress, Library, Import, Transcribe, Settings**. Routine is the landing page. Hash routing (`#routine`, `#licks`, `#library`, `#transcribe`, …); the old `#today` hash opens Licks. The Routine sections "Learn licks" and "Learn tunes" have an "Open Licks/Tunes" button; the Play tab (hash `#play`; formerly Tunes, the old `#tunes` hash still redirects) and Licks have no checklist; the checklist lives only on the Routine page. Opening a lick or a tune pushes a history entry so the back arrow returns to the previous view.

Visual style: dark theme, brass/gold accent (`#d9a441`-like), red `--again` for errors, panels with 1px lines and 8px radius, monospace for chord text. Layout is responsive: 4-column bar grid collapses to 2 columns below 900 px.

Toasts: a small transient message (default 2.6 s) for confirmations.

A generic `api(path, opts)` helper: JSON in/out; non-2xx responses throw an Error carrying the server's `detail` message, which the UI shows inline.

---

### 5.1 Learn tab (learn by ear)

`static/ear.js` (`VIEWS.learn`, second tab after Routine) is a loop controller around the real Transcribe cards: it calls `alignOpen(work, works, {learn: ctl})` in `static/align.js`. In that mode align.js renders the same bar cards (checkbox pick of any number of adjacent bars, Chorus #/Bar #, a waveform button (recording) and a square-wave button (MIDI) per card, labelled "Chorus 1 · Bar 3", score with chords, note selection/editing with every shortcut, undo/redo, locked bars, same colours: picked = red, playing bar = yellow) and hides everything marked `.al-tl-only`: transcription model select, import chords, tempo/key/bars, transcribe again, waveform timeline, export, zoom/snap/nudge. The song menu stays. Space and the card buttons are routed to the controller (`ctl.toggle`, `ctl.playSpan`).

Layout of the top box: row 1 = song menu, Sound, Listen ×, Your turn, Click, Count-in; row 2 = Play, tempo slider, Clear selection, Undo, Redo (gold, labelled). Controller: Sound (recording or MIDI), Tempo slider in BPM (30–120% of the song's average tempo from its bar lines; `audio.playbackRate`, pitch preserved; MIDI note times scaled by `A.rate`), Listen 1-4 times, optional **Your turn** (silence the length of the bars at the slider tempo, steady soft clicks on every beat when Click is on, independent of the recording's uneven bar lines) and optional one-bar count-in right before each turn right before each of your turns. It loops until stopped; changing bars or options takes effect on the next round. Pressing waveform / square-wave play buttons on a card loops that card's span. A **Clear selection** button, or a click on the blank space beside or between the cards (outside the cards), unticks every bar (`A.clearPick`). The ticked bars, song and options are remembered in the browser (`earSetup`).

### 5.2 Managing saved transcriptions

`GET /api/works/disk` lists every folder under `library/works` (title, artist, source URL, YouTube video id, audio present, transcription engines, whether `align.json` exists, size in MB, last change, and `same_video_as` for folders imported from the same video). `DELETE /api/align/{work}` removes the folder (name checked to be a direct child of `library/works`; refused with 409 while a transcription job for it runs) and drops it from the server cache. Licks already sent to the library are separate files and stay. UI: Transcribe tab, "Saved transcriptions on this computer" (Open / Delete with confirm), and a Delete button beside the song menu in the bar editor.

## 6. Licks page (formerly Today)

Shows today's session: first **reviews that are due** (most overdue first, capped by `max_reviews`, default 20), then **new licks** (`new_per_day`, default 2; favourites first).

Top: three stat cards, "due for review", "new today", "practised today"; a primary **Start session (N)** button that opens practice on the first item and moves through the list.

Each row: ▶ listen button, `#number`, name/label, "from the key of X", tag chips, a state badge (`new`, `learning`, `lapsed`, `reviewing`, `known`) and a **Practice** button.

State definitions (from the card): `new` (no reps), `lapsed` (last rating = Again), otherwise by FSRS stability: `known` ≥ 30 days, `reviewing` ≥ 7, else `learning`.

---

## 7. Library page

- A table of licks, sortable by clicking any column heading (#, Name, Bars, Function, Key, Tags, State, Next); clicking again reverses.
- Search box plus filters: function, tag, state.
- ▶ on a row plays the lick (swung, see §13.4).
- Clicking a row opens the **lick window**:
  - rendered notation (Verovio SVG), written or concert per toggle, with chord symbols;
  - the **key chooser** (all 12 keys in the player's key order, each showing the transposed, octave-fitted notation);
  - editable fields: name, tags (chips), function, artist, tune, year, comment, notes, favourite, archive;
  - **Original recording** audio player when a recording exists (plays the clip; the note being heard lights up);
  - download the **12-key sheet** (MusicXML generated on request, nothing stored);
  - **Edit notes** in place (§11.5): the same note editor as the Transcribe tab operating on the lick's ABC text; edits stay in the page until "Save notes to this lick" (keeps number, tags, notes, history);
  - "Related licks": same line in another key (`same`), contained inside another (`inside`), or mostly equal (`similar`);
  - delete lick; "Re-analyse" recomputes tonic, mode, function and auto-tags.
- A **repeats** view lists every pair of licks in the library that are the same line or nearly.

### 7.1 Lick similarity

A lick is reduced to a melodic shape: the interval from each note to the next plus time between starts; shapes ignore key and octave. Relations: `same` (identical shape), `inside` (one shape appears inside the other), `similar` (most intervals/rhythms agree).

---

## 8. Practice page

### 8.1 Practising a lick

Opened from Today/Library. Shows the lick in the current key and a strip of the 12 keys.

- **Key order** (`key_order` setting): `mine` = original key, up a 4th, up a 5th, down a whole step, then the remaining keys by the cycle of 4ths; or plain cycle of 4ths/5ths, chromatic. `my_steps` default `[0, 5, 7, -2]` semitones.
- **Range**: each key's lick is shifted by whole octaves into the player's written range (`range_low` 54 = F♯3, `range_high` 84 = C6). If it cannot fit, that key is flagged.
- **Cycle** (one "Play" pass): count-in (`lead_in` bars; side stick on every beat, accent on beat 1; default 1 bar, 0 allowed) → the lick → "your turn" (the same number of bars with only the band, when `call_response` is on) → repeat `reps_per_key` times (default 2) → move to the next key (unless `stay_in_key` keeps looping until the player moves on).
- **Backing band** (`backing`, `backing_style`: `band` = piano + bass + drums, or `piano` = chords only): generated per chord symbol of the lick (§13).
- **Tempo**: slider (quarter-note BPM), remembered per lick per key in `key_tempo` (`tempo`, `best`). Buttons nudge tempo by `tempo_step` (default 10). First tempo in a never-practised key is `start_tempo` (default 60).
- **Swing**: slider 50–72 % starting at the Settings value (default 62 %); changing it here affects only this session.
- **Hear lick** toggle (`play_lick`): off = band only.
- **Recording mode**: if the lick has a recording, Practice plays **that recording** shifted to the key and time-stretched to the tempo (ffmpeg, cached under `licks/audio/cache`), with the band still playing; the Sound menu switches to MIDI. Licks without a recording always use MIDI. The note being heard lights up in red in both modes (for the recording, using bar-position marks saved with the clip; for MIDI, from the clock).
- Keyboard: **Space** play/stop; **T** mark the current key as "hard" (trouble key); **1–4** rate Again/Hard/Good/Easy.
- **Remembered setup per lick.** Stay in this key, times per key, start key, key order, hear lick, sound, your turn, backing and swing are saved per lick (`lick_practice` table) every time one is changed, and used the next time that lick is opened. A lick with no setup of its own starts from the latest one used anywhere (`practice_last` setting; the start key and sound are not shared).
- **Hard keys.** "Mark key hard" (T) is saved per lick and key (`key_hard` table). Next time: hard keys come first in the key order and get twice the repetitions. A hard key that was played through and rated Good or Easy without being marked again that session is unmarked. The Licks tab has "Practice hard keys", a session of only those licks and keys. Progress shows, per key, in how many licks it is hard, and says so when it is three or more (a technique gap, not one lick). `GET /api/hard`, `POST /api/licks/{id}/hard`, `POST /api/licks/{id}/practice-cfg`.
- **Rating** ends the lick: the FSRS rating schedules the next review; the buttons show the interval each rating would give (`preview_intervals`). A `reviews` row logs rating, keys done, trouble keys, tempo.

### 8.2 FSRS parameters

`Scheduler(desired_retention=<setting, default 0.9>, learning_steps=(), relearning_steps=(), maximum_interval=365, enable_fuzzing=True)`. No minute-level steps: the shortest interval is one day, because practice is a once-a-day activity. "Due today" means due before the end of the local day.

Today's session = due cards sorted most-overdue first (limited), then new licks (favourites first) up to `new_per_day`, minus those already introduced today.

---

## 9. The work folder and form.json

```
works/<slug>/
  source.json          {url, title, uploader, ...} where the audio came from
  download.webm        the raw download (kept)
  audio.wav            mono/stereo wav used for everything
  form.json            the form map (below)
  bars.csv             bar → time
  grid_check.wav       click track over the audio (for the CLI check)
  align.json           editor state: engine, bar_times, locked bars, undo/redo stacks, notes edits
  align_peaks.json     cached waveform peaks
  engines/<engine>/    raw.mid, notes.json (horn), bass.json, engine.json {window,...}, solo.musicxml, solo.mid
```

### 9.1 form.json

```json
{
  "title": "Autumn Leaves", "artist": "Chet Baker", "tune": "Autumn Leaves",
  "album": "…(CTI, 1974)",
  "source": {"url": "...", "audio": "audio.wav"},
  "key": "Cm", "key_signature": "auto",
  "beats_per_bar": 4, "bpm": 184.6, "feel": "swing",
  "instrument": "trumpet",
  "ensemble": ["trumpet", "acoustic_piano", "acoustic_bass", "drums"],
  "grid": {"first_downbeat": 9.31, "anchors": [[bar, seconds]], "track_beats": false},
  "chorus": [["Cm7"], ["F7"], ...],           // concert chords, one list per bar
  "sections": [
    {"label": "head", "choruses": 2},
    {"label": "trumpet solo", "choruses": 2, "transcribe": true},
    {"label": "tag", "bars": 4, "repeat_last": 4},
    {"label": "vamp", "bars": 8, "vamp": ["Eb7"]}
  ],
  "free_form": true,                          // set for recordings from the Transcribe page
  "chorus_source": "iReal Pro: <title> (shifted N semitones, starting at chart bar B)",
  "chart_offset": 0,                          // rotation applied to the stored chorus
  "chorus_before_ireal": [...]                // backup of previous chords
}
```

- **Bar numbering is global and 1-based**; bar 1 starts at `grid.first_downbeat` seconds. Audio before it is ignored.
- `expand_sections` yields `Bar{number, section, chorus, bar_in_chorus, chords, transcribe, feel}` for every bar. Empty bars inherit the previous bar's last chord. `transcribed_bars()` returns the ones with `transcribe: true`.
- A section may have `"loop_chorus": true` (free-form recordings): it takes `bars: N`, and bar `i` of the section gets `chorus[i % len(chorus)]`, `bar_in_chorus = i % len + 1`, `chorus = i // len + 1`. This is how a chart repeats over a recording of unknown length.
- A work with `"tune"` and no `"chorus"` takes its chords from the imported iReal charts.
- **Free-form works** (made by the Transcribe page): `free_form: true`, `chorus: [["N.C."]]`, and one transcribe section `{bars: N, chords: [["N.C."]]}`. In this state the bar count is editable. After a chart is applied (`chorus_source` set) the work is no longer "free" for chart-detection purposes, but the bar count stays editable (`bars_editable` follows `free_form`).

---

## 10. Import page

Four inputs, each with a preview step then a save step (`/api/import/preview` → `/api/import/save`):

1. **Lick sheets (MusicXML)**: one file may hold many licks. Splitting rules in `auto` mode, in priority order: staff text `Lick 43, Blues, Major II V, taken from Chet Baker's 1956 Tenderly` (number, tags, source parsed by `parse_comment`); rehearsal marks; double barlines; cutting along known progressions (ii–V–I, minor ii–V–i, turnaround) by harmony. Modes: `auto`, `sheet` (never the whole file as one lick), `single` (whole file or bar range = one lick), `range`. Concert-pitch files are accepted; B♭ files read via `<transpose>`.
2. **Bars from a solo**: type bar ranges `17-20, 33-36`; one lick per range; an edge tie that crosses the cut is repaired.
3. **iReal playlist**: upload the forum HTML ("Share › HTML") or paste an `irealb://…` link → parsed into `charts.json` and the `tunes` table.
4. **Screenshot → lick** (optional): paste/choose an image of sheet music; a vision model (DeepSeek or Anthropic, HTTPS, key stored in a local secrets file never committed) is asked to answer in ABC. Settings used: key signature (sharps −7..7), time signature, basis (trumpet/alto/tenor/concert), chord basis. Option `tiles`: `bars` (one picture per bar, split on barlines, better for weaker models) or `whole`. The ABC is parsed to a score; **sanity checks flag suspicious bars** (wrong beat count etc.) for review before saving. ABC dialect: `L:1/8`, triplets `(3abc`, ties with `-`, rests `z`, chord quotes, grace `{g}`.

Preview shows each detected lick (notation, tags, function, number), with editable number/tags/name, and any warnings. Saving writes the MusicXML to `licks/`, a copy of the source to `sources/`, runs `analyse`, creates the card.

---

## 11. Tunes page (iReal charts and fitting)

### 11.1 iReal import (`ireal.py`)

`irealb://` strings are parsed with `pyRealParser`; `flatten` expands repeats and endings into a flat list of bars of plain chord symbols (e.g. `F-7`→`Fm7`, `Bb^7`→`Bbmaj7`, `Eh7`→`Em7b5`). Several chords per bar are positioned by the iReal convention (`bar_chord_offsets`). An empty bar continues the previous chord. Charts are cached in `library/charts/charts.json` (title, composer, style, key, tempo, bars). Title lookup is case-insensitive, closest match.

### 11.2 Tune view

Shows the chart in a grid, with **ii–V–I's and turnarounds marked** (`tune_sections`: function spans with bar numbers, harmonic analysis of the chart). A "learning" flag per tune.

### 11.3 Fit my licks

`fits_at(lick, tune, start_beat)`: a lick fits at a bar when, after transposing by some semitone shift 0–11, **every chord in the lick matches the tune's chord over the same beats** (same root, same chord family: major / minor / dominant / half-diminished / diminished), up to the beat where the lick's last note ends. `candidates` returns, per bar index, candidates sorted best first (favourites and licks being learned first; `pool` = favourites and/or today's exercise). `plan_tune` walks the tune and places a lick wherever one fits.

UI: the chart with a small notation snippet over each placed bar; click any bar for a menu to choose another candidate or clear it; **Play** the tune with the band (swung); **Save practice MusicXML** builds a whole-tune concert-pitch score with the chosen licks transposed and octave-fitted, other bars as whole-bar rests, labelled like "Chet Baker Tenderly (F)". Plans are stored in `plans`.

---

## 12. Progress and Settings pages

**Progress**: counts of known / learning / lapsed licks, practice per day (bar chart), upcoming reviews by day, keys the player marked hard.

**Settings**: see §14 for the complete list. Sound section shows the SoundFont in use and lets the player choose another; shows whether FluidSynth is available.

---

## 13. Sound and playback

### 13.1 Two engines

- **SoundFont engine (default)**: server renders each practice pass to audio with FluidSynth (`POST /api/render`), with channels lead=0, piano=1, bass=2, drums=9; programs from GM (trumpet or muted trumpet for the lead, per `lead_sound`); volumes lead 110 / piano 80 / bass 105 / drums 85; reverb send ~30 (bass 10). The browser plays the finished file, so timing is sample-accurate.
- **Built-in synth (fallback)**: `audio.js` Web Audio engine (`Engine`: lead synth, comp, bass, ride, drums; compressor; lead/back buses so Stop silences everything scheduled). Used if FluidSynth or a SoundFont is missing, or `sound_engine = synth`.

### 13.2 Band generation (both engines implement the same musical rules)

- **Comping**: rootless left-hand voicings, forms A and B alternating; shell intervals by quality: maj [4,11,14], min [3,10,14], dom [4,10,14], hdim [3,6,10], dim [3,6,9], aug [4,8,10]; altered dominant [4,10,15]; voiced in MIDI 53–65. Hits on beats and anticipations (a hit at the chord start, a second at +1.5 beats if room), light random velocity variation seeded per cycle.
- **Walking bass**: root on the chord change, chord/scale tones, chromatic approach into the next root; velocity 96 on the downbeat, 84 otherwise.
- **Drums**: ride on every beat, swung skip note after beats 2 and 4 (skip at +swing), hi-hat foot on 2 and 4, feathered kick (velocity ~22). Count-in uses side stick, never swung.
- **Chord symbol parsing** supports quality classes `maj, min, dom, hdim, dim, aug`, slash bass, and `alt` detection.

### 13.3 Playback data

`GET /api/licks/{id}/play?pc=` returns notes (offset, duration, concert midi), graces `[offset, midi]`, chords and length for the browser; the cycle renderer receives `{seq, tempo, swing, leadIn, playLick, backing, response, seed}`.

### 13.4 Swing rule (important)

**All playback in the app swings unless the user says otherwise.** The single control is the `swing` setting: the position of the off-beat eighth within the beat, default **0.62** (0.50 = straight, 0.667 = triplet feel). `warp(q, swing)`: for a position `q` whose fractional part is 0.5, return `floor(q) + swing`; every other position is unchanged. It applies to both the start and the end of each note, to the lick, to comping hits, to the ride skip note, and to MIDI playback in the Transcribe tab. Count-in clicks, bass quarter notes and kick are not swung. Real recordings play as recorded.

---

## 14. Settings (defaults)

| Key | Default | Meaning |
|---|---|---|
| `display` | `written` | `written` (B♭) or `concert` |
| `range_low`, `range_high` | 54, 84 | written MIDI range (F♯3–C6) |
| `key_order` | `mine` | `mine`, or cycle order |
| `my_steps` | [0, 5, 7, −2] | semitone steps for "my order" |
| `new_per_day` | 2 | |
| `max_reviews` | 20 | |
| `desired_retention` | 0.9 | FSRS target |
| `tempo` | 120 | quarter-note BPM |
| `start_tempo` | 60 | first tempo in a new key |
| `tempo_step` | 10 | |
| `stay_in_key` | true | |
| `swing` | 0.62 | §13.4 |
| `lead_in` | 1 | count-in bars |
| `reps_per_key` | 2 | |
| `call_response` | true | leave equal bars empty |
| `play_lick`, `backing` | true, true | |
| `backing_style` | `band` | `band` or `piano` |
| `instrument` | `trumpet` | |
| `sound_engine` | `soundfont` | or `synth` |
| `soundfont` | "" | path, empty = auto-find |
| `lead_sound` | `trumpet` | or `muted_trumpet` |
| `vision_provider`, `vision_model`, `vision_tiles` | `deepseek`, "", `bars` | screenshot reader |
| `capture_key`, `capture_meter`, `capture_basis`, `capture_chords` | 0, `4/4`, `trumpet`, `same` | last-used capture options |

Settings are stored as JSON values in the `settings` table; `PUT /api/settings` merges. Defaults fill any missing key.

---

## 15. The Transcribe tab (the main complex feature)

Purpose: audio in → editable, bar-aligned notation out → licks into the library. (Formerly called "Align". API paths still use `/api/align/…`.)

### 15.1 Page structure

1. **Transcriptions list** and **New transcription** (collapsed panel).
2. For the selected work: title "<Title> — <Artist>", help line, then the **editor card**: work menu, engine ("Transcription") menu, A▶ / M▶ buttons, time readout, zoom ± buttons, toolbar, **timeline canvas**, hint line, "make a lick" bar.
3. **Bars grid** of notation boxes, one per bar.
4. Collapsible panels: **Chords from iReal Pro**, **Tempo, key & bars**, **Transcribe again**.

The editor card floats (sticky) at the top while scrolling the bars grid, so the timeline stays visible. A Compact/Full-size button toggles its height. The pane's height is measured so scrolled-to bars are not hidden under it (`--al-top`).

### 15.2 New transcription (`transcribe.py`)

Inputs: a **YouTube / YouTube Music link** or an **uploaded audio file**; lead instrument (trumpet, alto sax, tenor sax, trombone, flute); Muscriptor model size (small / medium / large, large is the most accurate and slowest; licence per model accepted once on Hugging Face); checkboxes for **who else plays** on the recording (the band). Telling the model the true band matters: notes of an unlisted instrument get filed under another (a flute listed as "organ", a trumpet line landing in the bass track). **A bass is always included** because bass notes set the bar lines.

Title/artist are cleaned from the video title (`clean_title`: strips "(Official Video)", "[HD]" etc.; `split_artist_title`: "Artist - Title" pattern, else the uploader minus " - Topic").

**Job pipeline** (one job at a time, background thread, `Transcriber`):

1. `download` (yt-dlp → `download.webm`) **or** `convert` (uploaded file) → `audio.wav`.
2. `prepare`: first guess of the grid: tempo, time of the first beat, number of bars (`estimate_grid`, librosa; the guess is explicitly labelled as a guess in the UI).
3. `model`: start Muscriptor (model load).
4. `transcribe`: the whole recording; audio is cut by the model into **5-second chunks**.
5. `notes`: build horn notes (`notes.json`), bass notes (`bass.json`) from the MIDI, and guess the concert key from the horn notes (`estimate_key`: duration-weighted pitch classes against Krumhansl profiles) → written to `form.json` as `key`.

**Verbose progress** (requirement): each job card shows state label, bold title, a large **percentage**, a step checklist (✓ done, ● active, ○ todo) with detail text for the active step, "Running for … · about … left", and "Took …" when finished. Step weights for the percentage: `download .08, convert .04, prepare .04, model .04, transcribe .78, notes .02`. The active `transcribe` step reads "Chunk c of N · the recording is cut into 5-second chunks, m:ss of m:ss done" with a warm-up hint while c = 0. ETA = seconds per chunk since the first observed chunk × chunks left − time already spent in the current chunk, shown after 3 chunks. Real chunk progress comes from a wrapper (`mus_runner.py`) that monkey-patches `muscriptor.transcription_model.decode_model_tokens` to print `[progress] c/t` on stderr per `ProgressEvent` and `[progress] ready` at start; if the hook fails it prints `[progress] unavailable` and the page falls back to an estimate `audio_seconds × speed(model)` learned from past logs (defaults small 0.5, medium 1.0, large 1.7 seconds of compute per second of audio). The job log is parsed for phases: no `audio:` line → model loading/reading audio; `[progress] c/t` → chunk progress; `generate total` → writing MIDI; `Saved MIDI` → building notes.

Jobs live in memory; the works they produce are on disk. Job list: `GET /api/transcribe/jobs` (polled).

### 15.3 The timeline

A canvas with: a ruler with bar numbers, the **waveform** (peaks cached in `align_peaks.json`), **horn notes** (gold) and **bass notes** (blue) as piano-roll bars, **draggable bar lines** (vertical lines at the bar boundaries `A.times[0..n]`), the playback cursor (red), locked-bar shading, picked-bar highlight. Ctrl/⌘-scroll zooms (pixels-per-second `pps`), plain scroll pans, auto-scroll follows the cursor while playing (keeps it between 15 % and 85 % of the width).

Interactions:
- Click empty timeline: seek. Click a note: audition that note (synth), select it. Space: play/stop.
- **Drag a bar line**: moves only that line. **Shift-drag**: moves it and every later line. On release the server re-quantizes.
- **◀ [step] ▶**: move **all** bar lines earlier/later by the step (10, 25 (default), 50, 100 ms, ¼ beat, 1 beat). **Alt+←/→** does the same; add Shift for 5 steps. The lines move on screen at once; the server is updated 500 ms after the last press (debounced). Clamped so no line leaves the recording.
- **Set beat one**: after dragging **one** line (non-Shift), remember the anchor `{i, dt}` (line index, shift). The button turns gold and reads "Set beat one (bar N and after)". Pressing it moves **every line after bar N** by the same `dt` (clamped to the recording end); lines before stay. Any other move (snap, nudge, a new drag, re-apply) clears the anchor. Pressing with no anchor says "Drag a bar line onto beat 1 first, then press this."; pressing on the last line says nothing comes after it.
- **Snap lines to bass**: server pass that slides each bar line (up to 0.25 s) to where the bass-note onsets around it fit best; can be run twice.
- **Undo / redo** (⌘Z / ⇧⌘Z or ↶ ↷): server-side stacks in `align.json`.

### 15.4 Quantization and notation (`quantize.py`, `xmlout.py`, `align.py`)

`quantize_notes(horn_notes, grid, form, simplicity)` turns performed seconds into written beats:

1. Convert onsets/offsets to fractional beat positions on the bar-line grid (`time_to_beat`, linear within each bar).
2. Group onsets by beat. For each beat, choose the best **grid option** by sum of squared error plus a penalty for busier grids (× `simplicity`) plus a **collision cost of 0.2 per pair of notes mapped to the same slot**.
   - Swing feel options (performed position → written position): `swing8` ([0, 2/3, 1] → [0, ½, 1], penalty 0), `straight8` (.004), `triplet` ([0,⅓,⅔,1], .006), `swung16` ([0,1/6,½,⅔,1] → [0,¼,½,¾,1], .008), `sixteenth` (.008). Straight feel: `straight8`, `triplet`, `sixteenth`.
   - One or two notes in a beat never choose a 16th grid (`sync_guard`). Three notes prefer a triplet unless 16ths fit clearly better (extra 0.012 penalty).
   - **Consistency pass**: busy beats (≥3 notes) in one bar are made to agree (all 16ths or all triplets) unless the minority beat fits that way clearly worse (tolerance 0.006 beat² per note).
   - A swung off-beat at about ⅔ of the beat is **written as a plain eighth** (that is how books notate swing).
3. Snap each onset to the chosen written position. **If two notes land on the same slot** the earlier of the two steps one slot back (¼ beat, or ⅓ in a triplet beat) if that slot is free in the same beat; otherwise the later one steps forward; only if both are taken is the quieter one dropped. (This rule exists because a fast run was losing notes: three notes within 0.15 beat collapsed into one slot.)
4. End positions snap the same way; **gaps shorter than `min_rest` (½ beat) are closed** (legato, as books write it); a zero-length note gets a minimal length (½ beat in swing, ¼ in straight).
5. Output is a monophonic line of `QNote{start, end, pitch, velocity}` inside `transcribe` bars only.

`drop_chords`: a horn plays one note at a time. Notes starting within 40 ms of each other are a chord (comping mislabelled): keep at most one that continues the melody (nearest to the last kept note within 7 semitones), drop the rest. `in_range`: keep notes in the instrument's range. **Bass-track rescue**: the model sometimes files the melody under bass; notes in the bass track above `BASS_TOP`, or where two bass-track notes overlap by more than `BASS_OVERLAP` (the upper one), are moved to the horn list before filtering.

`xmlout.build_score` writes the B♭ part: spelling like a jazz transcriber (chord tones spelled by the chord, `spell_line`), splits spans into notatable values without odd tuplets (`_pieces`), triplet brackets per contiguous run in a beat, jazz-book beaming (`rebeam`: notes shorter than a quarter beamed per beat, rests break beams), and `validate_musicxml` checks the file the way MuseScore does (every note's written type + dots + tuplet ratio sums to its duration). Swing bars get a "Swing" text expression. **MusicXML export** = `align_solo.musicxml` (B♭).

Each bar is presented to the editor as ABC (`L:1/8`, e.g. `"Bbmaj7"z2 z B/ c/ B/ A/ G (3F _G D`), generated by `score_to_abc` from the music21 score, one string per bar (fallback: one call per measure if the bar split fails). ABC is the **editable text form** of a bar; verovio renders it to SVG.

### 15.5 Bars grid and editing

A grid of **bar boxes** (4 columns; 2 on narrow screens), each with: bar number (click = pick for a lick), a small caption `ch N · bar M` (chorus number and **chart bar number**; with a stored `chart_offset` it shows the real chart position), **A▶** (play the recording for this bar, or for the whole picked lick if inside one) and **M▶** (play the transcribed notes as MIDI, swung), the rendered notation, and an ABC text field.

- Boxes are re-rendered only when their signature (`abc|locked|chords|issues`) changes.
- **Editing** (MuseScore-like): click a note to select; drag up/down to change pitch (Shift = semitones); keys: ↑↓ semitone, ⌘↑⌘↓ octave, Alt↑↓ scale step, A–G note name, `1 2 4 8 6` whole/half/quarter/eighth/sixteenth, `-` (shorter) `.` (dot) `/` (slashed grace note, again = remove), Delete = make rest (again = remove), ←→ previous/next note, ⌘3 triplet, T tie to next, `R`/A–G on a rest to turn a rest into a note; ⌘Z undo. Edits are applied by the server on the ABC (`edit_note`), which re-renders and returns SVG + the event list. Clicking a note plays it (grace notes first).
- **Locking**: any edit locks the bar (shaded on the timeline; dashed border). A locked bar keeps your version when bar lines move or the chart changes (its chord symbol is carried inside the ABC). "auto" unlocks it back to the automatic transcription.
- **Issues**: bars whose rhythm does not fill the bar or otherwise fail validation carry an `issues` list shown on the box.

### 15.6 Make a lick, send to library

- Click a bar number to pick one bar (click again to un-pick); Shift-click another to extend, up to **3 adjacent bars**. The picked range is shaded on the timeline.
- The pick row shows "Bar N" or "Bars N–M", its duration, A▶ / M▶ for the whole range, three text boxes **Musician**, **Song name**, **tags**, and **Send to library**.
- **Musician and Song name are pre-filled** from the work's `form.json` (`artist`, `tune` or `title`). The user can change them. **All three boxes keep their text** while the user picks other bars or sends further licks (state kept for the open work).
- Send: server joins the picked bars' ABC (first bar gets its chord symbol if missing), cuts the matching piece of `audio.wav` ± 0.1 s (padding), stores the bar offsets inside the clip (`bars_at`) so the practice view can highlight notes during the recording, and calls `add_lick_from_abc`. Metadata: artist/tune from the boxes (blank falls back to the form), `year` read from the album string (19xx/20xx), `source_bars` = global bar range (single `5` or `5-7`), comment "Solo bar(s) …(<work>), t0–t1 s of the recording". Then `analyse` assigns number, tonic, function and auto-tags. Result toast: "Sent: <label> (with audio)".
- The server rejects more than 3 or fewer than 1 bars: "A lick is 1 to 3 adjacent bars".

### 15.7 Playback in the editor

- **A▶**: the real recording from the cursor (or the picked range), with the cursor and the current bar/note highlighted.
- **M▶**: MIDI of the transcribed notes while the recording keeps running **muted** as the master clock, so seek and cursor behave identically. Note times: `T[i] + warp(beat, swing)·w` and `T[i] + warp(beat+len, swing)·w` where `w` is the bar's seconds per beat; durations ×0.95, minimum 80 ms; grace notes 70 ms before the beat. Swung per §13.4. Switching A/M keeps the position.
- Scheduling is lookahead-based (a pump every 250 ms schedules notes up to a horizon); seeking drops what was scheduled and starts a fresh audio session.

### 15.8 Tempo, key & bars panel

Fields: BPM, beats per bar, first downbeat (seconds; **"Cursor" button** copies the current play time into it), key (text), bars (only when `bars_editable`). **Apply** rebuilds the bar lines as an **even grid** at that tempo starting at the first downbeat, re-quantizes, clears locks (it warns "your edited bars are cleared" when any bar is locked). Subtitle shows "BPM · N bars · key".

### 15.9 Chords from iReal Pro (manual, with best-guess assistance)

**Principles:** chart application is a **manual** action with parameters; nothing is written until **Use these chords**. The app offers a best guess but only fills it in when confident, to minimise human work without misleading.

Panel contents:
- Title field with datalist (autocomplete from `GET /api/align/{work}/charts?q=`), **Look up chart**, status text. When a chart with exactly this work's tune title exists and none is in use yet, the lookup and guess run automatically on open.
- After a lookup: a line `"<title>": <chart key>, <bars> bars (your recording has N)`; **Key to play in** (12 root names); **Set Bar 1 to Cursor** button; **"Your bar 1 is chart bar [n]"** number box; **Guess again**.
- **Set Bar 1 to Cursor**: takes the bar under the play cursor (index `b`, 0-based) as the one that should carry **chart bar 1**; the number box becomes `((−b) mod L) + 1` where `L` is the chart length. Feedback line (gold, then red for errors): "✓ Bar 4 (at 13.81 s) is now chart bar 1, so your bar 1 is chart bar 30. Check the preview, then press "Use these chords"." The button flashes gold for ~1.2 s and the chord preview refreshes. If the cursor is not inside a bar: red message to click the waveform between the first and last bar line.
- **Preview** (`POST …/chords/preview` `{title, transpose?, rotation}`, writes nothing): returns chart key, bars, the chosen transpose/rotation, the chord list per bar after rotation/transposition, a **root-match score**, the list of differing bars and the number of bars counted. Shown as "bass root = chart root in 62% of 60 bars; different in bars 3, 17…" plus the chords per bar.
- **Use these chords** (`POST …/chords/apply`): sets `chorus` to the chart transposed by `transpose` semitones and **rotated** by `rotation` (so stored chorus bar 1 = chart bar `rotation+1`), records `chart_offset = rotation mod L`, `chorus_source`, backs up the old chords into `chorus_before_ireal`, and for a free-form work sets the transcribe section to `loop_chorus` (removing its `chords`) so the chart repeats over the whole recording. Locked bars keep their notes and take the new chord. Then the bars grid is rebuilt.
- **Left-column alignment (requirement)**: after applying, the bar boxes are padded with invisible blank cells so that **the bar carrying chart bar 1 sits in the leftmost column** (`i0 = (L − chart_offset) mod L`; pad `= (cols − i0 mod cols) mod cols`; `cols` read from the computed grid). Re-computed on window resize and after every re-render. Box captions show the real chart bar number (`(bar_in_chorus − 1 + chart_offset) mod L + 1`).

**Reliability of the automatic guess (`detect_chart`, `preview_chart`):**
- Score per candidate = fraction of bars whose **bass root matches a chart chord root**: per bar, the most prominent bass pitch class (weight 2 for notes within ½ beat of beat 1 or beat 3, weight 1 elsewhere) is compared with the chord-root set of that bar (the slash-bass note first, then the root, of every chord in the bar); a bar also counts if the chart roots hold ≥ 40 % of the bass weight. For free-form works the chart index for transcribed bar `i` is `i` when `bar_in_chorus` is unset.
- Search all 12 transpositions × L rotations; ties broken by the expected key, then rotation 0.
- **`confident`** only when best score ≥ 0.45 **and** gap to the runner-up > 0.04. Only then are key and start bar pre-filled; otherwise the panel says "No reliable guess" and leaves the defaults, listing runner-up candidates as links to preview.
- Known limitation: on a test recording (Autumn Leaves with hidden chords) the true reading scored about 13–20 %, near chance, because Muscriptor's bass notes carry little root information. The guess is a helper, not an oracle. The iReal chart is treated as the **ground truth for the chord progression minus intro or tag**.

### 15.10 Transcribe again

Run another model or band on the same recording; each run is stored under `engines/<name>/` and chosen in the "Transcription" menu (`PUT /api/align/{work}/engine`).

### 15.11 Server-side state (`align.json`)

`engine`, `bar_times` (n+1 seconds), locked bars with their ABC, edit/undo/redo history, `bpm`, `bpb`, grid settings. Every mutation returns the full work summary: `work, title, artist, tune, bpb, n, free_form, bars_editable, chart_offset, chart_len, engine/engines, ensemble, bpm, key, times, bars[{n, abc, chords, notes[{beat, len, midi, grace, graces, rest}], locked, issues, t0, t1, global, chorus, bar_in_chorus}]` and the audio availability.

### 15.12 Transcribe API (all JSON)

```
GET  /api/transcribe/options                 models, instruments, band list, licence hints
POST /api/transcribe/url                     {url, instrument, model, band[]}
POST /api/transcribe/upload                  multipart file + same fields
POST /api/transcribe/{work}/run              run again (another model/band)
GET  /api/transcribe/jobs                    job cards: steps[], percent, elapsed_s, detail, state

GET  /api/align                              list of works
GET  /api/align/{work}                       full summary
GET  /api/align/{work}/peaks                 waveform peaks
GET  /api/align/{work}/audio                 the wav (range requests)
PUT  /api/align/{work}/engine                {engine}
PUT  /api/align/{work}/times                 {times[]}  re-quantize notes
POST /api/align/{work}/snap
POST /api/align/{work}/bar/{k}/abc           replace a bar's ABC (locks it)
POST /api/align/{work}/bar/{k}/shift         shift a bar's notes
POST /api/align/{work}/bar/{k}/edit          one editing operation
POST /api/align/{work}/bar/{k}/unlock
POST /api/align/{work}/lick                  {from,to,tags,artist,tune}
GET  /api/align/{work}/charts?q=             chart title search
POST /api/align/{work}/chords/detect         best guess
POST /api/align/{work}/chords/preview        {title,transpose?,rotation?}
POST /api/align/{work}/chords/apply          {title,transpose,rotation}
PUT  /api/align/{work}/grid                  {bpm,bpb,first_downbeat,key,bars}
POST /api/align/{work}/undo | /redo
POST /api/align/{work}/svg                   notation SVG for an ABC string
GET  /api/align/{work}/musicxml              align_solo.musicxml
```

---

## 16. Other server API

```
GET  /                                       index.html
GET  /api/meta | /api/settings | PUT /api/settings
GET  /api/today
POST /api/licks/{id}/review                  {rating, keys_done, trouble_keys, tempo, mode, note}
POST /api/licks/{id}/key-tempo               {pc, tempo, best}
GET  /api/licks ; GET /api/repeats ; GET/PATCH/DELETE /api/licks/{id}
GET/POST /api/licks/{id}/abc ; POST …/abc/preview ; POST …/abc/edit
POST /api/licks/{id}/reanalyse
GET  /api/licks/{id}/svg?pc=&written= ; /play ; /audio?semis=&bpm= ; /events ; /musicxml ; /twelve
GET  /api/licks/{id}/export                  lick JSON: notes/chords with t_audio (seconds in the clip) and t_video (seconds in the YouTube video), audio bar marks, origin {url, watch_url, embed_url, clip_start, clip_end}
GET  /api/licks/{id}/export.mid              standard MIDI file at the recording's tempo (concert pitch)
GET  /api/export/licks.zip?ids=1,2,3         bundle: index.json, README.txt, licks/L0043/{lick.json, lick.musicxml, lick.mid, audio.mp3}; no ids = every lick
POST /api/import/preview ; POST /api/import/save ; POST /api/playlist
GET  /api/tunes ; GET /api/tune?title= ; POST /api/tune/learning
POST /api/fit ; /api/fit/svg ; /api/fit/snippets ; /api/fit/play ; /api/fit/file
GET  /api/sound ; POST /api/render ; GET /api/stats
```

`/api/licks/{id}/audio` with `semis`/`bpm` returns a variant of the clip (ffmpeg pitch shift and tempo change, cached) with headers `X-Clip-Offset` and `X-Clip-Marks` (bar positions in the clip). Errors use `HTTPException` with a human-readable `detail`. Mutations are guarded so a `ValueError` becomes a 422 with its message.

---

## 17. CLI (`licksmith`, alias `l4mh`)

```
licksmith serve [--port]
licksmith licks import <file.musicxml> | licksmith licks list
licksmith charts import <playlist.html|txt> | licksmith charts show "<title>"
licksmith fetch <work> <url>               download audio
licksmith gridcheck <work>                 writes grid_check.wav and bars.csv
licksmith transcribe <work> [--engine muscriptor --model medium|large | --engine midi --midi f.mid --name n]
licksmith build <work> [--engine N] [--simplicity 1.0] [--concert]
licksmith bench <work> --book benchmarks/<x>.musicxml
```

`python -m lick4myhorn.selftest` is an offline end-to-end test: generate a known swing solo (a blues), humanise it into "performed" seconds, run grid → quantize → MusicXML → bench, and require near-zero fixes.

The **benchmark** (`bench.py`) compares a transcription with a published book transcription (both MusicXML, written B♭, bar 1 = first solo bar): notes matched in passes `match, octave, pitch, rhythm (within half a beat), missing, extra`; "fixes" = octave + pitch + rhythm + missing + extra; per-chorus table and a CSV of every disagreement. Verdict: 1–2 fixes per chorus is good; more than 10 rejects the engine. This is the Blue Train benchmark in the README (parked: the model is not accurate enough to skip editing).

---

## 18. Data, privacy and licensing rules

- All data stays local. The only outbound calls are: yt-dlp to the video site the user names; Hugging Face model download; the optional vision API (images the user chooses to send; API key stored in a local secrets file).
- **`library/` must never be committed to version control.** It contains the user's personal downloads of copyrighted recordings, derived audio, transcriptions of other people's solos, and (optionally) API keys. The repository ships code only.
- The README states: "Only use recordings for your own practice", and the UI says "The audio stays on this computer."
- Muscriptor weights are CC BY-NC 4.0 (personal, non-commercial). The code is MIT.
- Never write secrets into logs or into the repository.

---

## 19. Quality requirements and acceptance tests

1. **Round-trip**: import a MusicXML lick sheet; the lick appears with correct number, tags, function; the 12-key sheet opens in MuseScore in written B♭ with correct chord symbols.
2. **12 keys**: every key's lick is inside the written range or flagged; octave choice is the one nearest the range centre.
3. **Practice**: count-in → lick → your turn → repeat; tempo per key is remembered; rating shows the interval previews; the reviews table gains a row; next due date is ≥ 1 day.
4. **Swing**: with swing 0.62, an eighth pair plays long-short with the off-beat at 0.62 of the beat in lick playback, band playback and the Transcribe M▶; with swing 0.50 it is straight.
5. **Fit**: for a ii–V–I lick and a chart containing ii–V–I in other keys, the lick is offered at those bars transposed by the right interval.
6. **Transcribe**: a pasted link creates a job; the card shows steps, a rising percentage, a real chunk counter and ETA; after completion the work opens in the editor; bar lines drag and re-quantize in under a second per move; ◀ ▶ and Alt+arrows move all lines; "Set beat one" moves only later lines by the dragged amount (verified: dragging line 3 by +500 ms moved lines 3…n by exactly +0.5 s and left lines 1–2 unchanged).
7. **Notes are not lost**: a bar with 8 detected notes in a fast run keeps all 8 in the sheet (collision stepping rule). Across the author's four test recordings the dropped-note count fell from 43/26/3/13 to 13/9/2/12.
8. **Chart**: applying a chart rotated so bar 4 carries chart bar 1 puts the bar with chart-bar-1 in the left column; the captions read 30, 31, 32, 1, 2…
9. **Send to library**: one bar, two bars and three bars all send; four is refused; the new lick plays its recording shifted to any key and tempo; Musician and Song name are pre-filled, editable and persistent across picks.
10. **Persistence**: restarting the server loses only running jobs; all else reloads from `library/`.

---

## 20. Suggested build order for a re-implementation

1. DB schema, settings, FastAPI skeleton, static shell with tab routing.
2. Chord utilities and music21-based MusicXML import → lick store → SVG rendering (verovio) → 12-key transposition.
3. Analysis/auto-tagging, FSRS scheduling, Today/Library/Progress pages.
4. Practice page with the built-in Web Audio engine (swing, count-in, band) → tempo memory → ratings.
5. FluidSynth renderer and the SoundFont path.
6. iReal import, tune view, fit engine, practice MusicXML export.
7. Transcription backend: yt-dlp fetch, ffmpeg convert, Muscriptor wrapper with progress, grid estimate, key estimate, background job runner and progress UI.
8. Quantizer, MusicXML writer, ABC conversion, `AlignWork` with bar times, undo stack, locking.
9. Transcribe UI: timeline canvas, bar-line dragging and nudging, bars grid, note editor, MIDI playback with swing.
10. Chart panel with preview/apply/rotation/left-column alignment and the root-match guess.
11. Make-a-lick with audio clip cutting and clip shifting for practice.
12. Screenshot capture (optional), benchmark tools, self-test.

---

## 21. Known limitations and open items

- Chart auto-guess is unreliable when the bass transcription is poor; it only fills in on a clear winner. Chart position for a given recording must usually be set by ear.
- Very fast runs (faster than 16ths, e.g. sextuplets) are written on the nearest 16th/triplet grid; a proper sextuplet grid is not implemented. A few notes are still dropped when both neighbouring slots are taken.
- The Transcribe ETA can swing when the music gets busier.
- Exported `solo.mid`/MusicXML carry written (straight) eighths; only playback is swung.
- The job runner is in-process: stopping the server stops the job; jobs are not resumed.
- Not all YouTube sources can be downloaded; the user may need to upload a file instead.
