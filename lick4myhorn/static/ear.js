/* Learn tab: the Transcribe tab's bar cards (tick bars, chorus and bar number, A▶ / M▶, score with chords,
   note editing with all its keys) without the transcribing tools, plus a loop controller: the ticked bars
   play as the recording or as MIDI, optionally slowed down, followed by a silent "your turn".
   The cards, editing and playback machinery live in align.js (alignOpen with {learn}); this file only
   decides what plays when. */
"use strict";

VIEWS.learn = async () => {
  const works = (await api("/api/align")).filter(w => w.has_audio && w.engines.length);
  if (!works.length) {
    main().innerHTML = `<h1>Learn</h1><div class="card"><p class="muted">No songs yet. Add a recording on the <a href="#transcribe">Transcribe</a> tab, then come back here to loop bars from it.</p></div>`;
    return;
  }
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("earSetup")) || {}; } catch (e) { /* none */ }
  const pref = Object.assign({ work: works[0].work, a: 1, b: 4, speed: 100, source: "rec", listens: 1, turn: true, click: true, countin: false }, saved);
  const remember = () => { try { localStorage.setItem("earSetup", JSON.stringify(pref)); } catch (e) { /* storage blocked */ } };
  if (!works.some(w => w.work === pref.work)) pref.work = works[0].work;

  let A = null, token = 0, running = false, bpm0 = 120;   // bpm0: the song's average tempo from its bar lines
  const curBpm = () => bpm0 * pref.speed / 100;
  const setPhase = t => { const p = $("#eaPhase"); if (p) p.textContent = t; };
  const setPlayBtn = () => {
    const b = $("#eaPlay"); if (!b) return;
    b.textContent = running ? "■" : "▶"; b.classList.toggle("active", running);
  };
  // waits ms, or until stopped
  const sleep = (ms, my) => new Promise(res => {
    const t0 = performance.now();
    const id = setInterval(() => { if (token !== my || performance.now() - t0 >= ms) { clearInterval(id); res(); } }, 20);
  });
  // plays bars a..b once through align.js's own player (so the cursor, bar highlight and note lighting work), resolves at its end
  const segment = (a, b, mode, my) => new Promise(res => {
    const t0 = Date.now();
    let done = false, id = null;
    const fin = () => { if (!done) { done = true; clearInterval(id); A.onEnd = null; res(); } };
    A.onEnd = fin;
    A.playRange(a, b, mode === "midi" ? "midi" : "audio");
    id = setInterval(() => {   // stopped from outside, or the player never started
      if (token !== my || (!A.playing && Date.now() - t0 > 2500)) fin();
    }, 100);
  });
  // One steady tempo for clicks, count-in and the turn, whatever the bar lines do: the tempo slider.
  async function turn(a, b, my) {
    const e = Band.engine, bpb = A.D.bpb, spb = 60 / curBpm(), n = (b - a + 1) * bpb;
    A.liveBus();
    const now = e.ctx.currentTime + 0.08;
    if (pref.click) for (let k = 0; k < n; k++) e.click(now + k * spb, k % bpb === 0);
    const start = performance.now(), ms = n * spb * 1000;
    while (token === my && performance.now() - start < ms) {
      A.showBar(a - 1 + Math.min(b - a, Math.floor((performance.now() - start) / 1000 / spb / bpb)));
      await sleep(30, my);
    }
  }
  async function countIn(my) {
    const e = Band.engine, bpb = A.D.bpb, spb = 60 / curBpm();
    A.liveBus();
    const now = e.ctx.currentTime + 0.08;
    for (let j = 0; j < bpb; j++) e.click(now + j * spb, j === 0);
    await sleep(bpb * spb * 1000 + 80, my);
  }

  // span: {a, b} to loop that one stretch, or null to follow the ticked bars; mode: "rec" or "midi"
  async function start(span, mode) {
    stop();
    const my = ++token;
    running = true; setPlayBtn();
    const Ar = A;                                      // the song may change while this runs
    try {
      const first = span || Ar.pick;
      if (!first) { toast("Tick some bars first."); return; }
      A.rate = pref.speed / 100;
      Band.engine.init(); if (Band.engine.ctx.state === "suspended") await Band.engine.ctx.resume();
      let round = 0;
      while (token === my && S.tab === "learn" && A === Ar) {
        const r = span || A.pick;
        if (!r) break;
        round++;
        A.rate = pref.speed / 100;
        const label = r.a === r.b ? `bar ${r.a}` : `bars ${r.a}–${r.b}`;
        for (let k = 1; k <= pref.listens && token === my; k++) {
          setPhase(`Listen ${pref.listens > 1 ? `${k}/${pref.listens} · ` : ""}${label} · round ${round}`);
          await segment(r.a, r.b, mode, my);
        }
        if (token !== my) break;
        if (pref.turn) {
          if (pref.countin) { setPhase(`Count-in · ${label}`); await countIn(my); if (token !== my) break; }
          setPhase(`Your turn · ${label}`); await turn(r.a, r.b, my);
        }
      }
    } catch (err) { toast(err.message); }
    if (token === my) stop();
  }
  function stop() {
    token++;
    if (A) { A.onEnd = null; A.stopAt = null; A.stopAll(); A.rate = 1; }
    running = false; setPlayBtn(); setPhase("Ready");
  }

  const ctl = {
    toggle: () => { if (running) stop(); else start(null, pref.source); },
    playSpan: (span, mode) => { start({ a: span.a, b: span.b }, mode); },
    onPick: p => { if (p) { pref.a = p.a; pref.b = p.b; remember(); } else { pref.a = pref.b = 0; remember(); } },
    cleanup: () => { stop(); unblank(); },
    attach(a) {
      A = a;
      document.removeEventListener("click", blank); document.addEventListener("click", blank);   // cleanup() removes it, and a song change cleans up first
      { const T = A.times, nb = T.length - 1; bpm0 = Math.max(30, nb * A.D.bpb * 60 / Math.max(1, T[nb] - T[0])); }
      if (pref.work !== A.work) { pref.work = A.work; pref.a = 1; pref.b = Math.min(4, A.D.bars.length); }
      if (pref.a >= 1) { pref.a = Math.min(pref.a, A.D.bars.length); pref.b = Math.max(pref.a, Math.min(pref.b, A.D.bars.length)); A.pick = { a: pref.a, b: pref.b }; }
      else A.pick = null;
      remember();
      const hasMidi = A.D.bars.some(b => b.notes.some(x => x.midi != null));
      if (!hasMidi && pref.source === "midi") pref.source = "rec";
      $("#alLearn").innerHTML = `
        <div class="ea-bar" id="eaRow1">
          <span id="eaSong"></span>
          <label class="check">Sound <select id="eaSource"><option value="rec">Recording</option><option value="midi" ${hasMidi ? "" : "disabled"}>MIDI</option></select></label>
          <label class="check">Listen <select id="eaListens">${[1, 2, 3, 4].map(v => `<option>${v}</option>`).join("")}</select> ×</label>
          <label class="check"><input type="checkbox" id="eaTurn"> Your turn</label>
          <label class="check" title="A soft click on the beats while it is your turn"><input type="checkbox" id="eaClick"> Click</label>
          <label class="check" title="One bar of clicks at the tempo, right before each of your turns"><input type="checkbox" id="eaCountin"> Count-in</label>
        </div>
        <div class="ea-bar" id="eaRow2" style="margin-top:10px">
          <button class="primary play-btn" id="eaPlay" title="Play / stop (space)">▶</button>
          <label class="check" title="Drag to set the tempo. It slows the recording (pitch unchanged), and sets the clicks and the length of your turn.">Tempo <input type="range" id="eaTempo" min="${Math.round(bpm0 * 0.3)}" max="${Math.round(bpm0 * 1.2)}" step="1" style="width:150px"> <b id="eaBpm" style="min-width:64px;display:inline-block">♩ = ${Math.round(curBpm())}</b></label>
          <button id="eaClear" class="ea-gold" title="Untick all bars (clicking the empty space beside the cards does the same)">Clear selection</button>
          <span id="eaEdit" class="al-btns"></span>
          <div class="phase" id="eaPhase">Ready</div>
        </div>`;
      // reuse the editor's own song menu, undo/redo and message (they keep their handlers), arranged into the two rows
      const sticky = $("#alSticky"), oldRow = $(".row", sticky);
      $("#eaSong").appendChild($("#alWork"));
      const edit = $("#eaEdit");
      const undo = $("#alUndo"), redo = $("#alRedo");
      undo.textContent = "↶ Undo"; redo.textContent = "↷ Redo";
      [undo, redo].forEach(b => { b.classList.add("ea-gold"); edit.appendChild(b); });
      edit.appendChild($("#alMsg"));
      oldRow.style.display = "none";
      $("#eaSource").value = pref.source; $("#eaTempo").value = Math.round(curBpm()); $("#eaListens").value = pref.listens;
      $("#eaTurn").checked = pref.turn; $("#eaClick").checked = pref.click; $("#eaCountin").checked = pref.countin;
      const bind = (id, key, get) => $("#" + id).addEventListener("change", () => { pref[key] = get($("#" + id)); remember(); });
      bind("eaSource", "source", x => x.value); $("#eaTempo").addEventListener("input", () => {
        pref.speed = Math.max(30, Math.min(120, +$("#eaTempo").value / bpm0 * 100)); A.rate = running ? pref.speed / 100 : 1;
        $("#eaBpm").textContent = `♩ = ${Math.round(curBpm())}`; remember();
        if (running && A.mode === "audio") { const au = A.audio; if (au) au.playbackRate = pref.speed / 100; }
      });
      bind("eaListens", "listens", x => +x.value);
      bind("eaTurn", "turn", x => x.checked); bind("eaClick", "click", x => x.checked); bind("eaCountin", "countin", x => x.checked);
      $("#eaPlay").onclick = () => ctl.toggle();
      $("#eaClear").onclick = () => A.clearPick();
      setPlayBtn();
    },
  };
  // a click on the empty space beside or between the cards clears the selection
  const blank = ev => {
    if (S.tab !== "learn" || !A) return;
    const t = ev.target;
    if (t === document.body || t === document.documentElement || t === $("#main") || t === $("#alBars") || (t.classList && t.classList.contains("al-learn"))) A.clearPick();
  };
  const unblank = () => document.removeEventListener("click", blank);
  // leaving the tab ends the loop and releases the keyboard
  const watch = setInterval(() => {
    if (S.tab !== "learn") { clearInterval(watch); unblank(); stop(); if (window.__alCleanup) window.__alCleanup(); }
  }, 300);

  main().innerHTML = `<h1>Learn</h1><div id="trWork"></div>`;
  await alignOpen(pref.work, works, { learn: ctl });
};
