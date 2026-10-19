"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
let settings = {};

async function api(url, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), ms);
}

function fmtSize(n) {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

// ------------------------------------------------------------------ tabs
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${b.dataset.tab}`));
  if (b.dataset.tab === "library") loadTree();
  if (b.dataset.tab === "duplicates") loadDuplicates(true);
  if (b.dataset.tab === "quality") loadQuality(true);
}));

// -------------------------------------------------------------- settings
async function loadSettings() {
  const data = await api("/api/settings");
  settings = data.settings;
  $("#layout").innerHTML = data.layouts.map((l) => `<option value="${esc(l.id)}">${esc(l.label)}</option>`).join("");
  $("#source").value = settings.source;
  $("#library").value = settings.library;
  $("#layout").value = settings.layout;
  $(`input[name=mode][value=${settings.mode}]`).checked = true;
  $("#separate_kinds").checked = settings.separate_kinds;
  $("#delete_mode").value = settings.delete_mode;
}

async function saveSettings(quiet) {
  const body = {
    source: $("#source").value, library: $("#library").value, layout: $("#layout").value,
    mode: $("input[name=mode]:checked").value, separate_kinds: $("#separate_kinds").checked,
    delete_mode: $("#delete_mode").value,
  };
  settings = (await api("/api/settings", body)).settings;
  if (!quiet) {
    $("#saved").classList.remove("hidden");
    setTimeout(() => $("#saved").classList.add("hidden"), 2000);
  }
}

$("#save").addEventListener("click", () => saveSettings().catch((e) => toast(e.message)));
$$("[data-pick]").forEach((b) => b.addEventListener("click", async () => {
  b.disabled = true;
  try {
    const { path } = await api("/api/pick-folder", {});
    if (path) $(`#${b.dataset.pick}`).value = path;
  } finally { b.disabled = false; }
}));

// ------------------------------------------------------------------ jobs
let jobDone = null;

async function startJob(url, onDone) {
  await api(url, {});
  jobDone = onDone;
  $("#job").classList.remove("hidden");
  pollJob();
}

async function pollJob() {
  const s = await api("/api/job").catch(() => null);
  if (!s) return setTimeout(pollJob, 1000);
  if (s.state === "running") {
    $("#job-msg").textContent = s.total ? `${s.message}  (${s.done} / ${s.total})` : s.message;
    $("#job-bar").max = s.total || 1;
    $("#job-bar").value = s.done || 0;
    return setTimeout(pollJob, 400);
  }
  $("#job").classList.add("hidden");
  if (s.state === "error") toast(`Error: ${s.error}`, 8000);
  if (s.state === "cancelled") toast("Cancelled");
  const cb = jobDone;
  jobDone = null;
  if (cb) cb(s);
}

$("#job-cancel").addEventListener("click", () => api("/api/job/cancel", {}));

// ----------------------------------------------------------------- sort
function renderPreview(r) {
  const src = Object.entries(r.date_sources).map(([k, v]) => `${esc(k)}: ${v}`).join(", ") || "—";
  let html = `<h3>Preview</h3>
    <p><b>${r.total}</b> files (${r.photos} photos, ${r.videos} videos) → <b>${r.folders.length}</b> folders.
    Dates from: ${src}. <span class="hint">“file” = no capture date inside the file, the file's modified time was used.</span></p>
    <div class="scroll"><table><tr><th>Folder</th><th>Files</th></tr>
    ${r.folders.map((f) => `<tr><td>${esc(f.folder)}</td><td>${f.count}</td></tr>`).join("")}</table></div>`;
  if (r.skipped_count) {
    html += `<details><summary>${r.skipped_count} files will be skipped</summary><div class="scroll"><table>
      ${r.skipped.map((s) => `<tr><td>${esc(s.path)}</td><td>${esc(s.reason)}</td></tr>`).join("")}</table></div></details>`;
  }
  $("#sort-result").innerHTML = html;
}

function renderSortResult(r) {
  const c = r.counts;
  const labels = { copied: "Copied", moved: "Moved", renamed: "Saved with new name (name clash)",
    "already-backed-up": "Already in library (skipped)", error: "Errors", cancelled: "Not processed (cancelled)" };
  let html = `<h3>Backup finished</h3><table>${Object.entries(labels).filter(([k]) => c[k])
    .map(([k, l]) => `<tr><td>${l}</td><td class="${k === "error" ? "err" : ""}">${c[k]}</td></tr>`).join("")}</table>
    <p class="hint">Full log: ${esc(r.log)}</p>`;
  if (r.errors.length) {
    html += `<details open><summary class="err">Errors</summary><div class="scroll"><table>
      ${r.errors.map((e) => `<tr><td>${esc(e.path)}</td><td>${esc(e.error)}</td></tr>`).join("")}</table></div></details>`;
  }
  $("#sort-result").innerHTML = html;
}

$("#preview").addEventListener("click", async () => {
  try {
    await saveSettings(true);
    $("#sort-result").innerHTML = "";
    await startJob("/api/sort/preview", (s) => s.state === "finished" && renderPreview(s.result));
  } catch (e) { toast(e.message, 6000); }
});

$("#start").addEventListener("click", async () => {
  try {
    await saveSettings(true);
    const verb = settings.mode === "move" ? "MOVE (originals removed after verified copy)" : "COPY";
    if (!confirm(`Start backup?\n\nFrom: ${settings.source}\nTo: ${settings.library}\nMode: ${verb}`)) return;
    $("#sort-result").innerHTML = "";
    await startJob("/api/sort/start", (s) => s.result && renderSortResult(s.result));
  } catch (e) { toast(e.message, 6000); }
});

// --------------------------------------------------------------- tiles
// One selection per view, so ticking something in one tab never affects another.
const selections = { lib: new Set(), dup: new Set(), q: new Set() };
const selOf = (el) => selections[el.closest("[data-sel]").dataset.sel];

function tile(f, opts = {}, selected = new Set()) {
  const sel = selected.has(f.path);
  const meta = opts.meta ?? `${f.name} · ${fmtSize(f.size)}`;
  return `<div class="tile ${sel ? "selected" : ""}" data-path="${esc(f.path)}" data-kind="${esc(f.kind)}" data-name="${esc(f.name)}">
    <input type="checkbox" class="pick" ${sel ? "checked" : ""} title="Select for deletion">
    ${opts.keep ? '<span class="badge">KEEP</span>' : ""}
    <img loading="lazy" src="/api/thumb?path=${enc(f.path)}" alt="${esc(f.name)}">
    ${opts.reasons ? `<div class="reasons">${opts.reasons.map((r) => `<span>${esc(r)}</span>`).join("")}</div>` : ""}
    <div class="meta" title="${esc(f.path)}">${esc(meta)}</div></div>`;
}

document.addEventListener("change", (ev) => {
  if (!ev.target.classList.contains("pick")) return;
  const t = ev.target.closest(".tile");
  const selected = selOf(t);
  if (ev.target.checked) selected.add(t.dataset.path); else selected.delete(t.dataset.path);
  t.classList.toggle("selected", ev.target.checked);
  updateDupSummary();
});

document.addEventListener("click", (ev) => {
  if (ev.target.tagName === "IMG" && ev.target.closest(".tile")) {
    const t = ev.target.closest(".tile");
    openViewer(t.dataset.path, t.dataset.name, t.dataset.kind);
  }
});

function setSelection(container, on) {
  const selected = selOf($(container));
  $$(`${container} .tile`).forEach((t) => {
    if (on) selected.add(t.dataset.path); else selected.delete(t.dataset.path);
    t.classList.toggle("selected", on);
    t.querySelector(".pick").checked = on;
  });
}

async function deleteSelected(container, after) {
  const selected = selOf($(container));
  const paths = $$(`${container} .tile`).map((t) => t.dataset.path).filter((p) => selected.has(p));
  if (!paths.length) return toast("Nothing selected");
  const where = settings.delete_mode === "folder" ? "the _PhotoBackup\\Deleted folder" : "the Recycle Bin";
  if (!confirm(`Delete ${paths.length} file(s)?\n\nThey will be moved to ${where}, so you can restore them if needed.`)) return;
  try {
    const r = await api("/api/delete", { paths });
    paths.forEach((p) => selected.delete(p));
    const failed = r.results.filter((x) => !x.ok);
    toast(`Deleted ${r.deleted} file(s)` + (failed.length ? ` — ${failed.length} failed: ${failed[0].error}` : ""), 6000);
    after();
  } catch (e) { toast(e.message, 6000); }
}

// --------------------------------------------------------------- viewer
let viewing = null;
function openViewer(path, name, kind) {
  viewing = path;
  $("#viewer-name").textContent = path;
  $("#viewer-body").innerHTML = kind === "video"
    ? `<video controls autoplay src="/api/file?path=${enc(path)}"></video>`
    : `<img src="/api/preview?path=${enc(path)}" alt="${esc(name)}">`;
  $("#viewer").classList.remove("hidden");
}
function closeViewer() { $("#viewer").classList.add("hidden"); $("#viewer-body").innerHTML = ""; }
$("#viewer-close").addEventListener("click", closeViewer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeViewer(); });
$("#viewer-open").addEventListener("click", () => api("/api/open", { path: viewing }).catch((e) => toast(e.message)));
$("#viewer-reveal").addEventListener("click", () => api("/api/open", { path: viewing, reveal: true }).catch((e) => toast(e.message)));

// -------------------------------------------------------------- library
let currentFolder = null;

function treeHtml(node) {
  return node.children.map((c) => {
    const label = `<span data-folder="${esc(c.path)}">${esc(c.name)}<span class="count">${c.total}</span></span>`;
    return c.children.length
      ? `<details><summary>${label}</summary>${c.count ? `<div class="leaf" data-folder="${esc(c.path)}">(files here)<span class="count">${c.count}</span></div>` : ""}${treeHtml(c)}</details>`
      : `<div class="leaf" data-folder="${esc(c.path)}">${esc(c.name)}<span class="count">${c.total}</span></div>`;
  }).join("");
}

async function loadTree() {
  try {
    const root = await api("/api/library/tree");
    $("#tree").innerHTML = (root.count ? `<div class="leaf" data-folder="">(library root)<span class="count">${root.count}</span></div>` : "")
      + (treeHtml(root) || '<div class="empty">Library is empty</div>');
    const first = $("#tree details");
    if (first) first.open = true;
    if (currentFolder !== null) loadFolder(currentFolder);
  } catch (e) { $("#tree").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

$("#tree").addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-folder]");
  if (!el) return;
  $$("#tree .current").forEach((x) => x.classList.remove("current"));
  el.classList.add("current");
  loadFolder(el.dataset.folder);
});

async function loadFolder(folder) {
  currentFolder = folder;
  selections.lib.clear();
  $("#folder-title").textContent = folder || "(library root)";
  try {
    const { files } = await api(`/api/library/files?folder=${enc(folder || ".")}`);
    $("#lib-grid").innerHTML = files.length
      ? files.map((f) => tile(f, { meta: `${f.name} · ${f.mtime.slice(0, 10)}` })).join("")
      : '<div class="empty">No photos or videos directly in this folder — open a subfolder.</div>';
  } catch (e) { $("#lib-grid").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

$("#lib-all").addEventListener("click", () => setSelection("#lib-grid", true));
$("#lib-none").addEventListener("click", () => setSelection("#lib-grid", false));
$("#lib-delete").addEventListener("click", () => deleteSelected("#lib-grid", loadTree));

// ------------------------------------------------------------ analysis
$$(".scan").forEach((b) => b.addEventListener("click", async () => {
  try {
    await startJob("/api/analyze", (s) => {
      if (s.state !== "finished") return;
      const r = s.result;
      toast(`Scanned ${r.files} files (${r.photos} photos, ${r.videos} videos)` + (r.unreadable ? `, ${r.unreadable} unreadable` : ""), 5000);
      loadDuplicates();
      loadQuality();
    });
  } catch (e) { toast(e.message, 6000); }
}));

// ----------------------------------------------------------- duplicates
let dupGroups = [];

async function loadDuplicates(quiet) {
  const t = $("#dup-threshold").value;
  const sim = $("#dup-similar").checked ? 1 : 0;
  try {
    dupGroups = (await api(`/api/duplicates?threshold=${t}&similar=${sim}`)).groups;
  } catch (e) {
    $("#dup-groups").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    if (!quiet) toast(e.message);
    return;
  }
  // Pre-select every copy except the suggested keeper.
  const selected = selections.dup;
  selected.clear();
  dupGroups.forEach((g) => g.items.forEach((i) => { if (i.path !== g.keep) selected.add(i.path); }));
  $("#dup-groups").innerHTML = dupGroups.length ? dupGroups.map((g, gi) => `
    <div class="group card" data-group="${gi}">
      <h3><span class="pill ${g.kind}">${g.kind === "exact" ? "Exact duplicate" : "Similar"}</span> ${g.items.length} files</h3>
      <div class="grid">${g.items.map((i) => tile(i, {
        keep: i.path === g.keep,
        meta: `${i.path} · ${fmtSize(i.size)}${i.width ? ` · ${i.width}×${i.height}` : ""}`,
      }, selected)).join("")}</div>
    </div>`).join("") : '<div class="empty">No duplicates found 🎉</div>';
  updateDupSummary();
}

function updateDupSummary() {
  if (!dupGroups.length) { $("#dup-summary").textContent = ""; return; }
  let n = 0, bytes = 0;
  const selected = selections.dup;
  dupGroups.forEach((g) => g.items.forEach((i) => { if (selected.has(i.path)) { n++; bytes += i.size; } }));
  $("#dup-summary").textContent = `${dupGroups.length} groups · ${n} selected (${fmtSize(bytes)})`;
}

$("#dup-threshold").addEventListener("input", (e) => { $("#dup-threshold-v").textContent = e.target.value; });
$("#dup-threshold").addEventListener("change", () => loadDuplicates());
$("#dup-similar").addEventListener("change", () => loadDuplicates());
$("#dup-delete").addEventListener("click", () => {
  const wipesGroup = dupGroups.some((g) => g.items.every((i) => selections.dup.has(i.path)));
  if (wipesGroup && !confirm("Warning: in at least one group EVERY copy is selected, so that photo would be removed completely. Continue?")) return;
  deleteSelected("#dup-groups", () => loadDuplicates());
});

// --------------------------------------------------------------- quality
async function loadQuality(quiet) {
  const q = new URLSearchParams();
  [["blur", "#q-blur"], ["dark", "#q-dark"], ["bright", "#q-bright"], ["min_mp", "#q-minmp"]]
    .forEach(([k, id]) => { if ($(id).value !== "") q.set(k, $(id).value); });
  try {
    const { items, defaults } = await api(`/api/quality?${q}`);
    [["blur", "#q-blur"], ["dark", "#q-dark"], ["bright", "#q-bright"], ["min_mp", "#q-minmp"]]
      .forEach(([k, id]) => { if ($(id).value === "") $(id).value = defaults[k]; });
    selections.q.clear();
    $("#q-grid").innerHTML = items.length ? items.map((i) => tile(i, {
      reasons: i.reasons,
      meta: `${i.name} · sharpness ${Math.round(i.sharpness)} · ${i.width}×${i.height}`,
    })).join("") : '<div class="empty">No low-quality photos with these settings.</div>';
  } catch (e) {
    $("#q-grid").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    if (!quiet) toast(e.message);
  }
}

$("#q-apply").addEventListener("click", () => loadQuality());
$("#q-all").addEventListener("click", () => setSelection("#q-grid", true));
$("#q-none").addEventListener("click", () => setSelection("#q-grid", false));
$("#q-delete").addEventListener("click", () => deleteSelected("#q-grid", () => loadQuality()));

// ------------------------------------------------------------------ init
loadSettings().catch((e) => toast(e.message));
api("/api/job").then((s) => { if (s.state === "running") { $("#job").classList.remove("hidden"); pollJob(); } });
