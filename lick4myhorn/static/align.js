/* Align tab: waveform + bass + horn notes with draggable bar lines, real-recording playback with a
   cursor, and one editable bar (score + ABC) at a time under it. No libraries. */
"use strict";

/* The aligner is opened by the Transcribe page (transcribe.js) into #trWork. */
// opts.learn (the Learn tab) shows only the bar cards and hands playback to the loop controller in ear.js:
// no timeline, tempo/key/bars, chords or transcription controls.
async function alignOpen(work, usable, opts = {}) {
  const LEARN = opts.learn || null;
  if (window.__alCleanup) window.__alCleanup();
  try { localStorage.setItem("alignWork", work); } catch (e) { /* storage blocked */ }
  const [D, peaks] = await Promise.all([api(`/api/align/${work}`),
    LEARN ? null : api(`/api/align/${work}/peaks`)]);
  const A = { work, D, peaks: peaks || { duration: D.times[D.times.length - 1] + 1, per_sec: 1, max: [], min: [] }, times: D.times.slice(), compact: true, pps: 60, scroll: 0, sel: null, playing: false, dirty: false, svg: new Map() };
  const audio = new Audio(`/api/align/${work}/audio`);
  audio.preload = "auto";
  A.audio = audio;
  const dur = () => A.peaks.duration;

  ($("#trWork") || main()).innerHTML = `<div class="${LEARN ? "al-learn" : ""}">
    <h2 style="margin-top:22px">${esc(D.title)}${D.artist ? ` <span class="muted" style="font-weight:400">— ${esc(D.artist)}</span>` : ""}</h2>
    <p class="sub">${LEARN ? "Select the bars you want to learn: click a bar's header, then the bar next to it to add it (click an end bar again to drop it). Press Play or Space to start the loop. The waveform and square-wave buttons on a card only play it once. A bar you edit flips to Human (the person icon) and keeps your version; flip it back to Computer to clear your edits."
      : `Drag each bar line onto the bass downbeat (Shift-drag moves every later line too). Notes are re-quantized to your bar
      lines. A bar you edit flips to Human (the person icon) and keeps your version; flip it back to Computer to clear your edits.`}</p>
    <div class="card al-sticky" id="alSticky" style="padding:12px">
      <div class="row" style="margin-bottom:10px">
        <select id="alWork">${usable.map(w => `<option value="${esc(w.work)}" ${w.work === work ? "selected" : ""}>${esc(w.artist ? w.title + " — " + w.artist : w.title || w.work)}</option>`).join("")}</select>
        <button class="ghost al-tl-only" id="alDelete" title="Delete this song's recording, transcriptions and bar edits from this computer" style="color:var(--red)">Delete</button>
        <label class="small muted al-tl-only">Transcription <select id="alEngine">${D.engines.map(e => `<option ${e === D.engine ? "selected" : ""}>${esc(e)}</option>`).join("")}</select></label>
        <span class="al-btns al-tl-only"><button class="primary" id="alPlay" title="Play the real recording">A ▶</button><button id="alMidi" title="Play the transcribed notes (with your edits) as MIDI, on the same cursor">M ▶</button></span>
        <span id="alTime" class="small muted al-tl-only" style="min-width:120px;font-variant-numeric:tabular-nums"></span>
        <button class="al-tl-only" id="alZoomOut" title="Zoom out">−</button><button class="al-tl-only" id="alZoomIn" title="Zoom in">+</button>
        <button class="al-tl-only" id="alSnap" title="Slide each bar line (up to 0.25 s) to where the bass beats around it fit best. Run it twice if the grid has drifted a lot.">Snap lines to bass</button>
        <span class="al-btns al-tl-only" title="Move every bar line together, earlier or later, by the step in the box (Alt ← → does the same; add Shift for 5 steps)"><button id="alNudgeL">◀</button><select id="alNudgeStep" title="How far each press moves all the bar lines">
          <option value="10">10 ms</option><option value="25" selected>25 ms</option><option value="50">50 ms</option><option value="100">100 ms</option>
          <option value="q">¼ beat</option><option value="beat">1 beat</option></select><button id="alNudgeR">▶</button></span>
        <button class="al-tl-only" id="alTap" title="Play the recording and press Enter on beat 1 of every bar: each press sets that bar line. It starts at the first selected bar, or the bar under the cursor. Backspace undoes a tap, Space or Esc finishes.">● Tap bar lines</button>
        <button class="al-tl-only" id="alSetBeat1" title="First drag a bar line onto beat 1. Then press this: every bar line after it moves by the same amount, and the ones before it stay where they are.">Set beat one</button>
        <button id="alUndo" title="Undo (⌘Z)">↶</button><button id="alRedo" title="Redo (⇧⌘Z)">↷</button>
        <button class="al-tl-only" id="alExport">Export MusicXML</button>
        <button class="al-tl-only" id="alCompact" title="Switch the floating timeline between compact and full height">Full size</button>
        <span id="alMsg" class="small muted"></span>
      </div>
      <div id="alLearn"></div>
      <div id="alTl" class="al-tl al-tl-only"><div id="alSpacer"></div><canvas id="alCanvas"></canvas></div>
      <div class="small muted al-tl-only" style="margin-top:6px">
        <span style="color:#d9a441">■</span> horn notes &nbsp; <span style="color:#5a9bd0">■</span> bass notes &nbsp;
        Click a note on the timeline to hear it · click elsewhere to seek · Space = play/stop · Ctrl/⌘-scroll = zoom ·
        drag a bar line onto beat 1, then "Set beat one" moves every later line with it (Shift-drag does it while dragging) · <b>● Tap bar lines</b>: play, then press Enter on beat 1 of every bar · ◀ ▶ or Alt ← → move all the lines</div>
      <div id="alPick" class="al-pick-row al-tl-only"></div>
    </div>
    <h2>Bars</h2>
    <div class="small muted" style="margin-bottom:8px">Click a note to select it and hear it. Drag it up or down (Shift = semitones), or use the keys:
      <b>↑ ↓</b> semitone · <b>⌘↑ ⌘↓</b> octave · <b>Alt ↑↓</b> scale step · <b>A–G</b> note name · <b>1 2 4 8 6</b> whole / half / quarter / eighth / sixteenth ·
      <b>.</b> dotted · <b>/</b> grace note before the note (again = remove; <b>?</b> = unslashed) · <b>⌘3</b> make the selected note's beat a triplet, whatever is in it (again = straight) · <b>T</b> tie the note to the next one of the same pitch (again = remove) · <b>Delete</b> rest · <b>← →</b> next note or rest · <b>⌘Z</b> undo (⇧⌘Z redo). Select a rest and type <b>A–G</b> to turn it into a note.
      Or edit the ABC text. Any edit locks the bar.</div>
    <details id="alChords" class="card al-pick-panel al-tl-only"><summary><b>Chords from iReal Pro</b> <span class="small muted" id="alChordsSub"></span></summary>
      <div id="alChordsBody" class="small" style="margin-top:8px"></div></details>
    <details id="alGrid" class="card al-pick-panel al-tl-only"><summary><b>Tempo, key &amp; bars</b> <span class="small muted" id="alGridSub"></span></summary>
      <div class="row" style="margin-top:8px">
        <label class="small">Tempo <input id="gBpm" type="number" step="0.1" min="30" max="400" style="width:80px"> BPM</label>
        <label class="small">Beats per bar <select id="gBpb">${[2, 3, 4, 5, 6, 7].map(n => `<option>${n}</option>`).join("")}</select></label>
        <label class="small">First downbeat <input id="gFirst" type="number" step="0.01" min="0" style="width:80px"> s</label>
        <button id="gCursor" class="ghost" title="Use the time the cursor is at">use cursor</button>
        <label class="small">Key (concert) <input id="gKey" placeholder="e.g. F or Gm" style="width:80px"></label>
        ${D.bars_editable ? `<label class="small">Bars <input id="gBars" type="number" min="4" max="800" style="width:70px"></label>` : ""}
        <button id="gApply" class="primary">Apply</button><span id="gMsg" class="small muted"></span></div>
      <div class="small muted" style="margin-top:6px">Applying puts every bar line back on an even grid at this tempo, starting at the first downbeat,
        and clears the bars you edited. Do this first, then drag the lines onto the bass downbeats. The key only decides how notes are spelled.</div></details>
    <details id="alAgain" class="card al-pick-panel al-tl-only"><summary><b>Transcribe again</b> <span class="small muted">with another model or band</span></summary>
      <div id="alAgainBody" class="small" style="margin-top:8px"></div></details>
    <div id="alBars" class="al-bars"></div></div>`;

  const tl = $("#alTl"), cv = $("#alCanvas"), ctx = cv.getContext("2d");
  // The toolbar + timeline card floats at the top while you scroll the bars; "Compact" shrinks it further.
  const SIZES = { full: { ruler: 20, wave: 110, horn: 80, bass: 56 }, compact: { ruler: 18, wave: 52, horn: 44, bass: 30 } };
  const H = { ...SIZES.full };
  let HT = 0;
  const LANES = {
    horn: { y0: 0, h: 0, lo: 55, hi: 90, color: "#d9a441" },
    bass: { y0: 0, h: 0, lo: 28, hi: 60, color: "#5a9bd0" },
  };
  function layout(mode) {
    Object.assign(H, SIZES[mode]);
    HT = H.ruler + H.wave + H.horn + H.bass;
    Object.assign(LANES.horn, { y0: H.ruler + H.wave, h: H.horn });
    Object.assign(LANES.bass, { y0: H.ruler + H.wave + H.horn, h: H.bass });
  }
  layout("compact");
  const x2t = x => (x + A.scroll) / A.pps, t2x = t => t * A.pps - A.scroll;
  const msg = (t, bad) => { const m = $("#alMsg"); m.textContent = t || ""; m.style.color = bad ? "var(--again)" : ""; };

  function resize() {
    const dpr = window.devicePixelRatio || 1, w = tl.clientWidth;
    cv.width = w * dpr; cv.height = HT * dpr; cv.style.width = w + "px"; cv.style.height = HT + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    document.documentElement.style.setProperty("--al-top", ($("#alSticky").offsetHeight + (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--hdr-h")) || 56) + 14) + "px");
    $("#alSpacer").style.cssText = `width:${Math.ceil(dur() * A.pps)}px;height:1px`;
    draw();
  }

  const laneY = (L, p) => L.y0 + L.h - 4 - ((p - L.lo) / (L.hi - L.lo)) * (L.h - 8);
  function pitchLane(notes, L) {
    const t0 = x2t(0), t1 = x2t(cv.clientWidth);
    for (const [on, off, p] of notes) {
      if (off < t0 || on > t1) continue;
      const lit = A.lit && A.lit.notes === notes && A.lit.on === on;
      ctx.fillStyle = lit ? "#fff" : L.color;
      ctx.fillRect(t2x(on), laneY(L, p) - (lit ? 3 : 2), Math.max(2, (off - on) * A.pps - 1), lit ? 6 : 4);
    }
  }
  // click a note on the timeline: hear it (horn = lead, bass = bass), without moving the cursor
  function noteAt(x, y) {
    for (const [name, notes] of [["horn", A.D.horn], ["bass", A.D.bass]]) {
      const L = LANES[name];
      if (y < L.y0 || y > L.y0 + L.h) continue;
      let best = null, bd = 7;
      for (const n of notes) {
        const [on, off, p] = n, x0 = t2x(on), x1 = Math.max(x0 + 3, t2x(off));
        if (x < x0 - 3 || x > x1 + 3) continue;
        const d = Math.abs(y - laneY(L, p));
        if (d < bd) { bd = d; best = { name, n, notes }; }
      }
      if (best) return best;
    }
    return null;
  }

  function draw() {
    if (LEARN) return;  // no timeline in the Learn tab
    const W = cv.clientWidth;
    ctx.clearRect(0, 0, W, HT);
    ctx.fillStyle = "#14161b"; ctx.fillRect(0, 0, W, HT);
    // waveform: filled body plus a bright outline so note attacks are easy to spot
    const mid = H.ruler + H.wave / 2, per = peaks.per_sec, hw = H.wave / 2 - 1;
    const top = [], bot = [];
    for (let x = 0; x < W; x++) {
      const i0 = Math.floor(x2t(x) * per), i1 = Math.max(i0 + 1, Math.floor(x2t(x + 1) * per));
      if (i0 < 0 || i0 >= peaks.max.length) { top.push(null); bot.push(null); continue; }
      let mx = -1, mn = 1;
      for (let i = i0; i < Math.min(i1, peaks.max.length); i++) { mx = Math.max(mx, peaks.max[i]); mn = Math.min(mn, peaks.min[i]); }
      const gain = 1.25;  // a little boost: quiet passages stay readable
      top.push(mid - Math.min(1, mx * gain) * hw); bot.push(mid - Math.max(-1, mn * gain) * hw);
    }
    ctx.fillStyle = "#8da2c8";
    for (let x = 0; x < W; x++) if (top[x] != null) ctx.fillRect(x, top[x], 1, Math.max(1, bot[x] - top[x]));
    ctx.strokeStyle = "#e6efff"; ctx.lineWidth = 1.2;
    for (const edge of [top, bot]) {
      ctx.beginPath(); let pen = false;
      for (let x = 0; x < W; x++) {
        if (edge[x] == null) { pen = false; continue; }
        if (pen) ctx.lineTo(x + .5, edge[x]); else { ctx.moveTo(x + .5, edge[x]); pen = true; }
      }
      ctx.stroke();
    }
    // bars: shaded locked bars, then lines
    const T = A.times;
    A.D.bars.forEach((b, i) => {
      if (!b.locked) return;
      ctx.fillStyle = "rgba(217,164,65,.10)";
      ctx.fillRect(t2x(T[i]), H.ruler, (T[i + 1] - T[i]) * A.pps, HT - H.ruler);
    });
    if (A.pick && A.pick.b > A.pick.a) {
      ctx.fillStyle = "rgba(210,35,27,.22)";
      ctx.fillRect(t2x(T[A.pick.a - 1]), H.ruler, (T[A.pick.b] - T[A.pick.a - 1]) * A.pps, HT - H.ruler);
    }
    pitchLane(A.D.horn, LANES.horn);
    pitchLane(A.D.bass, LANES.bass);
    ctx.fillStyle = "#1b1e25"; ctx.fillRect(0, 0, W, H.ruler);
    ctx.font = "11px system-ui"; ctx.textBaseline = "middle";
    T.forEach((t, i) => {
      const x = t2x(t);
      if (x < -40 || x > W + 40) return;
      const hot = A.drag && A.drag.i === i;
      ctx.strokeStyle = hot ? "#fff" : (i === 0 || i === T.length - 1 ? "#e0b45a" : "#c9923a");
      ctx.lineWidth = hot ? 2 : 1;
      ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, HT); ctx.stroke();
      if (i < T.length - 1) {
        ctx.fillStyle = A.cur === i ? "#fff" : "#e8e6e1";
        ctx.fillText(String(i + 1), x + 4, H.ruler / 2);
      }
    });
    // cursor
    const cx = t2x(audio.currentTime);
    ctx.strokeStyle = "#ff5d5d"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, HT); ctx.stroke();
    if ($("#alTime")) $("#alTime").textContent = `${audio.currentTime.toFixed(2)} s` + (A.cur >= 0 && A.cur != null ? ` · bar ${A.cur + 1}` : "");
  }

  // ------------------------------------------------------------------ bar lines
  const hit = x => {
    let best = -1, bd = matchMedia("(pointer: coarse)").matches ? 20 : 7;  // a finger needs a wider target
    A.times.forEach((t, i) => { const d = Math.abs(t2x(t) - x); if (d < bd) { bd = d; best = i; } });
    return best;
  };
  cv.addEventListener("pointermove", e => {
    if (A.drag) return;
    cv.style.cursor = hit(e.offsetX) >= 0 ? "col-resize" : noteAt(e.offsetX, e.offsetY) ? "copy" : "pointer";
  });
  // On a touch screen, a finger that lands on a bar line drags it; anywhere else the timeline scrolls as usual.
  cv.addEventListener("touchstart", e => {
    const r = cv.getBoundingClientRect();
    if (e.touches.length === 1 && hit(e.touches[0].clientX - r.left) >= 0) e.preventDefault();
  }, { passive: false });
  cv.addEventListener("pointerdown", e => {
    defocus();
    const i = hit(e.offsetX);
    if (i < 0) {
      const hitNote = noteAt(e.offsetX, e.offsetY);
      if (hitNote) {
        const [on, off, p] = hitNote.n;
        liveBus();
        const t = Band.engine.ctx.currentTime + 0.02, d = Math.min(1.2, Math.max(0.2, off - on));
        if (hitNote.name === "bass") Band.engine.bass(t, d, p); else Band.engine.lead(t, d, p);
        A.lit = { notes: hitNote.notes, on }; draw(); setTimeout(() => { A.lit = null; draw(); }, 250);
        return;
      }
      audio.currentTime = Math.max(0, Math.min(dur(), x2t(e.offsetX))); tickUi(); return; }
    cv.setPointerCapture(e.pointerId);
    A.drag = { i, start: A.times.slice(), x0: e.offsetX, all: e.shiftKey };
  });
  cv.addEventListener("pointermove", e => {
    const d = A.drag; if (!d) return;
    let dt = (e.offsetX - d.x0) / A.pps;
    const T = d.start, n = T.length - 1;
    if (d.all) dt = Math.max(dt, -(T[d.i] - (d.i ? T[d.i - 1] : 0) - 0.31));
    else dt = Math.min(Math.max(dt, (d.i ? T[d.i - 1] + 0.31 : 0) - T[d.i]), (d.i < n ? T[d.i + 1] - 0.31 : dur()) - T[d.i]);
    A.times = T.map((t, j) => (j === d.i || (d.all && j > d.i)) ? t + dt : t);
    draw();
  });
  cv.addEventListener("pointerup", async () => {
    const d = A.drag; if (!d) return;
    A.drag = null;
    if (A.times.every((t, j) => Math.abs(t - d.start[j]) < 1e-6)) { draw(); return; }
    const dt = A.times[d.i] - d.start[d.i];
    await pushTimes();
    if (!d.all) {  // one line moved on its own: offer to carry every later line along by the same amount
      anchorBtn({ i: d.i, dt });
      msg(`Bar line ${d.i + 1} moved ${Math.round(Math.abs(dt) * 1000)} ms ${dt > 0 ? "later" : "earlier"}. Press "Set beat one" to move every later line by the same amount.`);
    }
  });
  function anchorBtn(a) {
    A.anchor = a || null;
    const b = $("#alSetBeat1");
    b.classList.toggle("primary", !!a);
    b.textContent = a ? `Set beat one (bar ${a.i + 1} and after)` : "Set beat one";
  }
  async function pushTimes() {
    anchorBtn(null);
    msg("Re-quantizing…");
    try {
      const r = await api(`/api/align/${work}/times`, { method: "PUT", body: { times: A.times } });
      A.D = r; A.times = r.times.slice(); renderBars(); msg("");
    } catch (err) { msg(err.message, true); }
    draw();
  }
  // Move every bar line by the same amount (the whole grid sits a little early or late). The lines move on screen at once;
  // the notes are re-quantized once, shortly after the last press.
  let nudgeTimer = 0;
  function nudgeAll(dt, now) {
    const T = A.times, last = T.length - 1;
    dt = Math.max(-T[0], Math.min(dur() - T[last], dt));  // keep every line inside the recording
    if (Math.abs(dt) < 1e-4) return;
    A.times = T.map(t => t + dt); draw();
    msg(`All bar lines ${dt > 0 ? "later" : "earlier"} by ${Math.round(Math.abs(dt) * 1000)} ms: bar 1 at ${A.times[0].toFixed(2)} s`);
    clearTimeout(nudgeTimer);
    if (now) pushTimes(); else nudgeTimer = setTimeout(pushTimes, 500);
  }
  const nudgeStep = () => {
    const v = $("#alNudgeStep").value, beat = (A.times[1] - A.times[0]) / A.D.bpb;
    return v === "beat" ? beat : v === "q" ? beat / 4 : +v / 1000;
  };
  $("#alNudgeL").onclick = e => nudgeAll(-nudgeStep() * (e.shiftKey ? 5 : 1));
  $("#alNudgeR").onclick = e => nudgeAll(nudgeStep() * (e.shiftKey ? 5 : 1));
  // "Set beat one": the last bar line you dragged is on beat 1. Every line after it moves by the same amount; the earlier ones stay.
  $("#alSetBeat1").onclick = async () => {
    const a = A.anchor, T = A.times, last = T.length - 1;
    if (!a) { msg("Drag a bar line onto beat 1 first, then press this.", true); return; }
    if (a.i >= last) { msg("That is the last bar line: nothing comes after it.", true); anchorBtn(null); return; }
    const dt = Math.min(a.dt, dur() - T[last]);  // keep the last line inside the recording
    A.times = T.map((t, j) => j > a.i ? t + dt : t);
    draw();
    await pushTimes();
    msg(`Every bar line after bar ${a.i + 1} moved ${Math.round(Math.abs(dt) * 1000)} ms ${dt > 0 ? "later" : "earlier"} with it. The earlier ones stayed.`);
  };
  tl.addEventListener("scroll", () => { A.scroll = tl.scrollLeft; draw(); });
  tl.addEventListener("wheel", e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.offsetX);
  }, { passive: false });
  function zoom(f, anchorX) {
    const ax = anchorX ?? cv.clientWidth / 2, t = x2t(ax);
    A.pps = Math.max(8, Math.min(400, A.pps * f));
    resize();
    tl.scrollLeft = A.scroll = Math.max(0, t * A.pps - ax); draw();
  }
  $("#alZoomIn").onclick = () => zoom(1.4); $("#alZoomOut").onclick = () => zoom(1 / 1.4);

  // ------------------------------------------------------------------ playback
  const curBar = t => { const T = A.times; if (t < T[0] || t >= T[T.length - 1]) return -1; let i = 0; while (i < T.length - 2 && t >= T[i + 1]) i++; return i; };
  function tickUi() {
    if (A.stopAt != null && audio.currentTime >= A.stopAt) { A.stopAt = null; if (A.playing) { stopAll(); if (A.onEnd) A.onEnd(); } }
    const c = curBar(audio.currentTime);
    if (c !== A.cur) { const prev = A.cur; A.cur = c; markBar(prev); }
    markNote();
    if (A.playing) {  // keep the cursor in view
      const x = t2x(audio.currentTime);
      if (x > cv.clientWidth * 0.85 || x < 0) { tl.scrollLeft = A.scroll = Math.max(0, audio.currentTime * A.pps - cv.clientWidth * 0.15); }
    }
    draw();
  }
  function loop() { if (!A.playing) return; tickUi(); A.raf = requestAnimationFrame(loop); }
  // Two ways to listen, same cursor: the real recording, or the transcribed notes as MIDI (the recording
  // keeps running muted as the clock, so seeking and the cursor work the same). Switching keeps the position.
  let sched = null;
  function midiNotes() {
    const out = [], T = A.times, bpb = A.D.bpb;
    const sw = (S.settings && S.settings.swing) || 0.62;  // MIDI plays swung, like every other playback in the app: the off-beat eighth sits late
    A.D.bars.forEach((b, i) => {
      const w = (T[i + 1] - T[i]) / bpb;
      b.notes.filter(n => n.midi != null).forEach(n => {
        const a = Band.warp(n.beat, sw), z = Band.warp(n.beat + n.len, sw);
        out.push({ t: T[i] + a * w, d: Math.max(0.08, (z - a) * w * 0.95), m: n.midi });
        (n.graces || []).forEach(gm => out.push({ t: T[i] + a * w - 0.07, d: 0.07, m: gm }));  // grace note just before the beat
      });
    });
    return out.sort((x, y) => x.t - y.t);
  }
  function midiStop() {
    if (sched) { clearInterval(sched.timer); sched = null; }
    const e = Band.engine;
    if (e.ctx && e.cur) { window.__alBusDead = true; const g = e.cur.bus.gain; g.setValueAtTime(g.value, e.ctx.currentTime); g.linearRampToValueAtTime(0, e.ctx.currentTime + 0.05); }
  }
  function midiStart(from) {
    Band.engine.init(); Band.engine.session(); window.__alBusDead = false;
    const notes = midiNotes();
    sched = { notes, upto: from - 0.03, last: audio.currentTime, timer: null };  // from = where you pressed play, not where the clock is by now
    const pump = () => {
      if (!sched) return;
      const now = audio.currentTime;
      if (now < sched.last - 0.05 || now > sched.last + 1.5 * (A.rate || 1)) {  // you sought: drop what was scheduled
        const e = Band.engine; const g = e.cur.bus.gain; g.setValueAtTime(g.value, e.ctx.currentTime); g.linearRampToValueAtTime(0, e.ctx.currentTime + 0.03);
        e.session(); sched.upto = now;
      }
      sched.last = now;
      const rate = A.rate || 1, horizon = now + 1.5 * rate, ctxNow = Band.engine.ctx.currentTime;
      for (const n of sched.notes) {
        if (n.t < sched.upto || n.t >= horizon) continue;
        Band.engine.lead(ctxNow + Math.max(0, n.t - now) / rate, n.d / rate, n.m);
      }
      sched.upto = horizon;
    };
    pump(); sched.timer = setInterval(pump, 250);
  }
  function setButtons() {
    const pa = $("#alPlay"), pm = $("#alMidi");   // gone when the page was replaced while a cleanup stops the player
    if (pa) pa.textContent = A.playing && A.mode === "audio" ? "A ⏸" : "A ▶";
    if (pm) pm.textContent = A.playing && A.mode === "midi" ? "M ⏸" : "M ▶";
  }
  function stopAll() { audio.pause(); midiStop(); A.playing = false; setButtons(); tickUi(); }
  // Play bars a..b once, from the recording ("audio") or from your notes ("midi"), then stop.
  function playRange(a, b, mode) {
    if (A.playing) stopAll();
    audio.currentTime = A.times[a - 1];
    A.stopAt = A.times[b];
    toggle(mode);
  }
  function toggle(mode = "audio") {
    const was = A.playing, prev = A.mode;
    if (was) stopAll();
    if (was && prev === mode) return;
    A.mode = mode; audio.muted = mode === "midi";
    audio.playbackRate = A.rate || 1; audio.preservesPitch = true;  // the Learn tab slows the recording without changing its pitch
    const from = audio.currentTime;
    audio.play().then(() => { A.playing = true; if (mode === "midi") midiStart(from); setButtons(); loop(); }).catch(e => msg(e.message, true));
  }
  audio.addEventListener("ended", () => { midiStop(); A.playing = false; setButtons(); if (A.onEnd) A.onEnd(); });
  $("#alPlay").onclick = () => toggle("audio");
  $("#alMidi").onclick = () => toggle("midi");

  // ------------------------------------------------------------------ tap bar lines
  // Play the recording and press Enter on beat 1 of each bar. The first tap sets the bar line of the first selected bar (or the bar
  // under the cursor), the next tap the following line, and so on. Lines after the last tap move along with it, as with "Set beat one".
  const tapLines = () => {
    const k = A.tap, n = k.orig.length - 1, delta = k.taps.length ? k.taps[k.taps.length - 1] - k.orig[k.s + k.taps.length - 1] : 0;
    A.times = k.orig.map((x, j) => j < k.s ? x : j < k.s + k.taps.length ? k.taps[j - k.s] : Math.min(x + delta, dur() - 0.31 * (n - j)));
    draw();
  };
  const tapPrompt = () => { const k = A.tap; msg(`Tapping: press Enter on beat 1 of bar ${k.s + k.taps.length + 1}. Backspace undoes a tap · Space or Esc finishes.`); };
  function tapStart() {
    if (A.tap) { tapEnd(); return; }
    const s = A.pick ? A.pick.a - 1 : Math.max(0, curBar(audio.currentTime));
    A.tap = { s, orig: A.times.slice(), taps: [] };
    const b = $("#alTap"); b.classList.add("primary"); b.textContent = "■ Stop tapping"; b.blur();
    if (!A.playing) toggle("audio");   // plays from the cursor
    tapPrompt();
  }
  function tapHit() {
    const k = A.tap, t = audio.currentTime, idx = k.s + k.taps.length, n = k.orig.length - 1;
    if (idx > n) { tapEnd(); return; }
    const prev = idx > 0 ? A.times[idx - 1] : 0;
    if (t < prev + 0.31) { msg("Too close to the previous bar line: ignored.", true); return; }
    k.taps.push(t); tapLines(); tapPrompt();
    if (k.s + k.taps.length > n) tapEnd();   // that was the last bar line
  }
  function tapUndo() {
    const k = A.tap; if (!k.taps.length) return;
    k.taps.pop(); tapLines(); tapPrompt();
  }
  async function tapEnd() {
    const k = A.tap; if (!k) return;
    A.tap = null;
    const b = $("#alTap"); if (b) { b.classList.remove("primary"); b.textContent = "● Tap bar lines"; }
    stopAll();
    if (!k.taps.length) { A.times = k.orig; draw(); msg("No taps: the bar lines are unchanged."); return; }
    await pushTimes();
    msg(`Set ${k.taps.length} bar line${k.taps.length === 1 ? "" : "s"} from bar ${k.s + 1}; every later line moved along with them. Use ◀ ▶ to nudge if you tapped a little late.`);
  }
  $("#alTap").onclick = tapStart;

  // MuseScore-style keys. Needs a selected note (click one). ↑↓ semitone, ⌘↑↓ octave, Alt+↑↓ scale step,
  // A–G note name, 1 2 4 8 6 = whole/half/quarter/eighth/sixteenth, . dotted, ⌘3 triplet, T tie to the next note, Delete = rest, ←→ move.
  const LEN = { "1": "4", "2": "2", "4": "1", "8": "1/2", "6": "1/4" };
  let queue = Promise.resolve();
  const edit = (op, value) => {
    if (!A.sel) return;
    const { bar, index } = A.sel;
    queue = queue.then(async () => {
      try {
        const nb = await api(`/api/align/${work}/bar/${bar}/edit`, { body: { index, op, value } });
        if (op !== "rest") { const m = nb.notes[index]; if (m) playMidi(m.midi); }  // hear the resulting note
        A.sel = { bar, index: Math.min(index, Math.max(0, nb.notes.length - 1)) };
        msg(""); await reload();
      } catch (err) { msg(err.message, true); }
    });
  };
  function undo(which) {
    queue = queue.then(async () => {
      try {
        A.D = await api(`/api/align/${work}/${which}`, { body: {} });
        A.times = A.D.times.slice();
        const n = A.sel && A.D.bars[A.sel.bar - 1].notes.length;
        if (A.sel && A.sel.index >= n) A.sel.index = Math.max(0, n - 1);
        renderBars(); draw(); paintSel(); msg("");
      } catch (err) { msg(err.message, true); }
    });
  }
  $("#alUndo").onclick = () => undo("undo");
  $("#alRedo").onclick = () => undo("redo");
  const moveSel = d => {
    if (!A.sel) { const b = A.D.bars[Math.max(0, A.cur ?? 0)]; select(b.n, 0); return; }
    let { bar, index } = A.sel; index += d;
    while (index < 0 || index >= A.D.bars[bar - 1].notes.length) {
      bar += index < 0 ? -1 : 1;
      if (bar < 1 || bar > A.D.bars.length) return;
      const n = A.D.bars[bar - 1].notes.length;
      index = index < 0 ? n - 1 : 0;
      if (!n) index = d < 0 ? -1 : 0;
    }
    select(bar, index);
    const m = A.D.bars[bar - 1].notes[index]; if (m) playMidi(m.midi);
    const el = $(`#alBars .al-bar[data-n="${bar}"]`); if (el) el.scrollIntoView({ block: "nearest" });
  };
  A.key = e => {
    if (S.tab !== (LEARN ? "learn" : "transcribe") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const cmd = e.metaKey || e.ctrlKey, k = e.key;
    if (A.tap) {   // tap mode owns Enter, Backspace, Space and Esc
      if (k === "Enter") { e.preventDefault(); if (!e.repeat) tapHit(); return; }
      if (k === "Backspace") { e.preventDefault(); tapUndo(); return; }
      if (k === "Escape" || e.code === "Space") { e.preventDefault(); tapEnd(); return; }
    }
    if (e.code === "Space") { e.preventDefault(); if (LEARN) LEARN.toggle(); else toggle(A.mode || "audio"); return; }
    if (e.altKey && (k === "ArrowLeft" || k === "ArrowRight")) { e.preventDefault(); nudgeAll((k === "ArrowLeft" ? -1 : 1) * nudgeStep() * (e.shiftKey ? 5 : 1)); return; }
    if (k === "ArrowLeft" || k === "ArrowRight") { e.preventDefault(); moveSel(k === "ArrowLeft" ? -1 : 1); return; }
    if (k === "Escape") { A.sel = null; paintSel(); return; }
    if (cmd && (k === "z" || k === "Z" || k === "y")) { e.preventDefault(); undo(k === "y" || e.shiftKey ? "redo" : "undo"); return; }
    if (!A.sel) return;
    const up = k === "ArrowUp", down = k === "ArrowDown";
    if (up || down) {
      e.preventDefault();
      const d = up ? 1 : -1;
      if (cmd) edit("octave", d); else if (e.altKey) edit("step", d); else edit("semitone", d);
    } else if (cmd && k === "3") { e.preventDefault(); edit("triplet"); }
    else if (!cmd && !e.altKey && /^[a-gA-G]$/.test(k)) { e.preventDefault(); edit("letter", k.toUpperCase()); }
    else if (!cmd && LEN[k]) { e.preventDefault(); edit("length", LEN[k]); }
    else if (!cmd && (k === "t" || k === "T")) { e.preventDefault(); edit("tie"); }
    else if (!cmd && k === ".") { e.preventDefault(); edit("dot"); }
    else if (!cmd && k === "/") { e.preventDefault(); edit("grace", { slash: true }); }
    else if (!cmd && k === "?") { e.preventDefault(); edit("grace", { slash: false }); }
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); edit("rest"); }
  };
  if (window.__alKey) document.removeEventListener("keydown", window.__alKey);
  window.__alKey = A.key; document.addEventListener("keydown", A.key);

  // ------------------------------------------------------------------ bars
  // Stopping playback fades the shared sound bus to silence; the next sound needs a fresh one.
  function liveBus() {
    Band.engine.init();
    if (!Band.engine.cur || window.__alBusDead) { Band.engine.session(); window.__alBusDead = false; }
  }
  function playMidi(m, graces) {
    if (m == null) return;  // a rest
    liveBus();
    const t = Band.engine.ctx.currentTime + 0.02, g = (graces || []).length ? 0.08 : 0;
    (graces || []).forEach(gm => Band.engine.lead(t, 0.08, gm));
    Band.engine.lead(t + g, 0.7, m);
  }
  async function svgOf(b) {
    const key = b.abc + "|" + b.chords.join(",");
    if (!A.svg.has(key)) {
      const r = await fetch(`/api/align/${work}/svg`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ abc: b.abc, chords: b.chords.slice(0, 1) }) });
      A.svg.set(key, r.ok ? await r.text() : `<div class="muted small">Can't draw this bar (${r.status}). Check the ABC.</div>`);
    }
    return A.svg.get(key);
  }
  // recording = a smooth sound wave; MIDI = a square wave
  const ICON_WAVE = `<svg viewBox="0 0 28 16" width="26" height="15" aria-hidden="true"><path d="M1 8h3l2-5 3 10 3-12 3 14 3-10 2 3h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const ICON_SQUARE = `<svg viewBox="0 0 28 16" width="26" height="15" aria-hidden="true"><path d="M1 12h4V4h6v8h6V4h6v8h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="square" stroke-linejoin="miter"/></svg>`;
  const ICON_HUMAN = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="7.5" r="4" fill="currentColor"/><path d="M4 21c0-4.4 3.6-7.5 8-7.5s8 3.1 8 7.5z" fill="currentColor"/></svg>`;
  const ICON_COMPUTER = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 20h8M12 16v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
  function barHtml(b) {
    const bad = (b.issues || []).length ? `<div class="al-issue">${esc(b.issues.map(i => i.msg).join("; "))}</div>` : "";
    return `<div class="al-bar-head" title="${LEARN ? "Select this bar to learn it. Click the bar next to it to add it, click an end bar again to remove it" : "Tick this bar for the lick. Click the bar next to it to add it (up to 3 in a row), click an end bar again to remove it"}"><b class="al-pick">${b.n}</b><span class="muted small">${b.chorus ? `ch ${b.chorus} - bar ${A.D.chart_offset ? (b.bar_in_chorus - 1 + A.D.chart_offset) % A.D.chart_len + 1 : b.bar_in_chorus}` : ""}</span>
        <span class="spacer"></span>
        <span class="al-btns"><button class="al-ga al-ic" aria-label="Play the recording" title="Play this bar (or the whole picked lick): the recording">${ICON_WAVE}</button><button class="al-gm al-ic" aria-label="Play as MIDI" title="Play this bar (or the whole picked lick): your notes as MIDI">${ICON_SQUARE}</button></span>
        <span class="al-sw ${b.locked ? "human" : "computer"}" role="group" aria-label="Who wrote this bar">
          <button class="al-sw-h" ${b.locked ? "" : "disabled"} aria-pressed="${!!b.locked}" title="${b.locked ? "Human: you edited this bar, so it keeps your version" : "Computer's transcription. Edit a note and this bar flips to Human."}">${ICON_HUMAN}</button>
          <button class="al-sw-c" ${b.locked ? "" : "disabled"} aria-pressed="${!b.locked}" title="${b.locked ? "Flip back to Computer: clears your edits to this bar (Undo brings them back)" : "Computer: this bar is the automatic transcription"}">${ICON_COMPUTER}</button>
        </span></div>
      <div class="paper al-svg"></div>
      <input class="al-abc" spellcheck="false" value="${esc(b.abc)}">${bad}`;
  }
  // Make a bar's drawing read like one stretch of a normal score when four sit in a row: no clef or time signature
  // (and the key signature only at the start of a row), an ordinary bar line instead of the final double bar.
  function bareSvg(b, holder) {
    const outer = $("svg", holder), inner = outer && $("svg", outer);
    if (!outer || !inner) return;
    try {
      const drop = $$("g.clef, g.meterSig" + ((b.n - 1) % 4 ? ", g.keySig" : ""), inner);
      const staff = $$("g.staff > path", inner);
      let left = Infinity, right = -Infinity;
      staff.forEach(p => { const m = /M\s*([\d.]+)/.exec(p.getAttribute("d") || ""); if (m) left = Math.min(left, +m[1]); });
      drop.forEach(g => { const bb = g.getBBox(); if (bb.width) right = Math.max(right, bb.x + bb.width); g.remove(); });
      $$("g.barLine", inner).forEach(g => { $$("path", g).slice(1).forEach(p => p.remove()); });
      // Verovio draws the bar on a much wider page, so the notes only fill the left part. Crop the drawing to the bar itself:
      // from where the clef and time signature were to the end of the staff lines (= the bar line).
      let end = -Infinity;
      staff.forEach(p => { const m = /L\s*([\d.]+)/.exec(p.getAttribute("d") || ""); if (m) end = Math.max(end, +m[1]); });
      const cut = isFinite(left) && isFinite(right) && right > left ? right - left + 120 : 0;  // a little air before the first note
      if (isFinite(left) && isFinite(end) && end > left) {
        if (cut) staff.forEach(p => p.setAttribute("d", (p.getAttribute("d") || "").replace(/M\s*([\d.]+)/, (m, x) => "M" + (+x + cut))));
        const vb = inner.viewBox.baseVal, ob = outer.viewBox.baseVal, k = vb.width / ob.width;
        const x0 = left + cut, w = end - x0 + 40;  // 40: half the thickness of the bar line
        // the same window around the staff in every bar (room above for chord names, brackets and high notes, below for low ones),
        // so the staves sit at the same height all along a row
        const ys = staff.map(p => { const m = /M\s*[\d.]+\s+([\d.]+)/.exec(p.getAttribute("d") || ""); return m ? +m[1] : NaN; }).filter(Number.isFinite);
        const top = Math.min(...ys), sp = (Math.max(...ys) - top) / 4 || 180, y0 = top - 8 * sp, h = 17 * sp;
        inner.setAttribute("viewBox", `${x0} ${y0} ${w} ${h}`);
        outer.setAttribute("viewBox", `${ob.x} ${ob.y} ${w / k} ${h / k}`);
        outer.removeAttribute("width"); outer.removeAttribute("height"); inner.removeAttribute("width"); inner.removeAttribute("height");
        outer.style.width = "100%"; outer.style.height = "auto";
      }
    } catch (e) { /* leave the drawing as it is */ }
  }
  async function paintBar(b, el) {
    el.innerHTML = barHtml(b);
    el.classList.toggle("locked", b.locked);
    const holder = $(".al-svg", el);
    holder.innerHTML = await svgOf(b);
    bareSvg(b, holder);
    // the whole header strip is the tap target (box, number, chorus text, empty space); only its buttons do something else
    $(".al-bar-head", el).onclick = e => { if (e.target.closest("button")) return; defocus(); pick(b.n, e.shiftKey); };
    wireNotes(b, holder);
    // inside a joined selection (2-3 bars picked for a lick) these play the whole lick, otherwise just this bar
    const span = () => (A.pick && A.pick.b > A.pick.a && b.n >= A.pick.a && b.n <= A.pick.b) ? A.pick : { a: b.n, b: b.n };
    $(".al-ga", el).onclick = () => { const r = span(); if (LEARN) LEARN.playOnce(r, "audio"); else playRange(r.a, r.b, "audio"); };
    $(".al-gm", el).onclick = () => { const r = span(); if (LEARN) LEARN.playOnce(r, "midi"); else playRange(r.a, r.b, "midi"); };
    const u = $(".al-sw-c", el);
    if (u && b.locked) u.onclick = async () => { await api(`/api/align/${work}/bar/${b.n}/unlock`, { method: "POST", body: {} }); await reload(); };
    const inp = $(".al-abc", el);
    const save = async () => {
      if (inp.value.trim() === b.abc.trim()) return;
      try { await api(`/api/align/${work}/bar/${b.n}/abc`, { body: { abc: inp.value } }); msg(""); await reload(); }
      catch (err) { msg(`Bar ${b.n}: ${err.message}`, true); }
    };
    inp.onchange = save;
    inp.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } };
    markBar();
  }
  // Clicking a note or the timeline must hand the keyboard back to the page: otherwise a text box or dropdown
  // used earlier keeps focus, and keys like Delete go into it (or nowhere) instead of editing the selected note.
  const defocus = () => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); };
  // drawn symbols of a bar, one per event (the drawing also holds grace notes, which are not events)
  function symbolsOf(b, root) {
    const all = $$("g.note, g.rest, g.mRest", root); let p = 0;
    return b.notes.map(n => { p += n.grace || 0; return all[p++]; });
  }
  function wireNotes(b, holder) {
    const notes = symbolsOf(b, holder);
    const svg = $("svg", holder);
    if (!svg) return;
    const staff = $("g.staff path", holder);
    const space = (() => {  // pixels per scale step = half the distance between two staff lines
      const ys = $$("g.staff > path", holder).map(p => p.getBoundingClientRect()).filter(r => r.width > 20).map(r => r.top).sort((a, c) => a - c);
      return ys.length >= 2 ? (ys[ys.length - 1] - ys[0]) / (ys.length - 1) / 2 : 6;
    })();
    notes.forEach((g, index) => {
      const isRest = !g.classList.contains("note");
      if (isRest) {  // thin rest glyphs are hard to hit: add an invisible click area around them
        try {
          const bb = g.getBBox(), pad = Math.max(bb.width, bb.height) * 0.35;
          const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
          hit.setAttribute("class", "hit");
          hit.setAttribute("x", bb.x - pad); hit.setAttribute("y", bb.y - pad);
          hit.setAttribute("width", bb.width + 2 * pad); hit.setAttribute("height", bb.height + 2 * pad);
          g.insertBefore(hit, g.firstChild);
        } catch (e) { /* not rendered yet */ }
      }
      g.style.cursor = isRest ? "pointer" : "ns-resize";
      g.addEventListener("pointerdown", e => {
        e.preventDefault(); defocus();
        const y0 = e.clientY, info = b.notes[index];
        select(b.n, index);
        if (isRest) return;  // a rest: select it, then type A-G to make it a note, or 1 2 4 8 6 to change its length
        g.setPointerCapture(e.pointerId);
        if (info && info.midi != null) playMidi(info.midi, info.graces);
        let steps = 0;
        const mv = ev => { steps = Math.round(-(ev.clientY - y0) / space); g.style.transform = `translateY(${-steps * space * (svg.viewBox.baseVal.width / svg.getBoundingClientRect().width)}px)`; };
        const up = ev => {
          g.removeEventListener("pointermove", mv); g.removeEventListener("pointerup", up);
          g.style.transform = "";
          if (steps) shift(b.n, index, steps, ev.shiftKey);
        };
        g.addEventListener("pointermove", mv); g.addEventListener("pointerup", up);
      });
    });
    if (A.sel && A.sel.bar === b.n) paintSel();
  }
  async function shift(bar, index, steps, chromatic) {
    try {
      const nb = await api(`/api/align/${work}/bar/${bar}/shift`, { body: { index, steps, chromatic: !!chromatic } });
      const m = nb.notes[index]; if (m) playMidi(m.midi);
      A.sel = { bar, index }; await reload();
    } catch (err) { msg(err.message, true); }
  }
  function select(bar, index) { A.sel = { bar, index }; paintSel(); }
  function paintSel() {
    $$("#alBars .sel.note, #alBars .sel.rest, #alBars .sel.mRest").forEach(g => g.classList.remove("sel"));
    if (!A.sel) return;
    const el = $(`#alBars .al-bar[data-n="${A.sel.bar}"]`);
    const g = el && symbolsOf(A.D.bars[A.sel.bar - 1], el)[A.sel.index];
    if (g) g.classList.add("sel");
  }
  // Highlight the bar under the cursor. When the cursor lands in a different bar (you clicked the waveform, or
  // playback moved on) the page scrolls to that bar's box: a jump puts it mid-screen, the next bar just keeps it in view.
  function markBar(prev) {
    $$("#alBars .al-bar.now").forEach(e => e.classList.remove("now"));
    if (A.cur >= 0 && A.cur != null) {
      const el = $(`#alBars .al-bar[data-n="${A.cur + 1}"]`);
      if (el) {
        el.classList.add("now");
        if (prev !== undefined) {
          const step = prev != null && A.cur - prev === 1;
          el.scrollIntoView({ block: step ? "nearest" : "center", behavior: "smooth" });
        }
      }
    }
  }
  function markNote() {
    $$("#alBars g.note.play").forEach(g => g.classList.remove("play"));
    if (!A.playing || A.cur < 0 || A.cur == null) return;
    const b = A.D.bars[A.cur], T = A.times;
    const beat = (audio.currentTime - T[A.cur]) / (T[A.cur + 1] - T[A.cur]) * A.D.bpb;
    let idx = -1; b.notes.forEach((n, i) => { if (n.beat <= beat + 1e-6) idx = i; });
    const el = $(`#alBars .al-bar[data-n="${b.n}"]`);
    const g = el && idx >= 0 && symbolsOf(b, el)[idx];
    if (g) g.classList.add("play");
  }
  // ---- chords: take them from an imported iReal chart. Nothing changes until you press "Use these chords".
  const NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  async function loadChords() {
    const box = $("#alChordsBody");
    let info;
    try { info = await api(`/api/align/${work}/charts`); } catch (err) { box.textContent = err.message; return; }
    const c = info.check;
    $("#alChordsSub").textContent = info.source ? "· now: " + info.source :
      A.D.free_form ? "· none yet" : `· form.json chords (${Math.round(c.score * 100)}% agree with the bass notes)`;
    box.innerHTML = `
      <div class="muted" style="margin-bottom:6px">The chart is the ground truth for the chords. The best key and starting bar are guessed from the bass notes. Check the preview, change anything, then press the button. Nothing is written before that.</div>
      <div class="row"><input id="alChartQ" list="alChartList" value="${esc(info.exact || info.tune)}" style="min-width:260px" placeholder="tune title">
        <datalist id="alChartList">${info.matches.map(m => `<option value="${esc(m)}">`).join("")}</datalist>
        <button id="alChartGo">Look up chart</button><span id="alChartMsg" class="muted"></span></div>
      ${info.exact || A.D.free_form ? "" : `<div class="muted" style="margin-top:6px">"${esc(info.tune)}" is not in your imported iReal playlist. Pick another title, or keep the chords in form.json.</div>`}
      <div id="alChartOut" style="margin-top:8px"></div>`;
    const input = $("#alChartQ", box), msg = $("#alChartMsg", box), out = $("#alChartOut", box);
    input.oninput = async () => {
      const r = await api(`/api/align/${work}/charts?q=${encodeURIComponent(input.value)}`);
      $("#alChartList", box).innerHTML = r.matches.map(m => `<option value="${esc(m)}">`).join("");
    };
    let cur = null;  // the chart on show: {title, chart_pc, chart_key, bars}
    const params = () => {
      const pc = NAMES.indexOf($("#alChartKey", out).value);
      return { title: cur.title, transpose: cur.chart_pc == null ? 0 : (pc - cur.chart_pc + 12) % 12, rotation: Math.max(0, (+$("#alChartStart", out).value || 1) - 1) };
    };
    async function preview(p) {
      msg.textContent = "";
      try {
        const d = await api(`/api/align/${work}/chords/preview`, { body: p || { title: cur ? cur.title : input.value, ...(cur ? params() : {}) } });
        cur = { title: d.title, chart_pc: d.chart_pc, chart_key: d.chart_key, bars: d.bars };
        const keyName = d.chart_pc == null ? "C" : NAMES[(d.chart_pc + d.transpose) % 12];
        if (!$("#alChartKey", out)) {
          out.innerHTML = `
            <div class="row">
              <span class="muted">"${esc(d.title)}": ${esc(d.chart_key || "?")}, ${d.bars} bars (your recording has ${d.n_bars}).</span>
              <label class="small">Key to play in <select id="alChartKey">${NAMES.map(n => `<option>${n}</option>`).join("")}</select></label>
              <button id="alChartCur" title="Play or click the waveform until the cursor is in the bar where the tune's form starts (bar 1 of the chart), then press this">Set Bar 1 to Cursor</button>
              <label class="small">Your bar 1 is chart bar <input id="alChartStart" type="number" min="1" max="${d.bars}" value="1" style="width:56px"></label>
              <button id="alChartSug" class="ghost" title="Compare the bass roots with the chart in every key and starting bar">Guess again</button>
            </div>
            <div id="alChartCurMsg" class="small" style="margin-top:6px;color:var(--brass)"></div><div id="alChartPrev" style="margin-top:8px"></div><div id="alChartSugOut"></div>
            <div class="row" style="margin-top:8px"><button class="primary" id="alChartApply">Use these chords</button>
              <span class="small muted">The chart repeats for the length of the recording. Your old chords are kept in form.json as chorus_before_ireal. Edited (locked) bars keep their notes and get the new chords.</span></div>`;
          $("#alChartKey", out).onchange = $("#alChartStart", out).onchange = () => preview();
          $("#alChartSug", out).onclick = () => suggest(true);
          $("#alChartCur", out).onclick = () => {
            let b = curBar(audio.currentTime);
            if (b < 0) b = A.cur != null && A.cur >= 0 ? A.cur : -1;
            const note = $("#alChartCurMsg", out);
            if (b < 0) { note.style.color = "var(--again)"; note.textContent = "The cursor is not inside a bar. Click the waveform between the first and last bar line, then press again."; return; }
            const L = cur.bars;  // the bar under the cursor must get chart bar 1, so your bar 1 gets chart bar 1 minus b (wrapping round)
            const s1 = ((-b) % L + L) % L + 1;
            $("#alChartStart", out).value = s1;
            note.style.color = "var(--brass)";
            note.textContent = `✓ Bar ${b + 1} (at ${audio.currentTime.toFixed(2)} s) is now chart bar 1` + (s1 > 1 ? `, so your bar 1 is chart bar ${s1}` : "") + `. Check the preview, then press "Use these chords".`;
            const btn = $("#alChartCur", out); btn.classList.add("primary"); setTimeout(() => btn.classList.remove("primary"), 1200);
            preview();
          };
          $("#alChartApply", out).onclick = apply;
        }
        $("#alChartKey", out).value = keyName;
        $("#alChartStart", out).value = d.rotation + 1;
        $("#alChartPrev", out).innerHTML = `<div class="small muted" style="margin-bottom:3px">Preview${d.score == null ? "" : ` · bass root = chart root in ${Math.round(d.score * 100)}% of ${d.counted} bars` +
            (d.bad.length ? `; different in bar${d.bad.length > 1 ? "s" : ""} ${d.bad.slice(0, 12).join(", ")}${d.bad.length > 12 ? "…" : ""}` : "")}</div>
          <div class="mono small">${d.chorus.map((b, i) => `<b class="muted">${i + 1}</b> ${esc(b.join(" "))}`).join("&nbsp; | &nbsp;")}</div>`;
      } catch (err) { msg.textContent = err.message; }
    }
    // Try every key and every starting bar, rank by how many bars have the chart's chord root in the bass, and (auto) fill in the best.
    async function suggest(auto) {
      const so = $("#alChartSugOut", out); so.textContent = "Comparing the bass roots with the chart in every key…";
      try {
        const d = await api(`/api/align/${work}/chords/detect`, { body: { title: cur.title } });
        const nm = o => NAMES[((cur.chart_pc || 0) + o.transpose) % 12];
        const best = d.candidates[0];
        const fill = auto && d.confident;  // only fill in a guess that clearly beats the others
        if (fill) await preview({ title: cur.title, transpose: best.transpose, rotation: best.rotation });
        so.innerHTML = d.confident ?
          `<div style="margin:6px 0 2px"><b>Best guess: ${nm(best)}, chart bar ${best.rotation + 1} under your bar 1</b>
            <span class="muted">· the bass root matches the chart in ${Math.round(best.score * 100)}% of ${d.counted} bars. Filled in above.</span></div>` :
          `<div style="margin:6px 0 2px"><b>No reliable guess.</b> <span class="muted">The closest reading matches the bass root in only ${Math.round(best.score * 100)}% of ${d.counted} bars, barely better than the others,
            so I left the key and start bar alone. Line the bar lines up with the bass first, then press Guess again, or pick a reading below to preview it.</span></div>`;
        so.innerHTML += d.candidates.slice(fill ? 1 : 0).map(o => `<div class="small"><a href="#" data-t="${o.transpose}" data-r="${o.rotation}">${nm(o)}, chart bar ${o.rotation + 1} under your bar 1</a>
            <span class="muted">${Math.round(o.score * 100)}%</span></div>`).join("");
        $$("a[data-t]", so).forEach(a => a.onclick = e => { e.preventDefault(); preview({ title: cur.title, transpose: +a.dataset.t, rotation: +a.dataset.r }); });
      } catch (err) { so.textContent = /bass/i.test(err.message) ? "No bass notes to guess from: set the key and start bar yourself." : err.message; }
    }
    async function apply() {
      msg.textContent = "Applying…";
      try {
        const p = params();
        A.D = await api(`/api/align/${work}/chords/apply`, { body: p });
        A.times = A.D.times.slice(); A.svg.clear(); $("#alBars").innerHTML = ""; renderBars(); draw(); toast("Chords updated from iReal"); loadChords();
      } catch (err) { msg.textContent = err.message; }
    }
    const lookup = async () => { out.innerHTML = ""; cur = null; msg.textContent = "Looking up…"; await preview({ title: input.value }); if (cur) await suggest(true); };
    $("#alChartGo", box).onclick = lookup;
    if (info.exact && !info.source) lookup();  // a chart with this title exists and none is in use yet: show the best guess right away
  }
  if (!LEARN && $("#alChords")) loadChords();

  // ---- tempo, key and bar count
  const gridFill = () => {
    $("#gBpm").value = A.D.bpm; $("#gBpb").value = A.D.bpb; $("#gFirst").value = A.D.first_downbeat; $("#gKey").value = A.D.key || "";
    if ($("#gBars")) $("#gBars").value = A.D.n;
    $("#alGridSub").textContent = `${A.D.bpm} BPM · ${A.D.n} bars${A.D.key ? " · " + A.D.key : ""}`;
  };
  gridFill();
  $("#gCursor").onclick = () => { $("#gFirst").value = audio.currentTime.toFixed(2); };
  let gArmed = false;
  $("#gApply").onclick = async () => {
    const btn = $("#gApply"), m = $("#gMsg");
    if (!gArmed) {
      gArmed = true; btn.textContent = "Click again to confirm";
      m.textContent = "Bar lines go back to an even grid" + (A.D.bars.some(b => b.locked) ? " and your edited bars are cleared." : ".");
      setTimeout(() => { gArmed = false; btn.textContent = "Apply"; }, 15000);
      return;
    }
    gArmed = false; btn.textContent = "Apply"; m.textContent = "Applying…";
    try {
      const body = { bpm: +$("#gBpm").value, beats_per_bar: +$("#gBpb").value, first_downbeat: +$("#gFirst").value, key: $("#gKey").value };
      if ($("#gBars")) body.bars = +$("#gBars").value;
      await api(`/api/align/${work}/grid`, { method: "PUT", body });
      S.svgCache.clear();
      await alignOpen(work, usable);
      toast("Bar lines reset to the new tempo");
    } catch (err) { m.textContent = err.message; m.style.color = "var(--again)"; }
  };
  window.transcribeAgainPanel && window.transcribeAgainPanel($("#alAgainBody"), work, A.D);

  // ---- take 1-3 adjacent bars as a lick and send it, with the recording of those bars, to the library
  // Plain clicks toggle bars into the lick (checkbox style): 1-3 bars in a row. Clicking the bar next to the picked ones adds
  // it, clicking an end bar takes it out again, anything else starts over with that bar. Shift-click still adds a whole range.
  function pick(n, extend) {
    const p = A.pick, MAX = LEARN ? Infinity : 3;
    if (!p) A.pick = { a: n, b: n };
    else if (extend) { const a = Math.min(p.a, n), b = Math.max(p.b, n); A.pick = b - a < MAX ? { a, b } : { a: n, b: n }; }
    else if (n >= p.a && n <= p.b) {
      if (p.a === p.b) A.pick = null;                 // the only bar: untick it
      else if (n === p.a) A.pick = { a: p.a + 1, b: p.b };
      else if (n === p.b) A.pick = { a: p.a, b: p.b - 1 };
      else A.pick = { a: n, b: n };                   // a middle bar can't leave a gap: start over from it
    }
    else if (n === p.a - 1 && p.b - n < MAX) A.pick = { a: n, b: p.b };
    else if (n === p.b + 1 && n - p.a < MAX) A.pick = { a: p.a, b: n };
    else { A.pick = { a: n, b: n }; toast(p.b - p.a + 1 >= MAX && (n === p.a - 1 || n === p.b + 1) ? `A lick is at most ${MAX} bars: started a new pick at bar ${n}` : `Started a new pick at bar ${n}`, 1800); }
    paintPick(); draw();
    if (LEARN) LEARN.onPick(A.pick);
  }
  function clearPick() { A.pick = null; paintPick(); draw(); if (LEARN) LEARN.onPick(null); }
  const fitTop = () => document.documentElement.style.setProperty("--al-top", ($("#alSticky").offsetHeight + (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--hdr-h")) || 56) + 14) + "px");
  function paintPick() {
    requestAnimationFrame(fitTop);  // the floating pane changes height with the pick row
    const p = A.pick, box = $("#alPick");
    $$("#alBars .al-bar.picked").forEach(e => e.classList.remove("picked"));
    if (p) for (let n = p.a; n <= p.b; n++) { const el = $(`#alBars .al-bar[data-n="${n}"]`); if (el) el.classList.add("picked"); }
    const len = p ? p.b - p.a + 1 : 0;
    if (len < 1) {
      box.innerHTML = `<span class="small muted">Make a lick: tick the bars (☐) one by one, up to 3 in a row, then Send to library.</span>`;
      return;
    }
    box.innerHTML = `<div class="row"><b>${len > 1 ? `Bars ${p.a}–${p.b}` : `Bar ${p.a}`}</b>
      <span class="small muted">${(A.times[p.b] - A.times[p.a - 1]).toFixed(1)} s</span>
      <span class="al-btns"><button id="alPickA" class="al-ic" aria-label="Play the recording" title="Play these bars: the recording">${ICON_WAVE}</button><button id="alPickM" class="al-ic" aria-label="Play as MIDI" title="Play these bars: your notes as MIDI">${ICON_SQUARE}</button></span>
      <input id="alArtist" placeholder="Musician" title="Musician (filled in from the recording; change it if you like)" style="width:150px">
      <input id="alTune" placeholder="Song name" title="Song name (filled in from the recording; change it if you like)" style="width:170px">
      <input id="alTags" placeholder="tags, e.g. Blues, Minor II V" style="min-width:220px">
      <button class="primary" id="alSend">Send to library</button><span id="alSendMsg" class="small muted"></span></div>`;
    A.meta = A.meta || { artist: A.D.artist || "", tune: A.D.tune || A.D.title || "", tags: "" };  // filled in once; what you type stays while you pick other bars
    $("#alArtist").value = A.meta.artist; $("#alTune").value = A.meta.tune; $("#alTags").value = A.meta.tags; $("#alTags").oninput = e => { A.meta.tags = e.target.value; };
    $("#alArtist").oninput = e => { A.meta.artist = e.target.value; }; $("#alTune").oninput = e => { A.meta.tune = e.target.value; };
    $("#alPickA").onclick = () => playRange(p.a, p.b, "audio");
    $("#alPickM").onclick = () => playRange(p.a, p.b, "midi");
    $("#alSend").onclick = async () => {
      const out = $("#alSendMsg"); out.textContent = "Sending…";
      try {
        const tags = $("#alTags").value.split(",").map(t => t.trim()).filter(Boolean);
        const r = await api(`/api/align/${work}/lick`, { body: { from: p.a, to: p.b, tags, artist: $("#alArtist").value, tune: $("#alTune").value } });
        out.textContent = ""; toast(`Sent: ${r.label}${r.audio ? " (with audio)" : ""}`, 5000);
        S.svgCache.clear();
      } catch (err) { out.textContent = err.message; out.style.color = "var(--again)"; }
    };
  }
  // Chart bar 1 starts the first column: blank cells before the first bar of the chart push it to the left edge
  function padBars() {
    const box = $("#alBars");
    $$(".al-pad", box).forEach(e => e.remove());
    const L = A.D.chart_len, off = A.D.chart_offset;  // the stored chorus starts at chart bar off+1: chart bar 1 sits (L - off) bars in
    if (!L || off == null) return;
    const i = (L - off) % L;
    if (i <= 0 || i >= A.D.bars.length) return;
    const cols = getComputedStyle(box).gridTemplateColumns.split(" ").length || 1;
    const first = $(`.al-bar[data-n="${A.D.bars[0].n}"]`, box);
    for (let k = (cols - i % cols) % cols; k > 0; k--) { const e = document.createElement("div"); e.className = "al-pad"; box.insertBefore(e, first); }
  }
  function renderBars() {
    const box = $("#alBars");
    A.D.bars.forEach(b => {
      let el = $(`.al-bar[data-n="${b.n}"]`, box);
      if (!el) { el = document.createElement("div"); el.className = "al-bar"; el.dataset.n = b.n; box.appendChild(el); }
      const sig = b.abc + "|" + b.locked + "|" + b.chords.join(",") + "|" + (b.issues || []).length;
      if (el.dataset.sig !== sig) { el.dataset.sig = sig; paintBar(b, el); }
    });
    padBars();
  }
  async function reload() { A.D = await api(`/api/align/${work}`); A.times = A.D.times.slice(); renderBars(); draw(); }

  // ------------------------------------------------------------------ toolbar
  if ($("#alDelete")) $("#alDelete").onclick = async () => {
    if (!confirm(`Delete "${D.title}" (${work}) from this computer?\n\nThis removes its recording, transcriptions and your bar edits, and cannot be undone. Licks you already sent to the library stay.`)) return;
    try { await api(`/api/align/${encodeURIComponent(work)}`, { method: "DELETE" }); } catch (err) { toast(err.message); return; }
    toast(`Deleted ${D.title}`);
    try { localStorage.removeItem("alignWork"); } catch (e) { /* storage blocked */ }
    go("transcribe");
  };
  $("#alWork").onchange = e => alignOpen(e.target.value, usable, opts);
  $("#alEngine").onchange = async e => {
    msg("Loading…"); A.D = await api(`/api/align/${work}/engine`, { method: "PUT", body: { engine: e.target.value } });
    A.times = A.D.times.slice(); renderBars(); msg(""); draw();
  };
  $("#alSnap").onclick = async () => {
    anchorBtn(null); msg("Snapping…"); const before = A.times.slice();
    try {
      A.D = await api(`/api/align/${work}/snap`, { method: "POST", body: {} });
      A.times = A.D.times.slice(); renderBars(); draw();
      const d = A.times.map((t, i) => Math.abs(t - before[i])), moved = d.filter(x => x > 0.01).length;
      msg(`Moved ${moved} of ${d.length} bar lines (up to ${Math.round(Math.max(...d) * 1000)} ms) onto the bass`);
    } catch (err) { msg(err.message, true); }
  };
  $("#alCompact").onclick = () => {
    A.compact = !A.compact; layout(A.compact ? "compact" : "full");
    $("#alCompact").textContent = A.compact ? "Full size" : "Compact"; resize();
  };
  $("#alExport").onclick = () => { location.href = `/api/align/${work}/musicxml`; };

  window.__alCleanup = () => { A.tap = null; if (LEARN && LEARN.cleanup) LEARN.cleanup(); audio.pause(); midiStop(); cancelAnimationFrame(A.raf); document.removeEventListener("keydown", A.key); window.removeEventListener("resize", resize); window.removeEventListener("resize", padBars); };
  window.addEventListener("resize", resize);
  window.addEventListener("resize", padBars);
  Object.assign(A, { clearPick, stopAll, playRange, markBar, paintPick, liveBus, midiNotes, showBar: i => { const prev = A.cur; if (i !== A.cur) { A.cur = i; markBar(prev); } } });
  if (LEARN) LEARN.attach(A);
  renderBars(); paintPick(); resize();
}
