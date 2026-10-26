/* Refolio front end. Plain JS, no build step, works offline. */
"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  info: null,
  doc: null,          // {id, name, pages, ...}
  page: 1,
  markers: [],        // [{page, pos, y}]
  saveTimer: null,
  previewTimer: null,
  mode: localStorage.getItem("refolio.mode") || "endnotes",
};

const ENGINE_DESC = {
  builtin: "Reads the PDF's own text. Quick, and good for most born-digital papers.",
  marker: "Uses specially trained ai models. Slower, but handles scans and unusual layouts.",
};
const MODE_DESC = {
  endnotes: "Notes are collected after the text. Reference numbers link to them.",
  after_paragraph: "Each note is read right after the paragraph that cites it.",
  inline: "Each note is read in parentheses where it's cited.",
  omit: "Notes and their numbers are removed.",
};

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res;
}
const json = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------------- routing ---------------- */

function route() {
  const m = location.hash.match(/^#\/doc\/([a-f0-9]{12})/);
  if (m) openDoc(m[1]);
  else showHome();
}
window.addEventListener("hashchange", route);

/* ---------------- home ---------------- */

function engineChoices(container, name, selected) {
  container.querySelectorAll("label").forEach((l) => l.remove());
  for (const e of state.info.engines) {
    const label = document.createElement("label");
    if (!e.available) label.classList.add("off");
    const desc = e.available ? ENGINE_DESC[e.id] : state.info.native_save ?
      'Not included in this app. To use it, install Refolio from the Terminal instead; see the README.' :
      'Not installed. To add it, run <code>pip install "refolio[marker]"</code> and restart Refolio.';
    label.innerHTML = `<input type="radio" name="${name}" value="${e.id}" ${e.available ? "" : "disabled"}
      ${e.id === selected ? "checked" : ""}><span>${escapeHtml(e.label)}</span><span class="desc">${desc}</span>`;
    container.appendChild(label);
  }
}

async function showHome() {
  $("#review").hidden = true;
  $("#home").hidden = false;
  $("#drop-idle").hidden = false;
  $("#drop-busy").hidden = true;
  $("#drop-error").hidden = true;
  document.title = "Refolio";
  const saved = localStorage.getItem("refolio.engine") || "builtin";
  engineChoices($("#engines"), "engine", saved);
  const items = await api("/api/projects").then((r) => r.json());
  const ul = $("#recent");
  ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    const when = new Date(it.updated * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    li.innerHTML = `<input type="checkbox" class="pick" aria-label="Select ${escapeHtml(it.name || "")}">
      <a href="#/doc/${it.id}">${escapeHtml((it.name || "Untitled").replace(/\.pdf$/i, ""))}</a>
      <span class="when">${when}</span>
      <button type="button" class="ghost del" aria-label="Delete ${escapeHtml(it.name || "")}">Delete</button>`;
    li.querySelector(".del").onclick = async () => {
      if (!confirm(`Delete “${it.name}” from Refolio? Exported EPUBs aren't affected.`)) return;
      await api(`/api/projects/${it.id}`, { method: "DELETE" });
      li.remove();
      $("#recent-wrap").hidden = !ul.children.length;
      updatePicks();
    };
    li.querySelector(".pick").onchange = updatePicks;
    li.dataset.id = it.id;
    li.dataset.name = it.name || "Untitled";
    ul.appendChild(li);
  }
  $("#recent-wrap").hidden = !items.length;
  updatePicks();
}

function updatePicks() {
  const boxes = [...document.querySelectorAll("#recent .pick")];
  const n = boxes.filter((b) => b.checked).length;
  const all = $("#pick-all");
  all.hidden = !boxes.length;
  all.checked = n > 0 && n === boxes.length;
  all.indeterminate = n > 0 && n < boxes.length;
  const btn = $("#delete-picked");
  btn.hidden = !n;
  btn.textContent = `Delete selected (${n})`;
}

$("#pick-all").onchange = (e) => {
  document.querySelectorAll("#recent .pick").forEach((b) => (b.checked = e.target.checked));
  updatePicks();
};

$("#delete-picked").onclick = async () => {
  const rows = [...document.querySelectorAll("#recent li")].filter((li) => li.querySelector(".pick").checked);
  if (!rows.length) return;
  if (!confirm(`Delete ${rows.length} document${rows.length === 1 ? "" : "s"} from Refolio? Exported EPUBs aren't affected.`)) return;
  for (const li of rows) {
    await api(`/api/projects/${li.dataset.id}`, { method: "DELETE" });
    li.remove();
  }
  $("#recent-wrap").hidden = !$("#recent").children.length;
  updatePicks();
};

function selectedEngine(name) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : "builtin";
}

async function upload(file) {
  $("#drop-error").hidden = true;
  if (!file || !/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
    showDropError("Choose a PDF file.");
    return;
  }
  const engine = selectedEngine("engine");
  localStorage.setItem("refolio.engine", engine);
  const fd = new FormData();
  fd.append("file", file);
  fd.append("engine", engine);
  $("#drop-idle").hidden = true;
  $("#drop-busy").hidden = false;
  $("#busy-name").textContent = file.name.replace(/\.pdf$/i, "");
  try {
    const { id } = await api("/api/projects", { method: "POST", body: fd }).then((r) => r.json());
    await waitForConversion(id);
    location.hash = `#/doc/${id}`;
  } catch (e) {
    $("#drop-idle").hidden = false;
    $("#drop-busy").hidden = true;
    showDropError(e.message);
  }
}

function showDropError(msg) {
  const el = $("#drop-error");
  el.textContent = msg;
  el.hidden = false;
}

async function waitForConversion(id, onProgress) {
  for (;;) {
    const d = await api(`/api/projects/${id}`).then((r) => r.json());
    const pct = Math.round((d.progress ?? (d.status === "done" ? 1 : 0)) * 100);
    $("#bar-fill").style.width = pct + "%";
    $("#busy-msg").textContent = d.message || "";
    if (onProgress) onProgress(d);
    if (d.status === "done") return d;
    if (d.status === "error") throw new Error(d.message || "Conversion failed.");
    await new Promise((r) => setTimeout(r, 600));
  }
}

const drop = $("#drop");
$("#choose").onclick = () => $("#file").click();
$("#file").onchange = (e) => upload(e.target.files[0]);
drop.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target === drop) { e.preventDefault(); $("#file").click(); }
});
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));
// let a PDF dropped anywhere on the home screen count
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  if (!$("#home").hidden && !drop.contains(e.target)) upload(e.dataTransfer.files[0]);
});

/* ---------------- review ---------------- */

const editor = $("#editor");

async function openDoc(id) {
  const d = await api(`/api/projects/${id}`).then((r) => r.json()).catch((e) => ({ status: "error", message: e.message }));
  if (d.status === "working") {
    $("#home").hidden = false;
    $("#review").hidden = true;
    $("#drop-idle").hidden = true;
    $("#drop-busy").hidden = false;
    $("#busy-name").textContent = (d.name || "").replace(/\.pdf$/i, "");
    try { await waitForConversion(id); } catch (e) { showDropError(e.message); return; }
    return openDoc(id);
  }
  if (d.status !== "done") {
    await showHome();
    showDropError(d.message || "That document couldn't be opened.");
    return;
  }
  state.doc = d;
  $("#home").hidden = true;
  $("#review").hidden = false;
  const name = d.title || (d.name || "Untitled").replace(/\.pdf$/i, "");
  $("#docname").textContent = name;
  document.title = `${name} · Refolio`;
  $("#pagecount").textContent = d.pages || 1;
  $("#savestate").textContent = "";
  setEditorValue(d.markdown, false);
  computeMarkers();
  // forget the previous document's page, or showPage(1) thinks nothing changed
  state.page = 0;
  delete $("#pageimg").dataset.src;
  showPage(1);
  renderNotices();
  if (!$("#preview").hidden) refreshPreview();
}

function setEditorValue(text, undoable = true) {
  if (undoable) {
    // keep the browser's own undo history: Cmd+Z undoes a cleanup step
    editor.focus();
    editor.select();
    const ok = document.execCommand && document.execCommand("insertText", false, text);
    if (!ok || editor.value !== text) editor.value = text;
    editor.setSelectionRange(0, 0);
    editor.scrollTop = 0;
  } else {
    editor.value = text;
  }
}

editor.addEventListener("input", () => {
  scheduleSave();
  clearTimeout(state.markerTimer);
  state.markerTimer = setTimeout(() => { computeMarkers(); renderNotices(); }, 400);
  if (!$("#preview").hidden) schedulePreview();
});

function scheduleSave() {
  $("#savestate").textContent = "Saving…";
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, 700);
}
async function save() {
  if (!state.doc) return;
  try {
    await json(`/api/projects/${state.doc.id}/markdown`, { markdown: editor.value }, "PUT");
    $("#savestate").textContent = "Saved";
  } catch (e) {
    $("#savestate").textContent = "Not saved: " + e.message;
  }
}

/* page markers and the page view */

const mirror = document.createElement("textarea");
mirror.setAttribute("aria-hidden", "true");
mirror.tabIndex = -1;
Object.assign(mirror.style, { position: "absolute", visibility: "hidden", height: "0", left: "-9999px", top: "0", overflow: "hidden" });
document.body.appendChild(mirror);

function yOf(pos) {
  const cs = getComputedStyle(editor);
  for (const p of ["font", "padding", "letterSpacing", "tabSize", "border", "boxSizing", "whiteSpace", "wordWrap"]) mirror.style[p] = cs[p];
  mirror.style.width = editor.clientWidth + "px";
  mirror.style.paddingBottom = "0";
  mirror.value = editor.value.slice(0, pos);
  return mirror.scrollHeight;
}

function computeMarkers() {
  const re = /^<!--\s*page\s+(\d+)\s*-->/gim;
  const text = editor.value;
  const out = [];
  let m;
  while ((m = re.exec(text))) out.push({ page: +m[1], pos: m.index });
  // measuring a marker lays out all the text before it, so for long books only a sample
  // is measured up front; markY() measures the ones in between when they are needed
  const step = out.length > 120 ? Math.ceil(out.length / 120) : 1;
  out.forEach((mk, i) => { if (i % step === 0 || i === out.length - 1) mk.y = yOf(mk.pos); });
  state.markers = out;
}

function markY(i) {
  const mk = state.markers[i];
  if (mk.y === undefined) mk.y = yOf(mk.pos);
  return mk.y;
}

function pageAtPos(pos) {
  let page = 1;
  for (const m of state.markers) { if (m.pos <= pos) page = m.page; else break; }
  return page;
}
function pageAtScroll() {
  const top = editor.scrollTop + 30;
  const ms = state.markers;
  if (!ms.length) return 1;
  // last measured marker above the top, then exact positions for the unmeasured ones after it
  let i = 0;
  for (let j = 0; j < ms.length; j++) { if (ms[j].y !== undefined) { if (ms[j].y <= top) i = j; else break; } }
  while (i + 1 < ms.length && markY(i + 1) <= top) i++;
  return ms[i].page;
}

function showPage(n) {
  const total = state.doc?.pages || 1;
  n = Math.max(1, Math.min(total, n));
  if (n === state.page && $("#pageimg").dataset.src) return;
  state.page = n;
  $("#pageno").textContent = n;
  const w = Math.min(2000, Math.round(($("#pageview").clientWidth || 700) * (window.devicePixelRatio || 1)));
  const src = `/api/projects/${state.doc.id}/pages/${n}.png?w=${Math.max(600, Math.round(w / 100) * 100)}`;
  $("#pageimg").src = src;
  $("#pageimg").dataset.src = src;
  $("#pageimg").alt = `Original PDF, page ${n}`;
}

function follow() { return $("#follow").checked && !$("#pageview").hidden; }
editor.addEventListener("scroll", () => { if (follow()) showPage(pageAtScroll()); });
["click", "keyup"].forEach((t) => editor.addEventListener(t, () => { if (follow()) showPage(pageAtPos(editor.selectionStart)); }));

function jumpToPage(n) {
  showPage(n);
  const i = state.markers.findIndex((m) => m.page === n);
  const mk = state.markers[i];
  if (mk) {
    editor.scrollTop = Math.max(0, markY(i) - 24);
    editor.setSelectionRange(mk.pos, mk.pos);
  }
}
$("#prev").onclick = () => jumpToPage(state.page - 1);
$("#next").onclick = () => jumpToPage(state.page + 1);
window.addEventListener("resize", () => { clearTimeout(state.rz); state.rz = setTimeout(computeMarkers, 300); });

/* tabs */
function selectTab(which) {
  const page = which === "page";
  $("#tab-page").setAttribute("aria-selected", page);
  $("#tab-preview").setAttribute("aria-selected", !page);
  $("#pageview").hidden = !page;
  $("#preview").hidden = page;
  $("#pager").style.visibility = page ? "visible" : "hidden";
  if (!page) refreshPreview();
}
$("#tab-page").onclick = () => selectTab("page");
$("#tab-preview").onclick = () => selectTab("preview");

function schedulePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(refreshPreview, 600);
}
async function refreshPreview() {
  try {
    const r = await json("/api/preview", { markdown: editor.value, mode: state.mode });
    $("#preview-body").innerHTML = r.html;
  } catch (e) {
    $("#preview-body").textContent = "The preview couldn't be built: " + e.message;
  }
}
$("#preview-mode").onchange = (e) => {
  state.mode = e.target.value;
  localStorage.setItem("refolio.mode", state.mode);
  refreshPreview();
};

/* notices: things worth a look */
function renderNotices() {
  const text = editor.value;
  const items = [];
  for (const w of state.doc?.warnings || []) {
    if (!/superscripts|no reference mark/.test(w)) items.push({ text: w });
  }
  const defs = new Set([...text.matchAll(/^\[\^([^\]\s]+)\]:/gm)].map((m) => m[1]));
  const refs = [...text.matchAll(/\[\^([^\]\s]+)\](?!:)/g)];
  const refSet = new Set(refs.map((m) => m[1]));
  const sups = [...text.matchAll(/<sup>.*?<\/sup>/g)];
  if (sups.length) items.push({
    text: `${sups.length} superscript mark${sups.length > 1 ? "s" : ""} didn't match a footnote. If they're note numbers, link them by hand, or remove them.`,
    find: "<sup>",
  });
  const missing = refs.filter((m) => !defs.has(m[1]));
  if (missing.length) items.push({ text: `${missing.length} footnote mark${missing.length > 1 ? "s have" : " has"} no note text.`, find: missing[0][0] });
  const uncited = [...defs].filter((d) => !refSet.has(d));
  if (uncited.length) items.push({
    text: `${uncited.length} note${uncited.length > 1 ? "s aren't" : " isn't"} cited in the text. ${state.mode === "endnotes" ? "They'll be listed at the end." : "They'll be left out."}`,
    find: `[^${uncited[0]}]:`,
  });
  const ul = $("#notice-list");
  ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(it.text)}</span>`;
    if (it.find) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "linklike";
      b.textContent = "Show";
      b.onclick = () => findText(it.find, false, true);
      li.appendChild(b);
    }
    ul.appendChild(li);
  }
  $("#notices").hidden = !items.length;
}

/* cleanup tools */
function buildTools() {
  const box = $("#tools");
  box.innerHTML = "";
  const tips = {
    page_numbers: "Delete lines that are only a page number.",
    repeated: "Delete short lines that repeat three or more times, like a journal's running head.",
    join_paragraphs: "Join paragraphs split by a page or column break (one ends mid-sentence, the next starts in lower case).",
    hyphens: "Join words split across lines, like “philo- sophy”, keeping real compounds.",
    superscripts: "Delete superscripts that didn't match a footnote.",
    citations: "Delete author-year citations such as (Smith 1990, 23), which are tedious to listen to.",
    urls: "Delete web addresses and DOIs.",
  };
  const groups = {};
  for (const op of state.info.cleanup) {
    if (!groups[op.group]) {
      const g = document.createElement("div");
      g.className = "tool-group";
      g.setAttribute("role", "group");
      g.setAttribute("aria-label", op.group);
      g.innerHTML = `<span class="tool-label">${escapeHtml(op.group)}</span>`;
      box.appendChild(g);
      groups[op.group] = g;
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tool";
    b.textContent = op.short;
    b.title = tips[op.id] || op.label;
    b.setAttribute("aria-label", op.label);
    b.onclick = async () => {
      try {
        const r = await json("/api/cleanup", { op: op.id, markdown: editor.value });
        if (!r.count) { toast(`${op.label}: nothing to change.`); return; }
        const top = editor.scrollTop;
        setEditorValue(r.markdown);
        editor.scrollTop = top;
        editor.dispatchEvent(new Event("input"));
        toast(`${op.label}: ${r.count} change${r.count > 1 ? "s" : ""}. Press ⌘Z to undo.`);
      } catch (e) { toast(e.message); }
    };
    groups[op.group].appendChild(b);
  }
}

/* find and replace */
$("#find-btn").onclick = () => {
  const bar = $("#findbar");
  bar.hidden = !bar.hidden;
  $("#find-btn").setAttribute("aria-expanded", !bar.hidden);
  if (!bar.hidden) $("#find").focus();
};
function makePattern() {
  const q = $("#find").value;
  if (!q) return null;
  try {
    return $("#regex").checked ? new RegExp(q, "gm") : new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gm");
  } catch (e) {
    $("#find-msg").textContent = "That pattern isn't valid: " + e.message;
    return null;
  }
}
function findText(q, regex = false, fromStart = false) {
  const text = editor.value;
  let idx, len;
  if (regex) {
    const re = makePattern();
    if (!re) return false;
    re.lastIndex = fromStart ? 0 : editor.selectionEnd;
    let m = re.exec(text);
    if (!m) { re.lastIndex = 0; m = re.exec(text); }
    if (!m) return false;
    idx = m.index; len = m[0].length;
  } else {
    idx = text.indexOf(q, fromStart ? 0 : editor.selectionEnd);
    if (idx < 0) idx = text.indexOf(q);
    if (idx < 0) return false;
    len = q.length;
  }
  editor.focus();
  editor.setSelectionRange(idx, idx + len);
  editor.scrollTop = Math.max(0, yOf(idx) - editor.clientHeight / 3);
  if (follow()) showPage(pageAtPos(idx));
  return true;
}
$("#find-next").onclick = () => {
  $("#find-msg").textContent = "";
  if ($("#find").value && !findText($("#find").value, $("#regex").checked)) $("#find-msg").textContent = "Not found.";
};
$("#find").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#find-next").click(); } });
$("#findbar").onsubmit = (e) => {
  e.preventDefault();
  $("#find-msg").textContent = "";
  const re = makePattern();
  if (!re) return;
  const matches = editor.value.match(re);
  if (!matches) { $("#find-msg").textContent = "Not found."; return; }
  const rep = $("#replace").value.replace(/\\n/g, "\n");
  const out = $("#regex").checked ? editor.value.replace(re, rep) : editor.value.split($("#find").value).join($("#replace").value);
  setEditorValue(out);
  editor.dispatchEvent(new Event("input"));
  $("#find-msg").textContent = `Replaced ${matches.length}.`;
};

/* export */
function buildModes() {
  const box = $("#modes");
  box.querySelectorAll("label").forEach((l) => l.remove());
  const sel = $("#preview-mode");
  sel.innerHTML = "";
  for (const m of state.info.footnote_modes) {
    const label = document.createElement("label");
    label.innerHTML = `<input type="radio" name="mode" value="${m.id}" ${m.id === state.mode ? "checked" : ""}>
      <span>${escapeHtml(m.label)}</span><span class="desc">${MODE_DESC[m.id] || ""}</span>`;
    box.appendChild(label);
    const o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.label.toLowerCase();
    o.selected = m.id === state.mode;
    sel.appendChild(o);
  }
}

function frontMatter(text) {
  const out = {};
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  if (m) for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim().replace(/^["'](.*)["']$/, "$1");
  }
  return out;
}

$("#export-btn").onclick = () => {
  const fm = frontMatter(editor.value);
  $("#ex-title").value = fm.title || $("#docname").textContent;
  $("#ex-author").value = fm.author || "";
  $("#ex-msg").textContent = "";
  buildModes();
  $("#export-dlg").showModal();
};
$("#ex-cancel").onclick = () => $("#export-dlg").close();
$("#export-form").onsubmit = async (e) => {
  e.preventDefault();
  const mode = document.querySelector('input[name="mode"]:checked')?.value || "endnotes";
  state.mode = mode;
  localStorage.setItem("refolio.mode", mode);
  const title = $("#ex-title").value.trim();
  const author = $("#ex-author").value.trim();
  // keep the edited title/author in the document itself
  let text = editor.value;
  const fmBlock = `---\ntitle: "${title.replace(/"/g, "'")}"\nauthor: "${author.replace(/"/g, "'")}"\n---`;
  text = /^---\n[\s\S]*?\n---/.test(text) ? text.replace(/^---\n[\s\S]*?\n---/, fmBlock) : fmBlock + "\n\n" + text;
  if (text !== editor.value) { editor.value = text; scheduleSave(); }
  $("#ex-go").disabled = true;
  $("#ex-msg").textContent = "Building…";
  try {
    const payload = { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown: text, mode, title, author }) };
    let name, warns;
    if (state.info?.native_save) {
      // the desktop app shows its own save dialog; WebViews can't be trusted to download blobs
      const r = await (await api(`/api/projects/${state.doc.id}/export-save`, payload)).json();
      if (!r.saved) { $("#ex-msg").textContent = ""; return; }
      name = r.name; warns = r.warnings;
    } else {
      const res = await api(`/api/projects/${state.doc.id}/export`, payload);
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      name = decodeURIComponent((cd.match(/filename\*=UTF-8''([^;]+)/) || [])[1] || "document.epub");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      warns = JSON.parse(decodeURIComponent(res.headers.get("X-Refolio-Warnings") || "%5B%5D"));
    }
    $("#export-dlg").close();
    toast(`Exported “${name}”.` + (warns.length ? " " + warns[0] : ""));
  } catch (err) {
    $("#ex-msg").textContent = "Export failed: " + err.message;
  } finally {
    $("#ex-go").disabled = false;
  }
};

/* convert again */
$("#reconvert-btn").onclick = () => {
  engineChoices($("#re-engines"), "re-engine", state.doc.engine || "builtin");
  $("#reconvert-dlg").showModal();
};
$("#re-cancel").onclick = () => $("#reconvert-dlg").close();
$("#re-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#reconvert-dlg").close();
  const fd = new FormData();
  fd.append("engine", selectedEngine("re-engine"));
  clearTimeout(state.saveTimer);
  await api(`/api/projects/${state.doc.id}/reconvert`, { method: "POST", body: fd });
  openDoc(state.doc.id);
};

$("#back").onclick = async () => { clearTimeout(state.saveTimer); await save(); location.hash = "#/"; };
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); }
  if ((e.metaKey || e.ctrlKey) && e.key === "f" && !$("#review").hidden) {
    e.preventDefault();
    $("#findbar").hidden = false;
    $("#find-btn").setAttribute("aria-expanded", "true");
    $("#find").focus();
    $("#find").select();
  }
});
window.addEventListener("beforeunload", () => { if (state.saveTimer) save(); });

/* start */
(async () => {
  state.info = await api("/api/info").then((r) => r.json());
  buildTools();
  buildModes();
  route();
})();
