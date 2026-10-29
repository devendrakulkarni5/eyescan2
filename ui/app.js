"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = {
  items: new Map(), tiles: new Map(), sel: new Set(), focus: null, anchor: null, rejected: new Set(),
  filter: "closed", sub: "all", query: "", sort: "name", view: "grid", overlay: "box", only: null,
  scanning: false, visible: [], cfg: {}, info: {}, about: {}, folder: "", review: null, zoomFace: 0,
  tabs: [], tab: -1, pendingView: null, restoreView: null, lib: [],
};
let api = null, renderTimer = null, sensTimer = null;

const FILTERS = [
  ["Status", [
    ["closed", "Closed eyes", "var(--coral)", (r) => r.flagged, "eye-off"],
    ["wink", "Winks", "var(--sun)", (r) => r.wink && !r.flagged, "eye-closed"],
    ["down", "Looking down", "var(--sun)", (r) => r.n_down > 0, "arrow-down"],
    ["dups", "Duplicates", "var(--ocean)", (r) => r.dup_group !== null, "copy"],
    ["blur", "Blurry / misfocused", "var(--lav)", (r) => r.unclear, "aperture"],
    ["prot", "Protected", "var(--sage-d)", (r) => r.protected, "lock"],
    ["good", "Good", "var(--sage)", (r) => !r.flagged && !(r.dup_group !== null && !r.dup_best) && !r.unclear && r.verdict !== "error", "circle-check"],
    ["all", "All photos", "var(--muted)", () => true, "images"]]],
  ["Who", [
    ["solo", "Solo", "var(--muted)", (r) => r.group === "solo", "user"],
    ["pair", "Pair", "var(--muted)", (r) => r.group === "pair", "users"],
    ["group", "Group (3+)", "var(--muted)", (r) => r.group === "group", "users"],
    ["animal", "Animals", "var(--ocean)", (r) => r.animals.length > 0, "paw-print"],
    ["smile", "Smiling", "var(--sun)", (r) => r.smiling, "smile"],
    ["noface", "No face", "var(--muted)", (r) => r.verdict === "no-face", "user-x"]]],
  ["Look", [
    ["teeth", "Teeth showing", "var(--sun)", (r) => r.teeth, "laugh"],
    ["funny", "Funny face", "var(--sun)", (r) => r.funny, "annoyed"],
    ["glasses", "Glasses", "var(--ocean)", (r) => r.glasses, "glasses"],
    ["hid", "Eyes hidden", "var(--muted)", (r) => r.eyes_hidden, "eye-off"],
    ["bindi", "Bindi", "var(--coral)", (r) => r.bindi, "circle-dot"]]],
];
const ALLF = FILTERS.flatMap((g) => g[1]);
const predOf = (k) => ALLF.find((f) => f[0] === k)[3];

/* ---------- helpers ---------- */
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), 3200); }
function status(msg) { $("status-msg").textContent = msg; }
const fmtSize = (n) => n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB";
const fmtEta = (s) => s == null ? "" : s > 90 ? Math.round(s / 60) + " min left" : s + " s left";
const baseName = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
function copyText(t) { const a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); document.execCommand("copy"); a.remove(); }
const selPaths = () => [...S.sel].filter((p) => S.items.has(p));
const selItems = () => selPaths().map((p) => S.items.get(p));
const isStart = () => !$("start").classList.contains("hidden");

/* ---------- events from Python ---------- */
function setProgress(done, total, eta) {
  $("bar-fill").style.width = (total ? 100 * done / total : 0) + "%";
  $("prog-text").textContent = `${done} / ${total}  ${fmtEta(eta)}`; $("phase").textContent = "Scanning…";
}

/* ---------- rendering ---------- */
function inView(r) {
  if (S.only && !S.only.has(r.path)) return false;
  if (!predOf(S.filter)(r)) return false;
  if (S.filter === "closed" && S.sub === "sure" && r.needs_check) return false;
  if (S.filter === "closed" && S.sub === "check" && !r.needs_check) return false;
  if (S.query) { const q = S.query.toLowerCase(); if (!(r.name.toLowerCase().includes(q) || r.tags.some((t) => t.includes(q)) || (r.names || []).some((n) => n.toLowerCase().includes(q)))) return false; }
  return true;
}
function renderSub() {
  let html = "";
  if (S.filter === "closed") {
    const rs = [...S.items.values()].filter((r) => r.flagged), sure = rs.filter((r) => !r.needs_check).length;
    html = [["all", `All ${rs.length}`], ["sure", `Sure ${sure}`], ["check", `Needs check ${rs.length - sure}`]]
      .map(([k, l]) => `<span class="pill ${S.sub === k ? "on" : ""}" data-sub="${k}">${l}</span>`).join("");
  } else if (S.filter === "dups") html = `<span class="pill" data-act="rej-dups">Reject all but the best frame</span>`;
  if (S.only) html += `<span class="pill on" data-act="clear-only">${esc(S.onlyLabel || "Filtered")} ✕</span>`;
  $("subbar").innerHTML = html;
}
function sortVisible(v) {
  const by = { name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }), blink: (a, b) => b.blink - a.blink,
    size: (a, b) => b.size - a.size, faces: (a, b) => b.people - a.people }[S.sort];
  v.sort(S.filter === "dups" ? (a, b) => (a.dup_group - b.dup_group) || (b.dup_best - a.dup_best) : by);
}
function badgeFor(r) {
  if (r.flagged) return r.needs_check ? ["check", "check"] : ["closed", "closed"];
  if (r.wink) return ["check", "wink"];
  if (r.dup_group !== null) return ["dup", r.dup_best ? "best" : "similar"];
  if (r.unclear) return ["check", r.focus];
  return null;
}
function tileFor(r) {
  let el = S.tiles.get(r.path);
  if (!el) {
    el = document.createElement("div"); el.className = "tile"; el.dataset.path = r.path; el.tabIndex = 0;
    el.innerHTML = `<img alt="" draggable="false"><span class="badge"></span><span class="icons"></span>`;
    S.tiles.set(r.path, el); io.observe(el);
  }
  el.style.setProperty("--ar", (r.w && r.h ? r.w / r.h : 1.5).toFixed(4));
  const b = badgeFor(r), bd = el.querySelector(".badge");
  bd.className = "badge " + (b ? "b-" + b[0] : "hidden"); bd.textContent = b ? b[1] : "";
  el.classList.toggle("sel", S.sel.has(r.path)); el.classList.toggle("focus", S.focus === r.path);
  el.classList.toggle("rej", S.rejected.has(r.path));
  el.querySelector(".icons").innerHTML = (S.rejected.has(r.path) ? ico("x") : "") + (r.protected ? ico("lock") : "") + (r.override === "keep" ? ico("check") : "");
  return el;
}
function renderList() {
  $("list").innerHTML = `<table><thead><tr><th>Name</th><th>Result</th><th>People</th><th>Closed</th><th>Blink</th><th>Focus</th><th>Size</th><th>Tags</th></tr></thead><tbody>` +
    S.visible.map((r) => `<tr data-path="${esc(r.path)}" class="${S.sel.has(r.path) ? "sel" : ""}"><td>${esc(r.name)}</td><td>${r.flagged ? (r.needs_check ? "needs check" : "closed") : r.verdict}</td><td>${r.people}</td><td>${r.n_closed}</td><td>${r.blink}</td><td>${r.focus}</td><td>${fmtSize(r.size)}</td><td>${esc(r.tags.join(", "))}</td></tr>`).join("") + `</tbody></table>`;
}
function refreshSel() {
  S.tiles.forEach((el, p) => { el.classList.toggle("sel", S.sel.has(p)); el.classList.toggle("focus", S.focus === p); });
  if (S.view === "list") $("list").querySelectorAll("tr[data-path]").forEach((tr) => tr.classList.toggle("sel", S.sel.has(tr.dataset.path)));
  $("status-counts").textContent = $("status-counts").textContent.replace(/\d+ selected/, S.sel.size + " selected");
  renderInspector();
}

/* ---------- face overlay & preview ---------- */
/* ---------- inspector ---------- */
function renderInspector() {
  const box = $("inspector"), rs = selItems();
  if (!rs.length) { box.innerHTML = `<div class="placeholder">Select a photo to inspect it.<br>Ctrl+click or Shift+click selects several.</div>`; return; }
  if (rs.length > 1) {
    box.innerHTML = `<div class="insp-name">${rs.length} photos selected</div><div class="kv"><span>Total size</span><b>${fmtSize(rs.reduce((s, r) => s + r.size, 0))}</b></div>
      <div class="btns"><button data-act="keep">Keep</button><button data-act="flag">Flag closed</button></div>
      <div class="btns"><button data-act="protect">Protect</button><button data-act="reject">Reject (X)</button></div>
      <div class="btns"><button data-act="sort-sel">Move to folder…</button><button data-act="delete" class="danger">Delete…</button></div>`;
    return;
  }
  const r = rs[0];
  const faces = r.faces.map((f, i) => `<div class="face"><span>#${i + 1}</span><span class="meter" title="L ${f.left} / R ${f.right}"><i style="width:${Math.round(Math.max(f.left, f.right) * 100)}%"></i></span>
    <span>${f.state}${f.wink ? " · wink" : ""}${f.smiling ? " · smile" : ""}</span>
    ${f.person != null ? `<button class="sm2" data-nameface="${i}" title="Name this person">${esc(f.pname || "Name…")}</button>` : ""}
    <select data-face="${i}" title="Mark this face"><option value="">auto</option><option value="open"${f.override === "open" ? " selected" : ""}>open</option><option value="closed"${f.override === "closed" ? " selected" : ""}>closed</option><option value="ignore"${f.override === "ignore" ? " selected" : ""}>ignore</option></select></div>`).join("") || `<div class="hint">No faces found.</div>`;
  box.innerHTML = `${pvHTML(r, "pv")}<div class="insp-name">${esc(r.name)}</div>
    <div class="tags">${r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div><div class="faces">${faces}${r.animals.map((a, i) => `<div class="face"><span>${esc(a.label)}</span><span class="grow"></span><button class="sm2" data-nameanimal="${i}" title="Name this animal">${esc(a.name || "Name…")}</button></div>`).join("")}</div>
    <div><div class="kv"><span>Size</span><b>${fmtSize(r.size)}</b></div><div class="kv"><span>Dimensions</span><b>${r.w} × ${r.h}</b></div>
    <div class="kv"><span>Focus</span><b>${r.focus}</b></div>${r.dup_why ? `<div class="kv"><span>Burst</span><b>${esc(r.dup_why)}</b></div>` : ""}</div>
    ${r.rescue ? `<button data-act="better">Select the better frame</button>` : ""}${r.dup_group !== null ? `<button data-act="dupgroup">Select the whole duplicate group</button>` : ""}
    <div class="btns"><button data-act="keep">Keep (K)</button><button data-act="flag">Flag (F)</button><button data-act="reset">Reset</button></div>
    <div class="btns"><button data-act="protect">${r.protected ? "Unprotect" : "Protect (P)"}</button><button data-act="reject">${S.rejected.has(r.path) ? "Unreject" : "Reject"} (X)</button></div>
    <div class="btns"><button data-act="review">Review…</button><button data-act="reveal">Show in folder</button></div>
    <div class="btns"><button data-act="sort-sel">Move / copy to folder…</button></div>
    <div class="hint">Space zooms to the face · Tab next face · Enter opens Review</div>`;
  initPV("pv", r);
}

/* ---------- selection ---------- */
function setSel(paths, focus) { S.sel = new Set(paths); S.focus = focus ?? [...S.sel].pop() ?? null; refreshSel(); }
function clickSel(path, e) {
  const idx = (p) => S.visible.findIndex((r) => r.path === p);
  if (e.shiftKey && S.anchor) {
    const a = idx(S.anchor), b = idx(path), [lo, hi] = a < b ? [a, b] : [b, a];
    const range = S.visible.slice(lo, hi + 1).map((r) => r.path);
    setSel(e.ctrlKey ? [...S.sel, ...range] : range, path);
  } else if (e.ctrlKey || e.metaKey) {
    const n = new Set(S.sel); n.has(path) ? n.delete(path) : n.add(path); S.anchor = path; setSel(n, path);
  } else { S.anchor = path; setSel([path], path); }
  const el = S.tiles.get(path); if (el && S.view === "grid") el.scrollIntoView({ block: "nearest" });
}
function moveSel(delta, extend) {
  if (!S.visible.length) return;
  const i = S.visible.findIndex((r) => r.path === S.focus), n = S.visible[Math.min(S.visible.length - 1, Math.max(0, (i < 0 ? 0 : i + delta)))];
  clickSel(n.path, { shiftKey: extend, ctrlKey: false });
}
const cols = () => S.view === "list" ? 1 : Math.max(1, Math.floor($("grid").clientWidth / ((S.cfg.thumb_size || 160) + 10)));

/* ---------- dialogs ---------- */
let closeModal = null;
async function deleteBulk(which) {
  const p = await api.bulk_preview(which);
  if (!p.count) return toast("Nothing to delete" + (p.protected ? ` (${p.protected} protected skipped)` : "."));
  confirmTrash(p.paths, p.protected);
}
function dropItem(p) { S.items.delete(p); S.tiles.delete(p); S.sel.delete(p); S.rejected.delete(p); if (S.focus === p) S.focus = null; }
const SHORTCUTS = `SELECTING
  Click · Ctrl+click · Shift+click      select · toggle · range
  Ctrl+A / Ctrl+D / Ctrl+I              all · none · invert
  Arrows · Shift+arrows                 move · extend
  Home / End                            first / last photo

VIEWING
  Space                                 zoom to face (press again to zoom out)
  Tab                                   next face while zoomed
  Enter / double-click                  Review window (← → move, K keep · F flag · P protect · X reject)
  Ctrl+K                                search & commands

ACTIONS
  Del                                   delete selected (Recycle Bin, shows size saved)
  F2 · Ctrl+C                           rename · copy file(s) to the clipboard
  F5 · Ctrl+F5 · Esc                    scan · full re-scan · stop
  Ctrl+O · Ctrl+E · Ctrl+Shift+S        open folder · CSV · contact sheets
  Ctrl+, · Ctrl+T · F1                  settings · theme · this help
  Right-click                           open · show in folder · mark · protect · move · delete`;
const DETECTION = `1. MediaPipe FaceLandmarker finds faces (whole image + tiles).
2. BlazeFace, an independent detector, confirms each face, rejecting feet, hands and patterns that only look like faces.
3. Each eye is judged separately (blink score + lid gap); BOTH must be closed. One closed eye is a wink and is never flagged.
4. "Looking down": lids still apart + gaze down + head pitch. Kept, not flagged.
5. Focus is judged on the face only: blurry, or misfocused (the background is sharper than the face).
6. Duplicates are grouped by image similarity; the best frame is suggested.
7. Results are saved in a local database so re-scans are fast.

Wrong call? Right-click, Mark as…, or Protect a deliberate pose so bulk sort and delete skip it.`;
function paletteDialog() {
  const cmds = []; MENUS.forEach(([m, items]) => items.forEach((it) => { if (it !== "-" && it.a) cmds.push({ name: `${m}: ${it.l}`, a: it.a }); }));
  dialog({ title: "Search & commands", sub: "Run any menu command", icon: "search", body: `<input type="text" id="pal" placeholder="Type a command…" style="width:100%"><div id="pal-list" style="margin-top:8px;max-height:46vh;overflow:auto"></div>`, buttons: [{ label: "Close", onClick: (x) => x() }],
    onMount: (b, close) => {
      const draw = () => { const q = $("pal").value.toLowerCase(); const hits = cmds.filter((c) => c.name.toLowerCase().includes(q)).slice(0, 14);
        $("pal-list").innerHTML = hits.map((c, i) => `<div class="mi" data-i="${i}">${esc(c.name)}</div>`).join(""); $("pal-list").hits = hits; };
      $("pal").oninput = draw; draw(); $("pal").focus();
      $("pal").onkeydown = (e) => { if (e.key === "Enter") { const h = $("pal-list").hits[0]; if (h) { close(); run(h.a); } } };
      $("pal-list").onclick = (e) => { const el = e.target.closest(".mi"); if (el) { const h = $("pal-list").hits[el.dataset.i]; close(); run(h.a); } }; } });
}

/* ---------- dialogs (shared chrome) ---------- */
/* Icons: Lucide (ISC), bundled offline in vendor/lucide.js. Old short names are aliased to Lucide names. */
const ICON_ALIAS = { sliders: "sliders-horizontal", folder: "folder-open", trash: "trash-2", key: "keyboard", edit: "pencil", check: "circle-check",
  sheet: "layout-grid", sort: "arrow-down-to-line", scan: "scan-line" };
const ico = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${(window.LUCIDE || {})[ICON_ALIAS[n] || n] || ""}</svg>`;
function hydrateIcons(root = document) { root.querySelectorAll("[data-i]").forEach((el) => { el.innerHTML = ico(el.dataset.i); }); }
function dialog({ title, sub = "", icon = "info", tone = "", body, buttons = [], cls = "", onMount }) {
  const m = $("modal");
  m.innerHTML = `<div class="dlg ${cls}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dhead tone-${tone}"><div class="ico">${ico(icon)}</div>
    <div class="tx"><h2>${esc(title)}</h2>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div><button class="x" aria-label="Close">${ico("x")}</button></div>
    <div class="dbody">${body}</div><div class="dfoot"></div></div>`;
  const foot = m.querySelector(".dfoot"), close = () => { m.classList.add("hidden"); m.innerHTML = ""; m.onkeydown = null; closeModal = null; S.review = null; };
  buttons.filter((b) => b.left).forEach((b) => foot.appendChild(mkBtn(b, close)));
  foot.appendChild(Object.assign(document.createElement("span"), { className: "spacer" }));
  buttons.filter((b) => !b.left).forEach((b) => foot.appendChild(mkBtn(b, close)));
  m.querySelector(".x").onclick = close;
  m.onkeydown = (e) => { if (e.key === "Enter" && !/TEXTAREA|BUTTON|SELECT/.test(e.target.tagName)) { const p = foot.querySelector(".primary,.danger"); if (p) { e.preventDefault(); p.click(); } } };
  m.classList.remove("hidden"); closeModal = close;
  if (onMount) onMount(m.querySelector(".dbody"), close);
  const t = m.querySelector(".dbody input[type=text]"); if (t) { t.focus(); if (t.select) t.select(); }
  return close;
}
function mkBtn(b, close) { const el = document.createElement("button"); el.textContent = b.label; if (b.cls) el.className = b.cls; el.onclick = () => b.onClick(close); return el; }
const sel1 = (v, cur) => v === cur ? " selected" : "";
const sw = (k, on) => `<label class="sw"><input type="checkbox" data-k="${k}" ${on ? "checked" : ""}><i></i></label>`;
const segr = (k, opts, cur) => `<div class="segr" data-seg="${k}" data-val="${cur}">${opts.map(([v, l]) => `<button type="button" data-v="${v}" class="${String(v) === String(cur) ? "on" : ""}">${l}</button>`).join("")}</div>`;
const srow = (t, d, c) => `<div class="srow"><div class="lab"><b>${t}</b><span>${d}</span></div><div class="ctl">${c}</div></div>`;
function segClick(e) { const s = e.target.closest(".segr button"); if (s) { const g = s.parentElement; g.dataset.val = s.dataset.v; g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === s)); } }

function confirmTrash(paths, protectedSkipped = 0) {
  const items = paths.map((p) => S.items.get(p)).filter(Boolean), prot = items.filter((r) => r.protected);
  const bytes = items.reduce((s, r) => s + r.size, 0);
  const go = async (x, inc) => { x(); const res = await api.trash(paths, inc); res.gone.forEach(dropItem); render(); renderInspector();
    toast(`${res.deleted} file(s) moved to the Recycle Bin` + (res.skipped ? ` · ${res.skipped} protected skipped` : "") + (res.failed.length ? ` · ${res.failed.length} failed` : "")); };
  const buttons = [{ label: "Cancel", onClick: (x) => x() }];
  if (prot.length) buttons.push({ label: `Delete all ${paths.length}`, onClick: (x) => go(x, true) }, { label: "Skip protected", cls: "danger", onClick: (x) => go(x, false) });
  else buttons.push({ label: "Move to Recycle Bin", cls: "danger", onClick: (x) => go(x, false) });
  dialog({ title: "Delete photos?", sub: "They go to the Recycle Bin and can be restored", icon: "trash", tone: "danger",
    body: `<div class="stat"><div><b>${paths.length}</b><span>photos</span></div><div><b>${fmtSize(bytes)}</b><span>space freed</span></div>${prot.length ? `<div><b>${prot.length}</b><span>protected</span></div>` : ""}</div>
      <div class="note">Matching RAW files with the same name are removed too.</div>${prot.length ? `<div class="note warn">${prot.length} selected photo(s) are protected. Choose “Skip protected” to keep them.</div>` : ""}${protectedSkipped ? `<div class="hint" style="margin-top:8px">${protectedSkipped} protected photo(s) were already left out.</div>` : ""}`, buttons });
}
function sheetsDialog() {
  if (!S.items.size) return toast("Scan a folder first.");
  const c = S.cfg;
  dialog({ title: "Export contact sheets", sub: "High-resolution JPG pages (q95)", icon: "sheet", body: `
    <div class="fgrid"><label>Include</label><select id="sh-only"><option value="flagged">Eyes-closed photos</option><option value="attention">Needs attention</option><option value="unclear">Blurry / misfocused</option><option value="extras">Duplicate extras</option><option value="all">All photos</option></select>
    <label>Content</label>${segr("sh-mode", [["photos", "Whole photos"], ["faces", "Faces"]], c.sheet_mode || "photos")}
    <label>Page ratio</label>${segr("sh-ratio", ["3:2", "4:3", "16:9", "5:4"].map((v) => [v, v]), c.sheet_ratio || "3:2")}
    <label>Long side</label>${segr("sh-long", [[4000, "4000 px"], [6000, "6000 px"], [8000, "8000 px"]], c.sheet_long || 6000)}</div>
    <div class="note">Pages are saved to a “Contact Sheets” folder inside the scanned folder.</div>`,
    buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: "Export", cls: "primary", onClick: async (x) => {
      const g = (id) => document.querySelector(`[data-seg=${id}]`).dataset.val;
      const r = await api.make_sheets({ only: $("sh-only").value, mode: g("sh-mode"), ratio: g("sh-ratio"), long: parseInt(g("sh-long"), 10) });
      x(); if (r.ok) status("Rendering contact sheets…"); else toast(r.error); } }],
    onMount: (b) => b.addEventListener("click", segClick) });
}
function shortcutsDialog() {
  const html = SHORTCUTS.split(/\n\s*\n/).map((sec) => { const [head, ...lines] = sec.trim().split("\n");
    return `<h4>${esc(head.replace(/\s*\(.*\)/, ""))}</h4>` + lines.map((l) => { const m = l.match(/^\s*(.+?)\s{2,}(.+)$/); if (!m) return "";
      return `<div>${m[1].split(" · ").map((k) => `<kbd>${esc(k.trim())}</kbd>`).join("")}</div><div class="muted">${esc(m[2])}</div>`; }).join(""); }).join("");
  dialog({ title: "Keyboard shortcuts", sub: "Work through a shoot without leaving the keys", icon: "key", cls: "wide", body: `<div class="kbd">${html}</div>`, buttons: [{ label: "Close", cls: "primary", onClick: (x) => x() }] });
}
function detectionDialog() {
  const lines = DETECTION.split("\n"), steps = lines.filter((l) => /^\d\./.test(l)).map((l) => l.replace(/^\d\.\s*/, "")), tail = lines.filter((l) => l && !/^\d\./.test(l)).join(" ");
  dialog({ title: "How detection works", sub: "Everything runs offline on this PC", icon: "eye", cls: "wide", body: `<ol class="steps">${steps.map((s) => `<li><span>${esc(s)}</span></li>`).join("")}</ol><div class="note">${esc(tail)}</div>`, buttons: [{ label: "Close", cls: "primary", onClick: (x) => x() }] });
}
function aboutDialog() {
  const a = S.about;
  dialog({ title: "About Keeper", icon: "info", body: `<div class="about" style="text-align:center"><img data-logo src="assets/icon-dark.png" alt="" width="92"><h3 style="margin:6px 0 0;font-size:22px;font-weight:600">${esc(a.name)}</h3><span class="ver">Version ${esc(a.version)}</span>
    <p>Find, sort and review photos fast.<br>Everything runs offline on this PC.</p><p>© ${a.year} ${esc(a.author)}<br><b>${esc(a.brand)}</b> · <a id="site" style="color:var(--sage-d);cursor:pointer;text-decoration:underline">www.deev.in</a><br><span class="muted">${esc(a.rights)}</span></p>
    <p class="hint">Built with MediaPipe, ONNX Runtime, Pillow, NumPy and pywebview. Icons by Lucide (ISC licence).</p></div>`,
    buttons: [{ label: "Close", cls: "primary", onClick: (x) => x() }], onMount: (b) => { b.querySelector("#site").onclick = () => api.open_url(a.website); } });
}
async function checkUpdate(manual) {
  if (manual) toast("Checking for updates…");
  const u = await api.check_update();
  if (!u.ok) return manual ? toast("Couldn't reach the update server. Are you online?") : null;
  if (!u.newer) return manual ? toast(`You're up to date (version ${u.current}).`) : null;
  dialog({ title: "Update available", icon: "info", body: `<p>Keeper <b>${esc(u.latest)}</b> is available. You have ${esc(u.current)}.</p>${u.notes ? `<div class="note">${esc(u.notes)}</div>` : ""}<p class="hint">Download the installer and run it over your current install. Your settings and database are kept.</p>`,
    buttons: [{ label: "Later", onClick: (x) => x() }, { label: "Download", cls: "primary", onClick: (x) => { api.open_url(u.url); x(); } }] });
}
function propsDialog(r) {
  const row = (k, v) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`;
  dialog({ title: "Properties", sub: r.name, icon: "list", body: `<table class="kvt">${row("File", r.name)}${row("Folder", r.folder)}${row("Size", fmtSize(r.size))}${row("Dimensions", `${r.w} × ${r.h}`)}${row("Result", r.verdict)}${row("People", r.people)}${row("Focus", r.focus)}${row("Tags", r.tags.join(", ") || "-")}</table>`,
    buttons: [{ label: "Show in folder", left: true, onClick: () => api.reveal(r.path) }, { label: "Close", cls: "primary", onClick: (x) => x() }] });
}
function renameDialog(r) {
  dialog({ title: "Rename photo", sub: r.name, icon: "edit", body: `<div class="fgrid"><label>New name</label><input type="text" id="rn" value="${esc(r.name)}"></div>`,
    buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: "Rename", cls: "primary", onClick: async (x) => {
      const res = await api.rename(r.path, $("rn").value.trim()); if (!res.ok) return toast(res.error);
      dropItem(res.old); S.items.set(res.item.path, res.item); setSel([res.item.path]); x(); render(); status("Renamed to " + res.item.name); } }] });
}

/* ---------- viewer: wheel zoom, drag pan, rotate, face zoom ---------- */
const VW = new WeakMap();
const vstate = (pv) => { let v = VW.get(pv); if (!v) { v = { s: 1, tx: 0, ty: 0, rot: 0 }; VW.set(pv, v); } return v; };
function zoomAt(pv, factor, px, py) {
  const v = vstate(pv), W = pv.clientWidth, H = pv.clientHeight, s2 = Math.max(1, Math.min(12, v.s * factor));
  const cx = (px ?? W / 2) - W / 2, cy = (py ?? H / 2) - H / 2;
  v.tx = cx - s2 * (cx - v.tx) / v.s; v.ty = cy - s2 * (cy - v.ty) / v.s; v.s = s2;
  if (s2 === 1) { v.tx = 0; v.ty = 0; }
  applyView(pv);
}
function zoomPV(pv, r, mode) {
  if (!pv || !r) return;
  const v = vstate(pv), faces = r.faces.filter((f) => f.state !== "open"), list = faces.length ? faces : r.faces;
  if (mode !== "auto" && mode !== true && v.s > 1.01) { v.s = 1; v.tx = v.ty = 0; return applyView(pv); }
  if (!list.length) { v.s = 1; v.tx = v.ty = 0; return applyView(pv); }
  if (mode === true && v.s > 1.01) S.zoomFace = (S.zoomFace + 1) % list.length; else S.zoomFace = 0;
  const f = list[S.zoomFace % list.length]; v.rot = 0;
  const { W, H } = pvSize(pv, v), fw = (f.box[2] - f.box[0]) * W, fh = (f.box[3] - f.box[1]) * H;
  v.s = Math.max(1.2, Math.min(8, (S.cfg.face_zoom_fill || 0.6) * Math.min(W, H) / Math.max(fw, fh, 1)));
  v.tx = -v.s * (((f.box[0] + f.box[2]) / 2 - 0.5) * W); v.ty = -v.s * (((f.box[1] + f.box[3]) / 2 - 0.5) * H);
  applyView(pv);
}
let drag = null;
document.addEventListener("wheel", (e) => {
  const pv = e.target.closest && e.target.closest(".pv");
  if (pv) { e.preventDefault(); const b = pv.getBoundingClientRect(); zoomAt(pv, e.deltaY < 0 ? 1.25 : 0.8, e.clientX - b.left, e.clientY - b.top); }
  else if (e.ctrlKey && e.target.closest && e.target.closest(".center")) { e.preventDefault(); setThumb(Math.max(110, Math.min(280, (S.cfg.thumb_size || 160) + (e.deltaY < 0 ? 10 : -10)))); }
}, { passive: false });
document.addEventListener("mousedown", (e) => {
  const pv = e.target.closest && e.target.closest(".pv"); if (!pv || e.button !== 0 || e.target.closest(".vtools")) return;
  const v = vstate(pv); drag = { pv, x: e.clientX, y: e.clientY, tx: v.tx, ty: v.ty, moved: false };
});
document.addEventListener("mousemove", (e) => {
  if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  const v = vstate(drag.pv); if (drag.moved && v.s > 1) { v.tx = drag.tx + dx; v.ty = drag.ty + dy; applyView(drag.pv); }
});
document.addEventListener("mouseup", () => { if (drag) { S.dragged = drag.moved; setTimeout(() => { S.dragged = false; }, 0); drag = null; } });
window.addEventListener("resize", () => document.querySelectorAll(".pv").forEach(applyView));

/* ---------- rubber-band selection ---------- */
let band = null;
function bandStart(e) {
  if (e.button !== 0 || (e.target.id !== "grid" && e.target.id !== "list")) return;
  band = { x: e.clientX, y: e.clientY, base: e.ctrlKey ? new Set(S.sel) : new Set(), el: null };
}
document.addEventListener("mousemove", (e) => {
  if (!band) return;
  if (!band.el && Math.abs(e.clientX - band.x) + Math.abs(e.clientY - band.y) > 5) { band.el = document.createElement("div"); band.el.className = "band"; document.body.appendChild(band.el); }
  if (!band.el) return;
  const x0 = Math.min(band.x, e.clientX), y0 = Math.min(band.y, e.clientY), x1 = Math.max(band.x, e.clientX), y1 = Math.max(band.y, e.clientY);
  Object.assign(band.el.style, { left: x0 + "px", top: y0 + "px", width: x1 - x0 + "px", height: y1 - y0 + "px" });
  const hit = new Set(band.base);
  S.visible.forEach((r) => { const el = S.view === "grid" ? S.tiles.get(r.path) : null; if (!el) return; const b = el.getBoundingClientRect(); if (b.right > x0 && b.left < x1 && b.bottom > y0 && b.top < y1) hit.add(r.path); });
  S.sel = hit; refreshSelFast();
});
document.addEventListener("mouseup", () => { if (band) { if (band.el) { band.el.remove(); S.banded = true; setTimeout(() => { S.banded = false; }, 0); refreshSel(); } band = null; } });
function refreshSelFast() { S.tiles.forEach((el, p) => el.classList.toggle("sel", S.sel.has(p))); }

/* ---------- people (face-recognition rail) ---------- */
/* ---------- settings (full parity with EyeScan 1) ---------- */
/* ---------- sort (bulk group or exactly the selected photos) ---------- */
function sortDialog(which = "closed", paths = null) {
  const groups = [["closed", "Eyes closed", "Photos flagged with closed eyes"], ["unclear", "Blurry", "Blurry or misfocused faces"], ["dups", "Duplicates", "Extra copies (best frame stays)"]];
  let cur = which;
  const items = paths ? paths.map((p) => S.items.get(p)).filter(Boolean) : [];
  const head = paths ? `<div class="stat"><div><b>${items.length}</b><span>selected photo(s)</span></div><div><b>${fmtSize(items.reduce((s, r) => s + r.size, 0))}</b><span>total size</span></div></div>`
    : `<div class="opt-cards" id="sd-cards">${groups.map(([k, t, d]) => `<div class="opt ${k === cur ? "on" : ""}" data-w="${k}"><b>${t}</b><span>${d}</span><div class="hint" id="sdc-${k}">…</div></div>`).join("")}</div>`;
  dialog({ title: paths ? "Move or copy to a folder" : "Sort photos into a folder", sub: "Protected photos are always skipped", icon: "sort", body: `${head}
    <div class="fgrid" style="margin-top:16px"><label>Action</label>${segr("sd-mode", [["copy", "Copy"], ["move", "Move"]], S.cfg.mode === "move" ? "move" : "copy")}<label>Folder name</label><input type="text" id="sd-name" value="${esc(paths ? S.cfg.out_name : "")}"></div>
    <div class="note" id="sd-prev"></div><div class="note">Moves can be undone from <b>Scan &rarr; Undo last sort</b>. Copies never touch your originals.</div>`,
    buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: "Sort photos", cls: "primary", onClick: async (x) => {
      const mode = document.querySelector("[data-seg=sd-mode]").dataset.val, r = await api.sort_bulk(cur, mode, $("sd-name").value.trim(), paths); x();
      if (!r.ok) return toast(r.error);
      r.gone.forEach(dropItem); render(); renderInspector();
      toast(`${r.moved} ${mode === "move" ? "moved" : "copied"} to ${baseName(r.folder)}` + (r.failed ? ` · ${r.failed} failed` : ""));
    } }],
    onMount: (b) => {
      const upd = async () => { if (paths) { const prot = items.filter((r) => r.protected).length; $("sd-prev").textContent = `${items.length - prot} photo(s) will be ${"sorted"}` + (prot ? ` · ${prot} protected skipped` : ""); return; }
        const p = await api.bulk_preview(cur); if (!$("sd-name")) return; $("sd-name").value = p.folder;
        $("sd-prev").textContent = `${p.count} photo(s) · ${fmtSize(p.bytes)} will be sorted` + (p.protected ? ` · ${p.protected} protected skipped` : ""); };
      if (!paths) groups.forEach(async ([k]) => { const p = await api.bulk_preview(k); const el = $("sdc-" + k); if (el) el.textContent = `${p.count} photos · ${fmtSize(p.bytes)}`; });
      b.addEventListener("click", (e) => {
        const o = e.target.closest(".opt"); if (o) { cur = o.dataset.w; b.querySelectorAll(".opt").forEach((x) => x.classList.toggle("on", x === o)); upd(); }
        segClick(e);
      }); upd();
    } });
}

/* ---------- menus (File > Recent folders is filled when opened) ---------- */
function menuHTML(items) {
  const expanded = items.flatMap((it) => it && it.recent ? (S.recent.length ? S.recent.slice(0, 6).map((r, i) => ({ l: baseName(r.folder), a: "recent:" + i, k: "" })) : [{ l: "(no recent folders)", a: "", dis: true }]) : [it]);
  return expanded.map((it) => it === "-" ? `<div class="msep"></div>` : `<div class="mi ${it.dis ? "dis" : ""}" data-a="${esc(it.a)}">${esc(it.l)}${it.k ? `<span class="ac">${it.k}</span>` : ""}</div>`).join("");
}
function buildMenus() {
  const host = $("menus");
  MENUS.forEach(([name, items]) => {
    const m = document.createElement("div"); m.className = "menu"; m._items = items;
    m.innerHTML = `<div class="mt">${name}</div><div class="drop-menu"></div>`; host.appendChild(m);
  });
  const open = (m) => { host.querySelectorAll(".menu").forEach((x) => x.classList.remove("open")); m.querySelector(".drop-menu").innerHTML = menuHTML(m._items); m.classList.add("open"); };
  host.onclick = (e) => {
    const it = e.target.closest(".mi"), mt = e.target.closest(".mt");
    host.querySelectorAll(".menu").forEach((m) => m.classList.remove("open"));
    if (it && it.dataset.a) run(it.dataset.a); else if (mt) open(mt.parentElement);
  };
  host.onmouseover = (e) => { const m = e.target.closest(".menu"); if (m && host.querySelector(".menu.open") && !m.classList.contains("open")) open(m); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#menus")) host.querySelectorAll(".menu").forEach((m) => m.classList.remove("open")); if (!e.target.closest("#ctx")) $("ctx").classList.add("hidden"); });
}

/* ---------- events from Python ---------- */
window.onEvent = (e) => {
  if (e.kind === "start") {
    if (!e.partial && S.folder) saveView();
    const view = e.partial ? null : takeView(e.folder);
    if (!e.partial) tabEnter(e.folder);
    if (!e.partial) { S.items.clear(); S.tiles.clear(); S.rejected.clear(); S.sel.clear(); S.focus = null; S.only = null; S.people = []; S.geo = {}; }
    if (!e.partial && (S.cfg.after_scan_tab || "closed") !== "off") { S.filter = "all"; S.sub = "all"; }
    if (view) {
      Object.assign(S, { filter: view.filter, sub: view.sub, query: view.query, sort: view.sort, rejected: new Set(view.rej), restoreView: view });
      $("search").value = view.query; $("sort").value = view.sort;
    }
    S.stopped = null; showNotice(); S.hiddenPeople = new Set(); resetStop();
    S.folder = e.folder; $("folder-name").textContent = baseName(e.folder);
    $("start").classList.add("hidden"); $("review").classList.remove("hidden");
    $("progress").classList.remove("hidden"); setProgress(0, e.total, null); S.scanning = true;
    status("Scanning " + e.folder); renderInspector(); scheduleRender(); renderTabs();
  } else if (e.kind === "results") {
    e.items.forEach((r) => S.items.set(r.path, r)); setProgress(e.done, e.total, e.eta); scheduleRender();
  } else if (e.kind === "final") {
    const n = [...S.items.values()].filter((r) => r.flagged).length;
    $("phase").textContent = e.stopped ? "Stopped early" : "Scan complete";
    status(`${e.stopped ? "Stopped" : "Done"}: ${S.items.size} photos, ${n} with closed eyes`);
    if (e.stopped) S.stopped = { done: e.done, total: e.total };
    const tab = S.cfg.after_scan_tab || "closed";
    if (S.restoreView) restoreTail(S.restoreView);
    else if (tab !== "off" && !S.only) { S.filter = tab; S.sub = "all"; }
    showNotice(); scheduleRender(); renderInspector();
    if (S.cfg.people && S.info.people_ok) loadPeople(true);
    api.recent().then((r) => { S.recent = r; });
  } else if (e.kind === "phase") { $("phase").textContent = e.text; if (S.scanning && !S.items.size) scheduleRender();
  } else if (e.kind === "idle") { S.scanning = false; $("progress").classList.add("hidden"); resetStop(); scheduleRender(); renderTabs();
  } else if (e.kind === "error") { toast(e.message); status(e.message);
  } else if (e.kind === "sheet_progress") { status(`Contact sheets: ${e.done} / ${e.total}`);
  } else if (e.kind === "sheet_done") {
    status(`${e.pages.length} contact sheet(s) saved to ${e.folder}`);
    dialog({ title: "Contact sheets ready", icon: "check", body: `<p>${e.pages.length} page(s) saved in<br><b>${esc(e.folder)}</b></p>`,
      buttons: [{ label: "Show in folder", onClick: (c) => { api.reveal(e.pages[0]); c(); } }, { label: "Close", cls: "primary", onClick: (c) => c() }] });
  }
};
function showNotice() {
  const n = $("notice"), st = S.stopped;
  if (!st) { n.classList.add("hidden"); return; }
  n.innerHTML = `<span class="nico">${ico("circle-pause")}</span><span><b>Scan stopped</b> at ${st.done} of ${st.total} photos. Results so far are kept.</span>
    <button class="primary sm2" data-act="scan">Resume scan</button><button class="sm2" data-act="rescan_all">Rescan everything</button><button class="ghost sm2" data-act="dismiss-notice">Dismiss</button>`;
  n.classList.remove("hidden");
}

/* ---------- rendering ---------- */
function scheduleRender() { if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; render(); }, S.scanning ? 450 : 120); }
function buildRail() {
  $("rail").innerHTML = FILTERS.map(([g, list]) => `<h4>${g}</h4>` + list.map(([k, label, col, , icon]) =>
    `<div class="item" data-k="${k}"><span class="ri" style="color:${col}">${ico(icon)}</span>${label}<span class="n"></span></div>`).join("")).join("") +
    `<h4>Recognised</h4><div class="item" data-act="people"><span class="ri" style="color:var(--lav-d)">${ico("scan-face")}</span>People…</div><div id="rail-people"></div>` +
    `<div class="sens">Sensitivity <b id="sens-val"></b><input type="range" id="sens" min="20" max="80"></div>`;
  $("rail").dataset.built = "1";
}
function renderRail() {
  if (!$("rail").dataset.built) buildRail();
  const counts = {}; ALLF.forEach((f) => { counts[f[0]] = 0; });
  S.items.forEach((r) => { ALLF.forEach((f) => { if (f[3](r)) counts[f[0]]++; }); });
  $("rail").querySelectorAll(".item[data-k]").forEach((it) => { it.classList.toggle("on", it.dataset.k === S.filter); it.querySelector(".n").textContent = counts[it.dataset.k]; });
  const sl = $("sens"), pct = Math.round((S.cfg.threshold || 0.5) * 100);
  if (document.activeElement !== sl) sl.value = pct;
  $("sens-val").textContent = pct + "%";
  drawPeopleRail();
}
function render() {
  renderRail(); renderSub();
  S.visible = [...S.items.values()].filter(inView); sortVisible(S.visible);
  const list = S.view === "list";
  $("grid").classList.toggle("hidden", list); $("list").classList.toggle("hidden", !list);
  $("grid").className = "grid r-" + (S.cfg.grid_ratio || "original").replace(":", "") + (list ? " hidden" : "");
  if (list) renderList(); else {
    const els = S.visible.map(tileFor), prev = S.gridPaths || [], paths = S.visible.map((r) => r.path);
    const samePrefix = prev.length <= paths.length && prev.every((p, i) => p === paths[i]);
    if (samePrefix && prev.length === paths.length) { /* nothing moved: states already refreshed by tileFor */ }
    else if (samePrefix && $("grid").children.length === prev.length) $("grid").append(...els.slice(prev.length));
    else $("grid").replaceChildren(...els);
    S.gridPaths = paths;
  }
  const loading = S.scanning && !S.items.size;
  $("empty").innerHTML = loading ? `<div class="spin"></div>&nbsp; ${esc($("phase").textContent || "Loading face models…")}` : "Nothing in this view.";
  $("empty").classList.toggle("hidden", S.visible.length > 0 && !loading);
  const all = [...S.items.values()];
  $("status-counts").textContent = `${all.length} photos · ${all.filter((r) => r.flagged).length} eyes closed · ${S.sel.size} selected`;
  updateActionBar();
}
function imgUrl(path, s) { return S.imgBase ? `${S.imgBase}p=${encodeURIComponent(path)}&s=${s}` : null; }
const io = new IntersectionObserver((es) => { for (const en of es) if (en.isIntersecting) { io.unobserve(en.target); loadThumb(en.target); } }, { rootMargin: "700px" });
function loadThumb(el) {
  const im = el.querySelector("img"), u = imgUrl(el.dataset.path, 360);
  const fallback = () => api.thumb(el.dataset.path).then((d) => { if (d) im.src = d; });
  if (u) { im.onerror = () => { im.onerror = null; fallback(); }; im.src = u; } else fallback();
}

/* ---------- face overlay & preview viewer ---------- */
const PART_COL = { outline: "#8FD48A", forehead: "#ECD489", jaw: "#F2E04A", chin: "#8FB8CA", cheeks: "#E88D78", brows: "#E8E8E8", nose: "#7FD6F0", lips: "#E8605A", eyes: "#C4B5D4" };
async function ensureGeo(r) {
  S.geo = S.geo || {}; if (!S.geo[r.path]) S.geo[r.path] = await api.geometry(r.path); return S.geo[r.path];
}
function overlayHTML(r) {
  if (S.overlay === "off") return "";
  const labels = S.cfg.box_labels !== false;
  const box = (b, cls, txt) => `<div class="fb ${cls}" style="left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%">${labels && txt ? `<em>${esc(txt)}</em>` : ""}</div>`;
  const faceLabel = (f, i) => { const nm = f.pname ? ` ${f.pname}` : ""; return f.state === "closed" ? `#${i + 1} ${f.wink ? "wink" : "closed"}${nm}` : f.state === "down" ? `#${i + 1} down${nm}` : f.state === "ignored" ? `#${i + 1} ignored` : (f.pname || ""); };
  const boxes = r.faces.map((f, i) => box(f.box, f.state, faceLabel(f, i))).join("") + r.animals.map((a) => box(a.box, "animal", a.name ? `${a.label}: ${a.name}` : a.label)).join("");
  if (S.overlay === "box") return boxes;
  const g = (S.geo || {})[r.path]; if (!g) return boxes;
  const bc = S.cfg.box_colors || {}, col = (st) => bc[st] || (st === "closed" ? "#E88D78" : st === "down" ? "#ECD489" : "#97B881");
  const poly = (pts) => pts.map((p) => p.join(",")).join(" "), parts = new Set(S.cfg.overlay_parts || Object.keys(PART_COL));
  if (S.overlay === "mesh") return `<svg class="ovl" viewBox="0 0 1 1" preserveAspectRatio="none">` + g.map((gf, i) => { const c = col(r.faces[i].state);
    return `<polygon points="${poly(gf.outline)}" fill="none" stroke="${c}" vector-effect="non-scaling-stroke"/>` + gf.eyes.map((e) => `<polygon points="${poly(e)}" fill="none" stroke="${c}" vector-effect="non-scaling-stroke"/>`).join("") + gf.iris.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="0.004" fill="${c}"/>`).join(""); }).join("") + `</svg>` + boxes;
  const fill = (pts, c, a = 0.2) => `<polygon points="${poly(pts)}" fill="${c}" fill-opacity="${a}" stroke="${c}" vector-effect="non-scaling-stroke"/>`;
  const line = (pts, c, close) => `<polyline points="${poly(close ? pts.concat([pts[0]]) : pts)}" fill="none" stroke="${c}" vector-effect="non-scaling-stroke"/>`;
  return `<svg class="ovl" viewBox="0 0 1 1" preserveAspectRatio="none">` + g.map((gf, i) => { const R = gf.regions || {}, c = PART_COL; let o = "";
    if (parts.has("outline") && R.oval_full) o += line(R.oval_full, c.outline, true);
    if (parts.has("forehead") && R.forehead) o += fill(R.forehead, c.forehead, 0.14);
    if (parts.has("jaw") && R.jaw) o += line(R.jaw, c.jaw);
    if (parts.has("chin") && R.chin) o += fill(R.chin, c.chin, 0.16);
    if (parts.has("cheeks")) { if (R.cheek_a) o += fill(R.cheek_a, c.cheeks, 0.16); if (R.cheek_b) o += fill(R.cheek_b, c.cheeks, 0.16); }
    if (parts.has("brows")) { if (R.brow_a) o += line(R.brow_a, c.brows, true); if (R.brow_b) o += line(R.brow_b, c.brows, true); }
    if (parts.has("nose")) { if (R.nose_bridge) o += line(R.nose_bridge, c.nose); if (R.nose_base) o += line(R.nose_base, c.nose); }
    if (parts.has("lips") && R.lips) o += fill(R.lips, c.lips, 0.2);
    if (parts.has("eyes")) { o += gf.eyes.map((e) => line(e, col(r.faces[i].state), true)).join("") + gf.iris.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="0.004" fill="${col(r.faces[i].state)}"/>`).join(""); }
    return o; }).join("") + `</svg>` + boxes;
}
function pvHTML(r, id, fit) {
  const ar = r.w && r.h ? (r.w / r.h) : 1.5;
  return `<div class="pv" id="${id}" data-ar="${ar}" data-path="${esc(r.path)}" ${fit ? 'data-fit="1"' : ""}><div class="in"><img alt=""><div class="ov">${overlayHTML(r)}</div></div>
    <div class="vtools"><button data-vt="rl" title="Rotate left">${ico("rotate-ccw")}</button><button data-vt="rr" title="Rotate right">${ico("rotate-cw")}</button><button data-vt="out" title="Zoom out">${ico("zoom-out")}</button><button data-vt="in" title="Zoom in">${ico("zoom-in")}</button><button data-vt="100" title="Actual size (100%)">1:1</button><button data-vt="fit" title="Fit">${ico("maximize")}</button></div></div>`;
}
function pvSize(pv, v) {
  const ar0 = parseFloat(pv.dataset.ar) || 1.5, odd = v.rot % 180 !== 0, ar = odd ? 1 / ar0 : ar0;
  let W = pv.parentElement.clientWidth;
  if (pv.dataset.fit) { const par = pv.parentElement; W = Math.min(par.clientWidth, par.clientHeight * ar); pv.style.width = W + "px"; }
  else W = pv.clientWidth || W;
  return { W, H: W / ar, odd };
}
function applyView(pv) {
  if (!pv) return;
  const v = vstate(pv), { W, H, odd } = pvSize(pv, v), inn = pv.querySelector(".in");
  const mx = (v.s - 1) * W / 2, my = (v.s - 1) * H / 2;
  v.tx = Math.max(-mx, Math.min(mx, v.tx)); v.ty = Math.max(-my, Math.min(my, v.ty));
  pv.style.height = H + "px";
  const tf = `translate(${v.tx}px,${v.ty}px) scale(${v.s}) rotate(${v.rot}deg)`;
  inn.style.cssText = odd ? `position:absolute;width:${H}px;height:${W}px;left:${(W - H) / 2}px;top:${(H - W) / 2}px;transform:${tf}` : `position:absolute;left:0;top:0;width:${W}px;height:${H}px;transform:${tf}`;
  pv.classList.toggle("zoom", v.s > 1.01);
  /* boxes and labels live inside the scaled layer: counter-scale them so zoomed views get thin lines and small text */
  const bw = S.cfg.box_thickness || 3, bl = S.cfg.box_label_size || 13, k = Math.sqrt(v.s);
  pv.style.setProperty("--bwe", Math.max(1, bw / k) / v.s + "px");
  pv.style.setProperty("--ble", Math.max(8, bl * 0.8 / k) / v.s + "px");
  pv.style.setProperty("--svw", Math.max(1, bw * 0.6 / k) + "px");
  clearTimeout(pv._res); pv._res = setTimeout(() => upgradeRes(pv), 200);
}
function upgradeRes(pv) {   /* zoomed in beyond the loaded preview: fetch a sharper one */
  if (!pv.isConnected) return;
  const r = S.items.get(pv.dataset.path), v = vstate(pv); if (!r || !S.imgBase) return;
  const need = pvSize(pv, v).W * v.s * (window.devicePixelRatio || 1), cur = +(pv.dataset.res || 2000);
  const want = need <= 2000 ? 2000 : need <= 3600 ? 3600 : 0, rank = (x) => x === 0 ? 9e9 : x;
  if (rank(want) > rank(cur)) { pv.dataset.res = want; const im = pv.querySelector("img"); const n = new Image(); n.onload = () => { im.src = n.src; }; n.src = imgUrl(r.path, want); }
}
async function initPV(id, r) {
  const pv = $(id); if (!pv) return;
  applyView(pv);
  const needGeo = S.overlay === "mesh" || S.overlay === "detail";
  const img = pv.querySelector("img");
  img.onload = () => { applyView(pv); if (S.cfg.auto_face_zoom && id === "pv") zoomPV(pv, r, "auto"); };
  const u = imgUrl(r.path, 2000);
  if (u) img.src = u; else api.preview(r.path).then((d) => { if (d) img.src = d; });
  if (needGeo) { await ensureGeo(r); const ov = pv.querySelector(".ov"); if (ov && pv.dataset.path === r.path) ov.innerHTML = overlayHTML(r); }
}
function viewerTool(pv, t) {
  const v = vstate(pv);
  if (t === "rl") v.rot = (v.rot + 270) % 360; else if (t === "rr") v.rot = (v.rot + 90) % 360;
  else if (t === "in") return zoomAt(pv, 1.5); else if (t === "out") return zoomAt(pv, 1 / 1.5);
  else if (t === "fit") { v.s = 1; v.tx = v.ty = 0; v.rot = 0; }
  else if (t === "100") {          /* actual pixels: one image pixel per screen pixel */
    const r = S.items.get(pv.dataset.path); if (!r) return;
    const { W } = pvSize(pv, v), natural = (v.rot % 180 ? r.h : r.w) || W;
    v.s = Math.max(1, natural / (W * (window.devicePixelRatio || 1)));
    const f = r.faces.find((x) => x.state !== "open") || r.faces[0], H = pvSize(pv, v).H;
    if (f) { v.tx = -v.s * (((f.box[0] + f.box[2]) / 2 - 0.5) * W); v.ty = -v.s * (((f.box[1] + f.box[3]) / 2 - 0.5) * H); } else { v.tx = v.ty = 0; }
  }
  applyView(pv);
}

/* ---------- people & names ---------- */
function applyAssign(assign) {
  Object.entries(assign || {}).forEach(([p, arr]) => { const r = S.items.get(p); if (!r) return;
    arr.forEach(([pid, nm], i) => { if (r.faces[i]) { r.faces[i].person = pid; r.faces[i].pname = nm; } });
    r.names = [...new Set([...r.faces.map((f) => f.pname).filter(Boolean), ...r.animals.map((a) => a.name).filter(Boolean)])].sort(); });
}
async function loadPeople(quiet) {
  const r = await api.people(quiet); if (!r.ok) { if (!quiet && r.error) toast(r.error); return null; }
  applyPeopleResult(r); return r;
}
function nameDialog({ title, sub, current = "", onSave, onForget }) {
  dialog({ title, sub, icon: "users", body: `<div class="fgrid"><label>Name</label><input type="text" id="nm" list="known" value="${esc(current)}" placeholder="e.g. Anaya"><datalist id="known">${(S.known || []).map((n) => `<option value="${esc(n)}">`).join("")}</datalist></div>
    <div class="note">The name is saved in the database with this person's face signature, so later scans recognise them automatically. Use the same name for a second group of the same person to merge them.</div>`,
    buttons: [...(onForget && current ? [{ label: "Forget this name", left: true, onClick: async (x) => { x(); await onForget(); } }] : []), { label: "Cancel", onClick: (x) => x() },
      { label: "Save name", cls: "primary", onClick: async (x) => { const v = $("nm").value.trim(); if (!v) return toast("Enter a name."); x(); await onSave(v); } }] });
}
async function nameCluster(p) {
  nameDialog({ title: p.name ? "Rename person" : "Name this person", sub: `${p.photos.length} photos`, current: p.name || "",
    onSave: async (nm) => { const r = await api.name_person(p.id, nm); if (!r.ok) return toast(r.error); applyPeopleResult(r); render(); toast(`Saved “${nm}”`); },
    onForget: async () => { const r = await api.forget_person(p.name); applyPeopleResult(r); render(); toast("Name removed"); } });
}
/* ---------- settings ---------- */
const FMT = { threshold: (v) => Math.round(v * 100) + "%", ear_down: (v) => (+v).toFixed(2), blur_cut: (v) => (+v).toFixed(1), misfocus_ratio: (v) => (+v).toFixed(1) + "×",
  min_face: (v) => (v * 100).toFixed(2) + "%", face_zoom_fill: (v) => Math.round(v * 100) + "%", box_thickness: (v) => v + " px", box_label_size: (v) => v + " px" };
const rrow = (k, t, d, min, max, step, val) => srow(t, d, `<div class="rng"><input type="range" data-k="${k}" min="${min}" max="${max}" step="${step}" value="${val}"><span class="val">${FMT[k](val)}</span></div>`);
const RES_LABEL = { 720: "720p", 1080: "1080p", 1280: "1280", 1600: "1600", 1920: "1920", 2560: "2560 · QHD", 3840: "3840 · 4K" };
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleString() : "-";
function dataPane(i) {
  const row = (t, d, btn) => srow(t, d, btn);
  const clr = (k, label, cls = "") => `<button type="button" class="${cls}" data-clear="${k}">${label}</button>`;
  return `<h3>Database</h3><p class="lead">Keeper keeps scan results, people names and tags in one SQLite file. It is safe to clear: photos are never touched.</p>
    <div class="sysinfo" id="db-card"><b>Location:</b> <span style="user-select:text">${esc(i.path)}</span><br><b>Size:</b> ${fmtSize(i.bytes)} · <b>Saved results:</b> ${i.results} photos · <b>People names:</b> ${i.persons} · <b>Animal tags:</b> ${i.tags}<br><b>Results from:</b> ${fmtDate(i.oldest)} &rarr; ${fmtDate(i.newest)}</div>
    <div class="btns" style="margin:10px 0"><button type="button" id="db-open">Open folder</button><button type="button" id="db-move">Move database…</button><button type="button" id="db-default" ${i.default ? "disabled" : ""}>Use default location</button></div>
    <div id="db-msg" class="hint"></div>
    <h3>Clear data</h3><p class="lead">Click a button twice to confirm.</p>` +
    row("Saved scan results", `${i.results} photos. Next scan analyses everything again.`, clr("results", "Clear results")) +
    row("This folder's results", S.folder ? esc(baseName(S.folder)) : "Scan a folder first.", clr("folder", "Clear folder")) +
    row("Results for missing files", "Removes entries for photos that were moved or deleted.", clr("missing", "Remove missing")) +
    row("People names", `${i.persons} named people (face signatures).`, clr("people", "Clear names")) +
    row("Animal tags", `${i.tags} tagged animals.`, clr("tags", "Clear tags")) +
    row("Thumbnail cache", `${i.thumbs} files, ${fmtSize(i.thumb_bytes)}. Rebuilt as you browse.`, clr("thumbs", "Clear thumbnails")) +
    row("Compact database", "Shrinks the file after clearing.", clr("compact", "Compact now")) +
    row("Everything", "Results, names and tags.", clr("all", "Clear everything", "danger"));
}
async function settingsDialog(startTab) {
  const [gpus, dbi] = await Promise.all([api.gpu_adapters(), api.db_info()]);
  const c = S.cfg, bc = { closed: "#E88D78", down: "#ECD489", open: "#97B881", animal: "#8FB8CA", ignored: "#9ca3af", ...(c.box_colors || {}) };
  const tabs = [["det", "Detection", "eye"], ["dup", "Duplicates", "layers"], ["scan", "Scanning", "scan"], ["sort", "Sorting", "sort"], ["look", "Appearance", "sun"], ["data", "Database", "layers"], ["sys", "Performance", "cpu"]];
  const res = (S.about.resolutions || [720, 1080, 1280, 1600, 1920, 2560, 3840]).map((v) => [v, RES_LABEL[v] || v]);
  const parts = Object.keys(PART_COL), on = new Set(c.overlay_parts || parts);
  const pane = {
    det: `<h3>Detection</h3><p class="lead">How strictly Keeper decides that eyes are closed.</p>` +
      rrow("threshold", "Sensitivity", "Higher flags more photos. Lower misses fewer open eyes.", 0.2, 0.8, 0.01, c.threshold) +
      rrow("ear_down", "Lid gap for looking down", "Lids at least this far apart while looking down are kept, not flagged.", 0.08, 0.2, 0.01, c.ear_down) +
      rrow("blur_cut", "Face is unclear below", "Face sharpness score under this counts as blurry.", 3, 15, 0.5, c.blur_cut) +
      rrow("misfocus_ratio", "Misfocus when background is", "An unclear face with a background this many times crisper is “misfocused”.", 1.2, 4, 0.1, c.misfocus_ratio) +
      rrow("min_face", "Smallest face to judge", "Faces smaller than this share of the photo are ignored.", 0, 0.02, 0.0005, c.min_face) +
      srow("Detection resolution", "Long side the detector works on. 1600 is the balanced default; 1920+ finds smaller faces but is slower.", segr("max_dim", res, c.max_dim)) +
      srow("Thorough scan", "Also checks overlapping tiles and refines each face. Finds small faces.", sw("robust", c.robust)) +
      srow("Deep scan", "Adds 3×3 tiles for very large group photos. Slower.", sw("deep", c.deep)) +
      srow("Keep looking-down faces", "Lids still apart while looking down are kept, not flagged.", sw("keep_down", c.keep_down)) +
      srow("Main subjects only", "Closed eyes on people behind or out of focus are tagged “background closed”, not flagged.", sw("main_only", c.main_only)) +
      srow("Detect animals", "Boxes pets and other animals in photos.", sw("animals", c.animals)) +
      srow("Recognise people", S.info.people_ok ? "On by default. Groups the same person across photos and lets you name them (GPU when available)." : "The recognition model was not found.", sw("people", c.people)),
    dup: `<h3>Duplicates</h3><p class="lead">Groups near-identical shots and suggests the best frame.</p>` +
      srow("Find duplicates", "Compare image similarity after each scan.", sw("dup_enabled", c.dup_enabled)) +
      srow("Strictness", "Strict only groups almost identical frames. Loose also groups similar poses.", segr("dup_level", [["strict", "Strict"], ["normal", "Normal"], ["loose", "Loose"]], c.dup_level)),
    scan: `<h3>Scanning</h3><p class="lead">What gets scanned and how fast.</p>` +
      srow("Include subfolders", "Scan every folder below the one you choose.", sw("recursive", c.recursive)) +
      srow("Use saved results", "Unchanged photos are read from the local database. Makes re-scans and resumed scans fast.", sw("use_cache", c.use_cache)) +
      srow("Worker processes", "Auto uses extra processes for big folders (40+ photos).", segr("workers", [[0, "Auto"], [1, "1"], [2, "2"], [3, "3"], [4, "4"]], c.workers)) +
      srow("Tab after scan finishes", "A scan starts on All photos and switches to this tab when done. Off keeps whichever tab you have open.", `<select data-k="after_scan_tab" class="field">${[["closed", "Closed eyes"], ["wink", "Winks"], ["down", "Looking down"], ["dups", "Duplicates"], ["blur", "Blurry / misfocused"], ["good", "Good"], ["all", "All photos"], ["off", "Off (don't switch)"]].map(([v, l]) => `<option value="${v}"${(c.after_scan_tab || "closed") === v ? " selected" : ""}>${l}</option>`).join("")}</select>`) +
      srow("Check for updates on start", "Fetches one small version file. Nothing else leaves this PC. Turn off to stay fully offline.", sw("check_updates", c.check_updates !== false)),
    sort: `<h3>Sorting</h3><p class="lead">Defaults for “Sort photos into a folder”.</p>` +
      srow("Default action", "Copy keeps originals in place. Move can be undone from the Scan menu.", segr("mode", [["copy", "Copy"], ["move", "Move"]], c.mode)) +
      srow("Folder name", "Created inside the scanned folder.", `<input type="text" class="field" data-k="out_name" value="${esc(c.out_name)}">`),
    look: `<h3>Appearance</h3><p class="lead">Colours, grid and the photo preview.</p>` +
      srow("Theme", "Dark is easier on the eyes when reviewing portraits.", segr("theme", [["dark", "Dark"], ["light", "Light"]], c.theme)) +
      srow("Photo grid shape", "Original keeps each photo's own proportions (best for mixed landscape and portrait).", segr("grid_ratio", [["original", "Original"], ["3:2", "3:2"], ["2:3", "2:3"], ["square", "Square"]], c.grid_ratio || "original")) +
      srow("Overlay style", "How faces are drawn on the preview. Detail adds forehead, jaw, chin, cheeks, brows, nose and lips.", segr("overlay", [["box", "Box"], ["mesh", "Mesh"], ["detail", "Detail"], ["off", "Off"]], c.overlay)) +
      srow("Detail overlay shows", "Parts drawn in the Detail style.", `<div class="parts">${parts.map((p) => `<label class="chip-opt ${on.has(p) ? "on" : ""}" data-part="${p}"><i style="background:${PART_COL[p]}"></i>${p}</label>`).join("")}</div>`) +
      srow("Zoom to the face on select", "The preview zooms onto the face when you pick a photo.", sw("auto_face_zoom", c.auto_face_zoom)) +
      rrow("face_zoom_fill", "Face fills this much of the view", "Used by auto zoom and the Space key.", 0.3, 0.9, 0.05, c.face_zoom_fill) +
      srow("Labels on face boxes", "Show “closed”, “down”, “wink” and names on each box.", sw("box_labels", c.box_labels !== false)) +
      rrow("box_thickness", "Box thickness", "Line width of face boxes (thinner automatically when zoomed in).", 1, 6, 1, c.box_thickness) +
      rrow("box_label_size", "Label size", "Text size on face boxes.", 9, 20, 1, c.box_label_size) +
      srow("Box colours", "Closed, looking down, open, animal, ignored.", `<div class="swatches">${["closed", "down", "open", "animal", "ignored"].map((k) => `<label title="${k}"><input type="color" data-color="${k}" value="${bc[k]}"></label>`).join("")}</div>`),
    data: dataPane(dbi),
    sys: `<h3>Performance</h3><p class="lead">Where the face-recognition model runs.</p>` +
      srow("Processing device", "Automatic tries NVIDIA, then AMD, then other GPUs, else the CPU.", `<select data-k="gpu_device" class="field"><option value="auto"${sel1("auto", c.gpu_device)}>Automatic (best GPU)</option><option value="cpu"${sel1("cpu", c.gpu_device)}>CPU only</option>${gpus.map((g) => `<option value="${esc(g)}"${sel1(g, c.gpu_device)}>${esc(g)}</option>`).join("")}</select>`) +
      `<div class="srow"><div class="lab"><b>GPU check</b><span id="gpu-text">${esc(S.info.gpu || "unknown")}</span></div><div class="ctl"><button type="button" id="gpu-test">Test GPUs</button></div></div>` +
      `<div class="sysinfo"><b>CPU cores:</b> ${S.info.cores || "?"}<br><b>People model:</b> ${S.info.people_ok ? "found" : "missing"}<br><b>Engine:</b> MediaPipe + BlazeFace, fully offline</div>` +
      `<div class="note">Detection changes apply on the next scan. Use Scan &rarr; Full re-scan to re-analyse every photo.</div>`,
  };
  dialog({ title: "Settings", sub: "Detection, scanning, appearance, database and performance", icon: "sliders", cls: "settings flush",
    body: `<div class="sx"><nav class="snav">${tabs.map(([k, l, i]) => `<button type="button" data-tab="${k}" class="${k === (startTab || "det") ? "on" : ""}">${ico(i)}${l}</button>`).join("")}</nav>
      <div class="spane">${tabs.map(([k]) => `<section data-pane="${k}" class="${k === (startTab || "det") ? "on" : ""}">${pane[k]}</section>`).join("")}</div></div>`,
    buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: "Save changes", cls: "primary", onClick: async (x) => {
      const patch = {}, NUM = ["workers", "max_dim"], colors = { ...(c.box_colors || {}) };
      $("modal").querySelectorAll("[data-k]").forEach((el) => { patch[el.dataset.k] = el.type === "checkbox" ? el.checked : el.type === "range" ? parseFloat(el.value) : el.value; });
      $("modal").querySelectorAll("[data-seg]").forEach((el) => { patch[el.dataset.seg] = NUM.includes(el.dataset.seg) ? parseInt(el.dataset.val, 10) : el.dataset.val; });
      $("modal").querySelectorAll("[data-color]").forEach((el) => { colors[el.dataset.color] = el.value; }); patch.box_colors = colors;
      patch.overlay_parts = [...$("modal").querySelectorAll("[data-part].on")].map((el) => el.dataset.part);
      const thrChanged = patch.threshold !== c.threshold;
      S.cfg = await api.save_settings(patch); applyCfg();
      if (thrChanged) (await api.set_threshold(S.cfg.threshold)).forEach((r) => S.items.set(r.path, r));
      S.gridPaths = null; x(); render(); renderInspector(); toast("Settings saved. Re-scan to apply detection changes.");
    } }],
    onMount: (b) => {
      const msg = (t) => { const m = b.querySelector("#db-msg"); if (m) m.textContent = t; };
      const refreshData = async () => { const i = await api.db_info(); b.querySelector('[data-pane="data"]').innerHTML = dataPane(i); };
      b.addEventListener("click", async (e) => {
        const t = e.target.closest("[data-tab]");
        if (t) { b.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === t)); b.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("on", p.dataset.pane === t.dataset.tab)); }
        segClick(e);
        const part = e.target.closest("[data-part]"); if (part) part.classList.toggle("on");
        if (e.target.id === "gpu-test") { e.target.disabled = true; $("gpu-text").textContent = "Checking the GPU…"; api.gpu_test().then((r) => { $("gpu-text").textContent = r.text; e.target.disabled = false; S.info.gpu = r.text; }); }
        if (e.target.id === "db-open") api.db_open_folder();
        if (e.target.id === "db-move") {
          const f = await api.pick_folder(); if (!f) return; msg("Moving the database…");
          let r = await api.db_move(f);
          if (r.exists) { msg("A database already exists in that folder. Click “Use existing” to switch to it (the current one is left where it is)."); const bt = document.createElement("button"); bt.textContent = "Use existing"; bt.onclick = async () => { const r2 = await api.db_move(f, true); if (r2.ok) { await refreshData(); } else msg(r2.error); }; b.querySelector("#db-msg").appendChild(bt); return; }
          if (r.ok) { await refreshData(); b.querySelector("#db-msg").textContent = "Database moved."; } else msg(r.error);
        }
        if (e.target.id === "db-default") { const r = await api.db_use_default(); if (r.ok) { await refreshData(); b.querySelector("#db-msg").textContent = "Back in the default location."; } else msg(r.error); }
        const cl = e.target.closest("[data-clear]");
        if (cl) {
          if (cl.dataset.arm !== "1") { cl.dataset.arm = "1"; cl.dataset.label = cl.textContent; cl.textContent = "Click again to confirm"; setTimeout(() => { if (cl.isConnected) { cl.dataset.arm = ""; cl.textContent = cl.dataset.label; } }, 4000); return; }
          const what = cl.dataset.clear, r = await api.db_clear(what);
          if (!r.ok) return msg(r.error);
          if (what === "people" || what === "names" || what === "all") { S.people = []; S.peopleKey = ""; S.items.forEach((it) => { it.faces.forEach((f) => { f.pname = null; }); it.names = it.animals.map((a) => a.name).filter(Boolean); }); }
          if (what === "tags" || what === "all") S.items.forEach((it) => { it.animals.forEach((a) => { a.name = null; }); it.names = it.faces.map((f) => f.pname).filter(Boolean); });
          await refreshData(); b.querySelector("#db-msg").textContent = `Done (${r.removed || 0} removed).`; scheduleRender();
        }
      });
      b.addEventListener("input", (e) => { const k = e.target.dataset && e.target.dataset.k; if (e.target.type === "range" && FMT[k]) e.target.nextElementSibling.textContent = FMT[k](e.target.value); });
    } });
}

/* ---------- review window ---------- */
function reviewOpen() {
  const r = S.items.get(S.focus) || selItems()[0] || S.visible[0]; if (!r) return;
  S.review = r.path; drawReview();
}
function drawReview() {
  const r = S.items.get(S.review); if (!r) return;
  const i = S.visible.findIndex((x) => x.path === r.path);
  dialog({ title: `Review · ${r.name}`, sub: `Photo ${i + 1} of ${S.visible.length}`, icon: "eye", cls: "full flush", body: `<div class="rv"><div class="stage">${pvHTML(r, "rv-pv", true)}<button class="nav-arrow l" data-nav="-1" aria-label="Previous">${ico("chevron-left")}</button><button class="nav-arrow r" data-nav="1" aria-label="Next">${ico("chevron-right")}</button></div>
    <aside><div class="tags">${r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    <div class="kv"><span>Result</span><b>${r.flagged ? (r.needs_check ? "needs check" : "eyes closed") : r.verdict}</b></div><div class="kv"><span>Size</span><b>${fmtSize(r.size)}</b></div><div class="kv"><span>Focus</span><b>${r.focus}</b></div>
    <div class="btns"><button data-rv="keep">Keep (K)</button><button data-rv="flag">Flag (F)</button></div>
    <div class="btns"><button data-rv="protect">${r.protected ? "Unprotect" : "Protect"} (P)</button><button data-rv="reject">${S.rejected.has(r.path) ? "Unreject" : "Reject"} (X)</button></div>
    <div class="btns"><button data-rv="move">Move… (M)</button></div><div class="hint">← → move · wheel zoom · drag pan · Space face · Tab next · Esc close</div></aside></div>`,
    buttons: [{ label: "Close", cls: "primary", onClick: (x) => x() }],
    onMount: (b) => { requestAnimationFrame(() => initPV("rv-pv", r)); } });
  S.review = r.path;
}
function reviewNav(d) {
  const i = S.visible.findIndex((x) => x.path === S.review), n = S.visible[Math.min(S.visible.length - 1, Math.max(0, i + d))];
  if (n) { S.review = n.path; S.focus = n.path; S.sel = new Set([n.path]); drawReview(); refreshSel(); }
}

/* ---------- context menu ---------- */
function showMenu(items, x, y, el = $("ctx")) {
  el.innerHTML = items.map((it, i) => it === "-" ? `<div class="msep"></div>` : `<div class="mi ${it.dis ? "dis" : ""}" data-i="${i}">${it.ck !== undefined ? `<span class="ck">${it.ck ? "✓" : ""}</span>` : ""}${esc(it.l)}${it.k ? `<span class="ac">${it.k}</span>` : ""}</div>`).join("");
  el.classList.remove("hidden"); el.style.left = Math.min(x, innerWidth - 270) + "px"; el.style.top = Math.min(y, innerHeight - el.offsetHeight - 10) + "px";
  el.onclick = (e) => { const m = e.target.closest(".mi"); if (!m) return; el.classList.add("hidden"); const a = items[m.dataset.i].a; if (typeof a === "function") a(); else run(a); };
}
function ctxItems() {
  const n = S.sel.size, one = n === 1, r0 = selItems()[0], pl = n === 1 ? "photo" : `${n} photos`;
  return [{ l: one ? "Open" : `Open ${n} photos`, a: "open" }, { l: "Review…", k: "Enter", a: "review" }, { l: "Zoom to face", k: "Space", a: "zoom" }, { l: "Show in folder", a: "reveal" }, "-",
    { l: n === 1 ? "Copy file" : `Copy ${n} files`, k: "Ctrl+C", a: "copyfiles" }, { l: "Copy path", a: "copypath" }, { l: "Rename…", k: "F2", a: "rename", dis: !one }, "-",
    { l: r0 && r0.protected ? "Unprotect" : "Protect (skip in sort / delete)", a: "protect" }, { l: "Keep - never flag", a: "keep" }, { l: "Flag as eyes closed", a: "flag" }, { l: "Reset photo", a: "reset" },
    ...(r0 && r0.rescue && one ? [{ l: `Select the better frame (${baseName(r0.rescue)})`, a: "better" }] : []),
    ...(r0 && r0.dup_group !== null && one ? [{ l: "Select the whole duplicate group", a: "dupgroup" }] : []), "-",
    { l: `Re-scan ${pl}`, a: "rescan-sel" }, { l: `Move / copy ${pl} to a folder…`, a: "sort-sel" }, { l: `Delete ${pl}…`, k: "Del", a: "delete" },
    { l: "Remove from list (keep file)", a: "remove" }, "-", { l: "Properties", a: "props", dis: !one }];
}

/* ---------- actions ---------- */
/* ---------- people: sort, merge, duplicate suggestions ---------- */
const PEOPLE_SORTS = [["photos", "Most photos"], ["name", "Name (A–Z)"], ["closed", "Most closed eyes"], ["unnamed", "Unnamed first"]];
function sortPeople(list, mode = S.cfg.people_sort || "photos") {
  const byPhotos = (a, b) => b.photos.length - a.photos.length || b.faces - a.faces;
  const cmp = { photos: byPhotos, closed: (a, b) => b.closed - a.closed || byPhotos(a, b),
    name: (a, b) => (!!b.name - !!a.name) || (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" }) || byPhotos(a, b),
    unnamed: (a, b) => (!!a.name - !!b.name) || byPhotos(a, b) }[mode] || byPhotos;
  return [...list].sort(cmp);
}
function setPeopleSort(mode) { S.cfg.people_sort = mode; api.save_settings({ people_sort: mode }); const h = document.getElementById("rail-people"); if (h) h.dataset.key = ""; renderRail(); }
function applyPeopleResult(r) {
  S.people = r.people; S.known = r.known; S.suggest = r.suggestions || []; applyAssign(r.assign);
  const h = document.getElementById("rail-people"); if (h) h.dataset.key = ""; scheduleRender(); renderInspector();
}
function faceThumbOf(p, size = 96) {
  const k = `t${size}:${p.path}:${p.box.join(",")}`; S.faceThumbs = S.faceThumbs || {};
  return S.faceThumbs[k] ? Promise.resolve(S.faceThumbs[k]) : api.face_thumb(p.path, p.box, size).then((u) => { if (u) S.faceThumbs[k] = u; return u; });
}
function drawPeopleRail() {
  const host = document.getElementById("rail-people"); if (!host) return;
  const hid = S.hiddenPeople || (S.hiddenPeople = new Set()), mode = S.cfg.people_sort || "photos";
  const list = sortPeople((S.people || []).filter((p) => !hid.has(p.id)), mode).slice(0, 10);
  const key = "k:" + mode + ":" + list.map((p) => p.id + ":" + (p.name || "") + ":" + p.photos.length).join("|");
  if (host.dataset.key === key) return; host.dataset.key = key;
  host.innerHTML = list.map((p) => `<div class="item" data-person="${p.id}"><img class="pface" alt="" data-id="${p.id}"><span>${esc(p.name || "Person " + p.id)}</span><span class="n">${p.photos.length}</span></div>`).join("");
  host.querySelectorAll("img").forEach((im) => { const p = S.people.find((x) => x.id == im.dataset.id); faceThumbOf(p, 48).then((u) => { if (u) im.src = u; }); });
}
function peopleListMenu() {
  const mode = S.cfg.people_sort || "photos", n = (S.suggest || []).length, hid = (S.hiddenPeople || new Set()).size;
  return [...PEOPLE_SORTS.map(([k, l]) => ({ l: "Sort: " + l, ck: mode === k, a: () => setPeopleSort(k) })), "-",
    { l: `Find possible duplicates${n ? ` (${n})` : ""}…`, a: () => peopleDialog({ suggest: true }) },
    { l: "Merge people…", a: () => peopleDialog() },
    ...(hid ? ["-", { l: `Show ${hid} hidden people`, a: () => { S.hiddenPeople = new Set(); setPeopleSort(S.cfg.people_sort || "photos"); } }] : [])];
}
function mergeDialog(ids) {
  const group = ids.map((id) => (S.people || []).find((p) => p.id === id)).filter(Boolean);
  if (group.length < 2) return toast("Pick at least two people to merge.");
  const named = group.find((p) => p.name), faces = group.reduce((s, p) => s + p.faces, 0), photos = new Set(group.flatMap((p) => p.photos)).size;
  dialog({ title: `Merge ${group.length} people`, sub: `${faces} faces in ${photos} photos become one person`, icon: "users", body: `
    <div class="mergerow">${group.map((p) => `<div class="mg"><img alt="" data-id="${p.id}"><span>${esc(p.name || "Person " + p.id)}</span></div>`).join('<span class="plus">+</span>')}</div>
    <div class="fgrid"><label>Name (optional)</label><input type="text" id="mg-name" list="known" value="${esc(named ? named.name : "")}" placeholder="Name this person"><datalist id="known">${(S.known || []).map((n) => `<option value="${esc(n)}">`).join("")}</datalist></div>
    <div class="note">With a name the merge is saved: later scans recognise all of these looks as this person. Without a name it lasts until you scan again. Merging never changes your photos.</div>`,
    buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: "Merge", cls: "primary", onClick: async (x) => {
      const nm = $("mg-name").value.trim(), r = await api.merge_people(ids, nm); x();
      if (!r.ok) return toast(r.error); applyPeopleResult(r); toast(nm ? `Merged into “${nm}”` : "Merged for this session"); peopleDialog({ suggest: (r.suggestions || []).length > 0 }); } }],
    onMount: (b) => b.querySelectorAll("img").forEach((im) => faceThumbOf(group.find((p) => p.id == im.dataset.id), 80).then((u) => { if (u) im.src = u; })) });
}
async function peopleDialog(opts = {}) {
  status("Grouping faces…"); const r = await api.people(false); status("Ready");
  if (!r.ok) return toast(r.error);
  applyPeopleResult(r);
  const sel = new Set(opts.select || []), dismissed = S.dismissedPairs || (S.dismissedPairs = new Set());
  let q = "", showSug = !!opts.suggest;
  dialog({ title: "People", sub: "Name people, merge duplicates, and filter photos by person", icon: "users", cls: "wide", body: `
    <div class="ptools"><input type="search" id="p-q" placeholder="Search people">
      <select id="p-sort">${PEOPLE_SORTS.map(([k, l]) => `<option value="${k}"${k === (S.cfg.people_sort || "photos") ? " selected" : ""}>${l}</option>`).join("")}</select>
      <button type="button" id="p-sug"></button><button type="button" class="primary" id="p-merge"></button></div>
    <div id="p-sug-panel"></div><div class="people" id="ppl"></div>`, buttons: [{ label: "Close", cls: "primary", onClick: (x) => x() }],
    onMount: (b, close) => {
      const sugs = () => (S.suggest || []).filter((s) => !dismissed.has(s.a + "-" + s.b) && S.people.some((p) => p.id === s.a) && S.people.some((p) => p.id === s.b));
      const draw = () => {
        const list = sortPeople(S.people.filter((p) => (p.name || "Person " + p.id).toLowerCase().includes(q)), $("p-sort").value);
        $("ppl").innerHTML = list.map((p) => `<div class="person ${sel.has(p.id) ? "picked" : ""}" data-id="${p.id}"><label class="pcheck" title="Select to merge"><input type="checkbox" data-pick="${p.id}" ${sel.has(p.id) ? "checked" : ""}></label><img alt="" data-id="${p.id}"><div class="pname">${esc(p.name || "Person " + p.id)}</div><div class="hint">${p.photos.length} photos${p.closed ? ` · ${p.closed} closed` : ""}</div><button class="sm2" data-name="${p.id}">${p.name ? "Rename" : "Name…"}</button><button class="sm2" data-split="${p.id}" title="Different people grouped together? Separate them">Split</button></div>`).join("") || `<div class="hint">No one matches.</div>`;
        $("ppl").querySelectorAll("img").forEach((im) => { const p = S.people.find((x) => x.id == im.dataset.id); faceThumbOf(p).then((u) => { if (u) im.src = u; }); });
        $("p-merge").textContent = `Merge selected (${sel.size})`; $("p-merge").disabled = sel.size < 2;
        const n = sugs().length; $("p-sug").innerHTML = `Possible duplicates${n ? ` (${n})` : ""}`; $("p-sug").classList.toggle("on", showSug);
        const panel = $("p-sug-panel"); panel.classList.toggle("hidden", !showSug);
        panel.innerHTML = showSug ? (n ? `<p class="hint">These people look alike to the recognition model. Similar is not always the same person, so check the faces before merging.</p>` + sugs().map((s) => { const A = S.people.find((p) => p.id === s.a), B = S.people.find((p) => p.id === s.b);
          return `<div class="sugrow" data-a="${s.a}" data-b="${s.b}"><img alt="" data-id="${A.id}"><span class="sn">${esc(A.name || "Person " + A.id)}<small>${A.photos.length} photos</small></span><span class="plus">≈</span><img alt="" data-id="${B.id}"><span class="sn">${esc(B.name || "Person " + B.id)}<small>${B.photos.length} photos</small></span><span class="simbadge" title="Similarity">${Math.round(s.sim * 100)}%</span><button class="sm2 primary" data-sugmerge>Merge</button><button class="sm2" data-sugdismiss>Not the same</button></div>`; }).join("")
          : `<p class="hint">No possible duplicates found.</p>`) : "";
        panel.querySelectorAll("img").forEach((im) => { const p = S.people.find((x) => x.id == im.dataset.id); faceThumbOf(p, 64).then((u) => { if (u) im.src = u; }); });
      };
      draw();
      $("p-q").oninput = (e) => { q = e.target.value.trim().toLowerCase(); draw(); };
      $("p-sort").onchange = (e) => { setPeopleSort(e.target.value); draw(); };
      $("p-sug").onclick = () => { showSug = !showSug; draw(); };
      $("p-merge").onclick = () => { close(); mergeDialog([...sel]); };
      b.onclick = (e) => {
        const pick = e.target.closest("[data-pick]"); if (pick) { const id = +pick.dataset.pick; pick.checked ? sel.add(id) : sel.delete(id); draw(); return; }
        const sm = e.target.closest("[data-sugmerge]"); if (sm) { const row = sm.closest(".sugrow"); close(); return mergeDialog([+row.dataset.a, +row.dataset.b]); }
        const sd = e.target.closest("[data-sugdismiss]"); if (sd) { const row = sd.closest(".sugrow"); dismissed.add(row.dataset.a + "-" + row.dataset.b); draw(); return; }
        const sp = e.target.closest("[data-split]"); if (sp) { sp.disabled = true; api.split_person(+sp.dataset.split).then((r) => { sp.disabled = false; if (!r.ok) return toast(r.error); applyPeopleResult(r); sel.clear(); draw(); toast("Split into separate people"); }); return; }
        const nb = e.target.closest("[data-name]"); if (nb) { const p = S.people.find((x) => x.id == nb.dataset.name); close(); return nameCluster(p); }
        if (e.target.closest(".pcheck")) return;
        const el = e.target.closest(".person"); if (!el) return; const p = S.people.find((x) => x.id == el.dataset.id);
        S.only = new Set(p.photos); S.onlyLabel = p.name || "Person " + p.id; S.filter = "all"; close(); render();
      };
    } });
}

/* ---------- close folder, stop button, person menu ---------- */
function resetStop() { const b = $("stop"); b.disabled = false; $("stop-label").textContent = "Stop"; }
/* Reset everything that belongs to the open folder (the engine forgets it too). Photos and saved results are untouched. */
async function unloadFolder() {
  if (!S.folder) return true;
  $("stop-label").textContent = "Stopping…";
  const r = await api.close_folder(); if (!r.ok) { resetStop(); toast(r.error); return false; }
  S.items.clear(); S.tiles.clear(); S.sel.clear(); S.rejected.clear(); S.focus = null; S.only = null; S.people = []; S.geo = {}; S.gridPaths = null; S.hiddenPeople = new Set();
  S.folder = ""; S.stopped = null; S.scanning = false; showNotice(); resetStop(); $("progress").classList.add("hidden");
  if (closeModal) closeModal();
  $("folder-name").textContent = ""; $("grid").replaceChildren();
  renderInspector(); status("Ready"); $("status-counts").textContent = "";
  return true;
}
function goStart() {
  S.tab = -1; $("review").classList.add("hidden"); $("start").classList.remove("hidden"); renderTabs(); renderLibrary();
}
async function closeFolder() {
  if (!S.folder) return toast("No folder is open.");
  if (S.tab >= 0) return closeTab(S.tab);
  if (await unloadFolder()) goStart();
}

/* ---------- folder tabs: each opened folder is a tab; the engine holds one folder, switching reloads it from the saved results ---------- */
const sameF = (a, b) => a.replace(/[\\/]+$/, "").toLowerCase() === b.replace(/[\\/]+$/, "").toLowerCase();
const tabOf = (folder) => S.tabs.findIndex((t) => sameF(t.folder, folder));
function persistTabs() { if (api) api.save_tabs(S.tabs.map((t) => t.folder)); }
function saveView() {
  const t = S.tabs[S.tab]; if (!t || !S.folder) return;
  const sc = $(S.view === "list" ? "list" : "grid");
  t.view = { filter: S.filter, sub: S.sub, query: S.query, sort: S.sort, scroll: sc ? sc.scrollTop : 0, sel: [...S.sel], rej: [...S.rejected] };
}
function takeView(folder) { const p = S.pendingView; S.pendingView = null; return p && sameF(p.folder, folder) ? p.view : null; }
function restoreTail(v) {      // selection and scroll can only be restored once the photos are back
  S.restoreView = null;
  S.sel = new Set(v.sel.filter((p) => S.items.has(p))); S.focus = S.sel.size ? [...S.sel][0] : null;
  setTimeout(() => { const sc = $(S.view === "list" ? "list" : "grid"); if (sc) sc.scrollTop = v.scroll; }, 350);
}
function tabEnter(folder) {
  let i = tabOf(folder);
  if (i < 0) { S.tabs.push({ folder, view: null }); i = S.tabs.length - 1; persistTabs(); }
  S.tab = i;
}
function renderTabs() {
  const el = $("ftabs"); el.classList.toggle("hidden", !S.tabs.length);
  el.innerHTML = S.tabs.map((t, i) => `<div class="ft${i === S.tab ? " on" : ""}" role="tab" aria-selected="${i === S.tab}" tabindex="0" data-i="${i}" title="${esc(t.folder)}"><span class="dot${i === S.tab && S.scanning ? " scan" : ""}"></span><span class="fn">${esc(baseName(t.folder))}</span><button class="ghost x" data-x="${i}" title="Close tab (Ctrl+W)" aria-label="Close tab">${ico("x")}</button></div>`).join("") +
    `<button class="ghost ftnew" data-lib title="Library: all scanned folders" aria-label="Library">${ico("plus")}</button>`;
}
async function openTab(t) {
  S.pendingView = { folder: t.folder, view: t.view }; S.tab = S.tabs.indexOf(t); renderTabs();
  if (!(await startScan(t.folder, false))) { S.pendingView = null; if (!S.folder) goStart(); }
}
async function switchTab(i) {
  const t = S.tabs[i]; if (!t || (i === S.tab && S.folder)) return;
  if (!(await api.folder_ok(t.folder))) return toast("That folder isn't available (is the drive connected?).");
  const go = async () => { saveView(); if (S.folder && !(await unloadFolder())) return; openTab(t); };
  if (S.scanning) return confirmBox({ title: "Stop scanning?", sub: baseName(S.folder), icon: "square", okLabel: "Stop and switch",
    text: "Photos scanned so far are saved. Open this folder's tab again to resume.", onOk: go });
  go();
}
async function closeTab(i) {
  const t = S.tabs[i]; if (!t) return;
  if (i !== S.tab) { S.tabs.splice(i, 1); if (i < S.tab) S.tab--; persistTabs(); return renderTabs(); }
  if (!(await unloadFolder())) return;
  S.tabs.splice(i, 1); persistTabs();
  const next = S.tabs[Math.min(i, S.tabs.length - 1)];
  S.tab = -1;
  if (next && await api.folder_ok(next.folder)) openTab(next); else goStart();
}
function cycleTab(d) { const n = S.tabs.length; if (n > 1) switchTab(((S.tab < 0 ? (d > 0 ? -1 : 0) : S.tab) + d + n) % n); }
async function showLibrary() {
  if (isStart()) return;
  const go = async () => { saveView(); if (await unloadFolder()) goStart(); };
  if (S.scanning) return confirmBox({ title: "Stop scanning?", sub: baseName(S.folder), icon: "square", okLabel: "Stop and open library",
    text: "Photos scanned so far are saved. Open this folder's tab again to resume.", onOk: go });
  go();
}
/* open a folder: reuse its tab (and view) when it has one */
function openFolderTab(folder) { const i = tabOf(folder); if (i >= 0) { const t = S.tabs[i]; return isStart() ? openTab(t) : switchTab(i); } return startScan(folder, false); }
function tabMenu(i) {
  const t = S.tabs[i];
  return [{ l: "Close tab", k: "Ctrl+W", a: () => closeTab(i) },
    { l: "Close other tabs", dis: S.tabs.length < 2, a: () => { for (let j = S.tabs.length - 1; j >= 0; j--) if (S.tabs[j] !== t && j !== S.tab) S.tabs.splice(j, 1); S.tab = S.tabs.findIndex((x) => x === t); persistTabs(); renderTabs(); } },
    "-", { l: "Show in Explorer", a: () => api.reveal(t.folder) }];
}

/* ---------- library (start screen) ---------- */
function ago(ts) {
  if (!ts) return "";
  const d = (Date.now() / 1000 - ts) / 86400;
  if (d < 1 / 24) return "just now"; if (d < 1) return Math.floor(d * 24) + " h ago"; if (d < 2) return "yesterday";
  if (d < 30) return Math.floor(d) + " days ago"; if (d < 365) return Math.floor(d / 30) + " mo ago"; return Math.floor(d / 365) + " y ago";
}
async function renderLibrary() {
  const rows = S.lib = await api.library();
  $("library").innerHTML = rows.length ? `<h3>Your library<span class="muted">${rows.length} scanned folder${rows.length > 1 ? "s" : ""}</span></h3><div class="libgrid">` + rows.map((r, i) => {
    const open = tabOf(r.folder) >= 0;
    const mos = [0, 1, 2, 3].map((k) => r.thumbs[k] && r.exists ? `<img src="${imgUrl(r.thumbs[k], 240)}" alt="" loading="lazy">` : "<i></i>").join("");
    const chips = [r.total !== null ? `<span class="lb">${r.total.toLocaleString()} photos</span>` : "",
      r.flagged ? `<span class="lb eyes">${r.flagged} closed</span>` : ""].join("");
    const meta = [open ? `<span class="lb ok">Open</span>` : "", r.exists ? "" : `<span class="lb off">Offline</span>`,
      r.total === null ? `<span class="lb">Not scanned yet</span>` : ""].join("");
    return `<div class="lcard${r.exists ? "" : " off"}${r.flagged ? " has-closed" : ""}" tabindex="0" role="button" data-i="${i}" title="${esc(r.folder)}"><div class="mw"><div class="mos">${mos}</div><div class="chips">${chips}</div></div>` +
      `<div class="lp"><b>${esc(baseName(r.folder))}</b><span class="path">${esc(r.folder)}</span>${meta ? `<div class="lmeta">${meta}</div>` : ""}<span class="when">${r.scanned ? "Scanned " + ago(r.scanned) : ""}</span></div>` +
      `<button class="ghost sm lmore" data-more="${i}" title="More" aria-label="More actions">${ico("more-horizontal")}</button></div>`;
  }).join("") + `</div>` : "";
}
function libMenu(r) {
  return [{ l: "Open", a: () => r.exists ? openFolderTab(r.folder) : toast("That folder isn't available (is the drive connected?).") },
    { l: "Scan again from scratch", dis: !r.exists, a: () => startScan(r.folder, true) },
    { l: "Show in Explorer", dis: !r.exists, a: () => api.reveal(r.folder) }, "-",
    { l: "Remove from library…", a: () => confirmBox({ title: "Remove from library?", sub: r.folder, icon: "trash", tone: "danger", okLabel: "Remove",
        text: "The saved scan results for this folder are removed from the database. Your photos are not touched; scanning the folder again brings it back.",
        onOk: async () => { const x = await api.library_remove(r.folder); if (!x.ok) return toast(x.error); const i = tabOf(r.folder); if (i >= 0) { S.tabs.splice(i, 1); persistTabs(); renderTabs(); } renderLibrary(); } }) }];
}
function confirmBox({ title, sub = "", icon = "info", tone = "", text, okLabel, onOk }) {
  dialog({ title, sub, icon, tone, body: `<p>${text}</p>`, buttons: [{ label: "Cancel", onClick: (x) => x() }, { label: okLabel, cls: tone === "danger" ? "danger" : "primary", onClick: async (x) => { x(); await onOk(); } }] });
}
function personMenu(p) {
  const label = p.name || "Person " + p.id, refresh = async () => { await loadPeople(true); renderInspector(); };
  return [
    { l: `Show photos of ${label}`, a: () => { S.only = new Set(p.photos); S.onlyLabel = label; S.filter = "all"; render(); } },
    { l: "Select their photos", a: () => { S.only = null; S.filter = "all"; render(); setSel(p.photos.filter((x) => S.items.has(x))); } },
    "-",
    { l: p.name ? "Rename…" : "Name this person…", a: () => nameCluster(p) },
    { l: "Merge with other people…", a: () => peopleDialog({ select: [p.id] }) },
    { l: "Split into separate people…", a: () => confirmBox({ title: "Split this person?", sub: label, icon: "users", okLabel: "Split",
        text: "Use this when different people were grouped together. The faces are grouped again more strictly, until you scan again. Your photos and saved names are not changed.",
        onOk: async () => { const r = await api.split_person(p.id); if (!r.ok) return toast(r.error); applyPeopleResult(r); toast("Split into separate people"); } }) },
    "-",
    { l: "Copy name", dis: !p.name, a: () => { copyText(p.name); toast("Name copied"); } },
    { l: "Forget name and face signature", dis: !p.name, a: () => confirmBox({ title: "Forget this name?", sub: p.name, icon: "users", tone: "danger", okLabel: "Forget name",
        text: `“${esc(p.name)}” and its saved face signature are removed from the database. The photos are not touched, and you can name this person again at any time.`,
        onOk: async () => { const r = await api.forget_person(p.name); applyPeopleResult(r); render(); toast("Name forgotten"); } }) },
    { l: "Ignore these faces (not a person)", a: () => confirmBox({ title: "Ignore these faces?", sub: label, icon: "users", tone: "danger", okLabel: "Ignore faces",
        text: `All ${p.faces || p.photos.length} face(s) of this group are marked <b>ignored</b>, so they no longer count as people or as closed eyes. Use Reset on a photo to undo.`,
        onOk: async () => { const r = await api.ignore_person(p.id); r.items.forEach((u) => S.items.set(u.path, u)); applyPeopleResult(r.payload); render(); toast("Faces ignored"); } }) },
    { l: "Hide from this list", a: () => { (S.hiddenPeople || (S.hiddenPeople = new Set())).add(p.id); S.peopleKey = ""; $("rail-people").dataset.key = ""; renderRail(); } },
  ];
}

/* File > Open folder (and recent folders): same scan options as the start screen, in a dialog */
async function openFolder(folder) {
  const f = folder || await api.pick_folder(); if (!f) return;
  if (isStart()) return startScan(f, false);          // the start screen already shows the options inline
  const c = S.cfg;
  dialog({ title: "Scan this folder", sub: f, icon: "folder", body:
    srow("Include subfolders", "Scan every folder below this one.", sw("so-recursive", c.recursive)) +
    srow("Faster, less thorough", "Skips the tiled face search. Quicker, may miss small faces.", sw("so-fast", !c.robust)) +
    srow("Fresh scan", "Ignore saved results and analyse every photo again.", sw("so-force", false)) +
    `<div class="note">Photos already scanned and unchanged are read from the saved results, so repeat scans are quick.</div>`,
    buttons: [{ label: "Choose another folder…", left: true, onClick: async (x) => { const n = await api.pick_folder(); if (n) { x(); openFolder(n); } } },
      { label: "Cancel", onClick: (x) => x() },
      { label: "Start scan", cls: "primary", onClick: (x) => { const v = (id) => document.querySelector(`[data-k=${id}]`).checked;
        const o = { recursive: v("so-recursive"), robust: !v("so-fast"), force: v("so-force") }; x(); startScan(f, o.force, o); } }] });
}
async function startScan(folder, force, given) {
  if (!folder) return;
  const opts = { force: !!force };
  if (given) Object.assign(opts, given);
  else if (isStart()) { opts.recursive = $("o-recursive").checked; opts.robust = !$("o-fast").checked; opts.force = opts.force || $("o-force").checked; }
  const res = await api.start_scan(folder, opts); if (!res.ok) toast(res.error); else S.cfg = await api.get_settings();
  return res.ok;
}
async function applyOverride(value) {
  const ps = selPaths(); if (!ps.length) return;
  ps.forEach((p) => S.rejected.delete(p));
  (await api.set_override(ps, value)).forEach((u) => S.items.set(u.path, u)); scheduleRender(); renderInspector();
  toast(value === "keep" ? "Kept" : value === "flag" ? "Flagged" : "Reset");
}
const ACT = {
  open_folder: () => openFolder(),
  scan: () => S.folder ? startScan(S.folder, false) : ACT.open_folder(),
  rescan_all: () => S.folder ? startScan(S.folder, true) : ACT.open_folder(),
  "rescan-sel": async () => { const r = await api.rescan(selPaths()); if (!r.ok) toast(r.error); },
  stop: () => { $("stop").disabled = true; $("stop-label").textContent = "Stopping…"; api.stop_scan(); },
  close_folder: closeFolder, library: showLibrary,
  csv: async () => { const r = await api.export_csv(); if (r.ok) status("Report saved: " + r.path); else if (!r.cancelled) toast(r.error); },
  sheets: sheetsDialog, settings: settingsDialog, exit: () => api.exit(),
  all: () => setSel(S.visible.map((r) => r.path), S.focus), none: () => setSel([]),
  invert: () => setSel(S.visible.filter((r) => !S.sel.has(r.path)).map((r) => r.path)),
  sel_closed: () => setSel(S.visible.filter((r) => r.flagged).map((r) => r.path)),
  sel_dups: () => setSel([...S.items.values()].filter((r) => r.dup_group !== null && !r.dup_best).map((r) => r.path)),
  sel_blur: () => setSel([...S.items.values()].filter((r) => r.unclear).map((r) => r.path)),
  copyfiles: async () => { if (await api.copy_files(selPaths())) toast(`${S.sel.size} file(s) copied. Paste in Explorer.`); },
  copypath: () => { copyText(selPaths().join("\n")); toast("Path copied"); },
  rename: () => { const r = selItems(); if (r.length === 1) renameDialog(r[0]); else toast("Select exactly one photo to rename."); },
  delete: () => { const ps = selPaths(); ps.length ? confirmTrash(ps) : toast("Select photos first."); },
  remove: () => { const ps = selPaths(); api.forget(ps); ps.forEach(dropItem); render(); renderInspector(); status("Removed from the list (files kept)."); },
  review: reviewOpen, open: () => selPaths().forEach((p) => api.open_file(p)), reveal: () => S.focus && api.reveal(S.focus),
  zoom: () => { const pv = $("rv-pv") || $("pv"); zoomPV(pv, S.items.get(S.review || S.focus), false); },
  keep: () => applyOverride("keep"), flag: () => applyOverride("flag"), reset: () => applyOverride(null),
  protect: async () => { const rs = selItems(); if (!rs.length) return; const to = !rs.every((r) => r.protected);
    (await api.set_protect(selPaths(), to)).forEach((u) => { S.items.set(u.path, u); S.rejected.delete(u.path); }); scheduleRender(); renderInspector(); toast(to ? "Protected" : "Unprotected"); },
  reject: () => { selPaths().forEach((p) => S.rejected.has(p) ? S.rejected.delete(p) : S.rejected.add(p)); scheduleRender(); updateActionBar(); renderInspector(); },
  "rej-dups": () => { S.visible.filter((r) => !r.dup_best && !r.protected).forEach((r) => S.rejected.add(r.path)); render(); toast("Marked similar frames; the best of each group is kept"); },
  "clear-only": () => { S.only = null; render(); },
  better: () => { const r = selItems()[0]; if (r && r.rescue) { S.filter = "all"; render(); setSel([r.rescue]); } },
  dupgroup: () => { const r = selItems()[0]; if (r) setSel([...S.items.values()].filter((x) => x.dup_group === r.dup_group).map((x) => x.path), r.path); },
  sort_closed: () => sortDialog("closed"), sort_blur: () => sortDialog("unclear"), sort_dups: () => sortDialog("dups"),
  undo: async () => { const r = await api.undo_sort(); toast(r.ok ? `Restored ${r.restored} file(s). ${r.note}` : r.error); },
  del_closed: () => deleteBulk("closed"), del_dups: () => deleteBulk("dups"),
  props: () => { const r = selItems()[0]; if (r) propsDialog(r); },
  people: peopleDialog, palette: paletteDialog,
  shortcuts: shortcutsDialog, detection: detectionDialog, about: aboutDialog, update: () => checkUpdate(true),
  theme: () => { S.cfg.theme = S.cfg.theme === "dark" ? "light" : "dark"; api.save_settings({ theme: S.cfg.theme }); applyCfg(); },
  grid: () => setView("grid"), list: () => setView("list"),
  ov_box: () => setOverlay("box"), ov_mesh: () => setOverlay("mesh"), ov_detail: () => setOverlay("detail"), ov_off: () => setOverlay("off"),
  ratio_original: () => setRatio("original"), ratio_32: () => setRatio("3:2"), ratio_23: () => setRatio("2:3"), ratio_square: () => setRatio("square"),
  settings_db: () => settingsDialog("data"), "dismiss-notice": () => { S.stopped = null; showNotice(); },
  size_s: () => setThumb(120), size_m: () => setThumb(160), size_l: () => setThumb(220),
  pane_rail: () => { const off = $("body").classList.toggle("no-rail"); S.cfg.pane_filters = !off; api.save_settings({ pane_filters: !off, pane_auto: false }); },
  pane_insp: () => { const off = $("body").classList.toggle("no-insp"); S.cfg.pane_inspector = !off; api.save_settings({ pane_inspector: !off, pane_auto: false }); },
};
function run(a) {
  if (a === "sort-sel" || a === "move") { const ps = selPaths(); return ps.length ? sortDialog(null, ps) : toast("Select photos first."); }
  if (a.startsWith("recent:")) { const r = S.recent[+a.slice(7)]; return r && openFolder(r.folder); }
  if (ACT[a]) ACT[a]();
}
function setView(v) { S.view = v; S.cfg.view = v; api.save_settings({ view: v }); syncSegs(); render(); }
function setOverlay(o) { S.overlay = o; api.save_settings({ overlay: o }); syncSegs(); render(); renderInspector(); }
function setRatio(r) { S.cfg.grid_ratio = r; $("ratio").value = r; api.save_settings({ grid_ratio: r }); S.gridPaths = null; render(); }
function setThumb(n) { S.cfg.thumb_size = n; document.documentElement.style.setProperty("--ts", n + "px"); $("thumbsize").value = n; api.save_settings({ thumb_size: n }); }
function syncSegs() {
  $("viewseg").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
  $("ovseg").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.o === S.overlay));
}
function applyCfg() {
  const rt = document.documentElement, bc = { closed: "#E88D78", down: "#ECD489", open: "#97B881", animal: "#8FB8CA", ignored: "#9ca3af", ...(S.cfg.box_colors || {}) };
  rt.dataset.theme = S.cfg.theme === "light" ? "light" : "dark";
  Object.entries(bc).forEach(([k, v]) => rt.style.setProperty("--bc-" + k, v));
  rt.style.setProperty("--bw", (S.cfg.box_thickness || 3) + "px"); rt.style.setProperty("--bl", (S.cfg.box_label_size || 13) * 0.8 + "px");
  S.view = S.cfg.view === "list" ? "list" : "grid";
  $("body").classList.toggle("no-rail", S.cfg.pane_filters === false); $("body").classList.toggle("no-insp", S.cfg.pane_inspector === false);
  S.overlay = S.cfg.overlay || "box"; document.documentElement.style.setProperty("--ts", (S.cfg.thumb_size || 160) + "px");
  $("thumbsize").value = S.cfg.thumb_size || 160; syncSegs();
  $("ratio").value = S.cfg.grid_ratio || "original";
  $("theme-btn").innerHTML = ico("sun-moon");
  const logo = "assets/icon-" + (S.cfg.theme === "light" ? "light" : "dark") + ".png";
  document.querySelectorAll("img[data-logo]").forEach((im) => { im.src = logo; });
}
function updateActionBar() {
  const n = S.rejected.size; $("actionbar").classList.toggle("hidden", n === 0);
  if (n) $("rej-text").textContent = `${n} rejected · frees ${fmtSize([...S.rejected].reduce((s, p) => s + (S.items.get(p)?.size || 0), 0))}`;
}

/* ---------- menus ---------- */
const MENUS = [
  ["File", [{ l: "Open folder…", k: "Ctrl+O", a: "open_folder" }, { recent: true }, { l: "Library (all folders)", a: "library" }, { l: "Close tab", k: "Ctrl+W", a: "close_folder" }, "-", { l: "Export CSV report…", k: "Ctrl+E", a: "csv" }, { l: "Export contact sheets…", k: "Ctrl+Shift+S", a: "sheets" }, "-", { l: "Settings…", k: "Ctrl+,", a: "settings" }, { l: "Database…", a: "settings_db" }, { l: "Exit", a: "exit" }]],
  ["Edit", [{ l: "Select all", k: "Ctrl+A", a: "all" }, { l: "Select none", k: "Ctrl+D", a: "none" }, { l: "Invert selection", k: "Ctrl+I", a: "invert" }, "-",
    { l: "Select all eyes-closed photos", a: "sel_closed" }, { l: "Select extra copies (keep the best)", a: "sel_dups" }, { l: "Select blurry / misfocused", a: "sel_blur" }, "-",
    { l: "Copy file(s)", k: "Ctrl+C", a: "copyfiles" }, { l: "Rename…", k: "F2", a: "rename" }, { l: "Delete selected…", k: "Del", a: "delete" }, { l: "Remove selected from list", a: "remove" }]],
  ["Scan", [{ l: "Scan", k: "F5", a: "scan" }, { l: "Resume scan (after Stop)", a: "scan" }, { l: "Full re-scan (ignore saved results)", k: "Ctrl+F5", a: "rescan_all" }, { l: "Re-scan selected photos", a: "rescan-sel" }, { l: "Stop", k: "Esc", a: "stop" }, "-",
    { l: "Review…", k: "Enter", a: "review" }, "-", { l: "Sort eyes-closed photos into a folder…", a: "sort_closed" }, { l: "Sort blurry / misfocused photos…", a: "sort_blur" },
    { l: "Sort duplicate extra copies…", a: "sort_dups" }, { l: "Undo last sort", a: "undo" }, "-", { l: "Delete all eyes-closed photos…", a: "del_closed" }, { l: "Delete duplicate extra copies…", a: "del_dups" }]],
  ["View", [{ l: "Thumbnail grid", a: "grid" }, { l: "Detail list", a: "list" }, "-", { l: "Small thumbnails", a: "size_s" }, { l: "Medium thumbnails", a: "size_m" }, { l: "Large thumbnails", a: "size_l" }, "-",
    { l: "Grid: original shape", a: "ratio_original" }, { l: "Grid: 3:2 landscape", a: "ratio_32" }, { l: "Grid: 2:3 portrait", a: "ratio_23" }, { l: "Grid: square", a: "ratio_square" }, "-",
    { l: "Overlay: Box", a: "ov_box" }, { l: "Overlay: Mesh", a: "ov_mesh" }, { l: "Overlay: Detail (jaw, cheeks, forehead…)", a: "ov_detail" }, { l: "Overlay: Off", a: "ov_off" }, "-", { l: "Zoom to face", k: "Space", a: "zoom" }, { l: "People…", a: "people" },
    { l: "Toggle dark / light", k: "Ctrl+T", a: "theme" }, { l: "Search & commands…", k: "Ctrl+K", a: "palette" }, "-", { l: "Filter list", a: "pane_rail" }, { l: "Inspector", a: "pane_insp" }]],
  ["Help", [{ l: "Keyboard shortcuts", k: "F1", a: "shortcuts" }, { l: "How detection works…", a: "detection" }, { l: "Check for updates…", a: "update" }, { l: "About Keeper", a: "about" }]],
];
/* ---------- wiring ---------- */
async function init() {
  api = window.pywebview.api;
  [S.cfg, S.about, S.info] = await Promise.all([api.get_settings(), api.about(), api.system_info()]);
  S.imgBase = S.about.img_base; applyCfg(); $("credit").textContent = S.about.credit.replace(" (www.deev.in)", ""); document.title = S.about.name;
  $("o-recursive").checked = !!S.cfg.recursive; $("o-fast").checked = !S.cfg.robust;
  status(S.info.gpu || "Ready");
  if (S.cfg.check_updates !== false && !init.checked) { init.checked = true; setTimeout(() => checkUpdate(false), 3000); }
  S.recent = await api.recent();
  if (!init.tabs) { init.tabs = true; S.tabs = (S.cfg.tabs || []).map((f) => ({ folder: f, view: null })); renderTabs(); }
  renderLibrary();
}
window.addEventListener("pywebviewready", init);
hydrateIcons(); buildMenus();

$("pick").onclick = () => ACT.open_folder();
$("library").onclick = (e) => {
  const m = e.target.closest("[data-more]"), c = e.target.closest(".lcard"); if (!c) return;
  const r = S.lib[+c.dataset.i]; if (!r) return;
  if (m) { const b = m.getBoundingClientRect(); return showMenu(libMenu(r), b.left, b.bottom); }
  if (r.exists) openFolderTab(r.folder); else toast("That folder isn't available (is the drive connected?).");
};
$("library").addEventListener("contextmenu", (e) => { const c = e.target.closest(".lcard"); if (c) { e.preventDefault(); showMenu(libMenu(S.lib[+c.dataset.i]), e.clientX, e.clientY); } });
$("library").addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("lcard")) { e.preventDefault(); e.target.click(); } });
$("library").addEventListener("error", (e) => { if (e.target.tagName === "IMG") e.target.remove(); }, true);
$("ftabs").onclick = (e) => {
  if (e.target.closest("[data-lib]")) return showLibrary();
  const x = e.target.closest("[data-x]"); if (x) return closeTab(+x.dataset.x);
  const t = e.target.closest(".ft"); if (t) switchTab(+t.dataset.i);
};
$("ftabs").onauxclick = (e) => { const t = e.target.closest(".ft"); if (t && e.button === 1) { e.preventDefault(); closeTab(+t.dataset.i); } };
$("ftabs").addEventListener("contextmenu", (e) => { const t = e.target.closest(".ft"); if (t) { e.preventDefault(); showMenu(tabMenu(+t.dataset.i), e.clientX, e.clientY); } });
$("ftabs").addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("ft")) { e.preventDefault(); switchTab(+e.target.dataset.i); } });
$("theme-btn").onclick = ACT.theme;
$("stop").onclick = ACT.stop;
$("close-folder").onclick = closeFolder;
$("rail").addEventListener("contextmenu", (e) => {
  const it = e.target.closest("[data-person]");
  if (it) { e.preventDefault(); const p = (S.people || []).find((x) => x.id == it.dataset.person); if (p) showMenu(personMenu(p), e.clientX, e.clientY); return; }
  if (e.target.closest("[data-act=people]") || e.target.closest("#rail-people")) { e.preventDefault(); showMenu(peopleListMenu(), e.clientX, e.clientY); }
});
$("rej-clear").onclick = () => { S.rejected.clear(); render(); renderInspector(); };
$("rej-go").onclick = () => confirmTrash([...S.rejected]);
$("search").oninput = (e) => { S.query = e.target.value.trim(); render(); };
$("sort").onchange = (e) => { S.sort = e.target.value; render(); };
$("ratio").onchange = (e) => setRatio(e.target.value);
$("notice").onclick = (e) => { const b = e.target.closest("[data-act]"); if (b) run(b.dataset.act); };
$("viewseg").onclick = (e) => { const b = e.target.closest("button"); if (b) setView(b.dataset.v); };
$("ovseg").onclick = (e) => { const b = e.target.closest("button"); if (b) setOverlay(b.dataset.o); };
$("thumbsize").oninput = (e) => setThumb(+e.target.value);
$("rail").onclick = (e) => { const it = e.target.closest(".item"); if (!it) return;
  if (it.dataset.person) { const p = S.people.find((x) => x.id == it.dataset.person); S.only = new Set(p.photos); S.onlyLabel = "Person " + p.id; S.filter = "all"; return render(); }
  if (it.dataset.act) return run(it.dataset.act); S.filter = it.dataset.k; S.sub = "all"; render(); };
$("rail").oninput = (e) => {
  if (e.target.id !== "sens") return;
  const v = e.target.value / 100; S.cfg.threshold = v; $("sens-val").textContent = e.target.value + "%";
  clearTimeout(sensTimer); sensTimer = setTimeout(async () => { (await api.set_threshold(v)).forEach((r) => S.items.set(r.path, r)); render(); renderInspector(); }, 250);
};
$("subbar").onclick = (e) => { const p = e.target.closest("[data-sub]"); if (p) { S.sub = p.dataset.sub; render(); return; } const a = e.target.closest("[data-act]"); if (a) run(a.dataset.act); };
const rowTarget = (e) => e.target.closest(".tile") || e.target.closest("tr[data-path]");
document.querySelector(".center").addEventListener("mousedown", bandStart);
document.querySelector(".center").addEventListener("click", (e) => { if (S.banded) return; const t = rowTarget(e); if (t) clickSel(t.dataset.path, e); else if (e.target.id === "grid" || e.target.id === "list") setSel([]); });
document.querySelector(".center").addEventListener("dblclick", (e) => { const t = rowTarget(e); if (t) { S.focus = t.dataset.path; reviewOpen(); } });
document.querySelector(".center").addEventListener("contextmenu", (e) => {
  e.preventDefault(); const t = rowTarget(e); if (t && !S.sel.has(t.dataset.path)) clickSel(t.dataset.path, {});
  if (S.sel.size) showMenu(ctxItems(), e.clientX, e.clientY);
  else showMenu([{ l: "Open folder…", k: "Ctrl+O", a: "open_folder" }, { l: "Scan", k: "F5", a: "scan" }, { l: "Select all", k: "Ctrl+A", a: "all" }], e.clientX, e.clientY);
});
$("inspector").addEventListener("click", (e) => {
  const vt = e.target.closest("[data-vt]"); if (vt) return viewerTool(vt.closest(".pv"), vt.dataset.vt);
  const nf = e.target.closest("[data-nameface]");
  if (nf) { const r = S.items.get(S.focus), f = r && r.faces[+nf.dataset.nameface], p = f && (S.people || []).find((x) => x.id === f.person); return p ? nameCluster(p) : toast("Open People… once to group faces, then name them."); }
  const na = e.target.closest("[data-nameanimal]");
  if (na) { const r = S.items.get(S.focus), i = +na.dataset.nameanimal, a = r && r.animals[i];
    return a && nameDialog({ title: "Name this animal", sub: a.label, current: a.name || "", onSave: async (nm) => { const u = await api.tag_animal(r.path, i, nm); if (u) { S.items.set(u.path, u); renderInspector(); scheduleRender(); toast(`Saved “${nm}”`); } },
      onForget: async () => { const u = await api.tag_animal(r.path, i, ""); if (u) { S.items.set(u.path, u); renderInspector(); scheduleRender(); } } }); }
  if (e.target.closest("#pv")) { if (S.dragged) return; return zoomPV($("pv"), S.items.get(S.focus), false); }
  const b = e.target.closest("[data-act]"); if (b) run(b.dataset.act);
});
$("inspector").addEventListener("change", async (e) => {
  if (e.target.dataset.face === undefined) return;
  const u = await api.set_face_override(S.focus, +e.target.dataset.face, e.target.value || null); if (u) { S.items.set(u.path, u); scheduleRender(); renderInspector(); }
});
$("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal" && closeModal) return closeModal();
  const nav = e.target.closest("[data-nav]"); if (nav) return reviewNav(+nav.dataset.nav);
  const vt = e.target.closest("[data-vt]"); if (vt) return viewerTool(vt.closest(".pv"), vt.dataset.vt);
  if (e.target.closest("#rv-pv")) { if (S.dragged) return; return zoomPV($("rv-pv"), S.items.get(S.review), false); }
  const rv = e.target.closest("[data-rv]"); if (rv) { const a = rv.dataset.rv; S.sel = new Set([S.review]); if (a === "move") { run("move"); return; } run(a); setTimeout(drawReview, 250); }
});
const drop = $("drop");
["dragover", "dragenter"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.style.borderColor = "var(--accent-d)"; }));
drop.addEventListener("dragleave", () => { drop.style.borderColor = ""; });
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.style.borderColor = ""; const f = e.dataTransfer.files[0]; const p = f && (f.pywebviewFullPath || f.path); if (p) startScan(p, false); else toast("Use Choose folder to pick a folder."); });
document.addEventListener("contextmenu", (e) => { if (!e.target.closest(".center")) e.preventDefault(); });

document.addEventListener("keydown", (e) => {
  const typing = /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && e.target.type !== "checkbox" && e.target.type !== "range";
  if (e.key === "Escape") { if (closeModal) return closeModal(); $("ctx").classList.add("hidden"); if (S.scanning) ACT.stop(); return; }
  const c = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  if (c && k === "k") { e.preventDefault(); return paletteDialog(); }
  if (c && k === "o") { e.preventDefault(); return ACT.open_folder(); }
  if (c && k === "w") { e.preventDefault(); return closeFolder(); }
  if (c && e.key === "Tab") { e.preventDefault(); return cycleTab(e.shiftKey ? -1 : 1); }
  if (c && /^[1-9]$/.test(e.key) && S.tabs[+e.key - 1]) { e.preventDefault(); return switchTab(+e.key - 1); }
  if (c && k === "e") { e.preventDefault(); return ACT.csv(); }
  if (c && e.shiftKey && k === "s") { e.preventDefault(); return sheetsDialog(); }
  if (c && k === ",") { e.preventDefault(); return settingsDialog(); }
  if (c && k === "t") { e.preventDefault(); return ACT.theme(); }
  if (e.key === "F1") { e.preventDefault(); return ACT.shortcuts(); }
  if (e.key === "F5") { e.preventDefault(); return c ? ACT.rescan_all() : ACT.scan(); }
  if (typing || isStart()) return;
  if (S.review && $("modal") && !$("modal").classList.contains("hidden")) {
    if (e.key === "ArrowRight") return reviewNav(1); if (e.key === "ArrowLeft") return reviewNav(-1);
    if (e.key === " ") { e.preventDefault(); return ACT.zoom(); }
    if (e.key === "Tab") { e.preventDefault(); return zoomPV($("rv-pv"), S.items.get(S.review), true); }
    if (k === "m" && !c) { S.sel = new Set([S.review]); return run("move"); }
    const m = { k: "keep", f: "flag", p: "protect", x: "reject" }[k]; if (m && !c) { S.sel = new Set([S.review]); run(m); setTimeout(drawReview, 250); }
    return;
  }
  if ($("modal") && !$("modal").classList.contains("hidden")) return;
  if (c && k === "a") { e.preventDefault(); return ACT.all(); }
  if (c && k === "d") { e.preventDefault(); return ACT.none(); }
  if (c && k === "i") { e.preventDefault(); return ACT.invert(); }
  if (c && k === "c") { e.preventDefault(); return ACT.copyfiles(); }
  const mv = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols(), ArrowUp: -cols() }[e.key];
  if (mv !== undefined) { e.preventDefault(); return moveSel(mv, e.shiftKey); }
  if (e.key === "Home") { e.preventDefault(); if (S.visible[0]) clickSel(S.visible[0].path, {}); return; }
  if (e.key === "End") { e.preventDefault(); const l = S.visible[S.visible.length - 1]; if (l) clickSel(l.path, {}); return; }
  if (e.key === "Delete") return ACT.delete();
  if (e.key === "F2") return ACT.rename();
  if (e.key === "Enter") return ACT.review();
  if (e.key === " ") { e.preventDefault(); return ACT.zoom(); }
  if (e.key === "Tab" && $("pv")) { e.preventDefault(); return zoomPV($("pv"), S.items.get(S.focus), true); }
  const m = { k: "keep", f: "flag", p: "protect", x: "reject" }[k]; if (m && !c && S.sel.size) run(m);
});
