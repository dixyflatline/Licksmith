/* Note editor for the lick detail window: the same editing as the Align tab (click/drag notes, rests, MuseScore keys,
   undo) on the lick's ABC text. Edits stay in the page until you press "Save notes to this lick". */
"use strict";

function lickEditor(bg, d, abcBody, setText, onResult) {
  const sheet = $("#abcSheet", bg), msg = $("#abcMsg", bg);
  const SEL = "g.note, g.rest, g.mRest";
  // The drawing also holds grace notes; the edit list does not. Match drawn symbols to events by skipping each event's graces.
  const symbols = () => { const all = $$(SEL, sheet); let p = 0; return E.events.map(ev => { p += ev.grace || 0; return all[p++]; }); };
  const LEN = { "1": "4", "2": "2", "4": "1", "8": "1/2", "6": "1/4" };
  const E = { events: [], sel: null, undo: [], redo: [], queue: Promise.resolve() };
  const say = (t, bad) => { msg.textContent = t || ""; msg.style.color = bad ? "var(--again)" : ""; };

  function play(m, graces) {
    if (m == null) return;
    try { Band.sequencer.stop(true); } catch (e) { /* not playing */ }
    Band.engine.init(); Band.engine.session();  // stopping the lick player silences its bus: take a fresh one
    const t = Band.engine.ctx.currentTime + 0.02, g = (graces || []).length ? 0.08 : 0;
    (graces || []).forEach(gm => Band.engine.lead(t, 0.08, gm));
    Band.engine.lead(t + g, 0.7, m);
  }

  function paintSel() {
    $$(".sel", sheet).forEach(g => g.classList.remove("sel"));
    if (E.sel == null) return;
    const g = symbols()[E.sel];
    if (g) g.classList.add("sel");
  }

  function render(r) {
    sheet.innerHTML = r.svg; E.events = r.events || [];
    sheet.classList.add("al-svg");
    const svg = $("svg", sheet); if (!svg) return;
    const els = symbols();
    const rows = $$("g.staff > path", sheet).map(p => p.getBoundingClientRect()).filter(b => b.width > 20).map(b => b.top).sort((a, c) => a - c);
    const space = rows.length >= 2 ? (rows[rows.length - 1] - rows[0]) / (rows.length - 1) / 2 : 6;
    els.forEach((g, index) => {
      const isRest = !g.classList.contains("note");
      if (isRest) {
        try {
          const bb = g.getBBox(), pad = Math.max(bb.width, bb.height) * 0.35;
          const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
          hit.setAttribute("class", "hit");
          hit.setAttribute("x", bb.x - pad); hit.setAttribute("y", bb.y - pad);
          hit.setAttribute("width", bb.width + 2 * pad); hit.setAttribute("height", bb.height + 2 * pad);
          g.insertBefore(hit, g.firstChild);
        } catch (e) { /* not rendered */ }
      }
      g.style.cursor = isRest ? "pointer" : "ns-resize";
      g.addEventListener("pointerdown", e => {
        e.preventDefault();
        const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur();
        E.sel = index; paintSel();
        if (isRest) return;
        g.setPointerCapture(e.pointerId);
        const y0 = e.clientY; let steps = 0;
        play((E.events[index] || {}).midi, (E.events[index] || {}).graces);
        const k = svg.viewBox.baseVal.width / svg.getBoundingClientRect().width;
        const mv = ev => { steps = Math.round(-(ev.clientY - y0) / space); g.style.transform = `translateY(${-steps * space * k}px)`; };
        const up = ev => {
          g.removeEventListener("pointermove", mv); g.removeEventListener("pointerup", up); g.style.transform = "";
          if (steps) edit(ev.shiftKey ? "semitone" : "step", steps);
        };
        g.addEventListener("pointermove", mv); g.addEventListener("pointerup", up);
      });
    });
    paintSel();
  }

  async function refresh() {
    try { const r = await api(`/api/licks/${d.id}/abc/preview`, { body: abcBody() }); render(r); onResult(r); }
    catch (err) { say(err.message, true); }
  }

  function edit(op, value) {
    if (E.sel == null) return;
    const index = E.sel;
    E.queue = E.queue.then(async () => {
      try {
        const before = abcBody().abc;
        const r = await api(`/api/licks/${d.id}/abc/edit`, { body: { ...abcBody(), index, op, value } });
        E.undo.push(before); E.redo.length = 0;
        setText(r.abc); render(r); onResult(r);
        if (op !== "rest") play((r.events[index] || {}).midi, (r.events[index] || {}).graces);
        $("#abcSave", bg).classList.add("dirty"); say("Edited. Press Save to keep it, or ⌘Z to undo.");
      } catch (err) { say(err.message, true); }
    });
  }

  function step(from, to) {
    E.queue = E.queue.then(async () => {
      if (!from.length) return;
      to.push(abcBody().abc); setText(from.pop()); await refresh();
    });
  }

  const onKey = e => {
    if (!document.body.contains(bg)) { document.removeEventListener("keydown", onKey); return; }
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const cmd = e.metaKey || e.ctrlKey, k = e.key;
    if (cmd && (k === "z" || k === "Z" || k === "y")) { e.preventDefault(); (k === "y" || e.shiftKey) ? step(E.redo, E.undo) : step(E.undo, E.redo); return; }
    if (cmd && k === "s") { e.preventDefault(); $("#abcSave", bg).click(); return; }
    if (k === "Escape" && E.sel != null) { E.sel = null; paintSel(); e.stopPropagation(); return; }
    if (k === "ArrowLeft" || k === "ArrowRight") {
      const n = E.events.length; if (!n) return;
      e.preventDefault(); E.sel = E.sel == null ? 0 : Math.max(0, Math.min(n - 1, E.sel + (k === "ArrowLeft" ? -1 : 1)));
      paintSel(); play((E.events[E.sel] || {}).midi, (E.events[E.sel] || {}).graces); return;
    }
    if (E.sel == null) return;
    const up = k === "ArrowUp", down = k === "ArrowDown";
    if (up || down) { e.preventDefault(); const dd = up ? 1 : -1; cmd ? edit("octave", dd) : e.altKey ? edit("step", dd) : edit("semitone", dd); }
    else if (cmd && k === "3") { e.preventDefault(); edit("triplet"); }
    else if (!cmd && !e.altKey && /^[a-gA-G]$/.test(k)) { e.preventDefault(); edit("letter", k.toUpperCase()); }
    else if (!cmd && LEN[k]) { e.preventDefault(); edit("length", LEN[k]); }
    else if (!cmd && (k === "t" || k === "T")) { e.preventDefault(); edit("tie"); }
    else if (!cmd && k === ".") { e.preventDefault(); edit("dot"); }
    else if (!cmd && k === "/") { e.preventDefault(); edit("grace", { slash: true }); }
    else if (!cmd && k === "?") { e.preventDefault(); edit("grace", { slash: false }); }
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); edit("rest"); }
  };
  document.addEventListener("keydown", onKey);

  // Highlight the note sounding at q quarter-beats from the start of the lick (null = none).
  function mark(q) {
    $$(".play", sheet).forEach(g => g.classList.remove("play"));
    if (q == null) return;
    let acc = 0, idx = -1;
    E.events.forEach((ev, i) => { if (acc <= q + 1e-6) idx = i; acc += ev.len; });
    const g = idx >= 0 && symbols()[idx];
    if (g) g.classList.add("play");
  }
  // Highlight by place in the lick: bar index (0-based) and quarter-beats into that bar (null = none).
  function markAt(bar, beats) {
    $$(".play", sheet).forEach(g => g.classList.remove("play"));
    if (bar == null) return;
    let idx = -1;
    E.events.forEach((ev, i) => { if (ev.bar - 1 < bar || (ev.bar - 1 === bar && ev.beat <= beats + 1e-6)) idx = i; });
    const g = idx >= 0 && symbols()[idx];
    if (g) g.classList.add("play");
  }
  return { refresh, mark, markAt };
}
