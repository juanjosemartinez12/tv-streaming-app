const $ = (sel, root = document) => root.querySelector(sel);
const ACTIVE = new Set(["en_cola", "descargando", "transcribiendo", "analizando", "renderizando"]);
const STATUS_LABEL = {
  en_cola: "En cola", descargando: "Descargando", transcribiendo: "Transcribiendo", analizando: "Analizando",
  renderizando: "Generando clips", completado: "Listo", error: "Error",
};
const BREAKDOWN = [["gancho", "Gancho"], ["fluidez", "Fluidez"], ["valor", "Valor"], ["tendencia", "Tendencia"]];

let source = "file";
let pollTimer = null;

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function schedule(fn, ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(fn, ms);
}

// ---------- Formulario ----------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    source = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== source));
  });
});

const drop = $("#drop");
const fileInput = $("#file");
function showFile() {
  const f = fileInput.files[0];
  $("#drop-title").textContent = f ? f.name : "Arrastra tu video aquí";
  $("#drop-sub").textContent = f ? `${(f.size / 1e6).toFixed(1)} MB · listo para subir` : "o haz clic para elegirlo · MP4, MOV, MKV, WEBM";
}
fileInput.addEventListener("change", showFile);
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    showFile();
  }
});

$("#form").addEventListener("submit", (e) => {
  e.preventDefault();
  const error = $("#form-error");
  error.hidden = true;
  const data = new FormData();
  if (source === "file") {
    if (!fileInput.files[0]) return showError("Elige un video primero.");
    data.append("file", fileInput.files[0]);
  } else {
    const url = $("#url").value.trim();
    if (!url) return showError("Pega un enlace primero.");
    data.append("url", url);
  }
  const [min, max] = $("#duration").value.split("-");
  data.append("min_duration", min);
  data.append("max_duration", max);
  for (const id of ["num_clips", "layout", "caption_style", "language", "instructions"]) {
    data.append(id, $(`#${id}`).value);
  }

  const button = $("#submit");
  const bar = $("#upload");
  button.disabled = true;
  bar.hidden = source !== "file";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) bar.firstElementChild.style.width = `${(ev.loaded / ev.total) * 100}%`;
  };
  xhr.onload = () => {
    button.disabled = false;
    bar.hidden = true;
    bar.firstElementChild.style.width = "0";
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status >= 300) {
      return showError(typeof body.detail === "string" ? body.detail : "No se pudo crear el proyecto.");
    }
    fileInput.value = "";
    $("#url").value = "";
    showFile();
    location.hash = `#/proyecto/${body.id}`;
  };
  xhr.onerror = () => {
    button.disabled = false;
    bar.hidden = true;
    showError("Error de conexión con el servidor.");
  };
  xhr.send(data);

  function showError(msg) {
    error.textContent = msg;
    error.hidden = false;
  }
});

// ---------- Lista de proyectos ----------

async function renderHome() {
  $("#home").hidden = false;
  $("#detail").hidden = true;
  const container = $("#jobs");
  let jobs;
  try {
    jobs = await api("/api/jobs");
  } catch (err) {
    container.replaceChildren(el("p", "error", `No se pudo cargar la lista: ${err.message}`));
    return schedule(route, 5000);
  }
  if (!jobs.length) {
    container.replaceChildren(el("p", "empty", "Todavía no hay proyectos. ¡Sube tu primer video!"));
  } else {
    container.replaceChildren(...jobs.map(jobCard));
  }
  if (jobs.some((j) => ACTIVE.has(j.status))) schedule(route, 3000);
}

function jobCard(job) {
  const card = el("a", "job card");
  card.href = `#/proyecto/${job.id}`;
  card.append(el("div", "job-name", job.source_name));
  const meta = el("div", "job-meta");
  const badge = el("span", "badge", STATUS_LABEL[job.status]);
  if (job.status === "completado") badge.classList.add("done");
  if (job.status === "error") badge.classList.add("fail");
  meta.append(badge, el("span", "", job.status === "completado" ? `${job.clips.length} clip${job.clips.length === 1 ? "" : "s"}` : job.message));
  card.append(meta);
  if (ACTIVE.has(job.status)) card.append(progressBar(job.progress));
  return card;
}

function progressBar(pct) {
  const bar = el("div", "progress");
  const fill = el("div");
  fill.style.width = `${Math.max(2, Math.min(100, pct))}%`;
  bar.append(fill);
  return bar;
}

// ---------- Detalle ----------

async function renderDetail(id) {
  $("#home").hidden = true;
  $("#detail").hidden = false;
  let job;
  try {
    job = await api(`/api/jobs/${id}`);
  } catch (err) {
    $("#d-title").textContent = "Proyecto no encontrado";
    $("#d-meta").textContent = err.message;
    $("#d-status").replaceChildren();
    $("#clips").replaceChildren();
    return;
  }
  $("#d-title").textContent = job.source_name;
  const meta = [];
  if (job.duration) meta.push(`Duración ${fmtTime(job.duration).replace(/\.\d$/, "")}`);
  if (job.language) meta.push(`Idioma: ${job.language}`);
  if (job.selector) meta.push(job.selector === "claude" ? "Selección con Claude" : "Selección heurística (sin IA)");
  $("#d-meta").textContent = meta.join(" · ");

  const status = $("#d-status");
  status.hidden = job.status === "completado" && !job.warning;
  const parts = [el("p", "", job.message)];
  if (ACTIVE.has(job.status)) parts.push(progressBar(job.progress));
  if (job.warning) parts.push(el("p", "muted", job.warning));
  if (job.error) parts.push(el("p", "error", job.error));
  status.replaceChildren(...parts);

  const deleteBtn = $("#d-delete");
  deleteBtn.disabled = ACTIVE.has(job.status);
  deleteBtn.onclick = async () => {
    if (!confirm("¿Eliminar este proyecto y todos sus clips?")) return;
    try {
      await api(`/api/jobs/${job.id}`, { method: "DELETE" });
      location.hash = "#/";
    } catch (err) {
      alert(err.message);
    }
  };

  updateClips(job);
  if (ACTIVE.has(job.status) || job.clips.some((c) => c.rendering)) schedule(route, 2000);
}

function updateClips(job) {
  const container = $("#clips");
  if (container.dataset.job !== job.id) {
    container.replaceChildren();
    container.dataset.job = job.id;
  }
  job.clips.forEach((clip) => {
    let card = container.querySelector(`[data-index="${clip.index}"]`);
    if (!card) {
      card = $("#clip-template").content.firstElementChild.cloneNode(true);
      card.dataset.index = clip.index;
      container.append(card);
      wireEditor(card, job.id, clip.index);
    }
    fillClip(card, job, clip);
  });
}

function fillClip(card, job, clip) {
  const base = `/media/${job.id}/`;
  const video = $("video", card);
  const version = String(clip.version);
  if (clip.video && video.dataset.version !== version) {
    video.dataset.version = version;
    video.poster = clip.thumbnail ? `${base}${clip.thumbnail}?v=${version}` : "";
    video.src = `${base}${clip.video}?v=${version}`;
  }
  $(".busy", card).hidden = !clip.rendering;

  const score = $(".score", card);
  score.className = `score ${clip.score >= 80 ? "high" : clip.score >= 60 ? "mid" : "low"}`;
  score.replaceChildren(document.createTextNode(clip.score), el("small", "", "/100"));

  $(".time", card).textContent = `#${clip.index + 1} · ${fmtTime(clip.start)} – ${fmtTime(clip.end)} · ${(clip.end - clip.start).toFixed(0)} s`;
  $(".title", card).textContent = clip.title;
  $(".hook", card).textContent = clip.hook ? `“${clip.hook}”` : "";
  $(".reason", card).textContent = clip.reason;

  const bars = $(".bars", card);
  bars.replaceChildren();
  for (const [key, label] of BREAKDOWN) {
    const value = clip.breakdown[key];
    bars.append(el("span", "", label), progressBar((value / 25) * 100), el("span", "", `${value}/25`));
  }
  $(".tags", card).replaceChildren(...clip.hashtags.map((h) => el("span", "tag", `#${h}`)));

  const err = $(".clip-error", card);
  err.hidden = !clip.error;
  err.textContent = clip.error || "";

  const slug = clip.title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 50) || "clip";
  const download = $(".download", card);
  download.hidden = !clip.video;
  download.href = clip.video ? `${base}${clip.video}?v=${version}` : "#";
  download.download = `${String(clip.index + 1).padStart(2, "0")}-${slug}.mp4`;
  const srt = $(".srt", card);
  srt.hidden = !clip.srt;
  srt.href = clip.srt ? `${base}${clip.srt}?v=${version}` : "#";
  srt.download = `${String(clip.index + 1).padStart(2, "0")}-${slug}.srt`;

  $(".copy", card).onclick = async (e) => {
    const text = [clip.title, "", clip.description, "", clip.hashtags.map((h) => `#${h}`).join(" ")].join("\n");
    await navigator.clipboard.writeText(text.trim());
    e.target.textContent = "¡Copiado!";
    setTimeout(() => (e.target.textContent = "Copiar texto"), 1500);
  };

  const editor = $(".editor", card);
  if (editor.hidden) {
    $(".e-start", editor).value = clip.start;
    $(".e-end", editor).value = clip.end;
    $(".e-layout", editor).value = clip.layout;
    $(".e-style", editor).value = clip.caption_style;
  }
  const editable = job.status === "completado" && !clip.rendering;
  $(".edit", card).disabled = !editable;
  $("button[type=submit]", editor).disabled = !editable;
}

function wireEditor(card, jobId, index) {
  const editor = $(".editor", card);
  $(".edit", card).addEventListener("click", () => (editor.hidden = !editor.hidden));
  editor.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      start: parseFloat($(".e-start", editor).value),
      end: parseFloat($(".e-end", editor).value),
      layout: $(".e-layout", editor).value,
      caption_style: $(".e-style", editor).value,
    };
    try {
      const job = await api(`/api/jobs/${jobId}/clips/${index}/rerender`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      editor.hidden = true;
      updateClips(job);
      schedule(route, 1500);
    } catch (err) {
      alert(err.message);
    }
  });
}

// ---------- Navegación ----------

function route() {
  clearTimeout(pollTimer);
  const match = location.hash.match(/^#\/proyecto\/(\w+)/);
  if (match) renderDetail(match[1]);
  else renderHome();
}

window.addEventListener("hashchange", route);
api("/api/config")
  .then((cfg) => ($("#engine").textContent = cfg.claude ? `IA: Claude (${cfg.model})` : "Modo sin IA (heurística)"))
  .catch(() => ($("#engine").textContent = "Sin conexión"));
route();
