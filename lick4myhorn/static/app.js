/* Licksmith web UI (no build step, no external libraries). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const main = () => $("#main");

async function api(path, opts = {}) {
  const o = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  if (ct.includes("json")) return r.json();
  return r.text();
}

function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

const S = { settings: null, meta: null, tab: "routine", practice: null, fit: null, svgCache: new Map() };

/* ---------------------------------------------------------------- music helpers */
const NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const SIMPLE_ROOTS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const LETTER_PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const midiName = m => NOTE_NAMES[m % 12] + (Math.floor(m / 12) - 1);
function nameMidi(s) {
  const m = /^\s*([A-Ga-g])([b#]?)(-?\d)\s*$/.exec(s || "");
  if (!m) return null;
  return LETTER_PC[m[1].toUpperCase()] + (m[2] === "b" ? -1 : m[2] === "#" ? 1 : 0) + (parseInt(m[3]) + 1) * 12;
}
function transposeSym(sym, semis) {
  if (!semis || !sym || sym.startsWith("N.C")) return sym;
  const m = /^([A-G])([b#]?)(.*?)(?:\/([A-G])([b#]?))?$/.exec(sym);
  if (!m) return sym;
  const mv = (l, a) => SIMPLE_ROOTS[(LETTER_PC[l] + (a === "b" ? -1 : a === "#" ? 1 : 0) + semis + 24) % 12];
  return mv(m[1], m[2]) + m[3] + (m[4] ? "/" + mv(m[4], m[5]) : "");
}
const pretty = s => esc(s || "").replace(/^([A-G])b/, "$1♭").replace(/^([A-G])#/, "$1♯")
  .replace(/b(\d)/g, "♭$1").replace(/#(\d)/g, "♯$1").replace(/\/([A-G])b/, "/$1♭").replace(/\/([A-G])#/, "/$1♯");
const written = () => (S.settings || {}).display !== "concert";
const showSym = s => pretty(written() ? transposeSym(s, 2) : s);
const keyText = l => pretty((written() ? transposeSym(l.tonic_name, 2) : l.tonic_name) + (l.mode === "minor" ? "m" : ""));
// The keys of a lick: the working set, and the keys that have been Good (so a lick that is Good in two keys never looks "known" in all 12).
const GOOD_TEXT = gd => `Good means ${gd.clean_passes} clean pass${gd.clean_passes == 1 ? "" : "es"} in a row in every key of your set${gd.min_tempo ? ` at ${gd.min_tempo} bpm or faster` : ""}. ${gd.sessions} Good rating${gd.sessions == 1 ? "" : "s"} in a row add${gd.sessions == 1 ? "s" : ""} ${gd.add_keys} more key${gd.add_keys == 1 ? "" : "s"}; ${gd.stuck_after} Hard/Again in a row shelves the lick for ${gd.shelve_days} days.`;
const keysLine = l => {
  const known = (l.known_labels || []).map(k => k.key), set = l.scope_labels || [];
  if (!set.length) return "";
  const n = known.length;
  return `<div class="small muted" title="${esc((l.known_labels || []).map(k => `${k.key}: Good on ${k.on}${k.bpm ? " at " + k.bpm + " bpm" : ""}`).join("; "))}">Working on ${esc(set.join(", "))}${n ? ` · Good in ${esc(known.join(", "))}` : " · not Good in any key yet"}${n && n < 12 ? ` · ${12 - n} key${12 - n === 1 ? "" : "s"} not learned` : ""}</div>`;
};
const stateBadge = st => `<span class="badge ${esc(st)}">${esc(st)}</span>`;
function dueText(iso) {
  if (!iso) return "";
  const d = Math.round((new Date(iso) - new Date()) / 86400000);
  if (d <= 0) return "due";
  return d === 1 ? "tomorrow" : `in ${d} days`;
}
function tagChips(l, max = 99) {
  const user = (l.tags || []).map(t => `<span class="chip user">${esc(t)}</span>`);
  const auto = (l.auto_tags || []).filter(t => !(l.tags || []).includes(t)).map(t => `<span class="chip">${esc(t)}</span>`);
  return user.concat(auto).slice(0, max).join("");
}

/* ---------------------------------------------------------------- quick listen */
let listenBtn = null;
async function listen(btn, getData) {
  const seq = Band.sequencer;
  const was = listenBtn;
  if (seq.running) seq.stop();
  if (was === btn) return;  // second click on the same button stops
  listenBtn = btn;
  btn.textContent = "■";
  let data;
  try { data = await getData(); } catch (e) { toast(e.message); btn.textContent = btn.dataset.idle || "▶"; listenBtn = null; return; }
  seq.start({
    tempo: () => S.settings.tempo, swing: () => S.settings.swing,
    next: n => n === 0 ? { seq: data, leadIn: 0, playLick: true, backing: S.settings.backing ? "piano" : false, response: false, info: {} } : null,
    onStop: () => { btn.textContent = btn.dataset.idle || "▶"; if (listenBtn === btn) listenBtn = null; },
  });
}
const playBtn = (attr, id) => `<button class="ghost listen" title="Listen" ${attr}="${id}">▶</button>`;
function wireListen(root = document) {
  $$("[data-listen]", root).forEach(b => b.onclick = e => {
    e.stopPropagation();
    listen(b, () => api(`/api/licks/${b.dataset.listen}/play`));
  });
}

// The header wraps onto two lines on phones; sticky panes below it (Transcribe) need its real height.
(() => {
  const h = $("header.top"); if (!h) return;
  const set = () => document.documentElement.style.setProperty("--hdr-h", h.offsetHeight + "px");
  set(); window.addEventListener("resize", set);
  if (window.ResizeObserver) new ResizeObserver(set).observe(h);
})();

/* ---------------------------------------------------------------- router */
const VIEWS = {};
async function go(tab) {
  if (tab === "align") tab = "transcribe";  // the page used to be called Align
  if (tab === "today") tab = "licks";        // the Today tab is now called Licks
  if (tab === "tunes") tab = "play";         // the Tunes tab is now called Play
  if ((S.tab === "transcribe" || S.tab === "learn") && tab !== S.tab && window.__alCleanup) window.__alCleanup();
  if (tab !== "practice" && Band.sequencer.running) Band.sequencer.stop(true);
  S.tab = tab;
  $$("nav.tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === tab));
  main().innerHTML = `<div class="empty spin">Loading…</div>`;
  try { await (VIEWS[tab] || VIEWS.routine)(); }
  catch (e) { main().innerHTML = `<div class="card"><b>Something went wrong.</b><p class="muted">${esc(e.message)}</p></div>`; }
}
window.addEventListener("hashchange", () => {
  let t = location.hash.slice(1) || "routine";
  if (t === "align") t = "transcribe";
  if (t === "today") t = "licks";
  if (t === "tunes") t = "play";
  if (t !== S.tab) go(t);
});
// Back / forward. A tab is a history entry (its #hash); so is an open lick window ({lick, tab}) and a practice session
// ({practice: {ids, idx}}), which is what lets you walk library → lick → practice → tunes and back again.
window.addEventListener("popstate", async e => {
  const st = e.state || {};
  if (Band.sequencer.running && !st.practice) Band.sequencer.stop(true);
  const modal = $(".modal-bg");
  if (modal) modal.remove();
  if (st.lick) {
    if (st.tab && st.tab !== S.tab) await go(st.tab);
    openLick(st.lick, "none");
    return;
  }
  if (st.practice) {
    const P = S.practice;
    if (!P || P.ids.join() !== st.practice.ids.join()) S.practice = { ids: st.practice.ids, idx: st.practice.idx, results: [], hardOnly: !!st.practice.hardOnly };
    else P.idx = st.practice.idx;
    S.tab = "practice";
    $$("nav.tabs a").forEach(a => a.classList.remove("active"));
    await loadPracticeLick();
    return;
  }
  let t = location.hash.slice(1) || "routine";
  if (t === "align") t = "transcribe";
  if (t === "today") t = "licks";
  if (t === "tunes") t = "play";
  if (t !== S.tab || modal) go(VIEWS[t] ? t : "routine");
});
function wireNavArrows() {
  const back = $("#navBack"), fwd = $("#navFwd");
  back.onclick = () => history.back();
  fwd.onclick = () => history.forward();
  const nav = window.navigation;  // where the browser says whether there is anything to go back or forward to
  const sync = () => { if (nav) { back.disabled = !nav.canGoBack; fwd.disabled = !nav.canGoForward; } };
  if (nav) { nav.addEventListener("currententrychange", sync); sync(); }
}

async function svgFor(lickId, pc) {
  const key = `${lickId}:${pc}:${S.settings.display}`;
  if (!S.svgCache.has(key)) S.svgCache.set(key, await api(`/api/licks/${lickId}/svg?pc=${pc}`));
  return S.svgCache.get(key);
}

/* ---------------------------------------------------------------- Licks (was Today) */
VIEWS.licks = async () => {
  const [t, hard] = await Promise.all([api("/api/today"), api("/api/hard").catch(() => ({ licks: [], by_key: {} }))]);
  const queue = [...t.focus, ...t.due, ...t.new];
  const mine = new Set(t.chosen);
  const row = (l, kind) => `
    <div class="lick-item">
      ${playBtn("data-listen", l.id)}
      <div class="num">#${l.number}</div>
      <div class="grow"><div class="title">${esc(l.label)}</div>
        <div class="small muted">from the key of ${keyText(l)}</div>${keysLine(l)}
        <div>${tagChips(l, 6)}</div></div>
      ${kind === "focus" || kind === "wait" ? `<span class="badge" title="${mine.has(l.id) ? "You started this one yourself and rated it Hard or Again: it comes first" : "Chosen by the schedule"}">${mine.has(l.id) ? "mine" : "hard"}</span>` : ""}
      ${stateBadge(kind === "new" ? "new" : l.state)}
      ${kind === "shelf" ? `<span class="muted small">until ${esc(l.shelved_until)}</span><button data-unshelve="${l.id}">Bring back</button>` : `<button data-practice="${l.id}">Practice</button>`}
    </div>`;
  main().innerHTML = `
    <h1>Licks</h1>
    <p class="sub">Today's session: a small focus of hard licks first, then reviews that are going well${t.new_held ? `. New licks wait (${t.new_remaining}) until the hard ones are rated Good` : t.new_remaining > t.new.length ? ` (${t.new_remaining} new licks waiting)` : ""}.</p>
    <div class="hero">
      <div class="tile"><div class="n">${t.focus.length}</div><div class="l">hard, in focus</div></div>
      <div class="tile"><div class="n">${t.due.length}</div><div class="l">due for review</div></div>
      <div class="tile"><div class="n">${t.new.length}</div><div class="l">new today</div></div>
      <div class="tile"><div class="n">${t.done_today.length}</div><div class="l">practised today</div></div>
      <div class="grow"></div>
      <div class="row">${queue.length ? `<button class="primary big" id="startSession">Start session (${queue.length})</button>` :
        `<div class="muted">Nothing due. <a href="#library">Pick a lick</a> or <a href="#play">fit licks to a tune</a>.</div>`}
        ${hard.licks.length ? `<button class="big" id="hardSession" title="Only the keys you marked hard, in the licks where you marked them">Practice hard keys (${hard.licks.length} lick${hard.licks.length === 1 ? "" : "s"})</button>` : ""}</div>
    </div>
    ${t.focus.length ? `<h2>Focus</h2><p class="small muted">${t.focus_total} hard lick${t.focus_total === 1 ? "" : "s"} for today. Get these to Good before anything new.</p><div class="lick-list">${t.focus.map(l => row(l, "focus")).join("")}</div>` : ""}
    ${t.due.length ? `<h2>Due</h2><div class="lick-list">${t.due.map(l => row(l, "due")).join("")}</div>` : ""}
    ${t.new.length ? `<h2>New</h2><div class="lick-list">${t.new.map(l => row(l, "new")).join("")}</div>` : ""}
    ${t.shelved.length ? `<details style="margin-top:14px"><summary class="muted">Shelved: ${t.shelved.length} lick${t.shelved.length === 1 ? "" : "s"} resting after repeated Hard/Again (cut back to one key)</summary><div class="lick-list">${t.shelved.map(l => row(l, "shelf")).join("")}</div></details>` : ""}
    ${t.backlog.length ? `<details style="margin-top:14px"><summary class="muted">Waiting: ${t.backlog.length} more hard lick${t.backlog.length === 1 ? "" : "s"} (today's focus is full)</summary><div class="lick-list">${t.backlog.map(l => row(l, "wait")).join("")}</div></details>` : ""}
    ${!queue.length && !t.done_today.length ? `<div class="card" style="margin-top:20px">
      <h3>Getting started</h3><p class="muted">Import a lick sheet (MusicXML from MuseScore) on the <a href="#import">Import</a> page.
      One file can hold many licks: start each with a text like <i>Lick 43, Blues, Major II V, taken from Chet Baker's 1956 Tenderly</i>
      or separate them with double barlines. Then import your iReal playlist to fit licks into tunes.</p></div>` : ""}`;
  const start = $("#startSession");
  if (start) start.onclick = () => startPractice(queue.map(l => l.id), 0);
  const hs = $("#hardSession");
  if (hs) hs.onclick = () => startPractice(hard.licks.map(l => l.id), 0, { hardOnly: true });
  $$("[data-practice]").forEach(b => b.onclick = () => startPractice([+b.dataset.practice], 0));
  $$("[data-unshelve]").forEach(b => b.onclick = async () => { await api(`/api/licks/${b.dataset.unshelve}/unshelve`, { method: "POST", body: {} }); VIEWS.licks(); });
  wireListen();
};

/* ---------------------------------------------------------------- Routine (daily practice checklist) */
const localDay = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const newId = () => "i" + Math.random().toString(36).slice(2, 9);

VIEWS.routine = async () => {
  const day = localDay();
  const R = await api(`/api/routine?day=${day}`);
  const done = new Set(R.checked);
  const notes = { ...R.notes };
  const open = new Set(Object.keys(notes));   // items whose comment box is showing
  const log = await api("/api/routine/log?days=60").catch(() => []);
  const draw = () => {
    const n = done.size, pct = R.total ? Math.round(100 * n / R.total) : 0;
    const strip = Array.from({ length: 7 }, (_, i) => {
      const d = new Date(); d.setDate(d.getDate() - (6 - i));
      const k = localDay(d), c = R.history[k] || 0;
      const full = R.total && c >= R.total;
      return `<div class="rday ${full ? "full" : c ? "some" : ""}" title="${k}: ${c} of ${R.total}"><b>${d.toLocaleDateString(undefined, { weekday: "narrow" })}</b><i></i></div>`;
    }).join("");
    main().innerHTML = `
      <div class="row"><h1 class="grow">Practice routine</h1><a class="btn" href="/api/routine/export.csv" download title="All ticks and notes as a spreadsheet (CSV)">Export CSV</a><button id="rEdit">Edit routine</button></div>
      <p class="sub">Everything you do every practice session. Ticks reset each day.</p>
      <div class="hero">
        <div class="tile"><div class="n">${n}/${R.total}</div><div class="l">done today</div></div>
        <div class="grow"><div class="rbar"><div style="width:${pct}%"></div></div></div>
        <div class="rweek" title="Last 7 days">${strip}</div>
        ${n ? `<button class="ghost" id="rClear">Clear today</button>` : ""}
      </div>
      ${R.sections.map(sec => {
        const c = sec.items.filter(i => done.has(i.id)).length;
        return `<div class="card rsec"><div class="row"><h3 class="grow">${esc(sec.title)}</h3>
          <span class="small muted">${c}/${sec.items.length}</span></div>
          ${sec.items.map(i => `<div class="ritem ${done.has(i.id) ? "done" : ""}">
            <div class="row"><label class="check grow"><input type="checkbox" data-item="${esc(i.id)}" ${done.has(i.id) ? "checked" : ""}> <span>${esc(i.text)}</span></label>
              <button class="ghost small" data-note="${esc(i.id)}" title="Add a comment">${notes[i.id] ? "💬" : "＋ note"}</button></div>
            ${open.has(i.id) ? `<textarea class="rnote" data-ta="${esc(i.id)}" rows="2" placeholder="How did it go? Tempo, trouble spots, what to fix…">${esc(notes[i.id] || "")}</textarea>` : ""}</div>`).join("")
          || `<div class="muted small">No items. Use Edit routine to add some.</div>`}</div>`;
      }).join("")}
      ${(() => {
        const past = log.filter(r => r.note && r.day !== day);
        return past.length ? `<details class="card rsec"><summary><b>Past notes</b> <span class="small muted">(${past.length}, last 60 days)</span></summary>
          ${past.map(r => `<div class="rlog"><span class="muted small">${esc(r.day)} · ${esc(r.section)}</span>
            <div><b>${esc(r.text)}</b>${r.done ? "" : ` <span class="muted small">(not ticked)</span>`}</div><div>${esc(r.note)}</div></div>`).join("")}</details>` : "";
      })()}`;
    $$("[data-note]").forEach(b => b.onclick = () => {
      const id = b.dataset.note;
      open.has(id) ? open.delete(id) : open.add(id);
      draw();
      const ta = $(`[data-ta="${CSS.escape(id)}"]`); if (ta) ta.focus();
    });
    $$("[data-ta]").forEach(ta => ta.onchange = async () => {
      const id = ta.dataset.ta;
      notes[id] = ta.value.trim();
      if (!notes[id]) delete notes[id];
      const i = log.findIndex(r => r.day === day && r.item === id);
      if (i >= 0) log.splice(i, 1);
      const it = R.sections.flatMap(s => s.items.map(x => ({ ...x, section: s.title }))).find(x => x.id === id);
      if (it && (notes[id] || done.has(id))) log.unshift({ day, item: id, done: done.has(id), note: notes[id] || "", section: it.section, text: it.text });
      try { await api("/api/routine/note", { body: { day, item: id, note: ta.value } }); }
      catch (e) { toast(e.message); }
    });
    $$("[data-item]").forEach(cb => cb.onchange = async () => {
      const id = cb.dataset.item;
      cb.checked ? done.add(id) : done.delete(id);
      R.history[day] = done.size;
      draw();
      try { await api("/api/routine/check", { body: { day, item: id, done: cb.checked } }); }
      catch (e) { toast(e.message); }
    });
    const clr = $("#rClear");
    if (clr) clr.onclick = async () => {
      for (const id of [...done]) await api("/api/routine/check", { body: { day, item: id, done: false } });
      done.clear(); R.history[day] = 0; draw();
    };
    $("#rEdit").onclick = () => edit();
  };
  const edit = () => {
    const secs = JSON.parse(JSON.stringify(R.sections));
    const drawEdit = () => {
      main().innerHTML = `
        <div class="row"><h1 class="grow">Edit routine</h1>
          <button class="ghost" id="eReset">Reset to default</button><button id="eCancel">Cancel</button>
          <button class="primary" id="eSave">Save</button></div>
        <p class="sub">Rename sections, add or remove items, and reorder with the arrows.</p>
        ${secs.map((sec, si) => `<div class="card rsec"><div class="row">
          <input class="grow" data-st="${si}" value="${esc(sec.title)}" placeholder="Section name">
          <button class="ghost" data-sup="${si}" ${si ? "" : "disabled"}>↑</button>
          <button class="ghost" data-sdn="${si}" ${si < secs.length - 1 ? "" : "disabled"}>↓</button>
          <button class="ghost" data-sdel="${si}" title="Delete section">✕</button></div>
          ${sec.items.map((it, ii) => `<div class="row ritem-edit">
            <input class="grow" data-it="${si}:${ii}" value="${esc(it.text)}" placeholder="Item">
            <button class="ghost" data-iup="${si}:${ii}" ${ii ? "" : "disabled"}>↑</button>
            <button class="ghost" data-idn="${si}:${ii}" ${ii < sec.items.length - 1 ? "" : "disabled"}>↓</button>
            <button class="ghost" data-idel="${si}:${ii}" title="Delete item">✕</button></div>`).join("")}
          <button class="ghost" data-iadd="${si}">+ Add item</button></div>`).join("")}
        <button id="sAdd" style="margin-top:14px">+ Add section</button>`;
      const pair = v => v.split(":").map(Number);
      const swap = (a, i, j) => { [a[i], a[j]] = [a[j], a[i]]; };
      const again = fn => () => { fn(); drawEdit(); };
      $$("[data-st]").forEach(el => el.oninput = () => { secs[+el.dataset.st].title = el.value; });
      $$("[data-it]").forEach(el => el.oninput = () => { const [a, b] = pair(el.dataset.it); secs[a].items[b].text = el.value; });
      $$("[data-sup]").forEach(b => b.onclick = again(() => swap(secs, +b.dataset.sup, +b.dataset.sup - 1)));
      $$("[data-sdn]").forEach(b => b.onclick = again(() => swap(secs, +b.dataset.sdn, +b.dataset.sdn + 1)));
      $$("[data-sdel]").forEach(b => b.onclick = again(() => secs.splice(+b.dataset.sdel, 1)));
      $$("[data-iup]").forEach(b => b.onclick = again(() => { const [a, i] = pair(b.dataset.iup); swap(secs[a].items, i, i - 1); }));
      $$("[data-idn]").forEach(b => b.onclick = again(() => { const [a, i] = pair(b.dataset.idn); swap(secs[a].items, i, i + 1); }));
      $$("[data-idel]").forEach(b => b.onclick = again(() => { const [a, i] = pair(b.dataset.idel); secs[a].items.splice(i, 1); }));
      $$("[data-iadd]").forEach(b => b.onclick = again(() => secs[+b.dataset.iadd].items.push({ id: newId(), text: "" })));
      $("#sAdd").onclick = again(() => secs.push({ id: newId(), title: "New section", items: [] }));
      $("#eCancel").onclick = draw;
      $("#eReset").onclick = async () => {
        if (!confirm("Replace your routine with the default one?")) return;
        const d = (await api("/api/meta")).default_routine;
        secs.splice(0, secs.length, ...JSON.parse(JSON.stringify(d)));
        drawEdit();
      };
      $("#eSave").onclick = async () => {
        try {
          const out = await api("/api/routine", { method: "PUT", body: { day, sections: secs } });
          Object.assign(R, out);
          const valid = new Set(R.sections.flatMap(s => s.items.map(i => i.id)));
          for (const id of [...done]) if (!valid.has(id)) done.delete(id);
          toast("Routine saved");
          draw();
        } catch (e) { toast(e.message); }
      };
    };
    drawEdit();
  };
  draw();
};

/* ---------------------------------------------------------------- Practice */
async function startPractice(ids, idx, opts = {}) {
  S.practice = { ids, idx, results: [], hardOnly: !!opts.hardOnly };
  history.pushState({ practice: { ids, idx, hardOnly: !!opts.hardOnly } }, "", "#practice");
  S.tab = "practice";
  $$("nav.tabs a").forEach(a => a.classList.remove("active"));
  await loadPracticeLick();
}

async function loadPracticeLick() {
  const P = S.practice;
  const id = P.ids[P.idx];
  history.replaceState({ practice: { ids: P.ids, idx: P.idx, hardOnly: P.hardOnly } }, "", "#practice");  // the entry remembers which lick of the session
  main().innerHTML = `<div class="empty spin">Loading lick…</div>`;
  const d = await api(`/api/licks/${id}`);
  P.events = null;
  api(`/api/licks/${id}/events`).then(ev => { if (S.practice === P && P.lick && P.lick.id === id) P.events = ev; }).catch(() => {});
  const st = S.settings;
  // Setup: what this lick was last practised with, else what the latest session used, else the Settings defaults.
  const cfg = d.practice_cfg || {};
  const C = {
    order: cfg.order ?? st.key_order, reps: cfg.reps ?? st.reps_per_key, hear: cfg.hear ?? st.play_lick,
    sound: d.audio ? (cfg.sound ?? "rec") : "midi", resp: cfg.resp ?? st.call_response, back: cfg.back ?? st.backing,
    stay: cfg.stay ?? (st.stay_in_key !== false), swing: cfg.swing ?? Math.round(st.swing * 100), start_pc: cfg.start_pc ?? null,
  };
  Object.assign(P, { lick: d, cfg: C, rep: 0, done: new Set(), trouble: new Set(), hard: new Set(d.hard_pcs || []), allKeys: false, data: {}, kt: d.key_tempo || {} });
  P.keys = prepKeys(d.keys);
  P.ki = Math.max(0, P.keys.findIndex(k => k.pc === C.start_pc));
  main().innerHTML = `
    <div class="practice-head">
      <div class="grow">
        <div class="small muted">Lick ${P.idx + 1} of ${P.ids.length} · ${esc(d.state)}${P.hardOnly ? " · hard keys only" : ""}</div>
        <h1>${esc(d.label)} <span class="star ${d.favorite ? "on" : ""}" id="pStar" title="Favorite">★</span></h1>
        <div>${tagChips(d, 12)}</div>
        ${d.comment ? `<div class="small muted" style="margin-top:6px">${esc(d.comment)}</div>` : ""}
        ${keysLine(d)}
        <label class="check small" title="Your set is the keys this lick has to be Good in before more keys join. Practising others does not change the rating."><input type="checkbox" id="pAll"> Also practise the other keys</label>
        <button class="small" id="pPick" title="Choose which keys this lick is practised in">Choose keys…</button>
        <div id="pPickBox" style="display:none;margin-top:8px"></div>
      </div>
      <div class="row">
        <select id="pOrder" title="Order of the keys">${Object.entries(S.meta.orders).map(([k, v]) => `<option value="${k}" ${k === C.order ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
        <label class="check" title="The key you begin in">Start in <select id="pStart">${P.keys.map(k => `<option value="${k.pc}" ${k.pc === P.keys[P.ki].pc ? "selected" : ""}>${esc(k.label)}</option>`).join("")}</select></label>
        <button id="pSkip">Skip ›</button>
      </div>
    </div>
    <div class="keys" id="pKeys"></div>
    <div class="paper big" id="pSheet"></div>
    <div class="transport">
      <button class="primary play-btn" id="pPlay" title="Play / stop (space)">▶</button>
      <div><div class="phase" id="pPhase">Ready</div><div class="beat-dots" id="pDots"></div></div>
      <div class="slider">Tempo <button id="pSlower" title="Slower">−${st.tempo_step}</button><input type="range" id="pTempo" min="40" max="320" step="5" value="${tempoFor(P, P.ki)}"><b id="pTempoV">${tempoFor(P, P.ki)}</b><button id="pFaster" title="Faster">+${st.tempo_step}</button></div>
      <div class="slider">Swing <input type="range" id="pSwing" min="50" max="72" value="${C.swing}"><b id="pSwingV">${C.swing}%</b></div>
      <div class="slider">Times per key <select id="pReps">${[1, 2, 3, 4, 6, 8].map(n => `<option ${n === C.reps ? "selected" : ""}>${n}</option>`).join("")}</select> <span class="small muted" title="Keys you marked hard get twice as many repetitions">hard keys ×2</span></div>
      <label class="check"><input type="checkbox" id="pHear" ${C.hear ? "checked" : ""}> Hear lick</label>
      <label class="check" title="Play the lick as MIDI, or the real recording shifted to each key and to this tempo">Sound
        <select id="pSound"><option value="rec" ${C.sound === "rec" ? "selected" : (d.audio ? "" : "disabled")}>Recording${d.audio ? "" : " (none saved)"}</option><option value="midi" ${C.sound === "midi" ? "selected" : ""}>MIDI</option></select></label>
      <label class="check"><input type="checkbox" id="pResp" ${C.resp ? "checked" : ""}> Your turn after</label>
      <label class="check"><input type="checkbox" id="pBack" ${C.back ? "checked" : ""}> Backing</label>
      <label class="check"><input type="checkbox" id="pStay" ${C.stay ? "checked" : ""}> Stay in this key</label>
      <button id="pNextKey" title="Go to the next key">Next key ›</button>
      <button id="pTrouble" title="Mark this key as hard (T)">Mark key hard</button>
    </div>
    <div class="card">
      <div class="small muted" style="margin-bottom:6px">${esc(GOOD_TEXT(d.good_def))} <b id="pMeets"></b></div>
      <div class="row"><b>How did it go?</b><span class="muted small">Rate after you've been through the keys. Keys done: <span id="pDone">0</span>/${P.keys.length}</span>
      <div class="grow"></div><input id="pNote" placeholder="Note (optional)" style="width:300px"></div>
      <div class="rate">
        ${[[1, "Again"], [2, "Hard"], [3, "Good"], [4, "Easy"]].map(([r, n]) =>
          `<button class="r${r}" data-rate="${r}">${n} <span class="kbd">${r}</span><small>next in ${d.intervals[r]} day${d.intervals[r] == 1 ? "" : "s"}</small></button>`).join("")}
      </div>
    </div>`;
  renderKeys();
  showKey(P.ki);
  for (let i = 0; i < 4; i++) $("#pDots").appendChild(document.createElement("i"));
  $("#pTempo").oninput = e => $("#pTempoV").textContent = e.target.value;
  $("#pTempo").onchange = () => { saveKeyTempo(); meetsGood(); };
  $("#pSlower").onclick = () => nudgeTempo(-st.tempo_step);
  $("#pFaster").onclick = () => nudgeTempo(st.tempo_step);
  $("#pNextKey").onclick = () => {
    const nxt = Math.min(P.keys.length - 1, (Band.sequencer.running && P.shownKi !== undefined ? P.shownKi : P.ki) + 1);
    if (Band.sequencer.running) P.jump = nxt; else showKey(nxt);
  };
  $("#pSwing").oninput = e => $("#pSwingV").textContent = e.target.value + "%";
  $("#pPlay").onclick = togglePlay;
  $("#pTrouble").onclick = toggleTrouble;
  $("#pSkip").onclick = nextLick;
  $("#pStar").onclick = async () => {
    const on = !$("#pStar").classList.contains("on");
    await api(`/api/licks/${d.id}`, { method: "PATCH", body: { favorite: on ? 1 : 0 } });
    $("#pStar").classList.toggle("on", on);
  };
  $("#pAll").onchange = e => {
    Band.sequencer.stop(true);
    P.allKeys = e.target.checked; P.keys = prepKeys(d.keys); P.ki = 0; P.done = new Set(); P.trouble = new Set(); P.data = {};
    $("#pStart").innerHTML = P.keys.map(k => `<option value="${k.pc}">${esc(k.label)}</option>`).join("");
    renderKeys(); showKey(0); prefetch();
  };
  $("#pReps").addEventListener("change", meetsGood);
  $("#pPick").onclick = () => {
    const box = $("#pPickBox"), known = new Set((d.known_labels || []).map(k => k.key));
    if (box.style.display === "") { box.style.display = "none"; return; }
    const sel = new Set(d.scope || []);
    box.style.display = "";
    box.innerHTML = `<div class="small muted">Pick the keys to work on now (any number). The Good count starts again for the new set.</div>
      <div class="row" style="flex-wrap:wrap;gap:6px;margin:6px 0">${d.keys.map(k => `<label class="check"><input type="checkbox" data-pc="${k.pc}" ${sel.has(k.pc) ? "checked" : ""}> ${esc(k.label)}${known.has(k.label) ? " ✓" : ""}</label>`).join("")}</div>
      <button class="primary small" id="pPickSave">Use these keys</button> <span class="small muted">✓ = Good before</span>`;
    $("#pPickSave").onclick = async () => {
      const pcs = $$("#pPickBox [data-pc]").filter(c => c.checked).map(c => +c.dataset.pc);
      if (!pcs.length) return toast("Pick at least one key");
      Band.sequencer.stop(true);
      await api(`/api/licks/${d.id}/scope`, { body: { scope: pcs } });
      toast(`Working on ${d.keys.filter(k => pcs.includes(k.pc)).map(k => k.label).join(", ")}.`);
      loadPracticeLick();
    };
  };
  $("#pOrder").onchange = async e => {
    Band.sequencer.stop(true);
    const dd = await api(`/api/licks/${d.id}?order=${e.target.value}`);
    P.keys = prepKeys(dd.keys); P.ki = Math.max(0, P.keys.findIndex(k => k.pc === P.cfg.start_pc)); P.data = {};
    $("#pStart").innerHTML = P.keys.map(k => `<option value="${k.pc}">${esc(k.label)}</option>`).join("");
    $("#pStart").value = P.keys[P.ki].pc;
    renderKeys(); showKey(P.ki); prefetch();
    saveCfg();
  };
  $("#pStart").onchange = e => {
    if (Band.sequencer.running) Band.sequencer.stop(true);
    const i = P.keys.findIndex(k => k.pc === +e.target.value);
    P.cfg.start_pc = +e.target.value; P.rep = 0; P.ki = Math.max(0, i);
    showKey(P.ki); saveCfg();
  };
  ["pReps", "pHear", "pSound", "pResp", "pBack", "pStay", "pSwing"].forEach(id => $("#" + id).addEventListener("change", saveCfg));
  $$("[data-rate]").forEach(b => b.onclick = () => rate(+b.dataset.rate));
  prefetch();
  requestAnimationFrame(tick);
}

// Keys you marked hard come first (and, in a hard-keys session, are the only ones).
function prepKeys(keys) {
  const P = S.practice, hard = k => P.hard.has(k.pc);
  let ks = P.allKeys || P.hardOnly ? keys : keys.filter(k => (P.lick.scope || []).includes(k.pc));   // only the keys in this lick's set, unless you ask for all
  if (!ks.length) ks = keys;
  if (P.hardOnly && ks.some(hard)) ks = ks.filter(hard);
  return [...ks.filter(hard), ...ks.filter(k => !hard(k))];
}
// Hard keys get twice the repetitions.
function repsFor(ki) {
  const P = S.practice, base = +$("#pReps").value, k = P.keys[ki];
  return k && P.hard.has(k.pc) ? Math.min(8, base * 2) : base;
}
// The setup is remembered for this lick, and the next lick without one of its own starts from it.
let cfgTimer = null;
function saveCfg() {
  const P = S.practice;
  if (!P || !P.lick) return;
  const cfg = {
    order: $("#pOrder").value, reps: +$("#pReps").value, hear: $("#pHear").checked, resp: $("#pResp").checked,
    back: $("#pBack").checked, stay: $("#pStay").checked, swing: +$("#pSwing").value, start_pc: P.cfg.start_pc ?? null,
  };
  if (P.lick.audio) cfg.sound = $("#pSound").value;
  Object.assign(P.cfg, cfg);
  clearTimeout(cfgTimer);
  const id = P.lick.id;
  cfgTimer = setTimeout(() => api(`/api/licks/${id}/practice-cfg`, { body: { cfg } }).catch(() => {}), 250);
}

// Tempo you last used in a key (or the slow starting tempo for a key you have not practiced yet).
function tempoFor(P, ki) {
  const k = P.keys[ki];
  return (k && P.kt[k.pc]) || S.settings.start_tempo || 60;
}
function setTempo(v) {
  v = Math.max(40, Math.min(320, Math.round(v)));
  $("#pTempo").value = v; $("#pTempoV").textContent = v;
}
function currentKi() {
  const P = S.practice;
  return Band.sequencer.running && P.shownKi !== undefined ? P.shownKi : P.ki;
}
async function saveKeyTempo(ki = currentKi()) {
  const P = S.practice, k = P.keys[ki];
  if (!k) return;
  const tempo = +$("#pTempo").value;
  P.kt[k.pc] = tempo;
  renderKeys();
  api(`/api/licks/${P.lick.id}/key-tempo`, { body: { pc: k.pc, tempo } }).catch(() => {});
}
function nudgeTempo(d) {
  setTempo(+$("#pTempo").value + d);
  saveKeyTempo();
}

// The real recording in practice key ki at `bpm`, pitch and tempo changed on the server (ffmpeg), decoded for WebAudio.
// Kept per key and tempo; the next key is fetched while the current one plays.
function clipKey(ki, bpm) { return ki + "|" + bpm; }
function loadClip(ki, bpm) {
  const P = S.practice;
  P.clipDone = P.clipDone || new Map();     // key -> { buffer, offset }, ready to play
  P.clipPending = P.clipPending || new Map();  // key -> promise while it is being made
  const key = clipKey(ki, bpm);
  if (P.clipDone.has(key)) return Promise.resolve(P.clipDone.get(key));
  if (!P.clipPending.has(key)) {
    const semis = (((P.data[ki] || {}).semis || 0) % 12 + 18) % 12 - 6;  // shift by the shortest way (a 12-semitone jump would sound wrong)
    P.clipPending.set(key, (async () => {
      Band.engine.init();
      const r = await fetch(`/api/licks/${P.lick.id}/audio?semis=${semis}&bpm=${bpm}&v=2`, { cache: "no-store" });  // v=2: skip copies cached before bar marks were reported
      if (!r.ok) throw new Error(await r.text());
      const offset = parseFloat(r.headers.get("X-Clip-Offset") || "0.1");
      let marks = []; try { marks = JSON.parse(r.headers.get("X-Clip-Marks") || "[]"); } catch (e) { /* no bar positions */ }
      return { buffer: await Band.engine.ctx.decodeAudioData(await r.arrayBuffer()), offset, marks };
    })().then(c => { P.clipDone.set(key, c); P.clipPending.delete(key); return c; },
              err => { P.clipPending.delete(key); throw err; }));
  }
  return P.clipPending.get(key);
}

async function prefetch() {
  const P = S.practice;
  await Promise.all(P.keys.map(async (k, i) => { P.data[i] = await api(`/api/licks/${P.lick.id}/play?pc=${k.pc}`); }));
  P.keys.forEach(k => svgFor(P.lick.id, k.pc).catch(() => {}));
}

function renderKeys() {
  const P = S.practice;
  $("#pKeys").innerHTML = P.keys.map((k, i) => `
    <div class="key ${i === (Band.sequencer.running && P.shownKi !== undefined ? P.shownKi : P.ki) ? "current" : ""} ${P.done.has(i) ? "done" : ""} ${P.hard.has(k.pc) ? "trouble" : ""} ${k.fits ? "" : "outrange"}"
      data-ki="${i}" title="${k.fits ? "" : "Some notes are outside your range in this key. "}${P.kt[k.pc] ? "Last played at " + P.kt[k.pc] : ""}">${esc(k.label)}<small>${P.kt[k.pc] ? "♩" + P.kt[k.pc] : i + 1}</small></div>`).join("");
  $$("#pKeys .key").forEach(el => el.onclick = () => {
    const i = +el.dataset.ki;
    if (Band.sequencer.running) { P.jump = i; } else { showKey(i); }
  });
  $("#pDone").textContent = P.done.size;
  syncTrouble();
  meetsGood();
}
// Does this pass meet your definition of Good? Every key of the set played, none marked hard, enough passes, fast enough.
function meetsGood() {
  const P = S.practice, el = $("#pMeets"), gd = P.lick.good_def;
  if (!el || !gd) return;
  const inSet = P.keys.map((k, i) => [k, i]).filter(([k]) => (P.lick.scope || []).includes(k.pc));
  const played = inSet.every(([, i]) => P.done.has(i)), clean = !inSet.some(([, i]) => P.trouble.has(i));
  const passes = +$("#pReps").value >= gd.clean_passes;
  const fast = !gd.min_tempo || inSet.every(([k, i]) => ((i === P.ki ? +$("#pTempo").value : 0) || P.kt[k.pc] || 0) >= gd.min_tempo);
  const ok = played && clean && passes && fast;
  el.textContent = ok ? "This pass meets it: Good." : !played ? "Not all keys of your set are played yet." : !clean ? "A key is marked hard." : !passes ? `Set times per key to ${gd.clean_passes} or more.` : "Some key is below the tempo.";
  el.style.color = ok ? "var(--good, #2e7d32)" : "";
  const g = document.querySelector("[data-rate='3']"); if (g) g.classList.toggle("suggest", ok);
}

async function showKey(i) {
  const P = S.practice;
  if (!Band.sequencer.running) P.ki = i;
  P.shownKi = i;
  if ($("#pTempo")) setTempo(tempoFor(P, i));
  renderKeys();
  const k = P.keys[i];
  $("#pSheet").innerHTML = await svgFor(P.lick.id, k.pc);
}

function toggleTrouble() {
  const P = S.practice;
  const k = Band.sequencer.running && P.shownKi !== undefined ? P.shownKi : P.ki;
  const pc = P.keys[k].pc, on = !P.hard.has(pc);
  if (on) { P.hard.add(pc); P.trouble.add(k); } else { P.hard.delete(pc); P.trouble.delete(k); }
  renderKeys();
  api(`/api/licks/${P.lick.id}/hard`, { body: { pc, hard: on } }).catch(e => toast(e.message));
  toast(on ? `${P.keys[k].label} marked hard: it comes first and gets double repetitions next time.` : `${P.keys[k].label} no longer marked hard.`);
}
// The button shows whether the key you are on is marked hard.
function syncTrouble() {
  const P = S.practice, b = $("#pTrouble");
  if (!P || !b) return;
  const k = Band.sequencer.running && P.shownKi !== undefined ? P.shownKi : P.ki;
  const on = P.hard.has(P.keys[k].pc);
  b.classList.toggle("on", on);
  b.textContent = on ? "✓ Key marked hard" : "Mark key hard";
}

async function togglePlay() {
  const P = S.practice, seq = Band.sequencer;
  if (seq.running) { seq.stop(); return; }
  const useRec = () => $("#pSound") && $("#pSound").value === "rec";
  if (useRec()) {  // get the first clip ready before starting
    $("#pPhase").textContent = "Preparing the recording…";
    try { await loadClip(P.ki, Math.round(+$("#pTempo").value)); }
    catch (e) { toast("Recording not available: " + e.message); return; }
  }
  P.rep = 0;
  P.startKi = P.ki;
  seq.start({
    tempo: () => +$("#pTempo").value,
    swing: () => +$("#pSwing").value / 100,
    next: n => {
      if (n > 0) {
        if (P.jump !== undefined) { P.ki = P.jump; P.jump = undefined; P.rep = 0; }
        else {
          P.rep++;
          if (P.rep >= repsFor(P.ki) && !$("#pStay").checked) {
            P.rep = 0; P.ki++;
            if (P.ki >= P.keys.length) { P.ki = P.keys.length - 1; return null; }
          }
        }
      }
      const data = P.data[P.ki];
      if (!data) return null;
      let clip = null;
      if (useRec()) {
        const bpm = Math.round(+$("#pTempo").value), got = P.clipDone && P.clipDone.get(clipKey(P.ki, bpm));
        if (got && got.buffer) clip = got; else loadClip(P.ki, bpm).catch(() => {});  // not ready yet: MIDI this time
        const upcoming = $("#pStay").checked || P.rep + 1 < repsFor(P.ki) ? P.ki : Math.min(P.ki + 1, P.keys.length - 1);
        loadClip(upcoming, bpm).catch(() => {});  // and have the next cycle's clip waiting
      }
      return {
        seq: data, leadIn: P.rep === 0 ? S.settings.lead_in : 0, playLick: $("#pHear").checked && !clip,
        clip: clip && $("#pHear").checked ? clip : null,
        backing: $("#pBack").checked && (S.settings.backing_style || "band"), response: $("#pResp").checked, info: { ki: P.ki, rep: P.rep },
      };
    },
    onCycle: info => {
      if (S.tab !== "practice") return;
      if (info.ki !== P.shownKi) {
        if (P.shownKi !== undefined && P.played) { P.done.add(P.shownKi); saveKeyTempo(P.shownKi); }
        P.shownKi = info.ki; showKey(info.ki);
      } else renderKeys();
      P.played = true;
    },
    onStop: () => {
      if (S.tab !== "practice") return;
      $("#pPlay").textContent = "▶";
      if (P.played && P.shownKi !== undefined) { P.done.add(P.shownKi); saveKeyTempo(P.shownKi); }
      P.played = false;
      renderKeys();
      if (P.done.size >= P.keys.length) toast(`All ${P.keys.length} keys done — rate how it went.`);
    },
  });
  P.shownKi = P.ki;
  $("#pPlay").textContent = "■";
}

// Light up the note being heard: from the clock for MIDI, from where the bar lines fall in the clip for the recording.
function markPlaying(pos) {
  $$("#pSheet .play").forEach(g => g.classList.remove("play"));
  const P = S.practice, ev = P && P.events;
  if (!pos || pos.phase !== "listen" || !ev || !$("#pHear").checked) return;
  let q = pos.q;
  const clip = pos.clip;
  if (clip && clip.marks && clip.marks.length > 1) {
    const t = pos.now - (pos.tStart - clip.offset), m = clip.marks;  // seconds into the clip
    if (t < m[0] || t >= m[m.length - 1]) return;
    let i = 0; while (i < m.length - 2 && t >= m[i + 1]) i++;
    const [num, den] = ((P.data[pos.info.ki] || {}).ts || [4, 4]);
    q = (i + (t - m[i]) / (m[i + 1] - m[i])) * num * 4 / den;
  }
  const swing = +$("#pSwing").value / 100;
  let idx = -1;
  ev.forEach((e, i) => { if (!e.g && (clip ? e.s : Band.warp(e.s, swing)) <= q + 1e-6) idx = i; });
  const els = $$("#pSheet g.note, #pSheet g.rest, #pSheet g.mRest");
  if (els.length !== ev.length) return;  // drawing and note list disagree (e.g. chords): better no highlight than a wrong one
  if (idx >= 0 && els[idx]) els[idx].classList.add("play");
}

function tick() {
  if (S.tab !== "practice" || !$("#pPhase")) return;
  const pos = Band.sequencer.position();
  markPlaying(pos);
  const dots = $$("#pDots i");
  if (pos) {
    let ph = pos.phase;
    if (ph === "listen" && !$("#pHear").checked) ph = "play it";
    const P = S.practice;
    const k = P.keys[pos.info && pos.info.ki !== undefined ? pos.info.ki : P.ki];
    $("#pPhase").textContent = `${ph}${ph ? " · " : ""}${k ? k.label : ""} ${pos.info ? "(" + (pos.info.rep + 1) + "/" + repsFor(pos.info.ki !== undefined ? pos.info.ki : P.ki) + ")" : ""}`;
    dots.forEach((d, i) => d.classList.toggle("on", i === pos.beat));
  } else if (!Band.sequencer.running) {
    $("#pPhase").textContent = "Ready";
    dots.forEach(d => d.classList.remove("on"));
  }
  requestAnimationFrame(tick);
}

async function rate(r) {
  const P = S.practice;
  Band.sequencer.stop(true);
  if (!P.done.size) P.done.add(P.ki);
  // A hard key you played through and rated Good or Easy, without marking it again, is no longer hard.
  const cleared = r >= 3 ? [...P.done].filter(i => P.keys[i] && P.hard.has(P.keys[i].pc) && !P.trouble.has(i)) : [];
  const res = await api(`/api/licks/${P.lick.id}/review`, {
    body: {
      rating: r, keys_done: [...P.done].map(i => P.keys[i].label), trouble_keys: [...P.trouble].map(i => P.keys[i].label),
      tempo: +$("#pTempo").value, mode: "drill", note: $("#pNote").value, clear_hard: cleared.map(i => P.keys[i].pc),
    }
  });
  if (cleared.length) toast(`${cleared.map(i => P.keys[i].label).join(", ")} no longer marked hard.`, 4000);
  P.results.push({ lick: P.lick.label, rating: r, days: res.in_days });
  toast(res.expanded_labels && res.expanded_labels.length ? `Good again: ${res.expanded_labels.join(", ")} joins your set (now ${res.scope_labels.join(", ")}). See it again tomorrow.`
    : res.shelved_until ? `Stuck: shelved until ${res.shelved_until}, cut back to ${res.scope_labels.join(", ")}.`
    : `Saved. Next review in ${Math.max(1, Math.round(res.in_days))} day${Math.round(res.in_days) > 1 ? "s" : ""}.`, 5000);
  nextLick();
}

function nextLick() {
  const P = S.practice;
  Band.sequencer.stop(true);
  if (P.idx + 1 < P.ids.length) { P.idx++; loadPracticeLick(); return; }
  const names = ["", "Again", "Hard", "Good", "Easy"];
  main().innerHTML = `<h1>Session done</h1><p class="sub">${P.results.length} lick${P.results.length === 1 ? "" : "s"} rated.</p>
    <div class="lick-list">${P.results.map(x => `<div class="lick-item"><div class="grow">${esc(x.lick)}</div>
      <div>${names[x.rating]}</div><div class="muted">next in ${Math.max(1, Math.round(x.days))} d</div></div>`).join("")}</div>
    <p><a class="btn" href="#licks">Back to Licks</a> <a class="btn" href="#play">Use them in a tune</a></p>`;
}

document.addEventListener("keydown", e => {
  if (S.tab !== "practice" || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (["Digit1", "Digit2", "Digit3", "Digit4"].includes(e.code)) rate(+e.code.slice(-1));
  else if (e.code === "KeyT") toggleTrouble();
  else if (e.code === "ArrowRight" && !Band.sequencer.running) showKey(Math.min(S.practice.ki + 1, S.practice.keys.length - 1));
  else if (e.code === "ArrowLeft" && !Band.sequencer.running) showKey(Math.max(S.practice.ki - 1, 0));
});

/* ---------------------------------------------------------------- Library */
VIEWS.library = async () => {
  const licks = await api("/api/licks?archived=true");
  S.licks = licks;
  const fns = [...new Set(licks.map(l => l.function))].sort();
  const tags = {};
  licks.forEach(l => l.all_tags.forEach(t => tags[t] = (tags[t] || 0) + 1));
  main().innerHTML = `
    <div class="row"><div class="grow"><h1>Library</h1><p class="sub">${licks.filter(l => !l.archived).length} licks. Click a lick to see it in any key, edit its tags or practise it.</p></div>
      <button id="practiceSel">Practise the filtered licks</button>
      <button id="exportSel" title="A zip with notation, MIDI, audio clips and JSON (with YouTube sync times) for each lick">Export the filtered licks</button>
      <button id="repeatsBtn">Find repeated licks</button>
      <button class="danger" id="deleteSel">Delete the filtered licks…</button></div>
    <div id="repeatsOut"></div>
    <div class="row" style="margin-bottom:12px">
      <input id="fText" placeholder="Search number, artist, tune, tag, source file…" style="width:280px">
      <select id="fFn"><option value="">All functions</option>${fns.map(f => `<option>${esc(f)}</option>`).join("")}</select>
      <select id="fState"><option value="">Any state</option>${["new", "learning", "reviewing", "known", "lapsed"].map(s => `<option>${s}</option>`).join("")}</select>
      <select id="fTag"><option value="">Any tag</option>${Object.entries(tags).sort((a, b) => b[1] - a[1]).map(([t, n]) => `<option value="${esc(t)}">${esc(t)} (${n})</option>`).join("")}</select>
      <label class="check"><input type="checkbox" id="fFav"> Favorites</label>
      <label class="check"><input type="checkbox" id="fArch"> Show archived</label>
    </div>
    <div class="card tablecard" style="padding:0"><table><thead><tr><th class="sortable" data-sort="fav" title="Sort by favorites">★</th><th></th>
      <th class="sortable" data-sort="number">#</th><th class="sortable" data-sort="name">Name</th><th class="sortable" data-sort="bars">Bars</th>
      <th class="sortable" data-sort="fn">Function</th><th class="sortable" data-sort="key">Key</th><th class="sortable" data-sort="tags">Tags</th>
      <th class="sortable" data-sort="state">State</th><th class="sortable" data-sort="next">Next</th></tr></thead>
    <tbody id="libRows"></tbody></table></div>`;
  // Click a column heading to sort by it; click again to reverse. The choice is remembered.
  const STATE_ORDER = ["new", "learning", "reviewing", "known", "lapsed"];
  const SORTS = {
    fav: l => -(l.favorite || 0), number: l => l.number, name: l => (l.label || "").toLowerCase(), bars: l => l.bars || 0,
    fn: l => (l.function || "").toLowerCase(), key: l => ((l.tonic_pc || 0) + (written() ? 2 : 0)) % 12 * 2 + (l.mode === "minor" ? 1 : 0),
    tags: l => ((l.all_tags || [])[0] || "~").toLowerCase(), state: l => STATE_ORDER.indexOf(l.state), next: l => l.due || "9999",
  };
  let sort = { by: "number", dir: 1 };
  try { sort = JSON.parse(localStorage.getItem("libSort")) || sort; } catch (e) { /* default */ }
  const sortRows = rows => {
    const f = SORTS[sort.by] || SORTS.number;
    return rows.slice().sort((a, b) => { const x = f(a), y = f(b); return (x < y ? -1 : x > y ? 1 : a.number - b.number) * sort.dir; });
  };
  const markSort = () => $$("th.sortable").forEach(th => th.dataset.dir = th.dataset.sort === sort.by ? (sort.dir > 0 ? "▲" : "▼") : "");
  $$("th.sortable").forEach(th => th.onclick = () => {
    sort = { by: th.dataset.sort, dir: sort.by === th.dataset.sort ? -sort.dir : 1 };
    try { localStorage.setItem("libSort", JSON.stringify(sort)); } catch (e) { /* storage blocked */ }
    draw();
  });
  // Search and filters are kept while you visit other tabs (and across a reload within this browser tab).
  const FIELDS = { fText: "value", fFn: "value", fState: "value", fTag: "value", fFav: "checked", fArch: "checked" };
  let saved = S.libFilter;
  if (!saved) { try { saved = JSON.parse(sessionStorage.getItem("libFilter")); } catch (e) { /* none */ } }
  for (const [id, prop] of Object.entries(FIELDS)) if (saved && saved[id] !== undefined) $("#" + id)[prop] = saved[id];
  const draw = () => {
    markSort();
    S.libFilter = Object.fromEntries(Object.entries(FIELDS).map(([id, prop]) => [id, $("#" + id)[prop]]));
    try { sessionStorage.setItem("libFilter", JSON.stringify(S.libFilter)); } catch (e) { /* storage blocked */ }
    const q = $("#fText").value.toLowerCase(), fn = $("#fFn").value, st = $("#fState").value, tg = $("#fTag").value;
    const rows = sortRows(licks.filter(l => (!q || [l.number, l.label, l.artist, l.tune, l.comment, l.source, ...l.all_tags].join(" ").toLowerCase().includes(q))
      && (!fn || l.function === fn) && (!st || l.state === st) && (!tg || l.all_tags.includes(tg))
      && (!$("#fFav").checked || l.favorite) && ($("#fArch").checked || !l.archived)));
    S.filtered = rows.filter(l => !l.archived).map(l => l.id);
    S.filteredAll = rows.map(l => l.id);
    $("#libRows").innerHTML = rows.map(l => `<tr class="clickable" data-id="${l.id}">
      <td><span class="star ${l.favorite ? "on" : ""}" data-star="${l.id}">★</span></td>
      <td>${playBtn("data-listen", l.id)}</td>
      <td><b>${l.number}</b></td><td>${esc(l.label)}${l.archived ? ' <span class="chip">archived</span>' : ""}</td>
      <td class="muted">${l.bars || ""}</td><td>${esc(l.function || "")}</td>
      <td>${keyText(l)}</td>
      <td>${tagChips(l, 5)}</td><td>${stateBadge(l.state)}</td><td class="muted small">${dueText(l.due)}</td></tr>`).join("")
      || `<tr><td colspan="10" class="empty">No licks${licks.length ? " match" : " yet — import some on the Import page"}.</td></tr>`;
    wireListen($("#libRows"));
    $$("#libRows tr[data-id]").forEach(tr => tr.onclick = e => {
      if (e.target.dataset.star || e.target.dataset.listen) return;
      openLick(+tr.dataset.id);
    });
    $$("[data-star]").forEach(s => s.onclick = async () => {
      const l = licks.find(x => x.id === +s.dataset.star);
      l.favorite = l.favorite ? 0 : 1;
      await api(`/api/licks/${l.id}`, { method: "PATCH", body: { favorite: l.favorite } });
      s.classList.toggle("on", !!l.favorite);
    });
  };
  ["fText", "fFn", "fState", "fTag", "fFav", "fArch"].forEach(id => $("#" + id).addEventListener("input", draw));
  $("#exportSel").onclick = () => S.filtered.length ? (location.href = `/api/export/licks.zip?ids=${S.filtered.join(",")}`) : toast("No licks selected");
  $("#practiceSel").onclick = () => S.filtered.length ? startPractice(S.filtered, 0) : toast("No licks selected");
  $("#repeatsBtn").onclick = async () => {
    const reps = await api("/api/repeats");
    $("#repeatsOut").innerHTML = `<div class="card" style="margin-bottom:12px"><h3>${reps.length ? reps.length + " pair" + (reps.length > 1 ? "s" : "") +
      " of licks share a line" : "No repeated licks found"}</h3><p class="small muted">“Same” = the same notes and rhythm in another key or octave.
      “Contained” = one lick is part of the other. “Similar” = a few notes or rhythms differ.</p>
      <div class="lick-list">${reps.map(r => `<div class="lick-item"><span class="chip ${r.kind === "same" ? "user" : ""}">${r.kind === "inside" ? "contained" : r.kind}</span>
        <div class="grow"><a href="#" data-open="${r.b.id}">${esc(r.b.label)}</a> — ${esc(r.text)}</div>
        <a href="#" data-open="${r.a.id}" class="small">open ${esc(r.a.label.split(",")[0])}</a></div>`).join("")}</div></div>`;
    $$("[data-open]").forEach(a => a.onclick = e => { e.preventDefault(); openLick(+a.dataset.open); });
  };
  $("#deleteSel").onclick = async () => {
    const ids = S.filteredAll || [];
    if (!ids.length) { toast("No licks selected"); return; }
    if (!confirm(`Delete ${ids.length} lick${ids.length > 1 ? "s" : ""} shown in the list, with their practice history? ` +
      "The MusicXML files stay in library/licks and library/sources.")) return;
    for (const id of ids) await api(`/api/licks/${id}`, { method: "DELETE" });
    toast(`Deleted ${ids.length}`);
    go("library");
  };
  draw();
};

async function openLick(id, nav = "push") {
  const d = await api(`/api/licks/${id}`);
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  if (nav === "push") history.pushState({ lick: id, tab: S.tab }, "", location.href);  // the window is a history entry: ← closes it
  else if (nav === "replace") history.replaceState({ lick: id, tab: S.tab }, "", location.href);
  bg.innerHTML = `<div class="modal">
    <button class="close ghost" id="mClose">✕</button>
    <h1>${esc(d.label)} <span class="star ${d.favorite ? "on" : ""}" id="mStar">★</span> <span class="chip">#${d.number}</span> ${stateBadge(d.state)}</h1>
    <div style="margin:6px 0 12px">${tagChips(d)}${keysLine(d)}</div>
    <div class="row" style="margin-bottom:10px">
      <select id="mKey" title="Key for ▶ Play (the notes below stay as written for B♭ trumpet)">${d.keys.map(k => `<option value="${k.pc}">${esc(k.label)}${k.fits ? "" : " (out of range)"}</option>`).join("")}</select>
      <button id="mPlay">▶ Play</button>
      <button class="primary" id="mPractice">Practise now</button>
      <a class="btn" href="/api/licks/${d.id}/twelve">12-key sheet (MusicXML)</a>
      <a class="btn" href="/api/licks/${d.id}/musicxml">Lick file</a>
      <a class="btn" href="/api/export/licks.zip?ids=${d.id}" title="Notation, MIDI, audio clip and a JSON file with the YouTube sync times">Export…</a>
      <a class="btn" href="/api/licks/${d.id}/export.mid" title="MIDI file at the recording's tempo (concert pitch)">MIDI</a>
    </div>
    ${d.audio ? `<div class="row" style="margin:8px 0"><b class="small">Original recording</b><audio controls preload="none" src="/api/licks/${d.id}/audio" style="height:32px"></audio></div>` : ""}
    <details id="mAbcBox" open style="margin:10px 0"><summary><b>Edit notes</b> <span class="small muted">(written for B♭ trumpet; the notes as text, or click them below)</span></summary>
      <div class="small muted" style="margin:6px 0">Click a note or rest to select it (a note also plays). Drag a note up or down (Shift = semitones), or use the keys:
        <b>↑ ↓</b> semitone · <b>⌘↑ ⌘↓</b> octave · <b>Alt ↑↓</b> scale step · <b>A–G</b> note name (on a rest: makes a note) ·
        <b>1 2 4 8 6</b> whole / half / quarter / eighth / sixteenth · <b>.</b> dotted · <b>/</b> grace note (again = remove; <b>?</b> = unslashed) · <b>⌘3</b> triplet beat · <b>T</b> tie to the next note · <b>Delete</b> rest ·
        <b>← →</b> move · <b>⌘Z</b> undo · <b>⌘S</b> save.</div>
      <div class="paper big" id="abcSheet" style="margin:6px 0 10px"></div>
      <div class="grid cols-4" style="margin-top:8px">
        <label class="field">Key signature<select id="abcKey"></select></label>
        <label class="field">Time signature<input id="abcMeter" value="4/4"></label>
      </div>
      <label class="field">ABC (L:1/8; chords in quotes; ^ # · _ b · = natural; the key signature applies by itself)
        <textarea id="abcText" rows="4" spellcheck="false" style="font-family:ui-monospace,Menlo,monospace"></textarea></label>
      <div class="row" style="margin-top:6px"><button id="abcPreview">Preview</button>
        <button class="primary" id="abcSave">Save notes to this lick</button><span id="abcMsg" class="small muted"></span></div>
    </details>
    <h2>Details</h2>
    <div class="grid cols-2" style="margin-bottom:8px">
      <label class="field">Name <span class="muted small">(leave empty to use artist + tune)</span><input id="eName" value="${esc((d.name || "").match(/^Lick\s*\d+$/i) ? "" : d.name || "")}" placeholder="${esc([d.artist, d.tune].filter(Boolean).join(" ") || "Lick " + d.number)}"></label>
    </div>
    <div class="grid cols-4">
      <label class="field">Number<input id="eNumber" type="number" value="${d.number}"></label>
      <label class="field">Function<select id="eFunction">${S.meta.functions.map(f => `<option ${f === d.function ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
      <label class="field">Key (concert tonic)<input id="eTonic" value="${esc(d.tonic_name)}"></label>
      <label class="field">Mode<select id="eMode"><option ${d.mode === "major" ? "selected" : ""}>major</option><option ${d.mode === "minor" ? "selected" : ""}>minor</option></select></label>
      <label class="field">Artist<input id="eArtist" value="${esc(d.artist)}"></label>
      <label class="field">Tune<input id="eTune" value="${esc(d.tune)}"></label>
      <label class="field">Year<input id="eYear" value="${esc(d.year || "")}"></label>
      <label class="field">Your tags (comma separated)<input id="eTags" value="${esc((d.tags || []).join(", "))}"></label>
    </div>
    <div class="grid cols-2" style="margin-top:10px">
      <label class="field">Comment (from the lick sheet)<textarea id="eComment" rows="2">${esc(d.comment)}</textarea></label>
      <label class="field">Practice notes<textarea id="eNotes" rows="2">${esc(d.notes)}</textarea></label>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="primary" id="mSave">Save</button>
      <button id="mReanalyse">Re-run auto tags</button>
      <div class="grow"></div>
      <button id="mArchive">${d.archived ? "Unarchive" : "Archive"}</button>
      <button class="danger" id="mDelete">Delete</button>
    </div>
    ${d.related.length ? `<h2>Same line elsewhere</h2><div class="lick-list">${d.related.map(r =>
      `<div class="lick-item"><div class="grow">${esc(r.text)}</div><button data-rel="${r.lick_id}">Open</button></div>`).join("")}</div>` : ""}
    <p class="small muted">Source: ${esc(d.source)} bars ${esc(d.source_bars)} · file ${esc(d.file)}</p>
    ${d.history.length ? `<h2>History</h2><table><tr><th>When</th><th>Rating</th><th>Keys</th><th>Hard keys</th><th>Tempo</th></tr>
      ${d.history.map(h => `<tr><td>${new Date(h.ts).toLocaleDateString()}</td><td>${["", "Again", "Hard", "Good", "Easy"][h.rating]}</td>
        <td>${(h.keys_done || []).length}</td><td>${esc((h.trouble_keys || []).join(" "))}</td><td>${h.tempo || ""}</td></tr>`).join("")}</table>` : ""}
  </div>`;
  document.body.appendChild(bg);
  const close = () => {
    if (history.state && history.state.lick === d.id) { history.back(); return; }  // the popstate handler closes the window
    Band.sequencer.stop(true); bg.remove(); if (S.tab === "library") go("library");
  };
  $("#mClose", bg).onclick = close;
  bg.onclick = e => { if (e.target === bg) close(); };
  $("#mPlay", bg).onclick = async () => {
    if (Band.sequencer.running) { Band.sequencer.stop(); return; }
    const data = await api(`/api/licks/${d.id}/play?pc=${$("#mKey", bg).value}`);
    $("#mPlay", bg).textContent = "■ Stop";
    Band.sequencer.start({
      tempo: () => S.settings.tempo, swing: () => S.settings.swing,
      next: n => n === 0 ? { seq: data, leadIn: S.settings.lead_in, playLick: true, backing: S.settings.backing && (S.settings.backing_style || "band"), response: false, info: {} } : null,
      onStop: () => { const b = $("#mPlay", bg); if (b) b.textContent = "▶ Play"; },
    });
    // light up the note that is sounding
    const follow = () => {
      const pos = document.body.contains(bg) && Band.sequencer.running ? Band.sequencer.position() : null;
      ed.mark(pos && pos.phase === "listen" ? pos.q : null);
      if (document.body.contains(bg) && Band.sequencer.running) requestAnimationFrame(follow);
    };
    follow();
  };
  $$("[data-rel]", bg).forEach(b => b.onclick = () => { Band.sequencer.stop(true); bg.remove(); openLick(+b.dataset.rel); });
  const abcBody = () => ({ abc: $("#abcText", bg).value, sharps: +$("#abcKey", bg).value, meter: $("#abcMeter", bg).value.trim() || "4/4" });
  const abcIssues = r => (r.issues || []).length ? "Check: " + r.issues.map(i => `bar ${i.bar}: ${i.msg}`).join("; ") : "";
  const ed = lickEditor(bg, d, abcBody, t => { $("#abcText", bg).value = t; }, r => { if ((r.issues || []).length) $("#abcMsg", bg).textContent = abcIssues(r); });
  (async () => {
    try {
      const a = await api(`/api/licks/${d.id}/abc`);
      $("#abcKey", bg).innerHTML = a.keys.map(k => `<option value="${k.sharps}" ${k.sharps === a.sharps ? "selected" : ""}>${esc(k.name)}</option>`).join("");
      $("#abcMeter", bg).value = a.meter; $("#abcText", bg).value = a.abc;
      await ed.refresh();
    } catch (err) { $("#abcMsg", bg).textContent = err.message; }
  })();
  $("#abcPreview", bg).onclick = () => ed.refresh();
  // Original recording: light up the note being heard. Bar positions inside the clip were saved when the lick was sent from
  // Align; for other clips assume the bars share the clip evenly (after a 0.1 s lead-in).
  const clip = $("audio", bg);
  if (clip) {
    let raf = 0;
    const where = () => {
      const t = clip.currentTime, [num, den] = ($("#abcMeter", bg).value || "4/4").split("/").map(Number), barLen = num * 4 / den;
      let marks = d.audio_bars;
      if (!marks || marks.length < 2) {
        const n = Math.max(1, d.bars || 1), pad = 0.1, span = Math.max(0.5, (clip.duration || 0) - 2 * pad);
        marks = Array.from({ length: n + 1 }, (_, i) => pad + span * i / n);
      }
      if (t < marks[0] || t >= marks[marks.length - 1]) return null;
      let i = 0; while (i < marks.length - 2 && t >= marks[i + 1]) i++;
      return [i, (t - marks[i]) / (marks[i + 1] - marks[i]) * barLen];
    };
    const loop = () => {
      if (!document.body.contains(bg) || clip.paused || clip.ended) { ed.markAt(null); return; }
      const w = where(); w ? ed.markAt(w[0], w[1]) : ed.markAt(null);
      raf = requestAnimationFrame(loop);
    };
    clip.addEventListener("play", () => { cancelAnimationFrame(raf); loop(); });
    ["pause", "ended"].forEach(ev => clip.addEventListener(ev, () => { cancelAnimationFrame(raf); ed.markAt(null); }));
    clip.addEventListener("seeked", () => { if (clip.paused) { const w = where(); w ? ed.markAt(w[0], w[1]) : ed.markAt(null); } });
  }
  $("#abcSave", bg).onclick = async () => {
    try {
      const r = await api(`/api/licks/${d.id}/abc`, { body: abcBody() });
      S.svgCache.clear(); toast("Notes saved" + (r.issues.length ? " — " + abcIssues(r) : ""));
      Band.sequencer.stop(true); bg.remove(); openLick(d.id, "replace");
    } catch (err) { $("#abcMsg", bg).textContent = err.message; }
  };
  $("#mPractice", bg).onclick = () => { bg.remove(); startPractice([d.id], 0); };
  $("#mStar", bg).onclick = async () => {
    const on = !$("#mStar", bg).classList.contains("on");
    await api(`/api/licks/${d.id}`, { method: "PATCH", body: { favorite: on ? 1 : 0 } });
    $("#mStar", bg).classList.toggle("on", on);
  };
  $("#mSave", bg).onclick = async () => {
    try {
      await api(`/api/licks/${d.id}`, {
        method: "PATCH", body: {
          name: $("#eName", bg).value.trim(), number: +$("#eNumber", bg).value, function: $("#eFunction", bg).value, tonic_name: $("#eTonic", bg).value.trim(),
          mode: $("#eMode", bg).value, artist: $("#eArtist", bg).value, tune: $("#eTune", bg).value,
          year: $("#eYear", bg).value ? +$("#eYear", bg).value : null,
          tags: $("#eTags", bg).value.split(",").map(s => s.trim()).filter(Boolean),
          comment: $("#eComment", bg).value, notes: $("#eNotes", bg).value,
        }
      });
      S.svgCache.clear();
      toast("Saved");
    } catch (e) { toast(e.message); }
  };
  $("#mReanalyse", bg).onclick = async () => { const r = await api(`/api/licks/${d.id}/reanalyse`, { body: {} }); toast("Auto tags: " + r.auto_tags.slice(0, 6).join(", ")); };
  $("#mArchive", bg).onclick = async () => { await api(`/api/licks/${d.id}`, { method: "PATCH", body: { archived: d.archived ? 0 : 1 } }); close(); };
  $("#mDelete", bg).onclick = async () => {
    if (!confirm(`Delete Lick ${d.number} and its practice history? The MusicXML file stays in library/licks.`)) return;
    await api(`/api/licks/${d.id}`, { method: "DELETE" }); close();
  };
}

/* ---------------------------------------------------------------- Import */
VIEWS.import = async () => {
  main().innerHTML = `
    <h1>Import</h1>
    <p class="sub">Licks come from MusicXML (export from MuseScore: File › Export › MusicXML). Chords come from your iReal Pro playlists.</p>
    <div class="grid cols-2">
      <div class="card">
        <h3>Lick sheet or solo</h3>
        <div class="grid cols-2">
          <label class="field" style="grid-column: span 2">MusicXML file (.musicxml, .xml, .mxl)<input type="file" id="iFile" accept=".musicxml,.xml,.mxl"></label>
          <label class="field">How to split<select id="iMode">
            <option value="auto">Auto (Lick N texts, double barlines, else short licks by harmony)</option>
            <option value="sheet">Lick sheet (never one big lick)</option>
            <option value="phrases">A solo: cut into short licks by harmony</option>
            <option value="single">The whole file is one lick</option>
            <option value="range">Bars from a solo</option></select></label>
          <label class="field">Only these bars (optional; with "bars from a solo" each range is one lick)<input id="iRanges" placeholder="e.g. 1-90 or 17-20, 33-36"></label>
          <label class="field">Pitch in the file<select id="iPitch">
            <option value="auto">Auto (B♭ trumpet unless the file says otherwise)</option>
            <option value="written">Written for B♭ trumpet</option><option value="concert">Concert pitch</option></select></label>
          <label class="field">Tags for all (comma separated)<input id="iTags" placeholder="e.g. Blues"></label>
          <label class="field">Artist (if the lick's staff text doesn't name one)<input id="iArtist"></label>
          <label class="field">Tune / year (if the staff text doesn't name them)<div class="row"><input id="iTune" class="grow"><input id="iYear" style="width:80px" placeholder="year"></div></label>
        </div>
        <p><button class="primary" id="iPreview">Preview</button></p>
      </div>
      <div class="card">
        <h3>iReal Pro playlist</h3>
        <p class="small muted">In iReal Pro: Share › the playlist › HTML (or copy the irealb:// link from the forum). Tunes are stored with their chords; repeats and endings are expanded.</p>
        <label class="field">Playlist file (.html / .txt)<input type="file" id="plFile" accept=".html,.htm,.txt"></label>
        <label class="field" style="margin-top:8px">…or paste the irealb:// link<textarea id="plText" rows="3"></textarea></label>
        <label class="field" style="margin-top:8px">Playlist name<input id="plName" placeholder="e.g. Jazz 1400"></label>
        <p><button class="primary" id="plGo">Import tunes</button> <span id="plMsg" class="muted"></span></p>
      </div>
    </div>
    <div id="iResults"></div>`;
  $("#iPreview").onclick = previewImport;
  $("#plGo").onclick = async () => {
    const fd = new FormData();
    if ($("#plFile").files[0]) fd.append("file", $("#plFile").files[0]);
    fd.append("text", $("#plText").value); fd.append("name", $("#plName").value);
    $("#plMsg").textContent = "Importing…";
    try { const r = await api("/api/playlist", { body: fd }); $("#plMsg").innerHTML = `${r.imported} tunes imported. <a href="#play">Open Play</a>`; }
    catch (e) { $("#plMsg").textContent = e.message; }
  };
};

async function previewImport() {
  const f = $("#iFile").files[0];
  if (!f) { toast("Choose a MusicXML file first"); return; }
  const fd = new FormData();
  fd.append("file", f);
  for (const [k, id] of [["mode", "iMode"], ["ranges", "iRanges"], ["pitch_in_file", "iPitch"], ["artist", "iArtist"], ["tune", "iTune"], ["year", "iYear"], ["tags", "iTags"]])
    fd.append(k, $("#" + id).value);
  $("#iResults").innerHTML = `<div class="empty spin">Reading ${esc(f.name)}…</div>`;
  let p;
  try { p = await api("/api/import/preview", { body: fd }); }
  catch (e) { $("#iResults").innerHTML = `<div class="card warn">${esc(e.message)}</div>`; return; }
  renderImportPreview(p, "#iResults");
}

function sliceBars(play, from, to) {
  const bl = play.ts[0] * 4 / play.ts[1], a = (from - 1) * bl, z = to * bl;
  const before = play.chords.filter(c => c[0] <= a).slice(-1)[0];
  const chords = play.chords.filter(c => c[0] > a && c[0] < z).map(c => [c[0] - a, c[1]]);
  if (before && !chords.some(c => c[0] === 0)) chords.unshift([0, before[1]]);
  return { ts: play.ts, bars: to - from + 1, length: (to - from + 1) * bl, chords,
    notes: play.notes.filter(n => n[0] >= a && n[0] < z).map(n => [n[0] - a, Math.min(n[1], z - n[0]), n[2]]) };
}

function renderImportPreview(p, target, banner = "") {
  if (!p.items.length) { $(target).innerHTML = banner + `<div class="card">No licks found.</div>`; return; }
  $(target).innerHTML = banner + `<h2>${p.items.length} lick${p.items.length > 1 ? "s" : ""} found</h2>` + p.items.map(it => `
    <div class="card preview-item" data-i="${it.index}">
      <div><div class="row" style="margin-bottom:6px">${playBtn("data-plisten", it.index)}<b>Lick ${it.number}</b>
        <span class="muted small">bars ${esc(it.bars_in_source)}</span></div><div class="paper">${it.svg}</div>
        ${it.bars > 1 ? `<div class="row small" style="margin-top:6px;flex-wrap:wrap"><span class="muted">Listen to one bar:</span>${Array.from({ length: it.bars }, (_, k) =>
          `<button class="ghost listen" data-idle="▶ ${k + 1}" data-plbar="${it.index}:${k + 1}">▶ ${k + 1}</button>`).join("")}</div>` : ""}
        <div style="margin-top:8px"><span class="chip fn">${esc(it.function)} · ${esc(it.tonic_name)}${it.mode === "minor" ? "m" : ""}</span>${it.auto_tags.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>
        <div class="small muted">Bars ${esc(it.bars_in_source)} in the file · chords: ${it.chords.map(c => showSym(c[1])).join(" ")}</div></div>
      <div class="fields">
        <label class="check" style="grid-column: span 2"><input type="checkbox" class="keep" ${it.duplicate ? "" : "checked"}> Import this lick</label>
        ${(it.related || []).map(r => `<div class="warn" style="grid-column: span 2">${esc(r.text)}</div>`).join("")}
        ${it.replaces ? `<div class="warn" style="grid-column: span 2">Lick ${it.number} exists — importing updates its music and tags and keeps its practice history.</div>` : ""}
        <label class="field">Number<input class="number" type="number" value="${it.number}"></label>
        <label class="field">Function<select class="function">${p.functions.map(fn => `<option ${fn === it.function ? "selected" : ""}>${esc(fn)}</option>`).join("")}</select></label>
        <label class="field">Artist<input class="artist" value="${esc(it.artist)}"></label>
        <label class="field">Tune<input class="tune" value="${esc(it.tune)}"></label>
        <label class="field">Year<input class="year" value="${esc(it.year || "")}"></label>
        <label class="field">Your tags<input class="tags" value="${esc(it.tags.join(", "))}"></label>
        <label class="field" style="grid-column: span 2">Comment<input class="comment" value="${esc(it.comment)}"></label>
      </div>
    </div>`).join("") + `<p><button class="primary big" id="iSave">Save to library</button></p>`;
  $$("[data-plisten]").forEach(b => b.onclick = () => listen(b, async () => p.items.find(x => x.index === +b.dataset.plisten).play));
  $$("[data-plbar]").forEach(b => b.onclick = () => {
    const [i, k] = b.dataset.plbar.split(":").map(Number);
    listen(b, async () => sliceBars(p.items.find(x => x.index === i).play, k, k));
  });
  $("#iSave").onclick = async () => {
    const items = $$(".preview-item").map(el => ({
      index: +el.dataset.i, keep: $(".keep", el).checked, number: +$(".number", el).value, function: $(".function", el).value,
      artist: $(".artist", el).value, tune: $(".tune", el).value, year: $(".year", el).value || null,
      tags: $(".tags", el).value.split(",").map(s => s.trim()).filter(Boolean), comment: $(".comment", el).value,
    }));
    const nums = items.filter(i => i.keep).map(i => i.number);
    if (new Set(nums).size !== nums.length) { toast("Two licks have the same number"); return; }
    try {
      const r = await api("/api/import/save", { body: { token: p.token, items } });
      S.svgCache.clear();
      $(target).innerHTML = `<div class="card">Saved ${r.ids.length} lick${r.ids.length === 1 ? "" : "s"}. <a href="#library">Open the library</a> or <a href="#licks">start today's session</a>.</div>`;
    } catch (e) { toast(e.message); }
  };
}

/* ---------------------------------------------------------------- Tunes */
VIEWS.play = async () => {
  const tunes = await api("/api/tunes");
  main().innerHTML = `
    <h1>Play</h1>
    <p class="sub">${tunes.length} tunes from iReal. Pick one to see its harmony and fit your licks into it.</p>
    ${tunes.length ? "" : `<div class="card">No tunes yet. <a href="#import">Import an iReal playlist</a>.</div>`}
    <div class="tune-layout" ${tunes.length ? "" : 'style="display:none"'}>
      <div class="card"><input id="tSearch" placeholder="Search tunes" style="width:100%;margin-bottom:8px">
        <label class="check small"><input type="checkbox" id="tLearning"> Only tunes I'm learning</label>
        <div class="tune-list" id="tList"></div></div>
      <div id="tDetail"><div class="empty">Choose a tune</div></div>
    </div>`;
  if (!tunes.length) return;
  const draw = () => {
    const q = $("#tSearch").value.toLowerCase();
    $("#tList").innerHTML = tunes.filter(t => (!q || t.title.toLowerCase().includes(q) || (t.composer || "").toLowerCase().includes(q))
      && (!$("#tLearning").checked || t.learning)).slice(0, 400)
      .map(t => `<div data-t="${esc(t.title)}" class="${S.fit && S.fit.title === t.title ? "sel" : ""}">${t.learning ? "★ " : ""}${esc(t.title)}
        <span class="small muted">${esc(t.composer || "")}</span></div>`).join("");
    $$("#tList [data-t]").forEach(el => el.onclick = () => { openTune(el.dataset.t); $$("#tList div").forEach(x => x.classList.remove("sel")); el.classList.add("sel"); });
  };
  $("#tSearch").oninput = draw; $("#tLearning").onchange = draw;
  draw();
  if (S.fit && S.fit.title) openTune(S.fit.title, true);
};

async function openTune(title, keepPlan) {
  const t = await api(`/api/tune?title=${encodeURIComponent(title)}`);
  if (!keepPlan || !S.fit || S.fit.title !== title) S.fit = { title, tune: t, plan: null, placements: [] };
  S.fit.tune = t;
  $("#tDetail").innerHTML = `
    <div class="card">
      <div class="row"><div class="grow"><h1>${esc(t.title)}</h1>
        <div class="muted">${esc(t.composer || "")} · ${esc(t.style || "")} · key ${showSym(t.key.replace("-", "m"))} · ${t.ts[0]}/${t.ts[1]} · ${t.bars.length} bars</div></div>
        <label class="check"><input type="checkbox" id="tLearn" ${t.learning ? "checked" : ""}> I'm learning this tune</label></div>
      <div class="chart" id="tChart"></div>
      <div class="row">
        <label class="field">Space between licks<select id="fGap"><option value="0" selected>none</option><option value="1">1 bar</option><option value="2">2 bars</option><option value="4">4 bars</option></select></label>
        <span class="field">Use licks that are<span class="row"><label class="check"><input type="checkbox" id="fFavs" checked> Favorites</label>
          <label class="check"><input type="checkbox" id="fToday" checked> Today's exercise</label></span></span>
        <button class="primary" id="fGo">Fit my licks</button>
        <span class="muted small">Licks are matched by their chords (root motion and chord type) and transposed to fit.</span>
      </div>
    </div>
    <div id="fOut"></div>`;
  $("#tLearn").onchange = e => api("/api/tune/learning", { body: { title, learning: e.target.checked } });
  $("#fGo").onclick = () => runFit(title);
  drawChart();
  if (S.fit.plan) drawFit();
}

function drawChart() {
  const F = S.fit, t = F.tune;
  const fnAt = {};
  (t.sections || []).forEach(s => { fnAt[s.bar] = s; });
  const placed = {};
  F.placements.forEach(p => { for (let k = 0; k < p.bars; k++) placed[p.bar + k] = p; });
  const cands = F.plan ? F.plan.candidates : {};
  let lastChord = null;
  $("#tChart").innerHTML = t.bars.map((cs, i) => {
    const s = fnAt[i + 1];
    const txt = cs.length ? cs.map(c => { lastChord = c; return showSym(c); }).join("  ") : "%";
    const nc = (cands[i] || []).length;
    const pl = placed[i + 1];
    return `<div class="bar ${s ? "fn" : ""} ${pl ? "placed" : ""}" data-bar="${i}" title="${nc ? nc + " licks fit here — click" : ""}">
      <span class="n">${i + 1}${nc ? " · " + nc : ""}</span>${txt}${s ? `<span class="fnl">${esc(s.function)} → ${esc(written() ? transposeSym(s.key, 2) : s.key)}</span>` : ""}
      ${pl ? `<div class="snip" data-snip="${i + 1}" title="#${pl.number} — click to choose another lick">${pl.bar === i + 1 ? `<span class="snipno">#${pl.number}</span>` : ""}</div>` : ""}</div>`;
  }).join("");
  $$("#tChart .bar").forEach(el => el.onclick = () => {
    const b = +el.dataset.bar, pl = placed[b + 1];
    barMenu(pl ? pl.bar - 1 : b);
  });
  loadSnippets();
}

async function loadSnippets() {
  const F = S.fit;
  if (!F.placements.length) return;
  const key = JSON.stringify([F.title, S.settings.display, F.placements.map(p => [p.bar, p.lick_id, p.target_pc])]);
  if (F.snipKey !== key) {
    F.snipKey = key;
    F.snips = null;
    try { F.snips = await api("/api/fit/snippets", { body: { title: F.title, placements: F.placements } }); }
    catch (e) { return; }
    if (F.snipKey !== key) return;  // placements changed while rendering
  }
  if (!F.snips) return;
  $$("#tChart [data-snip]").forEach(el => {
    const svg = F.snips[el.dataset.snip];
    if (svg && !el.querySelector("svg")) el.insertAdjacentHTML("beforeend", svg);
  });
}

function barMenu(b) {
  const F = S.fit;
  if (!F.plan) { toast("Press “Fit my licks” first"); return; }
  const cs = F.plan.candidates[b] || [];
  const cur = F.placements.find(p => p.bar === b + 1);
  if (!cs.length) { toast(`No lick fits at bar ${b + 1}`); return; }
  const box = $("#barMenu") || document.createElement("div");
  box.id = "barMenu"; box.className = "card"; box.style.marginTop = "10px";
  box.innerHTML = `<div class="row"><b class="grow">Licks that fit at bar ${b + 1}</b>
      ${cur ? `<button id="bmRemove">Leave bar${cur.bars > 1 ? "s" : ""} empty</button>` : ""}<button class="ghost" id="bmClose">✕</button></div>
    <div class="lick-list" style="margin-top:8px">${cs.slice(0, 20).map((c, i) =>
    `<div class="lick-item" ${cur && cur.lick_id === c.lick_id ? 'style="border-color:var(--red)"' : ""}>${playBtn("data-clisten", i)}
     <span class="star ${c.favorite ? "on" : ""}" data-cstar="${i}" title="Favorite">★</span>
     <div class="grow">${esc(c.label)} <span class="muted small">in ${esc(c.key)}${c.fits_range ? "" : " · out of range"}</span></div>
     ${cur && cur.lick_id === c.lick_id ? '<span class="muted small">placed</span>' : `<button data-add="${i}">Use this</button>`}</div>`).join("")}</div>`;
  $("#tChart").after(box);
  box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  $("#bmClose", box).onclick = () => box.remove();
  if (cur) $("#bmRemove", box).onclick = () => {
    F.placements = F.placements.filter(p => p !== cur);
    box.remove(); drawChart(); drawFit();
  };
  $$("[data-cstar]", box).forEach(star => star.onclick = async () => {
    const c = cs[+star.dataset.cstar], on = c.favorite ? 0 : 1;
    await api(`/api/licks/${c.lick_id}`, { method: "PATCH", body: { favorite: on } });
    // the same lick may be offered at other bars, and may already be placed: keep them all in step
    for (const list of [...Object.values(F.plan.candidates), F.placements])
      for (const x of list) if (x.lick_id === c.lick_id) x.favorite = on;
    star.classList.toggle("on", !!on);
  });
  $$("[data-clisten]", box).forEach(btn => btn.onclick = () => {
    const c = cs[+btn.dataset.clisten];
    listen(btn, () => api(`/api/licks/${c.lick_id}/play?pc=${c.target_pc}`));
  });
  $$("[data-add]", box).forEach(btn => btn.onclick = () => {
    const c = cs[+btn.dataset.add];
    F.placements = F.placements.filter(p => p.bar + p.bars - 1 < c.bar || p.bar > c.bar + c.bars - 1);
    F.placements.push({ ...c });
    F.placements.sort((a, b) => a.bar - b.bar);
    box.remove();
    drawChart(); drawFit();
  });
}

async function runFit(title) {
  $("#fOut").innerHTML = `<div class="empty spin">Matching licks to the changes…</div>`;
  const plan = await api("/api/fit", { body: { title, gap_bars: +$("#fGap").value,
    pool: [$("#fFavs").checked ? "favorites" : "", $("#fToday").checked ? "today" : ""].filter(Boolean) } });
  S.fit.plan = plan;
  S.fit.placements = plan.placements;
  drawChart();
  drawFit();
}

async function drawFit() {
  const F = S.fit;
  const nSpots = Object.keys(F.plan.candidates).length;
  $("#fOut").innerHTML = `
    <div class="card" style="margin-top:14px">
      <div class="row"><h3 class="grow">${F.placements.length} lick${F.placements.length === 1 ? "" : "s"} placed · ${nSpots} bar${nSpots === 1 ? "" : "s"} where something fits</h3>
        <button class="primary" id="fPlay">▶ Play with band</button>
        <div class="slider small muted">Tempo <input type="range" id="fTempo" min="60" max="320" value="${S.settings.tempo}"> <b id="fTempoV">${S.settings.tempo}</b></div>
        <button id="fSave">Download MusicXML</button></div>
      ${F.placements.length ? `<table class="placements"><tr><th>Bar</th><th>Lick</th><th>Key</th><th></th></tr>
      ${F.placements.map((p, i) => `<tr><td>${p.bar}</td><td><select data-pi="${i}">${(F.plan.candidates[p.bar - 1] || [p]).map((c, j) =>
        `<option value="${j}" ${c.lick_id === p.lick_id ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></td>
        <td>${esc(p.key)}${p.fits_range ? "" : ' <span class="warn">range</span>'}</td><td><button class="ghost" data-del="${i}">Remove</button></td></tr>`).join("")}</table>`
        : `<p class="muted">No lick fits this tune's changes yet. Licks fit where the chords match beat for beat — e.g. a 1-bar ii–V lick needs a bar with two chords.</p>`}
      <p class="small muted">Click a bar in the chart to place a different lick there. Empty bars are yours to improvise.</p>
    </div>
    <div class="paper big" id="fSheet" style="margin-top:12px"><span class="muted">Rendering…</span></div>`;
  $$("[data-pi]").forEach(sel => sel.onchange = () => {
    const i = +sel.dataset.pi, p = F.placements[i];
    F.placements[i] = { ...(F.plan.candidates[p.bar - 1][+sel.value]) };
    drawChart(); drawFit();
  });
  $$("[data-del]").forEach(b => b.onclick = () => { F.placements.splice(+b.dataset.del, 1); drawChart(); drawFit(); });
  $("#fTempo").oninput = e => $("#fTempoV").textContent = e.target.value;
  $("#fPlay").onclick = playTune;
  $("#fSave").onclick = async () => {
    try {
      const r = await fetch("/api/fit/file", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: F.title, placements: F.placements, bpm: +$("#fTempo").value }) });
      if (!r.ok) throw new Error(await r.text());
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "tune.musicxml";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click();
      toast("Downloaded " + name);
    } catch (e) { toast(e.message); }
  };
  try { $("#fSheet").innerHTML = await api("/api/fit/svg", { body: { title: F.title, placements: F.placements } }); }
  catch (e) { $("#fSheet").textContent = e.message; }
}

async function playTune() {
  const seq = Band.sequencer;
  if (seq.running) { seq.stop(); return; }
  const data = await api("/api/fit/play", { body: { title: S.fit.title, placements: S.fit.placements } });
  $("#fPlay").textContent = "■ Stop";
  seq.start({
    tempo: () => +($("#fTempo") || { value: S.settings.tempo }).value, swing: () => S.settings.swing,
    next: n => ({ seq: data, leadIn: n === 0 ? S.settings.lead_in : 0, playLick: true, backing: S.settings.backing_style || "band", response: false, info: {} }),
    onStop: () => { const b = $("#fPlay"); if (b) b.textContent = "▶ Play with band"; },
  });
}

/* ---------------------------------------------------------------- Progress */
VIEWS.progress = async () => {
  const s = await api("/api/stats");
  const max = Math.max(1, ...s.per_day.map(d => d.n));
  const maxU = Math.max(1, ...s.upcoming.map(d => d.n));
  const st = s.states;
  main().innerHTML = `
    <h1>Progress</h1><p class="sub">How your lick vocabulary is settling into long-term memory.</p>
    <div class="hero">
      <div class="tile"><div class="n">${s.licks}</div><div class="l">licks</div></div>
      <div class="tile"><div class="n">${st.known || 0}</div><div class="l">known (30+ day memory)</div></div>
      <div class="tile"><div class="n">${(st.learning || 0) + (st.reviewing || 0)}</div><div class="l">learning / reviewing</div></div>
      <div class="tile"><div class="n">${st.lapsed || 0}</div><div class="l">lapsed</div></div>
      <div class="tile"><div class="n">${st.new || 0}</div><div class="l">not started</div></div>
      <div class="tile"><div class="n">${s.streak}</div><div class="l">day streak</div></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><h3>Licks practised per day (30 days)</h3>
        <div class="bars-chart">${s.per_day.map(d => `<div class="${d.n ? "" : "zero"}" style="height:${d.n / max * 100}%" title="${d.day}: ${d.n}"></div>`).join("")}</div>
        <div class="axis"><span>${s.per_day[0].day.slice(5)}</span><span>today</span></div></div>
      <div class="card"><h3>Coming up (next 14 days)</h3>
        <div class="bars-chart">${s.upcoming.map(d => `<div class="${d.n ? "" : "zero"}" style="height:${d.n / maxU * 100}%" title="${d.in_days === 0 ? "today" : "in " + d.in_days + " days"}: ${d.n}"></div>`).join("")}</div>
        <div class="axis"><span>today</span><span>+13 days</span></div></div>
      <div class="card"><h3>Licks by harmonic function</h3>
        <table>${Object.entries(s.functions).map(([f, n]) => `<tr><td>${esc(f)}</td><td>${n}</td></tr>`).join("") || "<tr><td class=muted>No licks yet</td></tr>"}</table></div>
      <div class="card"><h3>Keys you find hard</h3>
        ${(() => {
          const rows = Object.entries(s.hard_by_key || {}).map(([pc, nums]) => ({ pc: +pc, nums })).sort((a, b) => b.nums.length - a.nums.length || a.pc - b.pc);
          if (!rows.length) return '<span class="muted">None marked now. Press “Mark key hard” (or T) while practising. Keys you play well are unmarked automatically.</span>';
          const name = pc => pretty(SIMPLE_ROOTS[(pc + (written() ? 2 : 0)) % 12]);
          const wide = rows.filter(r => r.nums.length >= 3);
          return `<div>${rows.map(r => `<span class="chip user" title="Hard in lick ${r.nums.map(n => "#" + n).join(", ")}">${esc(name(r.pc))} · ${r.nums.length} lick${r.nums.length === 1 ? "" : "s"}</span>`).join("")}</div>
            ${wide.length ? `<p class="small" style="margin-top:8px">${wide.map(r => esc(name(r.pc))).join(", ")} ${wide.length === 1 ? "is" : "are"} hard in three or more licks. That points to the key itself (fingering, range), not one lick: try scales and arpeggios there.</p>` : ""}
            <button id="hardFromProgress" style="margin-top:8px">Practice hard keys</button>`;
        })()}
        <div class="small muted" style="margin-top:10px">Marked in recent sessions: ${Object.entries(s.trouble_keys).map(([k, n]) => `${esc(k)} × ${n}`).join(", ") || "none"}</div>
        <h3 style="margin-top:16px">Recent ratings</h3>
        <div>${Object.entries(s.recent_ratings).map(([k, n]) => `<span class="chip">${k}: ${n}</span>`).join("") || '<span class="muted">—</span>'}</div></div>
    </div>`;
  const hp = $("#hardFromProgress");
  if (hp) hp.onclick = async () => {
    const h = await api("/api/hard");
    if (h.licks.length) startPractice(h.licks.map(l => l.id), 0, { hardOnly: true }); else toast("No keys are marked hard.");
  };
};

/* ---------------------------------------------------------------- Settings */
VIEWS.settings = async () => {
  const st = S.settings = await api("/api/settings");
  const meta = S.meta;
  main().innerHTML = `
    <h1>Settings</h1><p class="sub">Library folder: <code>${esc(meta.library)}</code></p>
    <div class="grid cols-2">
      <div class="card"><h3>Notation and range</h3>
        <div class="grid cols-2">
          <label class="field">Show music<select id="sDisplay"><option value="written" ${st.display === "written" ? "selected" : ""}>Written for B♭ trumpet</option>
            <option value="concert" ${st.display === "concert" ? "selected" : ""}>Concert pitch</option></select></label>
          <span></span>
          <label class="field">Lowest written note<input id="sLow" value="${midiName(st.range_low)}"></label>
          <label class="field">Highest written note<input id="sHigh" value="${midiName(st.range_high)}"></label>
        </div>
        <p class="small muted">When a key would go outside this range, the lick moves up or down an octave. Keys where it still doesn't fit are dimmed.</p>
      </div>
      <div class="card"><h3>Key order</h3>
        <label class="field">Default order<select id="sOrder">${Object.entries(meta.orders).map(([k, v]) => `<option value="${k}" ${k === st.key_order ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        <label class="field" style="margin-top:8px">“My order” steps from the original key, in semitones<input id="sSteps" value="${st.my_steps.join(", ")}"></label>
        <p class="small muted">0, 5, 7, -2 = original key, a 4th up, a 5th up, a whole step down. The remaining keys follow in cycle-of-4ths order.</p>
      </div>
      <div class="card"><h3>Daily practice</h3>
        <div class="grid cols-2">
          <label class="field">New licks per day<input id="sNew" type="number" min="0" value="${st.new_per_day}"></label>
          <label class="field">Max reviews per day<input id="sMax" type="number" min="1" value="${st.max_reviews}"></label>
          <label class="field" title="Licks still rated Hard or Again that go into one day's plan. About 3-4 fit in an hour; the others wait.">Hard licks in focus<input id="sFocus" type="number" min="1" value="${st.focus_cap ?? 4}"></label>
          <label class="field" title="On: no new licks are offered while any lick is still rated Hard or Again."><span>Hold new licks until hard ones are Good</span><input id="sHold" type="checkbox" ${st.hold_new !== false ? "checked" : ""}></label>
          <label class="field" title="Clean passes in a row, in every key of the lick's set, for a Good rating">Good: clean passes<input id="sGPass" type="number" min="1" max="8" value="${(st.good_def || {}).clean_passes ?? 2}"></label>
          <label class="field" title="Every key of the set must reach this tempo. 0 means no tempo requirement.">Good: minimum tempo (bpm)<input id="sGTempo" type="number" min="0" max="320" value="${(st.good_def || {}).min_tempo ?? 0}"></label>
          <label class="field" title="Good ratings in a row before more keys join the set">Good ratings to add keys<input id="sGSess" type="number" min="1" value="${(st.good_def || {}).sessions ?? 2}"></label>
          <label class="field" title="Keys joining the set each time">Keys added each time<input id="sGAdd" type="number" min="1" max="12" value="${(st.good_def || {}).add_keys ?? 1}"></label>
          <label class="field" title="Keys a new lick starts with, in 'my order'">Starter keys per lick<input id="sGStart" type="number" min="1" max="12" value="${(st.good_def || {}).starter_keys ?? 2}"></label>
          <label class="field" title="Hard or Again in a row before the lick is shelved and cut back to one key">Shelve after Hard/Again ×<input id="sGStuck" type="number" min="1" value="${(st.good_def || {}).stuck_after ?? 4}"></label>
          <label class="field" title="How long a stuck lick rests">Shelve for (days)<input id="sGDays" type="number" min="1" value="${(st.good_def || {}).shelve_days ?? 21}"></label>
          <label class="field">Target recall (FSRS retention)<select id="sRet">${[0.8, 0.85, 0.9, 0.93, 0.95].map(v => `<option ${v === st.desired_retention ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        </div>
        <p class="small muted">Higher recall means shorter gaps between reviews (more practice, fewer forgotten licks).</p>
      </div>
      <div class="card" style="grid-column: span 2"><h3>Sound</h3>
        <p class="small ${S.sound && S.sound.available ? "muted" : "warn"}">${S.sound && S.sound.available
          ? `Real instruments from <code>${esc(S.sound.soundfont)}</code>`
          : `Using the built-in synth: ${esc((S.sound && S.sound.error) || "SoundFont playback is not available")}`}</p>
        <div class="grid cols-3">
          <label class="field">Playback<select id="sEngine"><option value="soundfont" ${st.sound_engine !== "synth" ? "selected" : ""}>SoundFont (real instruments)</option>
            <option value="synth" ${st.sound_engine === "synth" ? "selected" : ""}>Built-in synth</option></select></label>
          <label class="field">Lick instrument<select id="sLead"><option value="trumpet" ${st.lead_sound !== "muted_trumpet" ? "selected" : ""}>Trumpet</option>
            <option value="muted_trumpet" ${st.lead_sound === "muted_trumpet" ? "selected" : ""}>Muted trumpet</option></select></label>
          <label class="field">SoundFont file (.sf2 / .sf3; empty = automatic)<input id="sFont" value="${esc(st.soundfont || "")}" list="sFonts" placeholder="automatic">
            <datalist id="sFonts">${((S.sound && S.sound.candidates) || []).map(c => `<option value="${esc(c)}">`).join("")}</datalist></label>
        </div>
      </div>
      <div class="card"><h3>Playback</h3>
        <div class="grid cols-2">
          <label class="field">Tempo (quarter = )<input id="sTempo" type="number" value="${st.tempo}"></label>
          <label class="field">Swing (50 = straight, 67 = triplet)<input id="sSwing" type="number" min="50" max="75" value="${Math.round(st.swing * 100)}"></label>
          <label class="field">Count-in bars<input id="sLeadIn" type="number" min="0" max="2" value="${st.lead_in}"></label>
          <label class="field">Times through each key<input id="sReps" type="number" min="1" value="${st.reps_per_key}"></label>
          <label class="field">Lick practice: starting tempo in a new key<input id="sStart" type="number" min="40" max="320" value="${st.start_tempo}"></label>
          <label class="field">Lick practice: faster/slower step<input id="sStep" type="number" min="1" max="40" value="${st.tempo_step}"></label>
          <label class="check"><input type="checkbox" id="sStay" ${st.stay_in_key !== false ? "checked" : ""}> Stay in one key until I move on</label>
          <label class="check"><input type="checkbox" id="sHear" ${st.play_lick ? "checked" : ""}> Hear the lick</label>
          <label class="check"><input type="checkbox" id="sResp" ${st.call_response ? "checked" : ""}> Leave bars for me to play it back</label>
          <label class="check"><input type="checkbox" id="sBack" ${st.backing ? "checked" : ""}> Backing</label>
          <label class="field">Backing for practice and tunes<select id="sStyle"><option value="band" ${st.backing_style !== "piano" ? "selected" : ""}>Piano, bass and drums</option>
            <option value="piano" ${st.backing_style === "piano" ? "selected" : ""}>Piano chords only</option></select></label>
        </div>
        <div class="row" style="margin-top:10px"><span class="small muted">Volume</span>
          lick <input type="range" id="sVolL" min="0" max="150" value="${Math.round(Band.engine.vol ? Band.engine.vol.lead * 100 : 90)}">
          band <input type="range" id="sVolB" min="0" max="150" value="${Math.round(Band.engine.vol ? Band.engine.vol.back * 100 : 70)}"></div>
      </div>
    </div>
    <p><button class="primary big" id="sSave">Save settings</button></p>
    <div class="card" style="margin-top:18px"><h3>Server</h3>
      <p class="small muted">Starts Licksmith again, with the same options. Use it after an update. Pages reload by themselves when it is back (a few seconds).
        Everything you saved stays. A transcription that is running is stopped.</p>
      <button id="sRestart">Restart server</button> <span class="small muted" id="sRestartMsg"></span></div>`;
  $("#sRestart").onclick = async () => {
    const msg = $("#sRestartMsg");
    if (!confirm("Restart the Licksmith server now?")) return;
    const go = force => api("/api/restart", { body: { force } });
    try { await go(false); }
    catch (e) {
      if (!/transcription/i.test(e.message) || !confirm(e.message + "\n\nRestart anyway?")) { msg.textContent = e.message; return; }
      await go(true).catch(() => {});
    }
    msg.textContent = "Restarting…";
    // wait until the old process is gone and the new one answers, then reload the page
    let down = false;
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 500));
      try { await api("/api/meta"); if (down || i > 6) { location.reload(); return; } } catch (e) { down = true; }
    }
    msg.textContent = "The server did not come back. Start it again in the terminal: licksmith serve";
  };
  $("#sSave").onclick = async () => {
    const lo = nameMidi($("#sLow").value), hi = nameMidi($("#sHigh").value);
    if (lo === null || hi === null || hi - lo < 12) { toast("Range: use note names like F#3 and C6, at least an octave apart"); return; }
    const steps = $("#sSteps").value.split(/[ ,]+/).filter(Boolean).map(Number);
    if (steps.some(isNaN)) { toast("Steps must be numbers"); return; }
    S.settings = await api("/api/settings", {
      method: "PUT", body: {
        display: $("#sDisplay").value, range_low: lo, range_high: hi, key_order: $("#sOrder").value, my_steps: steps,
        new_per_day: +$("#sNew").value, max_reviews: +$("#sMax").value, focus_cap: Math.max(1, +$("#sFocus").value || 4),
        good_def: { clean_passes: +$("#sGPass").value || 2, min_tempo: +$("#sGTempo").value || 0, sessions: +$("#sGSess").value || 2, add_keys: +$("#sGAdd").value || 1,
          starter_keys: +$("#sGStart").value || 2, stuck_after: +$("#sGStuck").value || 4, shelve_days: +$("#sGDays").value || 21 }, hold_new: $("#sHold").checked, desired_retention: +$("#sRet").value,
        tempo: +$("#sTempo").value, swing: +$("#sSwing").value / 100, lead_in: +$("#sLeadIn").value, reps_per_key: +$("#sReps").value,
        start_tempo: +$("#sStart").value, tempo_step: +$("#sStep").value, stay_in_key: $("#sStay").checked,
        play_lick: $("#sHear").checked, call_response: $("#sResp").checked, backing: $("#sBack").checked,
        backing_style: $("#sStyle").value, sound_engine: $("#sEngine").value, lead_sound: $("#sLead").value,
        soundfont: $("#sFont").value.trim(),
      }
    });
    S.svgCache.clear();
    $("#concertToggle").checked = S.settings.display === "concert";
    await refreshSound();
    toast(Band.rendered.on || S.settings.sound_engine === "synth" ? "Settings saved" : "Saved — " + (S.sound.error || "SoundFont not available"));
    go("settings");
  };
  const vol = () => { Band.engine.init(); Band.engine.setVolume("lead", $("#sVolL").value / 100); Band.engine.setVolume("back", $("#sVolB").value / 100); };
  $("#sVolL").oninput = vol; $("#sVolB").oninput = vol;
};

async function refreshSound() {
  try { S.sound = await api("/api/sound"); } catch (e) { S.sound = { available: false, error: e.message }; }
  Band.rendered.on = !!(S.sound.available && S.settings.sound_engine !== "synth");
}

/* ---------------------------------------------------------------- boot */
(async () => {
  [S.settings, S.meta] = await Promise.all([api("/api/settings"), api("/api/meta")]);
  await refreshSound();
  if (!Band.rendered.on && S.settings.sound_engine !== "synth")
    toast("Real instrument sound is off — " + (S.sound.error || "FluidSynth not available") + " (see Settings › Sound)", 9000);
  $("#concertToggle").checked = S.settings.display === "concert";
  $("#concertToggle").onchange = async e => {
    S.settings = await api("/api/settings", { method: "PUT", body: { display: e.target.checked ? "concert" : "written" } });
    S.svgCache.clear();
    if (S.tab === "practice" && S.practice) loadPracticeLick(); else go(S.tab);
  };
  wireNavArrows();
  let t = location.hash.slice(1);
  if (t === "align") t = "transcribe";
  if (t === "today") t = "licks";
  go(VIEWS[t] ? t : "routine");
})();
