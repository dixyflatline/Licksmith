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

  let A = null, token = 0, running = false;
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
  async function turn(a, b, rate, my) {
    const e = Band.engine, T = A.times, bpb = A.D.bpb, t0 = T[a - 1], t1 = T[b];
    A.liveBus();
    const now = e.ctx.currentTime + 0.08;
    if (pref.click) for (let i = a - 1; i < b; i++) for (let j = 0; j < bpb; j++)
      e.click(now + (T[i] + j * (T[i + 1] - T[i]) / bpb - t0) / rate, j === 0);
    const start = performance.now(), ms = (t1 - t0) / rate * 1000;
    while (token === my && performance.now() - start < ms) {
      const t = t0 + (performance.now() - start) / 1000 * rate;
      let i = a - 1; while (i < b - 1 && t >= T[i + 1]) i++;
      A.showBar(i);
      await sleep(30, my);
    }
  }
  async function countIn(a, rate, my) {
    const e = Band.engine, T = A.times, bpb = A.D.bpb, spb = (T[a] - T[a - 1]) / bpb / rate;
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
      if (pref.countin) { setPhase("Count-in"); await countIn(first.a, A.rate, my); }
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
        if (pref.turn) { setPhase(`Your turn · ${label}`); await turn(r.a, r.b, A.rate, my); }
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
    onPick: p => { if (p) { pref.a = p.a; pref.b = p.b; remember(); } },
    cleanup: () => { stop(); },
    attach(a) {
      A = a;
      if (pref.work !== A.work) { pref.work = A.work; pref.a = 1; pref.b = Math.min(4, A.D.bars.length); }
      pref.a = Math.max(1, Math.min(pref.a, A.D.bars.length)); pref.b = Math.max(pref.a, Math.min(pref.b, A.D.bars.length));
      A.pick = { a: pref.a, b: pref.b };
      remember();
      const hasMidi = A.D.bars.some(b => b.notes.some(x => x.midi != null));
      if (!hasMidi && pref.source === "midi") pref.source = "rec";
      $("#alLearn").innerHTML = `<div class="ea-bar">
        <button class="primary play-btn" id="eaPlay" title="Play / stop (space)">▶</button>
        <div class="phase" id="eaPhase">Ready</div>
        <label class="check">Sound <select id="eaSource"><option value="rec">Recording</option><option value="midi" ${hasMidi ? "" : "disabled"}>MIDI</option></select></label>
        <label class="check">Speed <select id="eaSpeed">${[40, 50, 60, 70, 80, 90, 100].map(v => `<option value="${v}">${v}%</option>`).join("")}</select></label>
        <label class="check">Listen <select id="eaListens">${[1, 2, 3, 4].map(v => `<option>${v}</option>`).join("")}</select> ×</label>
        <label class="check"><input type="checkbox" id="eaTurn"> Your turn</label>
        <label class="check" title="A soft click on the beats while it is your turn"><input type="checkbox" id="eaClick"> Click</label>
        <label class="check" title="One bar of clicks before the first loop"><input type="checkbox" id="eaCountin"> Count-in</label>
      </div>`;
      $("#eaSource").value = pref.source; $("#eaSpeed").value = pref.speed; $("#eaListens").value = pref.listens;
      $("#eaTurn").checked = pref.turn; $("#eaClick").checked = pref.click; $("#eaCountin").checked = pref.countin;
      const bind = (id, key, get) => $("#" + id).addEventListener("change", () => { pref[key] = get($("#" + id)); remember(); });
      bind("eaSource", "source", x => x.value); bind("eaSpeed", "speed", x => +x.value); bind("eaListens", "listens", x => +x.value);
      bind("eaTurn", "turn", x => x.checked); bind("eaClick", "click", x => x.checked); bind("eaCountin", "countin", x => x.checked);
      $("#eaPlay").onclick = () => ctl.toggle();
      setPlayBtn();
    },
  };
  // leaving the tab ends the loop and releases the keyboard
  const watch = setInterval(() => {
    if (S.tab !== "learn") { clearInterval(watch); stop(); if (window.__alCleanup) window.__alCleanup(); }
  }, 300);

  main().innerHTML = `<h1>Learn</h1><div id="trWork"></div>`;
  await alignOpen(pref.work, works, { learn: ctl });
};
