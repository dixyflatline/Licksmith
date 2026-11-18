/* Transcribe tab: open a YouTube / YouTube Music link or an audio file, let Muscriptor transcribe the lead instrument and the bass,
   then hand the result to the bar editor (align.js: waveform, bar lines, per-bar notes, send to library). */
"use strict";

const INSTR_NAMES = {
  acoustic_piano: "Piano", electric_piano: "Electric piano", organ: "Organ", acoustic_guitar: "Guitar", clean_electric_guitar: "Electric guitar",
  distorted_electric_guitar: "Distorted guitar", acoustic_bass: "Acoustic bass", electric_bass: "Electric bass", drums: "Drums", violin: "Violin",
  viola: "Viola", cello: "Cello", contrabass: "Contrabass", string_ensemble: "Strings", voice: "Voice", trumpet: "Trumpet", trombone: "Trombone",
  tuba: "Tuba", french_horn: "French horn", brass_section: "Brass section", soprano_and_alto_sax: "Alto / soprano sax", tenor_sax: "Tenor sax",
  baritone_sax: "Baritone sax", clarinet: "Clarinet", flutes: "Flute", chromatic_percussion: "Vibes / mallets",
};
const MODEL_HINT = { small: "fastest, least accurate", medium: "balanced", large: "most accurate, slowest" };

// "Also in the recording" checkboxes, shared by the new-transcription form and the "transcribe again" panel.
function bandChips(opts, checked, idPrefix) {
  return opts.band.map(b => `<label class="chip band-chip ${checked.includes(b) ? "on" : ""}" title="${esc(b)}">
    <input type="checkbox" data-band="${esc(b)}" ${checked.includes(b) ? "checked" : ""} style="display:none">${esc(INSTR_NAMES[b] || b)}</label>`).join("");
}
function wireChips(root) {
  $$(".band-chip", root).forEach(l => {
    const cb = $("input", l);
    cb.onchange = () => l.classList.toggle("on", cb.checked);
  });
}
const chosenBand = root => $$("input[data-band]", root).filter(c => c.checked).map(c => c.dataset.band);

VIEWS.transcribe = async () => {
  const [works, opts] = await Promise.all([api("/api/align"), api("/api/transcribe/options")]);
  S.tr = S.tr || { jobs: [], seen: new Set() };
  const usableOf = ws => ws.filter(w => w.has_audio && w.engines.length);
  let usable = usableOf(works);

  main().innerHTML = `
    <h1>Transcribe</h1>
    <p class="sub">Open a YouTube or YouTube Music link, or an audio recording. Muscriptor transcribes the lead instrument and the bass, then you line
      the notes up with bars and make licks from them.</p>
    <details id="trNew" class="card" ${usable.length ? "" : "open"}>
      <summary><b>New transcription</b></summary>
      <div style="margin-top:10px">
        <div class="row" style="margin-bottom:8px">
          <label class="check"><input type="radio" name="trSrc" value="url" checked> YouTube / YouTube Music link</label>
          <label class="check"><input type="radio" name="trSrc" value="file"> Audio file</label>
        </div>
        <div id="trUrlRow"><input id="trUrl" placeholder="https://www.youtube.com/watch?v=…  or  https://music.youtube.com/watch?v=…" style="width:100%"></div>
        <div id="trFileRow" style="display:none"><input id="trFile" type="file" accept="audio/*,video/*,.mp3,.m4a,.wav,.flac,.ogg,.aac,.webm,.mp4"></div>
        <div class="row" style="margin-top:10px">
          <label class="small">Title <input id="trTitle" placeholder="from the video / file name" style="width:230px"></label>
          <label class="small">Artist <input id="trArtist" placeholder="optional" style="width:180px"></label>
          <label class="small">Lead instrument <select id="trLead">${opts.leads.map(l => `<option value="${l.id}">${esc(l.label)}</option>`).join("")}</select></label>
          <label class="small">Model <select id="trModel">${opts.models.map(m => `<option value="${m}" ${m === opts.default_model ? "selected" : ""}>${m} (${MODEL_HINT[m]})</option>`).join("")}</select></label>
        </div>
        <div class="small muted" style="margin:10px 0 4px">Also in the recording (tell Muscriptor who plays, so it files each note under the right instrument;
          a bass is always included because its notes set the bar lines):</div>
        <div id="trBand" class="band-chips">${bandChips(opts, opts.default_band)}</div>
        <div class="row" style="margin-top:12px"><button class="primary" id="trGo">Transcribe</button><span id="trMsg" class="small muted"></span></div>
        <div class="small muted" style="margin-top:8px">A recording of two minutes takes a few minutes. The audio stays on this computer. Only use recordings for your own practice.</div>
      </div>
    </details>
    <details id="trDisk" class="card" style="margin-top:12px"><summary><b>Saved transcriptions on this computer</b> <span class="small muted" id="trDiskSub"></span></summary>
      <div id="trDiskBody" class="small" style="margin-top:8px"></div></details>
    <div id="trJobs"></div>
    <div id="trWork"></div>`;
  wireChips($("#trBand"));
  $$("input[name=trSrc]").forEach(r => r.onchange = () => {
    const f = $("input[name=trSrc]:checked").value === "file";
    $("#trUrlRow").style.display = f ? "none" : ""; $("#trFileRow").style.display = f ? "" : "none";
  });

  // ---------------------------------------------------------------- saved transcriptions (library/works): open or delete
  async function drawDisk() {
    const body = $("#trDiskBody"); if (!body) return;
    let list = [];
    try { list = await api("/api/works/disk"); } catch (e) { body.textContent = e.message; return; }
    $("#trDiskSub").textContent = `${list.length} folder${list.length === 1 ? "" : "s"}, ${Math.round(list.reduce((n, w) => n + w.mb, 0))} MB`;
    const when = t => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const byName = Object.fromEntries(list.map(w => [w.work, w]));
    body.innerHTML = list.length ? list.map(w => {
      const ok = w.has_audio && w.engines.length;
      const chips = [w.aligned ? "bars edited" : "no bar edits yet", `${w.engines.length} transcription${w.engines.length === 1 ? "" : "s"}`,
        ...(ok ? [] : [w.has_audio ? "no transcription" : "no audio"]), `${w.mb} MB`, `changed ${when(w.changed)}`];
      return `<div class="row" style="padding:8px 0;border-top:1px solid var(--line);align-items:baseline">
        <div class="grow"><b>${esc(w.title)}</b>${w.artist ? ` <span class="muted">— ${esc(w.artist)}</span>` : ""}
          <div class="muted">${esc(w.work)} · ${chips.map(esc).join(" · ")}</div>
          ${w.same_video_as.length ? `<div style="color:var(--brass)">Same video as ${w.same_video_as.map(n => esc(byName[n] ? n : n)).join(", ")}: probably a duplicate import.</div>` : ""}</div>
        <button data-disk-open="${esc(w.work)}" ${ok ? "" : "disabled"} title="${ok ? "Open in the bar editor" : "This folder has no audio or no transcription to open"}">Open</button>
        <button class="ghost" data-disk-del="${esc(w.work)}" style="color:var(--red)">Delete</button></div>`;
    }).join("") : `<div class="muted">Nothing saved yet.</div>`;
    $$("[data-disk-open]", body).forEach(b => b.onclick = () => { showWork(b.dataset.diskOpen); $("#trWork").scrollIntoView({ behavior: "smooth" }); });
    $$("[data-disk-del]", body).forEach(b => b.onclick = () => deleteWork(byName[b.dataset.diskDel]));
  }
  async function deleteWork(w) {
    if (!confirm(`Delete "${w.title}" (${w.work}, ${w.mb} MB) from this computer?\n\nThis removes its recording, transcriptions and your bar edits, and cannot be undone. Licks you already sent to the library stay.`)) return;
    try { await api(`/api/align/${encodeURIComponent(w.work)}`, { method: "DELETE" }); }
    catch (e) { toast(e.message); return; }
    toast(`Deleted ${w.title}`);
    if (openWork === w.work) { openWork = null; if (window.__alCleanup) window.__alCleanup(); $("#trWork").innerHTML = ""; }
    works.splice(0, works.length, ...(await api("/api/align")));
    usable = usableOf(works);
    drawDisk();
  }
  $("#trDisk").addEventListener("toggle", () => { if ($("#trDisk").open) drawDisk(); });
  drawDisk();

  // ---------------------------------------------------------------- jobs
  const stateText = { queued: "Waiting", downloading: "Downloading", preparing: "Preparing", transcribing: "Transcribing", done: "Done", error: "Failed" };
  let timer = 0, openWork = null;
  const draw = jobs => {
    const box = $("#trJobs");
    if (!box) return;
    const mins = s => s == null ? "" : s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
    const mark = { done: "✓", active: "●", todo: "○" };
    const live = j => !["done", "error"].includes(j.state);
    box.innerHTML = jobs.length ? `<div class="card" style="margin-top:12px"><b>Transcriptions</b>${jobs.slice(0, 6).map(j => `
      <div class="tr-job ${j.state}">
        <div class="row" style="align-items:baseline">
          <span class="tr-state">${stateText[j.state] || j.state}</span>
          <span class="grow"><b>${esc(j.title || j.label)}</b>${j.state === "error" ? ` <span class="muted small">${esc(j.error || "")}</span>` : ""}</span>
          ${live(j) && j.state !== "queued" ? `<span class="tr-pct">${j.percent}%</span>` : ""}
          ${j.state === "done" ? `<button data-open-work="${esc(j.work)}">Open</button>` : ""}
        </div>
        ${j.state === "queued" ? `<div class="small muted">Waiting for the earlier transcription to finish.</div>` : ""}
        ${live(j) && j.state !== "queued" ? `
          <ol class="tr-steps">${j.steps.map(s => `<li class="${s.state}"><span class="tr-mark">${mark[s.state]}</span> ${esc(s.label)}${s.state === "active" ? `
            <span class="muted"> — ${esc(j.detail || "")}</span>` : ""}</li>`).join("")}</ol>
          <div class="small muted">${j.elapsed_s != null ? `Running for ${mins(j.elapsed_s)}` : ""}${j.eta_s != null ? ` · about ${mins(j.eta_s)} left${j.estimated ? " (a guess)" : ""}` :
            j.step === "transcribe" ? " · working out the time left…" : ""}</div>` : ""}
        ${j.state === "done" && j.elapsed_s != null ? `<div class="small muted">Took ${mins(j.elapsed_s)}.</div>` : ""}
      </div>`).join("")}</div>` : "";
    $$("[data-open-work]", box).forEach(b => b.onclick = () => showWork(b.dataset.openWork));
  };
  async function showWork(name) {
    works.splice(0, works.length, ...(await api("/api/align")));
    usable = usableOf(works);
    openWork = name;
    if (usable.some(w => w.work === name)) { try { localStorage.setItem("alignWork", name); } catch (e) { /* storage blocked */ } await alignOpen(name, usable); }
  }
  async function poll() {
    if (S.tab !== "transcribe" || !$("#trJobs")) { clearTimeout(timer); return; }
    let jobs = [];
    try { jobs = await api("/api/transcribe/jobs"); } catch (e) { /* server busy */ }
    S.tr.jobs = jobs; draw(jobs);
    for (const j of jobs) {
      if (j.state === "done" && !S.tr.seen.has(j.id)) {
        S.tr.seen.add(j.id);
        toast(`Transcribed: ${j.title || j.label}`, 6000);
        if (!openWork) await showWork(j.work);  // nothing open yet: show it right away
      }
      if (j.state === "error") S.tr.seen.add(j.id);
    }
    if (jobs.some(j => ["queued", "downloading", "preparing", "transcribing"].includes(j.state))) timer = setTimeout(poll, 2500);
  }
  window.trKick = () => { clearTimeout(timer); poll(); };

  $("#trGo").onclick = async () => {
    const m = $("#trMsg"), file = $("input[name=trSrc]:checked").value === "file";
    const body = { title: $("#trTitle").value, artist: $("#trArtist").value, instrument: $("#trLead").value, model: $("#trModel").value, band: chosenBand($("#trBand")) };
    m.style.color = ""; m.textContent = "Sending…"; $("#trGo").disabled = true;
    try {
      if (file) {
        const f = $("#trFile").files[0];
        if (!f) throw new Error("Choose an audio file first");
        const fd = new FormData();
        fd.append("file", f);
        for (const k of ["title", "artist", "instrument", "model"]) fd.append(k, body[k]);
        fd.append("band", body.band.join(","));
        const r = await fetch("/api/transcribe/upload", { method: "POST", body: fd });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      } else {
        if (!$("#trUrl").value.trim()) throw new Error("Paste a link first");
        await api("/api/transcribe/url", { body: { ...body, url: $("#trUrl").value.trim() } });
      }
      m.textContent = "Started: it appears below.";
      $("#trUrl").value = ""; $("#trFile").value = "";
      $("#trNew").open = false;
      window.trKick();
    } catch (err) { m.textContent = err.message; m.style.color = "var(--again)"; }
    $("#trGo").disabled = false;
  };

  // ---------------------------------------------------------------- the editor for the chosen recording
  let want = null;
  try { want = localStorage.getItem("alignWork"); } catch (e) { /* storage blocked */ }
  if (usable.length) { openWork = (usable.find(w => w.work === want) || usable[0]).work; await alignOpen(openWork, usable); }
  poll();
};

// "Transcribe again": another model or band for the recording that is open in the editor.
window.transcribeAgainPanel = async (box, work, D) => {
  if (!box) return;
  const opts = await api("/api/transcribe/options");
  const known = (D.ensemble && D.ensemble.length ? D.ensemble : opts.default_band).filter(b => opts.band.includes(b));
  box.innerHTML = `<div class="row">
      <label class="small">Model <select id="agModel">${opts.models.map(m => `<option value="${m}" ${m === "large" ? "selected" : ""}>${m} (${MODEL_HINT[m]})</option>`).join("")}</select></label>
      <button id="agGo">Transcribe again</button><span id="agMsg" class="small muted"></span></div>
    <div class="small muted" style="margin:8px 0 4px">Also in the recording:</div><div id="agBand" class="band-chips">${bandChips(opts, known)}</div>
    <div class="small muted" style="margin-top:8px">The new result is added next to the old ones: pick it in the Transcription menu above when it is done.</div>`;
  wireChips($("#agBand", box));
  $("#agGo", box).onclick = async () => {
    const m = $("#agMsg", box);
    try {
      await api(`/api/transcribe/${work}/run`, { body: { model: $("#agModel", box).value, band: chosenBand($("#agBand", box)) } });
      m.textContent = "Started: see Transcriptions at the top.";
      if (window.trKick) window.trKick();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) { m.textContent = err.message; m.style.color = "var(--again)"; }
  };
};
