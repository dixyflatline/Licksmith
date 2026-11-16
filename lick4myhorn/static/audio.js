/* Lick4MyHorn playback: a small WebAudio band (trumpet-ish lead, rootless piano comping,
   walking bass, ride + hi-hat) and a cycle sequencer for 12-key drills and whole tunes.
   All pitches arriving here are concert pitch (that is what sounds). */
"use strict";

const Band = (() => {
  const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  function parseChord(sym) {
    if (!sym || sym.startsWith("N.C")) return null;
    const m = /^([A-G])([b#]?)(.*?)(?:\/([A-G])([b#]?))?$/.exec(sym.trim());
    if (!m) return null;
    const pc = (PC[m[1]] + (m[2] === "b" ? -1 : m[2] === "#" ? 1 : 0) + 12) % 12;
    const r = m[3] || "";
    let q = "maj";
    if (/^(m7b5|m7-5|ø|min7b5|mi7b5)/.test(r)) q = "hdim";
    else if (/^(dim|o|°)/.test(r)) q = "dim";
    else if (/^(mmaj|mM|m\^|-\^)/.test(r)) q = "min";
    else if (/^(maj|M(?!i)|\^|Δ)/.test(r)) q = "maj";
    else if (/^(m|min|mi|-)(?!aj)/.test(r)) q = "min";
    else if (/^(7|9|11|13|alt|sus|b9|#9)/.test(r)) q = "dom";
    else if (/^(\+|aug)/.test(r)) q = "aug";
    let bass = pc;
    if (m[4]) bass = (PC[m[4]] + (m[5] === "b" ? -1 : m[5] === "#" ? 1 : 0) + 12) % 12;
    return { pc, q, bass, alt: /alt|b9|#9|b13/.test(r) };
  }

  const SHELL = { maj: [4, 11, 14], min: [3, 10, 14], dom: [4, 10, 14], hdim: [3, 6, 10], dim: [3, 6, 9], aug: [4, 8, 10] };
  const THIRD = { maj: 4, min: 3, dom: 4, hdim: 3, dim: 3, aug: 4 };
  const FIFTH = { maj: 7, min: 7, dom: 7, hdim: 6, dim: 6, aug: 8 };

  function voicing(ch) {
    const ivs = ch.alt && ch.q === "dom" ? [4, 10, 15] : SHELL[ch.q] || SHELL.maj;
    return ivs.map(i => {
      let n = ch.pc + i + 48;
      while (n < 53) n += 12;
      while (n > 65) n -= 12;
      return n;
    }).sort((a, b) => a - b);
  }

  function warp(q, swing) {
    const b = Math.floor(q + 1e-9), f = q - b;
    return Math.abs(f - 0.5) < 1e-3 ? b + swing : q;
  }

  class Engine {
    init() {
      if (this.ctx) { if (this.ctx.state === "suspended") this.ctx.resume(); return; }
      const ctx = this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -14; comp.ratio.value = 3;
      this.master = ctx.createGain(); this.master.gain.value = 0.9;
      this.master.connect(comp).connect(ctx.destination);
      const len = ctx.sampleRate;
      this.noise = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = this.noise.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
      this.vol = { lead: 0.9, back: 0.7 };
    }
    session() {  // fresh buses so stop() can silence everything already scheduled
      const ctx = this.ctx;
      const bus = ctx.createGain(); bus.connect(this.master);
      const lead = ctx.createGain(); lead.gain.value = this.vol.lead; lead.connect(bus);
      const back = ctx.createGain(); back.gain.value = this.vol.back; back.connect(bus);
      this.cur = { bus, lead, back };
      return this.cur;
    }
    setVolume(which, v) { this.vol[which] = v; if (this.cur) this.cur[which].gain.value = v; }
    hz(m) { return 440 * Math.pow(2, (m - 69) / 12); }

    lead(t, dur, midi) {
      const ctx = this.ctx, f = this.hz(midi), end = t + Math.max(0.05, dur);
      const g = ctx.createGain(), lp = ctx.createBiquadFilter();
      lp.type = "lowpass"; lp.Q.value = 1.5;
      lp.frequency.setValueAtTime(f * 1.5, t);
      lp.frequency.linearRampToValueAtTime(Math.min(7000, f * 7), t + 0.05);
      lp.frequency.linearRampToValueAtTime(Math.min(5000, f * 4), t + 0.2);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.2, t + 0.025);
      g.gain.linearRampToValueAtTime(0.15, t + 0.09);
      g.gain.setValueAtTime(0.15, end - 0.03);
      g.gain.linearRampToValueAtTime(0, end + 0.04);
      for (const det of [1, 1.004]) {
        const o = ctx.createOscillator(); o.type = "sawtooth"; o.frequency.value = f * det;
        o.connect(lp); o.start(t); o.stop(end + 0.08);
      }
      lp.connect(g).connect(this.cur.lead);
    }
    comp(t, dur, midis) {
      const ctx = this.ctx, end = t + dur;
      for (const m of midis) {
        const g = ctx.createGain();
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(0.045, t + 0.008);
        g.gain.exponentialRampToValueAtTime(0.012, t + 0.9);
        g.gain.setValueAtTime(g.gain.value, end);
        g.gain.linearRampToValueAtTime(0, end + 0.06);
        for (const [type, lvl] of [["triangle", 1], ["sine", 0.6]]) {
          const o = ctx.createOscillator(); o.type = type; o.frequency.value = this.hz(m);
          const og = ctx.createGain(); og.gain.value = lvl;
          o.connect(og).connect(g); o.start(t); o.stop(end + 0.1);
        }
        g.connect(this.cur.back);
      }
    }
    piano(t, dur, midis) {  // softer, longer-ringing chord for the piano-only backing
      const ctx = this.ctx, end = t + dur;
      for (const m of midis) {
        const g = ctx.createGain();
        const lvl = m < 55 ? 0.06 : 0.045;
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(lvl, t + 0.01);
        g.gain.exponentialRampToValueAtTime(lvl * 0.35, t + Math.min(2.5, dur));
        g.gain.setValueAtTime(lvl * 0.35, Math.max(t + 0.02, end - 0.05));
        g.gain.linearRampToValueAtTime(0, end + 0.12);
        const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 2600;
        for (const [type, mult, l] of [["triangle", 1, 1], ["sine", 2, 0.25], ["sine", 1, 0.7]]) {
          const o = ctx.createOscillator(); o.type = type; o.frequency.value = this.hz(m) * mult;
          const og = ctx.createGain(); og.gain.value = l;
          o.connect(og).connect(lp); o.start(t); o.stop(end + 0.15);
        }
        lp.connect(g).connect(this.cur.back);
      }
    }
    bass(t, dur, midi) {
      const ctx = this.ctx, end = t + dur * 0.92;
      const o = ctx.createOscillator(); o.type = "triangle"; o.frequency.value = this.hz(midi);
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 650;
      const g = ctx.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.55, t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.25, t + 0.3);
      g.gain.setValueAtTime(0.25, end);
      g.gain.linearRampToValueAtTime(0, end + 0.04);
      o.connect(lp).connect(g).connect(this.cur.back); o.start(t); o.stop(end + 0.06);
    }
    noiseHit(t, hp, level, decay) {
      const ctx = this.ctx, s = ctx.createBufferSource(); s.buffer = this.noise;
      const f = ctx.createBiquadFilter(); f.type = "highpass"; f.frequency.value = hp;
      const g = ctx.createGain();
      g.gain.setValueAtTime(level, t); g.gain.exponentialRampToValueAtTime(0.0008, t + decay);
      s.connect(f).connect(g).connect(this.cur.back);
      s.start(t, Math.random() * 0.5); s.stop(t + decay + 0.02);
    }
    ride(t, accent) { this.noiseHit(t, 6500, accent ? 0.07 : 0.045, 0.32); }
    hat(t) { this.noiseHit(t, 8500, 0.05, 0.045); }
    click(t, accent) {
      const ctx = this.ctx, o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = accent ? 2200 : 1600;
      g.gain.setValueAtTime(0.12, t); g.gain.exponentialRampToValueAtTime(0.0008, t + 0.05);
      o.connect(g).connect(this.cur.bus); o.start(t); o.stop(t + 0.06);
    }
  }

  /* Schedules "cycles". A cycle = optional count-in + the sequence (lick or tune) + optional
     response section (same chords, lick muted) for call-and-response. */
  /* Events are not handed to WebAudio all at once: they wait in a queue and a timer passes them
     on about half a second before they sound ("lookahead" scheduling). Creating a whole tune's
     worth of nodes up front can stall the audio thread, notably in Safari. */
  const LOOKAHEAD = 0.6, TICK_MS = 40;

  class Sequencer {
    constructor(engine) {
      this.e = engine; this.timers = []; this.running = false; this.queue = []; this.nextAt = null;
      this.q = new Proxy({}, { get: (_, name) => (...args) => this._push(args[0], () => this.e[name](...args)) });
    }

    start(opts) {
      this.e.init();
      this.stop(true);
      const ctx = this.e.ctx;
      if (ctx.state !== "running") ctx.resume();
      this.e.session();
      this.opts = opts;
      this.running = true;
      this.cycleNo = 0;
      this.queue = [];
      this.ticker = setInterval(() => this._pump(), TICK_MS);
      this._schedule(Band.rendered.on ? null : ctx.currentTime + 0.2);
      this._pump();
    }

    _push(t, fn) { this.queue.push({ t, fn }); this.dirty = true; }

    _pump() {
      if (!this.running || !this.e.cur) return;
      const now = this.e.ctx.currentTime;
      const ahead = Band.rendered.on ? 2.5 : LOOKAHEAD + 0.4;
      if (this.nextAt !== null && now + ahead > this.nextAt) {
        const t = this.nextAt;
        this.nextAt = null;
        this._schedule(t);
      }
      if (this.dirty) { this.queue.sort((a, b) => a.t - b.t); this.dirty = false; }
      let i = 0;
      while (i < this.queue.length && this.queue[i].t < now + LOOKAHEAD) {
        const ev = this.queue[i++];
        if (ev.t >= now - 0.03) {
          try { ev.fn(); } catch (err) { console.error("audio event failed", err); }
        }
      }
      if (i) this.queue.splice(0, i);
    }

    stop(silent) {
      this.timers.forEach(clearTimeout); this.timers = [];
      clearInterval(this.ticker);
      this.queue = []; this.nextAt = null;
      if (this.e.cur) {
        const bus = this.e.cur.bus, ctx = this.e.ctx;
        bus.gain.setValueAtTime(bus.gain.value, ctx.currentTime);
        bus.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.05);
        setTimeout(() => bus.disconnect(), 200);
        this.e.cur = null;
      }
      const was = this.running;
      this.running = false;
      this.cycles = [];
      if (was && !silent && this.opts && this.opts.onStop) this.opts.onStop();
    }

    _later(sec, fn) { this.timers.push(setTimeout(fn, Math.max(0, sec * 1000))); }

    _schedule(t0) {
      if (!this.running) return;
      if (Band.rendered.on) { this._scheduleRendered(t0); return; }
      const c = this.opts.next(this.cycleNo);
      if (!c) { this._later(t0 - this.e.ctx.currentTime + 0.25, () => this.stop()); return; }
      const e = this.q, ctx = this.e.ctx;
      const tempo = this.opts.tempo(), swing = this.opts.swing();
      const spq = 60 / tempo;
      const seq = c.seq, bpb = (seq.ts && seq.ts[0]) || 4;
      const lead = (c.leadIn || 0) * bpb;
      const L = seq.length;
      const R = c.response ? L : 0;
      const tStart = t0 + lead * spq;
      for (let b = 0; b < lead; b++) e.click(t0 + b * spq, b % bpb === 0);
      if (c.playLick) {
        for (const [o, d, m] of seq.notes) {
          const a = tStart + warp(o, swing) * spq;
          const z = tStart + warp(o + d, swing) * spq;
          e.lead(a, Math.max(0.05, z - a - 0.02), m);
        }
        this._playClip(c, tStart);
        // grace notes: a quick note just before the beat they lead into
        for (const [o, m] of (seq.graces || [])) e.lead(tStart + warp(o, swing) * spq - 0.07, 0.07, m);
      }
      if (c.backing) {
        for (let rep = 0; rep < (R ? 2 : 1); rep++) {
          if (c.backing === "piano") this._piano(tStart + rep * L * spq, seq, L, spq, bpb);
          else this._backing(tStart + rep * L * spq, seq, L, spq, swing, bpb);
        }
      }
      const tEnd = tStart + (L + R) * spq;
      this.cycles = (this.cycles || []).slice(-2).concat([{ t0, tStart, tEnd, spq, lead, L, R, bpb, info: c.info, clip: c.clip }]);
      const now = ctx.currentTime;
      this._later(t0 - now, () => this.opts.onCycle && this.opts.onCycle(c.info, this.cycleNo));
      this.cycleNo++;
      this.nextAt = tEnd;
    }

    /* SoundFont mode: the server renders the whole cycle (count-in, lick, band) to audio and we
       play it as one buffer, so timing is exact and the instruments are real samples. */
    async _scheduleRendered(t0) {
      const c = this.opts.next(this.cycleNo);
      const myRun = this.e.cur;
      if (!c) { this._later((t0 || this.e.ctx.currentTime) - this.e.ctx.currentTime + 0.25, () => this.stop()); return; }
      const n = this.cycleNo++;
      const tempo = this.opts.tempo(), swing = this.opts.swing();
      const spq = 60 / tempo, seq = c.seq, bpb = (seq.ts && seq.ts[0]) || 4;
      const lead = (c.leadIn || 0) * bpb, L = seq.length, R = c.response ? L : 0;
      const backing = c.backing === true ? "band" : c.backing;
      this.nextAt = null;
      let buf;
      try {
        const r = await fetch("/api/render", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ seq, tempo, swing, leadIn: c.leadIn || 0, playLick: c.playLick, backing, response: !!c.response, seed: n }),
        });
        if (!r.ok) throw new Error(await r.text());
        buf = await this.e.ctx.decodeAudioData(await r.arrayBuffer());
      } catch (err) {
        console.warn("SoundFont playback unavailable, using the built-in synth", err);
        Band.rendered.on = false;
        if (this.running && this.e.cur === myRun) { this.cycleNo = n; this.opts._replay = c; this._scheduleSynth(t0 || this.e.ctx.currentTime + 0.1, c); }
        return;
      }
      if (!this.running || this.e.cur !== myRun) return;
      const ctx = this.e.ctx, now = ctx.currentTime;
      const start = t0 === null || t0 === undefined ? now + 0.08 : t0;
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.e.cur.bus);
      if (start >= now) src.start(start); else src.start(now, now - start);
      const tStart = start + lead * spq, tEnd = tStart + (L + R) * spq;
      this._playClip(c, tStart);
      this.cycles = (this.cycles || []).slice(-2).concat([{ t0: start, tStart, tEnd, spq, lead, L, R, bpb, info: c.info, clip: c.clip }]);
      this._later(start - now, () => this.opts.onCycle && this.opts.onCycle(c.info, n));
      this.nextAt = tEnd;
    }

    // c.clip = { buffer, offset }: the real recording of the lick, already in the practice key and tempo; offset = seconds
    // from the start of the buffer to its first bar line, which lands on tStart.
    _playClip(c, tStart) {
      if (!c.clip || !this.e.cur) return;
      const ctx = this.e.ctx, src = ctx.createBufferSource(), at = tStart - c.clip.offset, now = ctx.currentTime;
      src.buffer = c.clip.buffer;
      src.connect(this.e.cur.lead);
      if (at >= now) src.start(at); else src.start(now, now - at);
    }

    _scheduleSynth(t0, c) {
      const saved = this.opts.next;
      let used = false;
      this.opts.next = n => { if (!used) { used = true; return c; } return saved(n); };
      this._schedule(t0);
      this.opts.next = saved;
    }

    _spans(seq, L) {
      const pts = (seq.chords || []).slice().sort((a, b) => a[0] - b[0]);
      const spans = [];
      pts.forEach(([o, s], i) => {
        const ch = parseChord(s);
        const end = i + 1 < pts.length ? pts[i + 1][0] : L;
        if (ch && end > o) spans.push({ o, end: Math.min(end, L), ch });
      });
      return spans;
    }

    // piano only: each chord held (root in the left hand + 3rd/7th/9th), re-struck every bar
    _piano(t, seq, L, spq, bpb) {
      for (const sp of this._spans(seq, L)) {
        let root = sp.ch.bass + 36;
        if (root < 41) root += 12;
        const v = [root, ...voicing(sp.ch)];
        for (let p = sp.o; p < sp.end - 1e-6; p += bpb) {
          const dur = Math.min(bpb, sp.end - p) * spq;
          this.q.piano(t + p * spq, dur, v);
        }
      }
    }

    _backing(t, seq, L, spq, swing, bpb) {
      const e = this.q;
      const pts = (seq.chords || []).slice().sort((a, b) => a[0] - b[0]);
      const spans = [];
      pts.forEach(([o, s], i) => {
        const ch = parseChord(s);
        const end = i + 1 < pts.length ? pts[i + 1][0] : L;
        if (ch && end > o) spans.push({ o, end: Math.min(end, L), ch });
      });
      // comping: a hit at the chord change and on the "and" of the next beat, every 2 beats
      for (const sp of spans) {
        const v = voicing(sp.ch);
        for (let p = sp.o; p < sp.end - 1e-6; p += 2) {
          e.comp(t + warp(p, swing) * spq, Math.min(1.2, sp.end - p) * spq, v);
          if (p + 1.5 < sp.end) e.comp(t + warp(p + 1.5, swing) * spq, 0.45 * spq, v);
        }
      }
      // walking bass: root, chord tone, chord tone, approach to the next root
      const total = Math.round(L);
      for (let q = 0; q < total; q++) {
        const sp = spans.find(s => q >= s.o - 1e-6 && q < s.end - 1e-6);
        if (!sp) continue;
        const nxt = spans.find(s => s.o > sp.o) || spans[0];
        const k = q - Math.ceil(sp.o - 1e-6), lastBeat = q + 1 >= sp.end - 1e-6;
        let pc;
        if (k === 0) pc = sp.ch.bass;
        else if (lastBeat && nxt) pc = nxt.ch.pc + ((q % 2) ? 1 : -1);
        else pc = sp.ch.pc + ([0, THIRD[sp.ch.q], FIFTH[sp.ch.q], THIRD[sp.ch.q]][k % 4]);
        let m = ((pc % 12) + 12) % 12 + 36;
        if (m < 40) m += 12;
        e.bass(t + q * spq, spq, m);
      }
      // drums: ride on every beat plus the swung skip note, hi-hat on 2 and 4
      for (let q = 0; q < total; q++) {
        const beat = q % bpb;
        e.ride(t + q * spq, beat === 0);
        if (beat % 2 === 1) {
          e.ride(t + (q + swing) * spq, false);
          e.hat(t + q * spq);
        }
      }
    }

    position() {
      if (!this.running || !this.cycles) return null;
      const now = this.e.ctx.currentTime;
      const p = this.cycles.filter(c => c.t0 <= now).pop();
      if (!p) return null;
      if (now < p.tStart) {
        const b = Math.floor((now - p.t0) / p.spq);
        return { phase: "count-in", beat: b % p.bpb, info: p.info };
      }
      const q = (now - p.tStart) / p.spq;
      if (q < p.L) return { phase: "listen", beat: Math.floor(q) % p.bpb, q, info: p.info, now, tStart: p.tStart, clip: p.clip };
      if (q < p.L + p.R) return { phase: "your turn", beat: Math.floor(q) % p.bpb, q: q - p.L, info: p.info };
      return { phase: "", beat: -1, info: p.info };
    }
  }

  const engine = new Engine();
  // create / resume the audio context inside a real click or key press (Safari requires this)
  const unlock = () => { try { engine.init(); } catch (e) { /* no audio */ } };
  document.addEventListener("pointerdown", unlock, true);
  document.addEventListener("keydown", unlock, true);
  return { engine, warp, Sequencer, parseChord, sequencer: new Sequencer(engine), rendered: { on: false } };
})();
