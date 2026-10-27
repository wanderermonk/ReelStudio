/* Story mode: pick characters -> write a story -> edit shots -> keyframes -> video -> sound. */
(function () {
  let castSel = [], sb = null, sbProject = null, sbTimer = null, opts = null, dirty = false;
  const SIZES = ["wide", "medium", "medium close-up", "close-up", "extreme close-up", "over-the-shoulder", "low angle", "high angle"];
  const EXAMPLES = [
    ["Lost puppy", "{m} finds a lost puppy in the park and helps it find its home."],
    ["Kite day", "{m} tries to fly a kite, it falls, {o} helps, and it finally soars."],
    ["Birthday surprise", "{m} secretly makes a surprise gift for {o}'s birthday."],
    ["Rainy day", "It starts to rain; {m} shares an umbrella with {o} and they dance in the puddles."],
    ["Helping grandma", "{m} helps grandma water the garden and finds a flower blooming."],
    ["Festival", "{m} lights a diya for Diwali and shares sweets with {o}."],
  ];
  const fmtT = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

  // ---------- mode switch ----------
  function setMode(m) {
    const story = m === "story";
    $("danceMode").hidden = story; $("storyMode").hidden = !story;
    $("modeDance").classList.toggle("on", !story); $("modeStory").classList.toggle("on", story);
    $("modeDance").setAttribute("aria-selected", !story); $("modeStory").setAttribute("aria-selected", story);
    $("step2Title").textContent = story ? "Story" : "Reel";
    try { localStorage.setItem("rs_mode", m); } catch (e) {}
    if (story) { renderCast(); loadOpts(); loadWriters(); renderRoles(); }
  }
  $("modeDance").onclick = () => setMode("dance");
  $("modeStory").onclick = () => setMode("story");

  // ---------- cast picker ----------
  function renderCast() {
    if (!castSel.length && typeof sel !== "undefined" && sel) castSel = [sel];
    castSel = castSel.filter(id => chars.some(c => c.id === id));
    $("castPick").innerHTML = chars.map(c => {
      const i = castSel.indexOf(c.id);
      return `<button type="button" data-cast="${c.id}" class="${i >= 0 ? "on" : ""}" aria-pressed="${i >= 0}">
        ${i >= 0 ? `<span class="ord">${i + 1}</span>` : ""}
        ${c.image ? `<img src="/api/characters/${c.id}/image" alt="">` : `<span class="note">No image</span>`}
        <b>${esc(c.name)}</b><span class="note">${esc(KIND[c.kind] || "")}</span></button>`;
    }).join("") || `<p class="note">Add characters in step 1 first (or use "Make a character" in the gallery).</p>`;
    $("s-examples").innerHTML = EXAMPLES.map(([t, x]) => `<button type="button" class="ghost" data-ex="${esc(x)}">${t}</button>`).join("");
  }
  $("castPick").onclick = e => {
    const b = e.target.closest("[data-cast]"); if (!b) return;
    const id = b.dataset.cast, i = castSel.indexOf(id);
    if (i >= 0) castSel.splice(i, 1); else if (castSel.length < 6) castSel.push(id);
    renderCast(); refreshVoiceCast(); renderRoles();
  };
  $("s-examples").onclick = e => {
    const b = e.target.closest("[data-ex]"); if (!b) return;
    const names = castSel.map(id => chars.find(c => c.id === id)?.name).filter(Boolean);
    $("s-idea").value = b.dataset.ex.replaceAll("{m}", names[0] || "Pari").replaceAll("{o}", names[1] || "a friend");
  };
  const cardsEl = document.getElementById("cards");
  if (cardsEl) new MutationObserver(() => { if (!$("storyMode").hidden) renderCast(); }).observe(cardsEl, { childList: true });

  // ---------- options (voices, sfx, songs) ----------
  async function loadOpts() {
    if (!opts) { try { opts = await api("/api/story/options"); } catch (e) { return; } }
    refreshVoiceCast();
    try {
      const songs = await api("/api/songs");
      const o = songs.map(s => `<option value="${s.id}">${esc(s.name)} · ${Math.round(s.bpm || 0)} BPM</option>`).join("") || `<option value="">(no songs yet: paste a link below)</option>`;
      $("s-song").innerHTML = o;
    } catch (e) {}
  }
  // ---------- voice cast: one voice per speaker ----------
  let voiceCast = {};
  const PITCH = [["-40", "Much lower"], ["-20", "Lower"], ["0", "Normal"], ["25", "Higher"], ["50", "Much higher"]];
  async function refreshVoiceCast() {
    if (!castSel.length) { $("s-voices").innerHTML = ""; return; }
    try {
      voiceCast = await post("/api/story/voice-cast", { character_ids: castSel, language: $("s-lang").value,
        voice: voiceCast.Narrator?.voice || ($("s-lang").value === "hi" ? "hi-IN-MadhurNeural" : "en-US-JennyNeural"), voices: voiceCast });
    } catch (e) { return; }
    renderVoices($("s-voices"), "form");
  }
  let allVoices = null;
  function voiceList() { return allVoices || (opts ? opts.voices.voices : []); }
  function voiceOptions(lang, sel) {
    const v = voiceList(), groups = {};
    v.forEach(x => (groups[x.group || x.lang] ||= []).push(x));
    if (sel && !v.some(x => x.id === sel)) (groups["Other"] ||= []).push({ id: sel, label: sel });
    return Object.entries(groups).map(([g, xs]) => `<optgroup label="${esc(g)}">${xs.map(x => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</optgroup>`).join("");
  }
  const RATE = [["-20", "Slower"], ["-10", "A bit slower"], ["0", "Normal speed"], ["10", "A bit faster"], ["20", "Faster"]];
  $("s-allvoices").onchange = async () => {
    if ($("s-allvoices").checked && !allVoices) {
      try { const r = await api("/api/story/voices?all=1"); allVoices = r.voices; if (r.error) showErr(r.error); } catch (e) { showErr(e); }
    }
    if (!$("s-allvoices").checked) allVoices = null;
    renderVoices($("s-voices"), "form"); if (sbProject) renderVoices($("x-voices"), "final");
  };
  function renderVoices(el, where) {
    if (!el) return;
    const vc = where === "final" ? (sbProject && sbProject.voice_cast) || {} : voiceCast;
    const lang = where === "final" ? (sbProject?.story_settings?.language) : $("s-lang").value;
    if (lang === "none" || !Object.keys(vc).length) { el.innerHTML = where === "form" && lang === "none" ? `<p class="note">No narration or dialogue.</p>` : ""; return; }
    const order = ["Narrator", ...(where === "final" ? (sbProject?.cast || []).map(c => c.name) : castSel.map(id => chars.find(c => c.id === id)?.name))];
    const rank = n => { const i = order.indexOf(n); return i < 0 ? 99 : i; };
    const entries = Object.entries(vc).sort((a, b) => rank(a[0]) - rank(b[0]));
    el.innerHTML = entries.map(([name, v]) => `<div class="vrow" data-sp="${esc(name)}">
      <b>${name === "Narrator" ? "🎙 Narrator" : "🗣 " + esc(name)}</b>
      <select data-vv>${voiceOptions(null, v.voice)}</select>
      <select data-vp title="Pitch">${PITCH.map(([p, l]) => `<option value="${p}" ${+p === +v.pitch ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select data-vr title="Speed">${RATE.map(([p, l]) => `<option value="${p}" ${+p === +(v.rate || 0) ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button type="button" data-vt title="Hear this voice">▶</button></div>`).join("");
    el.dataset.where = where;
  }
  function voicesFrom(el) {
    const out = {};
    el.querySelectorAll(".vrow").forEach(r => { out[r.dataset.sp] = { voice: r.querySelector("[data-vv]").value, pitch: +r.querySelector("[data-vp]").value, rate: +r.querySelector("[data-vr]").value }; });
    return out;
  }
  for (const id of ["s-voices", "x-voices"]) {
    $(id).addEventListener("change", () => { if (id === "s-voices") voiceCast = voicesFrom($(id)); });
    $(id).addEventListener("click", async e => {
      const b = e.target.closest("[data-vt]"); if (!b) return;
      const r = b.closest(".vrow"); b.disabled = true; b.textContent = "…";
      try {
        const res = await fetch("/api/story/voice-test", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ voice: r.querySelector("[data-vv]").value, pitch: +r.querySelector("[data-vp]").value,
            rate: +r.querySelector("[data-vr]").value, language: (id === "s-voices" ? $("s-lang").value : sbProject?.story_settings?.language) }) });
        if (!res.ok) throw new Error((await res.json()).error || "Voice test failed");
        new Audio(URL.createObjectURL(await res.blob())).play();
      } catch (err) { showErr(err); }
      b.disabled = false; b.textContent = "▶";
    });
  }

  function fillVoices(el, lang) {
    if (!opts) return;
    const keep = el.value;
    const v = opts.voices.voices.filter(x => !lang || lang === "none" || x.lang === lang);
    el.innerHTML = v.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join("");
    if (v.some(x => x.id === keep)) el.value = keep;
    else if (lang === "en") el.value = "en-US-AnaNeural";
  }
  $("s-lang").onchange = () => { voiceCast = {}; refreshVoiceCast(); };
  $("s-shots").onchange = () => { const n = +$("s-shots").value; $("s-long").hidden = n < 12; };
  $("sb-compact").onchange = () => $("sb-shots").classList.toggle("compact", $("sb-compact").checked);
  $("s-music").onchange = () => { const on = $("s-music").value === "song"; $("s-songWrap").hidden = !on; };
  async function storyUrlSong() {
    const b = $("s-urlGo"); b.disabled = true;
    $("s-urlNote").innerHTML = '<span class="spin"></span> Starting…';
    try {
      const v = await window.songFromUrl($("s-url").value, 0, m => { $("s-urlNote").innerHTML = '<span class="spin"></span> ' + esc(m); });
      await loadOpts(); $("s-song").value = v.id;
      $("s-music").value = "song"; $("s-songWrap").hidden = false;
      if (window.loadSongs) window.loadSongs();
      $("s-urlNote").textContent = `Added “${v.name}” · ${Math.round(v.bpm || 0)} BPM. It is now picked above.`;
    } catch (e) { $("s-urlNote").textContent = e.message || String(e); }
    finally { b.disabled = false; }
  }
  $("s-urlGo").onclick = storyUrlSong;
  $("s-url").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); storyUrlSong(); } };
  $("s-turbo").onchange = () => { $("s-dyn").disabled = !$("s-turbo").checked; updEst(); };
  $("s-dyn").onchange = updEst;

  // ---------- story writer (Claude / Ollama models / built-in) ----------
  async function loadWriters() {
    try {
      const r = await api("/api/story/writers"), keep = $("s-writer").value;
      $("s-writer").innerHTML = r.writers.map(w => `<option value="${esc(w.id)}">${esc(w.label)}</option>`).join("");
      let saved = null; try { saved = localStorage.getItem("rs_writer"); } catch (e) {}
      const want = keep !== "auto" ? keep : saved;
      if (want && r.writers.some(w => w.id === want)) $("s-writer").value = want;
    } catch (e) {}
  }
  $("s-writer").onchange = () => { try { localStorage.setItem("rs_writer", $("s-writer").value); } catch (e) {} };

  // ---------- story builder ----------
  const BF = ["wish", "problem", "attempts", "turning_point", "ending", "lesson", "twist"];
  let roles = {};
  function renderRoles() {
    const names = castSel.map(id => chars.find(c => c.id === id)).filter(Boolean);
    $("s-roles").innerHTML = names.map((c, i) => `<div class="r"><b>${esc(c.name)}</b><input data-role="${esc(c.name)}" value="${esc(roles[c.name] || "")}"
      placeholder="${i === 0 ? "the brave, curious hero" : c.kind === "animal" ? "the playful pet sidekick" : c.kind === "woman" || c.kind === "man" ? "the wise, funny grown-up" : "the loyal best friend"}"></div>`).join("")
      || `<p class="note">Pick the cast above first.</p>`;
  }
  $("s-roles").addEventListener("input", e => { if (e.target.dataset.role) roles[e.target.dataset.role] = e.target.value; });
  function readBrief() {
    const b = { roles: Object.entries(roles).filter(([n, r]) => r.trim()).map(([name, role]) => ({ name, role })) };
    BF.forEach(k => { const v = $("b-" + k).value.trim(); if (v) b[k] = v; });
    const beats = $("b-beats").value.split("\n").map(x => x.trim()).filter(Boolean);
    if (beats.length) b.beats = beats;
    return Object.keys(b).length > 1 || b.roles.length ? b : null;
  }
  function fillBrief(r) {
    (r.roles || []).forEach(x => { if (x.name) roles[x.name] = x.role || ""; });
    BF.forEach(k => { if (r[k]) $("b-" + k).value = r[k]; });
    if (r.beats && r.beats.length) $("b-beats").value = r.beats.join("\n");
    if (r.idea) $("s-idea").value = r.idea;
    renderRoles(); $("s-builder").open = true;
  }
  let progTimer = null;
  function progress(on) {
    clearInterval(progTimer);
    if (on) progTimer = setInterval(async () => {
      try { const r = await api("/api/story/progress"); const last = (r.log || []).slice(-1)[0]; if (last) $("s-note").innerHTML = `<span class="spin"></span> ${esc(last)}`; } catch (e) {}
    }, 1500);
  }
  async function think(btn, mode) {
    $("err").hidden = true;
    if (!castSel.length) return showErr("Pick the cast first.");
    btn.disabled = true; $("s-note").innerHTML = `<span class="spin"></span> ${mode === "ideas" ? "Thinking of ideas…" : "Building a full story…"}`; progress(true);
    try {
      const r = await post("/api/story/brainstorm", { character_ids: castSel, idea: $("s-idea").value, brief: readBrief(),
        language: $("s-lang").value, writer: $("s-writer").value, mode });
      if (mode === "ideas") {
        $("s-ideas").innerHTML = (r.result.ideas || []).map(x => `<button type="button" data-idea="${esc(x.idea)}"><b>${esc(x.title)}</b><span>${esc(x.idea)}</span><span class="note">Use this idea →</span></button>`).join("");
      } else fillBrief(r.result);
      $("s-note").textContent = `By ${r.writer}.` + (r.writer === "built-in" ? " (Tip: start the Ollama app or add a Claude key for richer stories.)" : "");
    } catch (e) { showErr(e); $("s-note").textContent = ""; }
    progress(false); btn.disabled = false;
  }
  $("s-ideasBtn").onclick = () => think($("s-ideasBtn"), "ideas");
  $("s-expandBtn").onclick = () => think($("s-expandBtn"), "expand");
  $("s-ideas").onclick = e => { const b = e.target.closest("[data-idea]"); if (!b) return; $("s-idea").value = b.dataset.idea; $("s-ideas").innerHTML = ""; $("s-idea").focus(); };

  // ---------- write the story ----------
  $("s-write").onclick = async () => {
    $("err").hidden = true;
    if (!castSel.length) return showErr("Pick at least one character for the story.");
    $("s-write").disabled = true; $("s-note").innerHTML = '<span class="spin"></span> Writing the story and shot list…'; progress(true);
    try {
      const r = await post("/api/story/plan", {
        character_ids: castSel, idea: $("s-idea").value, shots: +$("s-shots").value,
        setting: $("s-setting").value, tone: $("s-tone").value, language: $("s-lang").value,
        writer: $("s-writer").value, brief: readBrief() });
      progress(false);
      sb = r.story; sbProject = null; dirty = false;
      $("sb-compact").checked = sb.shots.length >= 12;
      sb.cast = castSel.map(id => chars.find(c => c.id === id)).filter(Boolean);
      $("sb-planner").textContent = "Written by " + r.planner;
      $("s-note").textContent = "";
      $("sbBox").hidden = false; renderSB(); $("sbBox").scrollIntoView({ behavior: "smooth" });
    } catch (e) { showErr(e); $("s-note").textContent = ""; progress(false); }
    $("s-write").disabled = false;
  };

  // ---------- storyboard ----------
  function castNames() { return (sbProject ? sbProject.cast : sb.cast || []).map(c => c.name); }
  function shotsArr() { return sbProject ? sbProject.clips : sb.shots; }
  function busy() { return sbProject && sbProject.status === "running"; }

  function kfBox(sh, kind) {
    const f = sh[`kf_${kind}`], pid = sbProject && sbProject.id;
    const img = f && pid ? `<img src="/api/projects/${pid}/file/${f}" alt="${kind} keyframe">` : `<div class="ph">${kind === "start" ? "Start" : "End"} frame</div>`;
    const ok = sh[`kf_${kind}_ok`];
    return `<div class="kf">${img}
      <span class="note">${kind === "start" ? "Start keyframe" : "End keyframe"}${sh[`kf_${kind}_engine`] ? " · " + esc(sh[`kf_${kind}_engine`]) : ""}</span>
      ${pid ? `<div class="row">
        <button type="button" data-redo="${sh.id}" data-kind="${kind}" ${busy() ? "disabled" : ""}>${f ? "Redo" : "Make"}</button>
        <label class="file" style="display:inline-flex"><input type="file" accept="image/*" data-up="${sh.id}" data-kind="${kind}" hidden><span style="border:1px solid var(--line);border-radius:999px;padding:5px 8px;cursor:pointer;text-transform:none;letter-spacing:0;font-size:12px;color:var(--fg)">Upload</span></label>
        ${f ? `<button type="button" data-ok="${sh.id}" data-kind="${kind}" class="${ok ? "playing" : ""}">${ok ? "✓ OK" : "Approve"}</button>` : ""}
      </div>` : ""}</div>`;
  }

  function shotCard(sh, i) {
    const t0 = i * 5, names = castNames();
    const ro = busy() ? "disabled" : "";
    const beats = (sh.beats && sh.beats.length ? sh.beats : [{ t: "0-5s", action: "" }]);
    const vid = sh.video && sbProject ? `<div class="kf"><video src="/api/projects/${sbProject.id}/file/${sh.video}" ${sh.thumb ? `poster="/api/projects/${sbProject.id}/file/${sh.thumb}"` : ""} controls muted playsinline></video>
        <span class="note">Video · ${sh.seconds || 5}s${sh.credits ? ` · ~${Math.round(sh.credits)} credits` : ""}</span>
        <div class="row"><button type="button" data-revid="${sh.id}" ${ro}>Redo video</button></div></div>` : "";
    const state = sh.state && sh.state !== "waiting" ? `<span class="pill">${esc(sh.state === "generating" ? `generating ${sh.elapsed || 0}s` : sh.state)}</span>` : "";
    const prev = shotsArr()[i - 1], sceneHdr = sh.scene && (!prev || prev.scene !== sh.scene) ? `<div class="scene">🎬 ${esc(sh.scene)}</div>` : "";
    const lines = sh.lines || [];
    const spk = ["Narrator", ...names];
    const linesHtml = `<div style="display:grid;gap:6px"><span class="lbl">Spoken lines (each speaker has their own voice)</span>
        ${lines.map((ln, j) => `<div class="line"><select data-ls="${j}" ${ro}>${spk.map(n => `<option ${ln.speaker === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
          <input data-lt="${j}" value="${esc(ln.text || "")}" placeholder="max ~12 words" ${ro}><button type="button" class="ghost" data-ldel="${j}" title="Remove" ${ro}>✕</button></div>`).join("")}
        ${lines.length < 3 && !busy() ? `<div><button type="button" class="ghost" data-addline="${i}">+ line</button></div>` : ""}</div>`;
    return sceneHdr + `<div class="shot" data-i="${i}">
      <div class="row between"><div class="row" style="gap:8px"><b>Shot ${i + 1}</b><span class="note">${fmtT(t0)}–${fmtT(t0 + 5)}</span>
        <input data-f="title" value="${esc(sh.title || "")}" style="max-width:220px" ${ro}></div>
        <div class="row" style="gap:6px">${state}
          ${!sbProject ? `<button type="button" class="ghost" data-up1="${i}" title="Move up">↑</button><button type="button" class="ghost" data-del="${i}">Delete</button>` : ""}</div></div>
      <div class="g3">
        <label>How it's made<select data-f="mode" ${ro}>${Object.entries({ i2v: "New shot from a start keyframe", flf: "Start + end keyframes (best for hands)", continue: "Continue previous shot (no cut)" })
          .map(([k, v]) => `<option value="${k}" ${sh.mode === k ? "selected" : ""} ${k === "continue" && i === 0 ? "disabled" : ""}>${v}</option>`).join("")}</select></label>
        <label>Shot size<select data-f="shot" ${ro}>${SIZES.map(s => `<option ${sh.shot === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label>Camera<input data-f="camera" value="${esc(sh.camera || "")}" ${ro}></label>
      </div>
      <div class="row" style="gap:12px"><span class="lbl">In this shot</span>${names.map(n => `<label class="opt"><input type="checkbox" data-ch="${esc(n)}" ${(sh.characters || []).includes(n) ? "checked" : ""} ${ro}>${esc(n)}</label>`).join("")}</div>
      <div class="g2 det"><label>Setting<input data-f="setting" value="${esc(sh.setting || "")}" ${ro}></label>
        <label>Props (big and simple)<input data-f="props" value="${esc(sh.props || "")}" placeholder="e.g. a red ball" ${ro}></label></div>
      <div class="g2 det"><label>${sh.mode === "continue" ? "Starts from the last frame of the previous shot" : "Start keyframe: the first picture"}<textarea data-f="start_frame" ${ro || (sh.mode === "continue" ? "disabled" : "")}>${esc(sh.start_frame || "")}</textarea></label>
        ${sh.mode === "flf" ? `<label>End keyframe: the last picture (where hands and props end up)<textarea data-f="end_frame" ${ro}>${esc(sh.end_frame || "")}</textarea></label>` : `<div></div>`}</div>
      <div style="display:grid;gap:6px"><span class="lbl">What happens, second by second (one action per shot)</span>
        ${beats.map((b, j) => `<div class="beat"><input data-bt="${j}" value="${esc(b.t || "")}" ${ro}><input data-ba="${j}" value="${esc(b.action || "")}" placeholder="one simple movement" ${ro}></div>`).join("")}
        ${beats.length < 4 && !busy() ? `<div><button type="button" class="ghost" data-addbeat="${i}">+ beat</button></div>` : ""}</div>
      <div class="g3 det"><label>Eyes look at…<input data-f="eyes" value="${esc(sh.eyes || "")}" ${ro}></label>
        <label>Hands do…<input data-f="hands" value="${esc(sh.hands || "")}" ${ro}></label>
        <label>Feeling<input data-f="emotion" value="${esc(sh.emotion || "")}" ${ro}></label></div>
      ${linesHtml}
      <div class="g2"><label>Sound effect<div class="row" style="flex-wrap:nowrap"><select data-f="sfx" ${ro}>${(opts ? opts.sfx : ["none", sh.sfx || "none"]).map(x => `<option ${sh.sfx === x ? "selected" : ""}>${x}</option>`).join("")}</select><button type="button" data-play="${i}" title="Hear it">▶</button></div></label><div></div></div>
      ${(sh.warnings || []).map(w => `<div class="warn">⚠️ ${esc(w)}</div>`).join("")}
      ${sbProject ? `<div class="kfs">${sh.mode !== "continue" ? kfBox(sh, "start") : ""}${sh.mode === "flf" ? kfBox(sh, "end") : ""}${vid}</div>` : ""}
    </div>`;
  }

  function renderSB() {
    const d = sbProject || sb;
    $("sb-title").value = d.title || ""; $("sb-logline").textContent = d.logline || "";
    $("sb-shots").innerHTML = shotsArr().map(shotCard).join("");
    $("sb-shots").classList.toggle("compact", $("sb-compact").checked);
    renderVoices($("x-voices"), "final");
    $("sb-addRow").hidden = !!sbProject;
    const st = sbProject ? sbProject.status : "plan", shots = shotsArr();
    const kfDone = shots.every(s => s.mode === "continue" || (s.kf_start && (s.mode !== "flf" || s.kf_end)));
    const vidDone = shots.every(s => s.video);
    $("sb-steps").innerHTML = [["Story", true], ["Keyframes", kfDone && !!sbProject], ["Shot videos", vidDone], ["Final short", sbProject && sbProject.final && st === "done"]]
      .map(([t, on]) => `<span class="${on ? "on" : ""}">${on ? "✓ " : ""}${t}</span>`).join("");
    $("sb-pill").textContent = !sbProject ? "Draft" : st === "running" ? (sbProject.stage === "video" ? "Making videos" : sbProject.stage === "keyframes" ? "Making keyframes" : "Working") : st === "done" ? "Done" : st === "error" ? "Stopped" : "Ready";
    $("sb-kf").disabled = busy(); $("sb-video").disabled = busy() || !sbProject || !kfDone;
    $("sb-stop").hidden = !busy(); $("sb-cancel").hidden = !busy();
    $("sb-kf").textContent = sbProject && shots.some(s => s.kf_start) ? "1 · Make missing keyframes" : "1 · Make keyframes";
    $("sb-video").textContent = vidDone ? "2 · Re-make all videos" : (shots.some(s => s.video) ? "2 · Make the remaining videos" : "2 · Make the video");
    const done = shots.filter(s => s.state === "done").length;
    $("sb-barWrap").hidden = !busy(); $("sb-bar").style.width = Math.round(done / shots.length * 100) + "%";
    $("sb-log").textContent = (sbProject ? sbProject.log || [] : []).join("\n");
    if (sbProject && sbProject.status === "error") showErr(sbProject.error || "Stopped.");
    const fin = sbProject && sbProject.final && st === "done";
    $("sb-final").hidden = !fin;
    if (fin) {
      const q = `?t=${(sbProject.log || []).length}`, base = `/api/projects/${sbProject.id}/file/`;
      const v = base + sbProject.final + q, vs = sbProject.final_subtitled ? base + sbProject.final_subtitled + q : null;
      $("sb-subsWrap").hidden = !vs; $("sb-dlsubs").hidden = !vs; $("sb-srt").hidden = !vs; $("sb-subsNote").hidden = !vs;
      const show = vs && $("sb-showsubs").checked ? vs : v;
      if ($("sb-vid").dataset.src !== show) { const t = $("sb-vid").currentTime || 0; $("sb-vid").src = show; $("sb-vid").dataset.src = show; $("sb-vid").currentTime = t; }
      $("sb-dl").href = v; if (vs) $("sb-dlsubs").href = vs; $("sb-srt").href = base + "subtitles.srt";
      $("sb-path").textContent = "Saved in ReelStudio/" + (sbProject.output_copy || "output");
      const ss = sbProject.story_settings || {};
      $("x-music").value = ss.music || "original"; $("x-mvol").value = ss.music_volume ?? 0.35; $("x-fvol").value = ss.sfx_volume ?? 0.6;
      $("x-rate").value = String(ss.voice_rate || 0);
    }
    updEst();
  }

  // edits
  $("sb-shots").addEventListener("input", e => {
    const card = e.target.closest(".shot"); if (!card) return;
    const sh = shotsArr()[+card.dataset.i], t = e.target;
    if (t.dataset.f) sh[t.dataset.f] = t.value;
    if (t.dataset.bt) { sh.beats = sh.beats || []; (sh.beats[+t.dataset.bt] ||= { t: "", action: "" }).t = t.value; }
    if (t.dataset.ba) { sh.beats = sh.beats || []; (sh.beats[+t.dataset.ba] ||= { t: "", action: "" }).action = t.value; }
    if (t.dataset.lt !== undefined) { sh.lines = sh.lines || []; (sh.lines[+t.dataset.lt] ||= { speaker: "Narrator", text: "" }).text = t.value; }
    if (t.dataset.ls !== undefined) { sh.lines = sh.lines || []; (sh.lines[+t.dataset.ls] ||= { speaker: "Narrator", text: "" }).speaker = t.value; }
    if (t.dataset.ch !== undefined) {
      const n = t.dataset.ch; sh.characters = (sh.characters || []).filter(x => x !== n); if (t.checked) sh.characters.push(n);
    }
    dirty = true;
  });
  $("sb-shots").addEventListener("change", e => { if (e.target.dataset.f === "mode") { renderSB(); } if (e.target.dataset.f) refreshWarnings(); });
  $("sb-title").oninput = () => { (sbProject || sb).title = $("sb-title").value; dirty = true; };
  async function refreshWarnings() {
    if (!sbProject) return; await saveShots();
  }
  $("sb-shots").addEventListener("click", async e => {
    const b = e.target.closest("button"); if (!b) return;
    const shots = shotsArr();
    if (b.dataset.addline) { const sh = shots[+b.dataset.addline]; (sh.lines ||= []).push({ speaker: "Narrator", text: "" }); dirty = true; renderSB(); return; }
    if (b.dataset.ldel !== undefined && b.closest(".shot")) { const sh = shots[+b.closest(".shot").dataset.i]; sh.lines.splice(+b.dataset.ldel, 1); dirty = true; renderSB(); return; }
    if (b.dataset.addbeat) { const sh = shots[+b.dataset.addbeat]; (sh.beats ||= []).push({ t: "", action: "" }); renderSB(); return; }
    if (b.dataset.del) { shots.splice(+b.dataset.del, 1); renderSB(); return; }
    if (b.dataset.up1) { const i = +b.dataset.up1; if (i > 0) [shots[i - 1], shots[i]] = [shots[i], shots[i - 1]]; renderSB(); return; }
    if (b.dataset.play) { const n = shots[+b.dataset.play].sfx; if (n && n !== "none") new Audio(`/api/story/sfx/${encodeURIComponent(n)}`).play(); return; }
    if (b.dataset.redo) { await run("keyframes", { only: [b.dataset.redo], which: b.dataset.kind }); return; }
    if (b.dataset.ok) {
      try { sbProject = await post(`/api/story/${sbProject.id}/keyframe/${b.dataset.ok}/${b.dataset.kind}`, { approve: true }); renderSB(); } catch (err) { showErr(err); }
      return;
    }
    if (b.dataset.revid) { await run("video", { only: [b.dataset.revid] }); return; }
  });
  $("sb-shots").addEventListener("change", async e => {
    const f = e.target.closest("[data-up]"); if (!f || !f.files[0]) return;
    const fd = new FormData(); fd.append("image", f.files[0]);
    try { sbProject = await api(`/api/story/${sbProject.id}/keyframe/${f.dataset.up}/${f.dataset.kind}`, { method: "POST", body: fd }); renderSB(); } catch (err) { showErr(err); }
  });
  $("sb-add").onclick = () => {
    const n = castNames()[0] || "Pari";
    sb.shots.push({ title: "New shot", characters: [n], shot: "medium", camera: "the camera holds steady", setting: $("s-setting").value || "",
      props: "", mode: "i2v", start_frame: `${n} standing, smiling`, end_frame: "", beats: [{ t: "0-2s", action: "" }, { t: "2-4s", action: "" }, { t: "4-5s", action: "" }],
      eyes: "", hands: "", emotion: "", lines: [], sfx: "none" });
    renderSB();
  };

  async function updEst() {
    if ($("sbBox").hidden) return;
    const shots = shotsArr(); if (!shots) return;
    const turbo = sbProject ? sbProject.settings.turbo : $("s-turbo").checked, dyn = sbProject ? sbProject.settings.dynamic : $("s-dyn").checked;
    try {
      const e = await post("/api/story/estimate", { shots, turbo, dynamic: dyn });
      const bal = typeof creditInfo !== "undefined" && creditInfo?.balance?.available ? creditInfo.balance.credits : null;
      $("sb-est").className = "est" + (bal != null && bal < e.credits ? " bad" : "");
      $("sb-est").textContent = `${shots.length} shots · ${shots.length * 5} s · about ${num(e.credits)} credits (${e.keyframes} keyframes ~${num(e.keyframe_credits)} + video ~${num(e.video_credits)}), ~$${e.usd.toFixed(2)}` + (bal != null ? ` · you have ${num(bal)}` : "");
    } catch (err) {}
  }

  async function saveShots() {
    if (!sbProject || busy()) return;
    try {
      sbProject = await post(`/api/story/${sbProject.id}/shots`, { shots: sbProject.clips, title: $("sb-title").value });
      dirty = false; renderSB();
    } catch (e) { showErr(e); }
  }

  async function ensureProject() {
    if (sbProject) { if (dirty) await saveShots(); return; }
    sbProject = await post("/api/story", {
      character_ids: castSel, story: { title: $("sb-title").value, logline: sb.logline, shots: sb.shots },
      turbo: $("s-turbo").checked, dynamic: $("s-dyn").checked, upscale: $("s-up").checked,
      story_settings: { language: $("s-lang").value, voice: (voiceCast.Narrator || {}).voice, voices: voiceCast,
        music: $("s-music").value, song_id: $("s-song").value || null, song_start: 0 } });
    dirty = false;
  }

  async function run(what, body) {
    $("err").hidden = true;
    try {
      await ensureProject();
      const url = `/api/story/${sbProject.id}/${what}`;
      try { sbProject = await post(url, body || {}); }
      catch (e) {
        if (!(e.data && e.data.turbo_off)) throw e;
        const a = await askTurbo(e.data.turbo);
        if (a === "enable") { await post(`/api/story/${sbProject.id}/shots`, { settings: { turbo: true } }); sbProject = await post(url, body || {}); }
        else if (a === "slow") sbProject = await post(url, { ...(body || {}), allow_slow: true });
        else throw new Error("Not started. Turbo is still off.");
      }
      renderSB(); watchStory();
    } catch (e) { showErr(e); }
  }
  $("sb-kf").onclick = () => run("keyframes", {});
  $("sb-video").onclick = () => {
    const shots = shotsArr(), done = shots.every(s => s.video);
    const first = shots.findIndex(s => !s.video);
    run("video", done ? { from_shot: 0 } : { from_shot: Math.max(0, first) });
  };
  $("sb-stop").onclick = () => sbProject && post(`/api/projects/${sbProject.id}/stop`);
  $("sb-cancel").onclick = () => sbProject && post(`/api/projects/${sbProject.id}/stop`, { now: true });
  $("sb-showsubs").onchange = () => renderSB();
  $("sb-reveal").onclick = () => sbProject && post(`/api/projects/${sbProject.id}/reveal`);
  $("x-remix").onclick = async () => {
    try {
      const vs = voicesFrom($("x-voices"));
      sbProject = await post(`/api/story/${sbProject.id}/assemble`, { voices: vs, voice: (vs.Narrator || {}).voice, voice_rate: +$("x-rate").value,
        music: $("x-music").value, song_id: $("s-song").value || null, music_volume: +$("x-mvol").value,
        sfx_volume: +$("x-fvol").value });
      renderSB(); watchStory();
    } catch (e) { showErr(e); }
  };

  function watchStory() { clearInterval(sbTimer); sbTimer = setInterval(tickStory, 3000); }
  async function tickStory() {
    if (!sbProject) return;
    let d; try { d = await api("/api/projects/" + sbProject.id); } catch (e) { return; }
    const focus = document.activeElement && $("sb-shots").contains(document.activeElement);
    sbProject = d;
    if (!focus || d.status !== "running") renderSB();
    if (d.status !== "running") { clearInterval(sbTimer); if (typeof loadHist === "function") loadHist(); if (typeof loadCredits === "function") loadCredits(true); }
  }

  // open a story from "Past reels"
  window.openStory = async function (pid) {
    setMode("story");
    sbProject = await api("/api/projects/" + pid); sb = null;
    castSel = (sbProject.cast || []).map(c => c.id);
    renderCast(); $("sb-planner").textContent = "Saved story"; $("sbBox").hidden = false; renderSB();
    $("sbBox").scrollIntoView({ behavior: "smooth" });
    if (sbProject.status === "running") watchStory();
  };

  let start = "dance"; try { start = localStorage.getItem("rs_mode") || "dance"; } catch (e) {}
  const wait = setInterval(() => { if (typeof chars !== "undefined" && chars.length) { clearInterval(wait); setMode(start); } }, 300);
  setTimeout(() => clearInterval(wait), 10000);
  loadOpts();
})();
