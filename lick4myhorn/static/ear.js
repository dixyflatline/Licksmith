/* Learn by ear: pick a stretch of bars in a transcribed song and loop it, as the recording or as MIDI,
   slowed down if you like, with a silent "your turn" after it. It reuses the bar lines saved in the
   Transcribe tab and shows none of the transcribing tools. */
"use strict";

VIEWS.ear = async () => {
  const works = (await api("/api/align")).filter(w => w.has_audio);
  const E = S.ear = S.ear || {};
  if (E.stop) E.stop();                       // coming back to the page: nothing keeps playing from before
  const name = w => w.artist ? `${w.title} — ${w.artist}` : (w.title || w.work);
  if (!works.length) {
    main().innerHTML = `<h1>Learn by ear</h1><div class="card"><p class="muted">No songs yet. Add a recording on the <a href="#transcribe">Transcribe</a> tab, then come back here to loop bars from it.</p></div>`;
    return;
  }
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("earSetup")) || {}; } catch (e) { /* none */ }
  const pref = Object.assign({ work: works[0].work, a: 1, b: 4, speed: 100, source: "rec", listens: 1, turn: true, click: true, countin: false }, saved);
  if (!works.some(w => w.work === pref.work)) pref.work = works[0].work;

  main().innerHTML = `
    <h1>Learn by ear</h1>
    <p class="sub">Choose a song and some bars. They loop, then it is your turn to play them back.</p>
    <div class="card">
      <div class="row">
        <select id="eWork" class="grow">${works.map(w => `<option value="${esc(w.work)}" ${w.work === pref.work ? "selected" : ""}>${esc(name(w))}</option>`).join("")}</select>
      </div>
      <div id="eBody"><div class="empty spin">Loading…</div></div>
    </div>`;
  $("#eWork").onchange = e => { pref.work = e.target.value; pref.a = 1; pref.b = 4; remember(); open(); };
  const remember = () => { try { localStorage.setItem("earSetup", JSON.stringify(pref)); } catch (e) { /* storage blocked */ } };

  let D = null, audio = null, token = 0, playing = false, cur = -1;

  async function open() {
    if (E.stop) E.stop();
    $("#eBody").innerHTML = `<div class="empty spin">Loading…</div>`;
    D = await api(`/api/align/${pref.work}`);
    const n = D.bars.length;
    pref.a = Math.max(1, Math.min(pref.a, n)); pref.b = Math.max(pref.a, Math.min(pref.b, n));
    audio = new Audio(`/api/align/${pref.work}/audio`);
    audio.preload = "auto";
    const hasMidi = D.bars.some(b => b.notes.some(x => x.midi != null));
    if (!hasMidi && pref.source === "midi") pref.source = "rec";
    $("#eBody").innerHTML = `
      <div class="small muted" style="margin:12px 0 6px">Bars <b id="eRange"></b>. Click a bar to start the stretch, click another to end it.</div>
      <div class="ear-strip" id="eStrip">${D.bars.map(b => `<button class="ear-bar" data-n="${b.n}" title="${esc((b.chords || []).join(" "))}">${b.n}<small>${pretty((b.chords || [])[0] || "")}</small></button>`).join("")}</div>
      <div class="row" style="margin:10px 0">
        <label class="check">From bar <input type="number" id="eA" min="1" max="${n}" style="width:70px"></label>
        <label class="check">to bar <input type="number" id="eB" min="1" max="${n}" style="width:70px"></label>
      </div>
      <div class="transport">
        <button class="primary play-btn" id="ePlay" title="Play / stop (space)">▶</button>
        <div><div class="phase" id="ePhase">Ready</div></div>
        <label class="check">Sound <select id="eSource"><option value="rec">Recording</option><option value="midi" ${hasMidi ? "" : "disabled"}>MIDI${hasMidi ? "" : " (no notes)"}</option></select></label>
        <div class="slider">Speed <select id="eSpeed">${[40, 50, 60, 70, 80, 90, 100].map(v => `<option value="${v}">${v}%</option>`).join("")}</select></div>
        <div class="slider">Listen <select id="eListens">${[1, 2, 3, 4].map(v => `<option>${v}</option>`).join("")}</select> × then</div>
        <label class="check"><input type="checkbox" id="eTurn"> Your turn</label>
        <label class="check" title="A soft click on the beats while it is your turn"><input type="checkbox" id="eClick"> Click</label>
        <label class="check" title="One bar of clicks before the first loop"><input type="checkbox" id="eCountin"> Count-in</label>
      </div>`;
    $("#eSource").value = pref.source; $("#eSpeed").value = pref.speed; $("#eListens").value = pref.listens;
    $("#eTurn").checked = pref.turn; $("#eClick").checked = pref.click; $("#eCountin").checked = pref.countin;
    paintRange();

    // choosing the stretch: first click starts it, the second ends it (either order), the third starts over
    let anchor = null;
    $$("#eStrip .ear-bar").forEach(el => el.onclick = () => {
      const k = +el.dataset.n;
      if (anchor == null) { anchor = k; pref.a = pref.b = k; }
      else { pref.a = Math.min(anchor, k); pref.b = Math.max(anchor, k); anchor = null; }
      changed();
    });
    $("#eA").onchange = () => { pref.a = +$("#eA").value; if (pref.b < pref.a) pref.b = pref.a; anchor = null; changed(); };
    $("#eB").onchange = () => { pref.b = +$("#eB").value; if (pref.a > pref.b) pref.a = pref.b; anchor = null; changed(); };
    const bind = (id, key, get) => $("#" + id).addEventListener("change", () => { pref[key] = get($("#" + id)); remember(); });
    bind("eSource", "source", x => x.value); bind("eSpeed", "speed", x => +x.value); bind("eListens", "listens", x => +x.value);
    bind("eTurn", "turn", x => x.checked); bind("eClick", "click", x => x.checked); bind("eCountin", "countin", x => x.checked);
    $("#ePlay").onclick = toggle;
    $("#eStrip .ear-bar.sel") && $("#eStrip .ear-bar.sel").scrollIntoView({ block: "nearest" });
  }

  function changed() {
    pref.a = Math.max(1, Math.min(pref.a, D.bars.length)); pref.b = Math.max(pref.a, Math.min(pref.b, D.bars.length));
    remember(); paintRange();      // a loop that is playing picks the new bars up at its next round
  }
  function paintRange() {
    $("#eRange").textContent = pref.a === pref.b ? `${pref.a}` : `${pref.a}–${pref.b}`;
    $("#eA").value = pref.a; $("#eB").value = pref.b;
    $$("#eStrip .ear-bar").forEach(el => { const k = +el.dataset.n; el.classList.toggle("sel", k >= pref.a && k <= pref.b); });
  }
  function paintNow(bar) {
    if (bar === cur) return;
    cur = bar;
    $$("#eStrip .ear-bar.now").forEach(e => e.classList.remove("now"));
    const el = bar > 0 && $(`#eStrip .ear-bar[data-n="${bar}"]`);
    if (el) { el.classList.add("now"); el.scrollIntoView({ block: "nearest" }); }
  }
  const barAt = t => { const T = D.times; let i = 0; while (i < D.bars.length - 1 && t >= T[i + 1]) i++; return i + 1; };

  // ---------------------------------------------------------------- playing
  const sleep = (ms, my) => new Promise(res => {
    const t0 = performance.now();
    const id = setInterval(() => { if (token !== my || performance.now() - t0 >= ms) { clearInterval(id); res(); } }, 20);
  });
  const midiNotes = (a, b) => {          // the transcribed notes of bars a..b, in seconds of the recording, swung like all playback
    const T = D.times, bpb = D.bpb, sw = (S.settings && S.settings.swing) || 0.62, out = [];
    for (let i = a - 1; i < b; i++) {
      const w = (T[i + 1] - T[i]) / bpb;
      D.bars[i].notes.filter(n => n.midi != null).forEach(n => {
        const s = Band.warp(n.beat, sw), z = Band.warp(n.beat + n.len, sw);
        out.push({ t: T[i] + s * w, d: Math.max(0.08, (z - s) * w * 0.95), m: n.midi });
        (n.graces || []).forEach(g => out.push({ t: T[i] + s * w - 0.07, d: 0.07, m: g }));
      });
    }
    return out;
  };
  const setPhase = (txt) => { const p = $("#ePhase"); if (p) p.textContent = txt; };

  // plays [t0, t1) of the recording at `rate`, resolves when done (or when stopped)
  async function listenRec(t0, t1, rate, my) {
    audio.playbackRate = rate; audio.preservesPitch = true; audio.mozPreservesPitch = true; audio.webkitPreservesPitch = true;
    audio.currentTime = t0;
    try { await audio.play(); } catch (e) { throw new Error("The recording will not play: " + e.message); }
    await new Promise(res => {
      const id = setInterval(() => {
        if (token !== my || audio.currentTime >= t1 - 0.005 || audio.ended) { clearInterval(id); res(); return; }
        paintNow(barAt(audio.currentTime));
      }, 25);
    });
    audio.pause();
  }
  async function listenMidi(a, b, t0, t1, rate, my) {
    const e = Band.engine, now = e.ctx.currentTime + 0.08;
    midiNotes(a, b).forEach(n => e.lead(now + (n.t - t0) / rate, n.d / rate, n.m));
    const start = performance.now(), ms = (t1 - t0) / rate * 1000;
    while (token === my && performance.now() - start < ms) {
      paintNow(barAt(t0 + (performance.now() - start) / 1000 * rate));
      await sleep(30, my);
    }
  }
  // the same length of silence, with soft clicks on the beats
  async function yourTurn(a, b, t0, t1, rate, my, click) {
    const e = Band.engine, T = D.times, bpb = D.bpb, now = e.ctx.currentTime + 0.08;
    if (click) for (let i = a - 1; i < b; i++) for (let j = 0; j < bpb; j++)
      e.click(now + (T[i] + j * (T[i + 1] - T[i]) / bpb - t0) / rate, j === 0);
    const start = performance.now(), ms = (t1 - t0) / rate * 1000;
    while (token === my && performance.now() - start < ms) {
      paintNow(barAt(t0 + (performance.now() - start) / 1000 * rate));
      await sleep(30, my);
    }
  }
  async function countIn(a, rate, my) {
    const e = Band.engine, T = D.times, bpb = D.bpb, spb = (T[a] - T[a - 1]) / bpb / rate, now = e.ctx.currentTime + 0.08;
    for (let j = 0; j < bpb; j++) e.click(now + j * spb, j === 0);
    await sleep(bpb * spb * 1000 + 80, my);
  }

  async function run(my) {
    const e = Band.engine;
    e.init(); if (e.ctx.state === "suspended") await e.ctx.resume();
    e.session();
    let round = 0;
    if (pref.countin) { setPhase("Count-in"); paintNow(0); await countIn(pref.a, pref.speed / 100, my); }
    while (token === my && S.tab === "ear") {
      round++;
      const a = pref.a, b = pref.b, rate = pref.speed / 100, T = D.times, t0 = T[a - 1], t1 = T[b];
      const label = a === b ? `bar ${a}` : `bars ${a}–${b}`;
      for (let k = 1; k <= pref.listens && token === my; k++) {
        setPhase(`Listen ${pref.listens > 1 ? `${k}/${pref.listens} · ` : ""}${label} · round ${round}`);
        if (pref.source === "midi") await listenMidi(a, b, t0, t1, rate, my);
        else await listenRec(t0, t1, rate, my);
      }
      if (token !== my) break;
      if (pref.turn) {
        setPhase(`Your turn · ${label}`);
        await yourTurn(a, b, t0, t1, rate, my, pref.click);
      }
    }
  }
  function stop() {
    token++;
    if (audio) audio.pause();
    const e = Band.engine;
    if (e.ctx && e.cur) { const g = e.cur.bus.gain; g.setValueAtTime(g.value, e.ctx.currentTime); g.linearRampToValueAtTime(0, e.ctx.currentTime + 0.05); }
    playing = false;
    const b = $("#ePlay"); if (b) { b.textContent = "▶"; b.classList.remove("active"); }
    setPhase("Ready"); if (D) paintNow(-1);
  }
  E.stop = stop;
  async function toggle() {
    if (playing) { stop(); return; }
    playing = true; const my = ++token;
    $("#ePlay").textContent = "■"; $("#ePlay").classList.add("active");
    try { await run(my); } catch (err) { toast(err.message); }
    if (token === my) stop();
  }

  if (window.__earKey) document.removeEventListener("keydown", window.__earKey);
  window.__earKey = ev => {
    if (S.tab !== "ear" || !D || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (ev.code === "Space") { ev.preventDefault(); toggle(); }
  };
  document.addEventListener("keydown", window.__earKey);

  // leaving the page ends the loop
  const watch = setInterval(() => { if (S.tab !== "ear") { clearInterval(watch); stop(); } }, 300);
  await open();
};
