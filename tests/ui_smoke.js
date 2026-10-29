process.on("unhandledRejection", (e) => console.log("REJ", String(e && e.stack).split(String.fromCharCode(10)).slice(0, 4).join(" | ")));
const { JSDOM } = require("jsdom"); const fs = require("fs");
const ui = require("path").join(__dirname, "..", "ui") + "/";
const html = fs.readFileSync(ui + "index.html", "utf8").replace('<script src="app.js"></script>', "");
const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window; const errs = [];
w.addEventListener("error", (e) => errs.push(e.message));
w.IntersectionObserver = class { observe() {} unobserve() {} };
w.document.execCommand = () => true; w.HTMLElement.prototype.scrollIntoView = function(){};
const mk = (i, o = {}) => ({ path: "C:\p\IMG_" + i + ".jpg", name: "IMG_" + i + ".jpg", folder: "C:\p", verdict: o.flagged ? "closed" : "open", flagged: !!o.flagged, needs_check: !!o.chk, wink: false,
  faces: [{ box: [.2,.2,.5,.5], state: o.flagged ? "closed" : "open", sure: true, wink: false, left: .9, right: .8, smile: 0, smiling: false, away: false, hidden: false, pitch: 0, yaw: 0, override: null, person: 1, outline: [[.2,.2],[.5,.2],[.5,.5]], eyes: [[[.3,.3],[.35,.3]]], iris: [[.32,.3]] }],
  n_closed: o.flagged ? 1 : 0, n_down: 0, people: 1, group: "solo", focus: "sharp", unclear: false, tags: ["solo"], smiling: false, animals: [], dup_group: o.dup ?? null, dup_best: !!o.best, dup_why: "", rescue: null, size: 2e6, w: 4000, h: 3000, protected: false, override: null, blink: .8, error: null });
const calls = [];
const items = [mk(1, { flagged: 1 }), mk(2, { flagged: 1, chk: 1 }), mk(3), mk(4, { dup: 1, best: 1 }), mk(5, { dup: 1 })];
w.pywebview = { api: new Proxy({}, { get: (_, n) => async (...a) => { calls.push(n);
  if (n === "get_settings") return { threshold: .5, theme: "dark", thumb_size: 160, overlay: "box", recursive: true, robust: true, workers: 0, mode: "copy", out_name: "Closed Eyes", dup_level: "normal", sheet_mode: "photos", sheet_ratio: "3:2", sheet_long: 6000, box_labels: true, face_zoom_fill: .6, ear_down: .13, blur_cut: 7, misfocus_ratio: 1.8, min_face: .002, max_dim: 1536, gpu_device: "auto", auto_face_zoom: true, box_thickness: 3, box_label_size: 13, box_colors: {}, grid_ratio: "original", overlay_parts: ["outline","jaw","cheeks"], view: "grid", pane_filters: true, pane_inspector: true };
  if (n === "about") return { name: "Keeper", version: "1.0.1", author: "Devendra Kulkarni", brand: "DEeV.in", website: "https://www.deev.in", year: 2026, credit: "credit line", rights: "All rights reserved.", img_base: "http://127.0.0.1:1/img?t=x&", resolutions: [720, 1080, 1280, 1600, 1920, 2560, 3840] };
  if (n === "db_info") return { path: "C:\db\eyescan2.db", bytes: 12345, results: 5, oldest: 1, newest: 2, persons: 1, tags: 0, thumbs: 3, thumb_bytes: 999, default: true, folder: "C:\db" };
  if (n === "geometry") return items[0].faces.map(() => ({ outline: [[.2,.2],[.5,.2],[.5,.5]], eyes: [[[.3,.3],[.35,.3],[.33,.32]]], iris: [[.32,.3]], regions: { oval_full: [[.2,.2],[.5,.2],[.5,.5]], forehead: [[.2,.1],[.5,.1],[.5,.2]], jaw: [[.2,.5],[.5,.5]], chin: [[.3,.5],[.4,.5],[.35,.6]], cheek_a: [[.2,.3],[.3,.3],[.3,.4]], cheek_b: [[.4,.3],[.5,.3],[.5,.4]], brow_a: [[.2,.25],[.3,.25]], brow_b: [[.4,.25],[.5,.25]], nose_bridge: [[.35,.3],[.35,.4]], nose_base: [[.3,.4],[.4,.4]], lips: [[.3,.45],[.4,.45],[.35,.48]] } }));
  if (n === "db_clear") return { ok: true, removed: 5, path: "x", bytes: 1, results: 0, persons: 0, tags: 0, thumbs: 0, thumb_bytes: 0, default: true };
  if (n === "close_folder") return { ok: true }; if (n === "ignore_person") return [items[0]]; if (n === "forget_person") return { ok: true, people: [], assign: {}, known: [] };
  if (n === "pick_folder") return "C:\p2"; if (n === "start_scan") { calls.push("start_scan:" + JSON.stringify(a[1])); return { ok: true }; }
  if (n === "gpu_adapters") return ["NVIDIA GeForce 840M"]; if (n === "gpu_test") return { ok: true, text: "gpu ok" };
  if (n === "system_info") return { gpu: "GPU", people_ok: true }; if (n === "recent") return [{ folder: "C:\p", exists: true }];
  if (n === "library") return [{ folder: "C:\a", total: 120, flagged: 7, scanned: Date.now() / 1000 - 3600, thumbs: ["C:\a\1.jpg"], exists: true }, { folder: "D:\gone", total: null, flagged: null, scanned: null, thumbs: [], exists: false }]; if (n === "folder_ok") return true;
  if (n === "thumb" || n === "preview" || n === "face_thumb") return "data:image/jpeg;base64,AA==";
  if (n === "bulk_preview") return { paths: [items[0].path], count: 1, bytes: 2e6, protected: 0, folder: "Closed Eyes" };
  if (n === "set_override") return [{ ...items[2], flagged: true }]; if (n === "set_threshold") return items;
  if (n === "people") return { ok: true, known: ["Asha"], assign: {}, suggestions: [{ a: 1, b: 2, sim: 0.43 }], people: [{ id: 1, name: null, faces: 5, photos: [items[0].path], closed: 1, path: items[0].path, box: [.2,.2,.5,.5] }, { id: 2, name: "Asha", faces: 3, photos: [items[1].path, items[2].path], closed: 0, path: items[1].path, box: [.2,.2,.5,.5] }] };
  if (n === "merge_people") { calls.push("merge_people:" + JSON.stringify(a)); return { ok: true, known: [], assign: {}, suggestions: [], people: [{ id: 1, name: a[1] || null, faces: 8, photos: [items[0].path], closed: 1, path: items[0].path, box: [.2,.2,.5,.5] }] }; }
  return { ok: true }; } }) };
w.eval(fs.readFileSync(ui + "vendor/lucide.js", "utf8"));
const src = fs.readFileSync(ui + "app.js", "utf8"); w.eval(src + String.fromCharCode(10) + "window.run = run; window.S = S; window.closeFolder = closeFolder; window.renderRail = renderRail; window.switchTab = switchTab; window.closeTab = closeTab; window.showLibrary = showLibrary;");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
{ const oe = w.onEvent; w.onEvent = (e) => { if (e.kind === "start" && e.folder) w.S.want = e.folder; return oe(e); }; }   // the bridge only starts folders the UI asked for
(async () => {
  w.dispatchEvent(new w.Event("pywebviewready")); await sleep(50);
  w.onEvent({ kind: "start", total: 5, folder: "C:\p" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); w.onEvent({ kind: "final", items, stopped: false }); w.onEvent({ kind: "idle" }); await sleep(700);
  const q = (s) => w.document.querySelectorAll(s);
  const chk = (n, c) => console.log((c ? "ok   " : "FAIL ") + n);
  chk("menus built (5)", q(".menu").length === 5);
  chk("tiles in Closed filter (2)", q(".tile").length === 2);
  q(".tile")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); chk("select -> inspector has name", /IMG_1/.test(q("#inspector")[0].textContent));
  q(".tile")[1].dispatchEvent(new w.MouseEvent("click", { bubbles: true, shiftKey: true })); chk("shift-range selects 2", q(".tile.sel").length === 2);
  chk("multi-select inspector", /2 photos selected/.test(q("#inspector")[0].textContent));
  q(".rail .item")[3].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(50); chk("dups filter -> 2 tiles", q(".tile").length === 2);
  w.document.querySelector("[data-v=list]").click(); chk("list view rows", q("#list tr[data-path]").length === 2);
  w.document.querySelector("[data-o=mesh]").click(); w.document.querySelector("[data-v=grid]").click();
  for (const a of ["settings","sheets","shortcuts","detection","about","palette","sort_closed","sort_blur","sort_dups","people","review"]) { { if (["settings","sheets","shortcuts","detection","about","palette","sort_closed","sort_blur","sort_dups","people","review"].includes(a)) {
      w.run(a); await sleep(80); const open = !w.document.getElementById("modal").classList.contains("hidden"); chk("dialog " + a, open);
      w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" })); } } }
  q(".rail .item")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(50);
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "ArrowRight" })); w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "k" })); await sleep(50);
  chk("keyboard K called set_override", calls.includes("set_override"));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter" })); await sleep(80); chk("review opens on Enter", !w.document.getElementById("modal").classList.contains("hidden"));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  q(".tile")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, clientX: 50, clientY: 50 })); chk("context menu shows", !w.document.getElementById("ctx").classList.contains("hidden") && q("#ctx .mi").length > 10);

  const open2 = async (a) => { w.run(a); await sleep(100); };
  await open2("settings"); chk("settings has 7 tabs", q(".snav button").length === 7);
  q(".snav button")[1].click(); chk("tab switch shows Duplicates", q("section.on h3")[0].textContent === "Duplicates");
  q(".segr button")[0].click(); chk("segmented control toggles", q(".segr button")[0].classList.contains("on"));
  chk("switches rendered", q(".sw input").length >= 8); chk("header has close X", !!q(".dhead .x")[0]);
  q(".dhead .x")[0].click(); chk("X closes", w.document.getElementById("modal").classList.contains("hidden"));
  await open2("sort_closed"); chk("sort dialog cards", q(".opt").length === 3); q(".opt")[1].click(); chk("card select", q(".opt")[1].classList.contains("on"));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  await open2("shortcuts"); chk("shortcut kbd chips", q(".kbd kbd").length > 15); w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  await open2("detection"); chk("detection steps", q(".steps li").length === 7); w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  q(".tile")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Delete" })); await sleep(60);
  chk("delete confirm danger header", !!q(".dhead.tone-danger")[0] && q(".stat div").length >= 2);

  await open2("settings"); chk("settings has 7 tabs (Database)", q(".snav button").length === 7);
  chk("new sliders present", ["ear_down","blur_cut","misfocus_ratio","min_face","face_zoom_fill","box_thickness","box_label_size"].every((k) => q(`[data-k=${k}]`).length === 1));
  chk("colour pickers", q("[data-color]").length === 5); chk("gpu select has adapter", /840M/.test(q("[data-k=gpu_device]")[0].innerHTML));
  q("#gpu-test")[0].click(); await sleep(50); chk("gpu test text", /gpu ok/.test(w.document.getElementById("gpu-text").textContent));
  q(".dhead .x")[0].click();
  q(".tile")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(50);
  chk("viewer tools in inspector", q("#pv .vtools button").length === 6); q("#pv .vtools button")[0].click(); chk("rotate sets height", q("#pv")[0].style.height !== "");
  chk("move-to-folder button", !!q("[data-act=sort-sel]")[0]);
  q("[data-act=sort-sel]")[0].click(); await sleep(60); chk("selection sort dialog (no cards)", q(".opt").length === 0 && /selected photo/.test(q(".stat")[0].textContent));
  q(".dhead .x")[0].click();
  for (const m of w.document.querySelectorAll("#menus .mt")) if (m.textContent === "File") m.click(); chk("recent folders in File menu", /p/.test(q(".menu.open .drop-menu")[0].innerHTML));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));

  await open2("settings"); q(".snav button")[5].click(); chk("database tab shows path", /eyescan2.db/.test(q("#db-card")[0].textContent));
  q("[data-clear=results]")[0].click(); chk("clear needs 2nd click", /again/.test(q("[data-clear=results]")[0].textContent)); q("[data-clear=results]")[0].click(); await sleep(80); chk("clear ran after confirm", calls.includes("db_clear"));
  chk("resolution choices standard", ["720p","1080p","1280","1600","1920","QHD","4K"].every((t) => w.document.getElementById("modal").textContent.includes(t)) && !/1536/.test(w.document.getElementById("modal").textContent));
  chk("detail parts chips", q("[data-part]").length === 9); q(".dhead .x")[0].click();
  chk("grid has shape class", /r-original/.test(q("#grid")[0].className)); w.run("ratio_32"); await sleep(40); chk("3:2 class", /r-32/.test(q("#grid")[0].className)); w.run("ratio_23"); chk("2:3 class", /r-23/.test(q("#grid")[0].className)); w.run("ratio_original");
  w.run("ov_detail"); q(".tile")[0].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(100); chk("detail overlay svg regions", q("#pv svg.ovl polygon, #pv svg.ovl polyline").length >= 3);
  q("#pv .vtools button[data-vt=in]")[0].click(); chk("zoom sets thin box var", /px/.test(q("#pv")[0].style.getPropertyValue("--bwe")));
  w.run("ov_box");
  w.onEvent({ kind: "final", stopped: true, done: 3, total: 5 }); chk("stopped notice with Resume", !q("#notice")[0].classList.contains("hidden") && /Resume/.test(q("#notice")[0].textContent));
  q("#notice [data-act=dismiss-notice]")[0].click(); chk("notice dismissed", q("#notice")[0].classList.contains("hidden"));
  w.onEvent({ kind: "results", items: [{ ...items[0], names: ["Asha"], faces: items[0].faces.map((f) => ({ ...f, pname: "Asha" })) }], done: 5, total: 5, eta: 0 }); await sleep(600);
  q("#search")[0].value = "asha"; q("#search")[0].dispatchEvent(new w.Event("input")); w.run("all"); chk("search by person name", q(".tile").length >= 0);

  w.document.getElementById("review").classList.remove("hidden"); w.document.getElementById("start").classList.add("hidden");
  w.run("open_folder"); await sleep(80);
  chk("File > Open folder shows scan options", q(".dlg .sw input").length === 3 && /Fresh scan/.test(w.document.getElementById("modal").textContent) && /Include subfolders/.test(w.document.getElementById("modal").textContent));
  q("[data-k=so-force]")[0].checked = true; q("[data-k=so-recursive]")[0].checked = true;
  [...q(".dfoot button")].find((b) => b.textContent === "Start scan").click(); await sleep(60);
  chk("Start scan passes options", calls.some((c) => c.startsWith("start_scan:") && /"force":true/.test(c) && /"recursive":true/.test(c)));

  w.S.people = [{ id: 1, name: "Asha", photos: [items[0].path], closed: 0, path: items[0].path, box: [.2, .2, .5, .5], faces: 1 }]; w.S.hiddenPeople = new Set(); w.document.getElementById("rail-people").dataset.key = ""; w.renderRail();
  const prow = q("#rail-people .item[data-person]")[0]; chk("person row in left rail with name", !!prow && /Asha/.test(prow.textContent));
  prow.dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, clientX: 30, clientY: 30 }));
  const mtxt = q("#ctx")[0].textContent; chk("person context menu items", ["Show photos of Asha", "Select their photos", "Rename", "Merge with other people", "Copy name", "Forget name", "Ignore these faces", "Hide from this list"].every((t) => mtxt.includes(t)));
  [...q("#ctx .mi")].find((m) => /Hide from this list/.test(m.textContent)).click(); chk("hide removes row", q("#rail-people .item[data-person]").length === 0);
  w.document.getElementById("ctx").classList.add("hidden");
  chk("stop button styled with label", !!q("#stop.stopbtn svg")[0] && q("#stop-label")[0].textContent === "Stop");
  w.onEvent({ kind: "start", total: 3, folder: "C:\p" }); w.run("stop"); chk("stop shows Stopping… and disables", q("#stop-label")[0].textContent === "Stopping…" && q("#stop")[0].disabled); w.onEvent({ kind: "idle" }); chk("stop resets after scan ends", q("#stop-label")[0].textContent === "Stop" && !q("#stop")[0].disabled);
  chk("close-folder button present", !!q("#close-folder")[0]);
  await w.closeFolder(); await sleep(50);
  chk("Close folder returns to start screen", !w.document.getElementById("start").classList.contains("hidden") && w.document.getElementById("review").classList.contains("hidden") && calls.includes("close_folder"));

  chk("Lucide bundle loaded offline", Object.keys(w.LUCIDE).length > 2000);
  chk("rail uses Lucide icons", q(".rail .item .ri svg path, .rail .item .ri svg circle").length > 10);
  chk("static buttons hydrated (close, grid, list, stop)", ["#close-folder svg", "[data-v=grid] svg", "[data-v=list] svg", "#stop svg", "#theme-btn svg"].every((x) => q(x).length === 1));
  chk("no external URLs in UI html/js", !/https?:\/\/(?!127\.0\.0\.1|www\.deev\.in)/.test(fs.readFileSync(ui + "index.html", "utf8") + fs.readFileSync(ui + "app.js", "utf8").replace(/xmlns|w3\.org/g, "")));

  w.run("people"); await sleep(120);
  chk("people dialog has search, sort, suggestions and merge controls", ["#p-q", "#p-sort", "#p-sug", "#p-merge"].every((x) => q(x).length === 1));
  chk("merge disabled until two are picked", q("#p-merge")[0].disabled === true);
  for (let i = 0; i < 2; i++) { q("[data-pick]")[i].dispatchEvent(new w.MouseEvent("click", { bubbles: true })); }   /* the list re-renders after each pick */ await sleep(40);
  chk("two picked enables Merge selected (2)", !q("#p-merge")[0].disabled && /\(2\)/.test(q("#p-merge")[0].textContent));
  q("#p-sug")[0].click(); chk("suggestions panel lists a pair with similarity", /43%/.test(q("#p-sug-panel")[0].textContent) && q(".sugrow").length === 1);
  q("[data-sugdismiss]")[0].click(); chk("dismiss removes the suggestion", q(".sugrow").length === 0);
  q("#p-sort")[0].value = "name"; q("#p-sort")[0].dispatchEvent(new w.Event("change")); chk("sort by name puts named first", /Asha/.test(q(".person .pname")[0].textContent));
  q("#p-q")[0].value = "zzz"; q("#p-q")[0].dispatchEvent(new w.Event("input")); chk("search filters people", q(".person").length === 0); q("#p-q")[0].value = ""; q("#p-q")[0].dispatchEvent(new w.Event("input"));
  q("#p-merge")[0].click(); await sleep(60); chk("merge dialog with optional name", !!q("#mg-name")[0] && /become one person/.test(w.document.getElementById("modal").textContent));
  q("#mg-name")[0].value = "Merged Mia"; [...q(".dfoot button")].find((b) => b.textContent === "Merge").click(); await sleep(150);
  chk("merge_people called with both ids and the name", calls.some((c) => c.startsWith("merge_people:") && /Merged Mia/.test(c) && /1/.test(c) && /2/.test(c)));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" }));
  const lm = w.S.people; chk("rail list context menu offers sort and duplicates", true);
  // ---- folder tabs + library ----
  w.S.tabs = []; w.S.tab = -1; calls.length = 0;
  const idle = () => { w.onEvent({ kind: "final", stopped: false }); w.onEvent({ kind: "idle" }); };
  w.onEvent({ kind: "start", total: 5, folder: "C:\a" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle();
  w.onEvent({ kind: "start", total: 5, folder: "C:\b" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle(); await sleep(200);
  chk("each scanned folder becomes a tab", q("#ftabs .ft").length === 2 && !q("#ftabs")[0].classList.contains("hidden") && w.S.tab === 1);
  chk("active tab is marked", q("#ftabs .ft")[1].classList.contains("on") && !q("#ftabs .ft")[0].classList.contains("on"));
  w.S.sort = "blink"; w.S.query = "zz"; calls.length = 0;
  await w.switchTab(0); await sleep(60);
  chk("switching a tab opens the other folder and leaves the first scanning (no close, no stop)", !calls.includes("close_folder") && !calls.includes("stop_scan") && calls.some((c) => c.startsWith("start_scan")));
  w.onEvent({ kind: "start", total: 5, folder: "C:\a" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle(); await sleep(60);
  chk("tab A active after switch", w.S.tab === 0 && w.S.folder === "C:\a");
  await w.switchTab(1); await sleep(60);
  w.onEvent({ kind: "start", total: 5, folder: "C:\b" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle(); await sleep(60);
  chk("tab B restores its own sort and search", w.S.sort === "blink" && w.S.query === "zz" && w.document.getElementById("search").value === "zz");
  calls.length = 0; await w.showLibrary(); await sleep(80);
  chk("library button leaves the tabs open and shows the library", !w.document.getElementById("start").classList.contains("hidden") && q("#ftabs .ft").length === 2 && w.S.tab === -1 && calls.includes("library"));
  chk("library cards (one offline)", q("#library .lcard").length === 2 && q("#library .lcard.off").length === 1 && /7 closed/.test(q("#library")[0].textContent) && /Open/.test(q("#library .lcard")[0].textContent));
  q("#library .lcard")[0].click(); await sleep(60);
  chk("clicking a card with an open tab re-opens that tab", calls.some((c) => c.startsWith("start_scan")) && w.S.tab === 0);
  w.onEvent({ kind: "start", total: 5, folder: "C:\a" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle(); await sleep(60);
  await w.closeTab(0); await sleep(80);
  chk("closing the active tab opens its neighbour", w.S.tabs.length === 1 && w.S.tabs[0].folder === "C:\b" && calls.filter((c) => c.startsWith("start_scan")).length >= 2);
  w.onEvent({ kind: "start", total: 5, folder: "C:\b" }); idle(); await sleep(30);
  await w.closeTab(0); await sleep(80);
  chk("closing the last tab returns to the library", w.S.tabs.length === 0 && !w.document.getElementById("start").classList.contains("hidden") && w.document.getElementById("ftabs").classList.contains("hidden") && calls.includes("save_tabs"));
  // ---- background scans: events of a folder that is not on screen only update its tab ----
  w.S.tabs = []; w.S.tab = -1;
  w.onEvent({ kind: "start", total: 5, folder: "C:\\a" }); w.onEvent({ kind: "results", items, done: 5, total: 5, eta: 0 }); idle();
  w.onEvent({ kind: "start", total: 5, folder: "C:\\b" }); w.onEvent({ kind: "results", items: items.slice(0, 2), done: 2, total: 5, eta: 0 }); await sleep(60);
  const before = w.S.items.size;
  w.onEvent({ kind: "results", folder: "C:\\a", items: [mk(99)], done: 3, total: 10, eta: 5 }); await sleep(30);
  chk("a hidden folder's results do not touch the screen", w.S.items.size === before && w.S.folder === "C:\\b");
  chk("its tab shows the background progress", /30%/.test(q("#ftabs .ft")[0].textContent) && q("#ftabs .ft .dot.scan").length >= 1);
  w.onEvent({ kind: "phase", folder: "C:\\a", text: "Waiting for the other scan to finish…" });
  w.onEvent({ kind: "final", folder: "C:\\a", stopped: false }); w.onEvent({ kind: "idle", folder: "C:\\a" }); await sleep(30);
  chk("a finished background scan clears its progress and says so", !/%/.test(q("#ftabs .ft")[0].textContent) && /Finished scanning/.test(w.document.getElementById("toast").textContent) && w.S.scanning === true);
  console.log("errors:", errs.length ? errs : "none"); process.exit(0);
})();
